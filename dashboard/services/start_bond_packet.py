"""Four-step start-bond-packet helper.

Reuses hydrate-from-roster, write-bond preflight, and DocuSeal submission.
It does not send a text, charge a card, or mark a POA used. Flag off, the
router hides it. ``POST /api/write-bond`` stays retired.
"""

from __future__ import annotations

from typing import Any

from dashboard.services.surety_entitlements import SuretyEntitlementError, assert_entitled, entitled_ids
from dashboard.services.surety_registry import normalize_surety, picker_options
from dashboard.tenancy.flag import multi_tenant_enabled

_PAY_KEYS = ("swipesimple_payment_link", "invoice_payment_link", "payment_link")


class PacketStartError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PacketStartDisabled(PacketStartError):
    def __init__(self):
        super().__init__("disabled")


def _name_from_arrest(doc: dict) -> str:
    full = str(doc.get("full_name") or doc.get("Full_Name") or doc.get("defendant_name") or "").strip()
    if full:
        return full
    first = str(doc.get("first_name") or doc.get("First_Name") or "").strip()
    last = str(doc.get("last_name") or doc.get("Last_Name") or "").strip()
    if first and last:
        return f"{first} {last}"
    return ""


async def _rows(cursor) -> list[dict]:
    if hasattr(cursor, "__aiter__"):
        return [doc async for doc in cursor]
    return list(cursor)


async def hydrate_booking(booking_number: str) -> dict[str, str]:
    from dashboard.extensions import get_collection

    booking = str(booking_number or "").strip()
    if not booking:
        raise PacketStartError("booking_required")
    cursor = get_collection("arrests").find(
        {"$or": [{"booking_number": booking}, {"Booking_Number": booking}]}
    )
    matches = await _rows(cursor)
    if len(matches) > 1:
        raise PacketStartError("ambiguous_identity")
    if not matches:
        raise PacketStartError("missing_identity")
    name = _name_from_arrest(matches[0])
    if not name:
        raise PacketStartError("missing_identity")
    return {
        "booking_number": booking,
        "defendant_name": name,
        "county": str(matches[0].get("county") or matches[0].get("County") or ""),
        "state": str(matches[0].get("state") or matches[0].get("State") or ""),
    }


async def suggest_poa(surety_id: str, extra_filter: dict | None = None) -> list[dict[str, Any]]:
    from dashboard.extensions import get_collection

    sid = normalize_surety(surety_id)
    if not sid:
        return []
    filt: dict[str, Any] = {"surety_id": sid, "status": "available"}
    if extra_filter:
        filt.update(extra_filter)
    rows = await _rows(get_collection("poa_inventory").find(filt))
    suggestions = []
    for row in rows:
        number = str(row.get("poa_number") or "").strip()
        if not number:
            continue
        suggestions.append(
            {
                "poa_number": number,
                "surety_id": sid,
                "max_bond_value": row.get("max_bond_value"),
                "status": "available",
            }
        )
        if len(suggestions) == 5:
            break
    return suggestions


async def _pay_link(booking_number: str) -> dict[str, str]:
    from dashboard.extensions import get_collection

    doc = await get_collection("active_bonds").find_one(
        {"$or": [{"booking_number": booking_number}, {"Booking_Number": booking_number}]}
    )
    if isinstance(doc, dict):
        for key in _PAY_KEYS:
            url = str(doc.get(key) or "")
            if url.startswith("https://"):
                return {"pay_url": url, "pay_status": "on_file"}
    return {"pay_url": "", "pay_status": "not_ready"}


async def _default_preflight(**kwargs):
    from dashboard.services.write_bond_forward_service import preflight_write_bond_forward

    return await preflight_write_bond_forward(**kwargs)


async def _default_submit(**kwargs):
    from dashboard.services.docuseal_service import get_docuseal_service

    return await get_docuseal_service().create_submission_for_packet(**kwargs)


async def prepare_packet(booking_number: str, surety_id: str, *, poa_filter: dict | None = None) -> dict:
    if not multi_tenant_enabled():
        raise PacketStartDisabled()
    from dashboard.services.agency_billing import suspension_block

    if await suspension_block():
        raise PacketStartError("tenant_suspended")
    sid = normalize_surety(surety_id)
    try:
        await assert_entitled(sid)
    except SuretyEntitlementError as exc:
        raise PacketStartError(exc.code) from exc
    defendant = await hydrate_booking(booking_number)
    allowed = await entitled_ids()
    sureties = [
        {"id": row["id"], "label": row["short"]}
        for row in picker_options()
        if row["id"] in allowed and row.get("active")
    ]
    return {
        "defendant": defendant,
        "surety_id": sid,
        "sureties": sureties,
        "poa_suggestions": await suggest_poa(sid, poa_filter),
        "messages_sent": 0,
    }


async def send_packet(
    payload: dict,
    *,
    poa_filter: dict | None = None,
    preflight=None,
    submit=None,
) -> dict:
    if not multi_tenant_enabled():
        raise PacketStartDisabled()
    if not isinstance(payload, dict) or payload.get("confirmed") is not True:
        raise PacketStartError("staff_confirmation_required")
    indemnitor = payload.get("indemnitor") or {}
    if not isinstance(indemnitor, dict):
        raise PacketStartError("indemnitor_required")
    name = str(indemnitor.get("name") or "").strip()
    email = str(indemnitor.get("email") or "").strip()
    if not name or "@" not in email:
        raise PacketStartError("indemnitor_required")
    poa_number = str(payload.get("poa_number") or "").strip()
    prepared = await prepare_packet(
        str(payload.get("booking_number") or ""),
        str(payload.get("surety_id") or ""),
        poa_filter=poa_filter,
    )
    numbers = {row["poa_number"] for row in prepared["poa_suggestions"]}
    if poa_number not in numbers:
        raise PacketStartError("poa_not_available")
    preflight_fn = preflight or _default_preflight
    pre = await preflight_fn(
        booking_number=prepared["defendant"]["booking_number"],
        bond_case_id=str(payload.get("bond_case_id") or "") or None,
    )
    if not isinstance(pre, dict) or pre.get("state") != "eligible_for_staff_approval":
        return {
            "state": "blocked",
            "block_reasons": (pre or {}).get("block_reasons") if isinstance(pre, dict) else ["preflight_rejected"],
            "sent": False,
            "messages_sent": 0,
            "poa_status": "available",
        }
    submit_fn = submit or _default_submit
    submission = await submit_fn(
        template_id=payload.get("template_id") or "template",
        packet_id=str(payload.get("packet_id") or prepared["defendant"]["booking_number"]),
        bond_data={
            "surety_id": prepared["surety_id"],
            "booking_number": prepared["defendant"]["booking_number"],
            "defendant_name": prepared["defendant"]["defendant_name"],
            "indemnitor_name": name,
            "indemnitor_email": email,
            "poa_number": poa_number,
        },
        indemnitors=[{"name": name, "email": email, "phone": str(indemnitor.get("phone") or "")}],
        send_email=False,
    )
    signing_url = ""
    if isinstance(submission, dict):
        signing_url = str(submission.get("signing_url") or submission.get("url") or "")
        if signing_url and not signing_url.startswith("https://"):
            signing_url = ""
    pay = await _pay_link(prepared["defendant"]["booking_number"])
    return {
        "state": "ready_for_staff_send",
        "sent": False,
        "messages_sent": 0,
        "signing_url": signing_url,
        "pay_url": pay["pay_url"],
        "pay_status": pay["pay_status"],
        "poa_number": poa_number,
        "poa_status": "available",
        "defendant_name": prepared["defendant"]["defendant_name"],
        "surety_id": prepared["surety_id"],
    }
