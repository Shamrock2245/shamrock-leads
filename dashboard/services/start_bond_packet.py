"""Four-step start-bond-packet helper.

Reuses hydrate-from-roster, write-bond preflight, and DocuSeal submission.
Each send stores a new paperwork packet (``PKT-<bond case>-v<n>``). A sent
or signed packet from this flow is not edited; staff void it before another
send. It does not send a text, charge a card, or mark a POA used. Flag off,
the router hides it. ``POST /api/write-bond`` stays retired.
"""

from __future__ import annotations

from datetime import datetime, timezone
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


class PacketStartConflict(PacketStartError):
    """A non-voided packet from this flow is already sent or signed."""

    def __init__(self, packet_id: str):
        self.packet_id = packet_id
        self.message = (
            f"Packet {packet_id} is already sent or signed. "
            "Void it before sending a new packet."
        )
        super().__init__("void_required")


_FLOW = "start_bond_packet"
_OPEN_STATUSES = frozenset({"sent", "signed"})
_OPEN_DOCUSEAL = frozenset({"sent", "signed", "completed"})
_VOID_STATUSES = frozenset({"voided", "cancelled", "canceled"})


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
    suggestions = await suggest_poa(sid, poa_filter)
    suggestions = await _with_bond_poa(suggestions, defendant["booking_number"], sid)
    return {
        "defendant": defendant,
        "surety_id": sid,
        "sureties": sureties,
        "poa_suggestions": suggestions,
        "messages_sent": 0,
    }


def _text(doc: dict | None, *keys: str) -> str:
    for key in keys:
        value = str((doc or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def _party_name(doc: dict | None) -> str:
    full = _text(doc, "name", "full_name", "Full_Name")
    if full:
        return full
    return " ".join(
        part for part in (_text(doc, "first_name", "First_Name"), _text(doc, "last_name", "Last_Name")) if part
    )


async def _find_one(name: str, filt: dict) -> dict | None:
    from dashboard.extensions import get_collection

    doc = await get_collection(name).find_one(filt)
    return doc if isinstance(doc, dict) else None


async def _bond_doc(booking_number: str, bond_case_id: str = "") -> dict | None:
    if bond_case_id:
        doc = await _find_one(
            "active_bonds",
            {"$or": [{"Bond_Case_ID": bond_case_id}, {"bond_case_id": bond_case_id}]},
        )
        if doc:
            return doc
        return await _find_one(
            "bond_cases",
            {"$or": [{"Bond_Case_ID": bond_case_id}, {"bond_case_id": bond_case_id}]},
        )
    return await _find_one(
        "active_bonds",
        {"$or": [{"Booking_Number": booking_number}, {"booking_number": booking_number}]},
    )


async def _with_bond_poa(suggestions: list[dict], booking_number: str, surety_id: str) -> list[dict]:
    """Keep the power already on the bond selectable so it can match preflight."""
    bond = await _bond_doc(booking_number)
    number = _text(bond, "POA_Number", "poa_number")
    bound_surety = _text(bond, "Surety_ID", "surety_id").lower()
    if not number or (bound_surety and bound_surety != surety_id):
        return suggestions
    if any(row.get("poa_number") == number for row in suggestions):
        return suggestions
    row = await _find_one("poa_inventory", {"poa_number": number, "surety_id": surety_id})
    status = _text(row, "status")
    if status not in {"available", "assigned", "used"}:
        return suggestions
    return [
        {
            "poa_number": number,
            "surety_id": surety_id,
            "max_bond_value": (row or {}).get("max_bond_value"),
            "status": status,
        },
        *suggestions,
    ][:5]


async def _authoritative_binding(
    *,
    booking_number: str,
    bond_case_id: str,
    surety_id: str,
    poa_number: str,
    staff_email: str,
) -> dict[str, Any]:
    """Fields create_submission_for_packet requires, taken from the bond and parties."""
    bond = await _bond_doc(booking_number, bond_case_id)
    if not bond:
        raise PacketStartError("binding_incomplete")
    data = {
        "bond_case_id": _text(bond, "Bond_Case_ID", "bond_case_id"),
        "booking_number": _text(bond, "Booking_Number", "booking_number") or booking_number,
        "case_number": _text(bond, "Case_Number", "case_number"),
        "surety_id": _text(bond, "Surety_ID", "surety_id").lower(),
        "poa_number": _text(bond, "POA_Number", "poa_number"),
        "defendant_id": _text(bond, "Defendant_ID", "defendant_id"),
        "indemnitor_id": _text(bond, "Indemnitor_ID", "indemnitor_id"),
        "match_id": _text(bond, "Match_ID", "match_id"),
    }
    if data["surety_id"] != surety_id or data["poa_number"] != poa_number:
        raise PacketStartError("poa_not_available")
    if any(not data[key] for key in data):
        raise PacketStartError("binding_incomplete")
    match = await _find_one(
        "matches",
        {"$or": [{"Match_ID": data["match_id"]}, {"match_id": data["match_id"]}]},
    )
    if _text(match, "Status", "status").lower() != "validated":
        raise PacketStartError("binding_incomplete")
    defendant = await _find_one(
        "defendants",
        {"$or": [{"Defendant_ID": data["defendant_id"]}, {"defendant_id": data["defendant_id"]}]},
    )
    indemnitor = await _find_one(
        "indemnitors",
        {"$or": [{"Indemnitor_ID": data["indemnitor_id"]}, {"indemnitor_id": data["indemnitor_id"]}]},
    )
    defendant_name = _party_name(defendant)
    defendant_email = _text(defendant, "email", "Email")
    indemnitor_name = _party_name(indemnitor)
    indemnitor_email = _text(indemnitor, "email", "Email")
    if "@" not in defendant_email or "@" not in indemnitor_email or not defendant_name or not indemnitor_name:
        raise PacketStartError("binding_incomplete")
    if staff_email.strip().lower() != indemnitor_email.lower():
        raise PacketStartError("indemnitor_required")
    data["match_status"] = "validated"
    data["defendant_name"] = defendant_name
    data["indemnitor_name"] = indemnitor_name
    data["indemnitor_email"] = indemnitor_email
    data["defendant"] = {"name": defendant_name, "email": defendant_email}
    data["indemnitor"] = {
        "name": indemnitor_name,
        "email": indemnitor_email,
        "phone": _text(indemnitor, "phone", "Phone"),
    }
    return data


def _sign_links(submission: Any) -> list[dict[str, str]]:
    """Signer URLs live on submitters[].sign_url. A top-level signing_url is ignored."""
    submitters = submission.get("submitters") if isinstance(submission, dict) else None
    if not isinstance(submitters, list):
        return []
    links = []
    for item in submitters:
        if not isinstance(item, dict):
            continue
        url = str(item.get("sign_url") or "")
        if not url.startswith("https://"):
            continue
        links.append({"role": str(item.get("role") or ""), "sign_url": url})
    return links


def _version_number(packet_id: str, bond_case_id: str) -> int | None:
    prefix = f"PKT-{bond_case_id}-v"
    if not packet_id.startswith(prefix):
        return None
    tail = packet_id[len(prefix):]
    if not tail.isdigit() or int(tail) < 1:
        return None
    return int(tail)


def _from_this_flow(doc: dict, bond_case_id: str) -> bool:
    """Packets this flow created, including the pre-version bond-case id."""
    if str(doc.get("packet_flow") or "") == _FLOW:
        return True
    packet_id = str(doc.get("packet_id") or "")
    if packet_id == bond_case_id:
        return True
    return _version_number(packet_id, bond_case_id) is not None


def _is_voided(doc: dict) -> bool:
    if doc.get("voided") is True:
        return True
    return str(doc.get("status") or "").strip().lower() in _VOID_STATUSES


def _blocks_new_send(doc: dict) -> bool:
    if _is_voided(doc):
        return False
    status = str(doc.get("status") or "").strip().lower()
    docuseal = str(doc.get("docuseal_status") or "").strip().lower()
    return status in _OPEN_STATUSES or docuseal in _OPEN_DOCUSEAL


def _occupied_version(doc: dict, bond_case_id: str) -> int:
    parsed = _version_number(str(doc.get("packet_id") or ""), bond_case_id)
    if parsed:
        return parsed
    stored = doc.get("packet_version")
    if isinstance(stored, int) and not isinstance(stored, bool) and stored > 0:
        return stored
    return 0


async def _packets_for_case(bond_case_id: str) -> list[dict]:
    from dashboard.extensions import get_collection

    cursor = get_collection("paperwork_packets").find({"bond_case_id": bond_case_id})
    rows = await _rows(cursor)
    return [row for row in rows if isinstance(row, dict)]


async def _reserve_packet_id(bond_case_id: str) -> tuple[str, int]:
    """Next ``PKT-<bond case>-v<n>``.

    A non-voided sent or signed packet from this flow blocks the send.
    The id is one this collection does not already hold, so the packet_id
    upsert cannot edit a sent, signed, or voided row.
    """
    rows = await _packets_for_case(bond_case_id)
    ours = [row for row in rows if _from_this_flow(row, bond_case_id)]
    blocking = [row for row in ours if _blocks_new_send(row)]
    if blocking:
        blocking.sort(key=lambda row: _occupied_version(row, bond_case_id))
        raise PacketStartConflict(str(blocking[-1].get("packet_id") or bond_case_id))
    version = 1 + max((_occupied_version(row, bond_case_id) for row in ours), default=0)
    taken = {str(row.get("packet_id") or "") for row in rows}
    packet_id = f"PKT-{bond_case_id}-v{version}"
    while packet_id in taken:
        version += 1
        packet_id = f"PKT-{bond_case_id}-v{version}"
    return packet_id, version


async def _store_packet(
    binding: dict,
    template_id: str,
    submission: dict,
    links: list[dict],
    packet_id: str,
    version: int,
) -> str:
    from dashboard.extensions import get_collection

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    submission_id = str((submission or {}).get("submission_id") or "")
    col = get_collection("paperwork_packets")
    await col.update_one(
        {"packet_id": packet_id},
        {
            "$set": {
                "packet_id": packet_id,
                "bond_case_id": binding["bond_case_id"],
                "packet_version": version,
                "packet_flow": _FLOW,
                "booking_number": binding["booking_number"],
                "case_number": binding["case_number"],
                "match_id": binding["match_id"],
                "match_status": "validated",
                "defendant_id": binding["defendant_id"],
                "indemnitor_id": binding["indemnitor_id"],
                "surety_id": binding["surety_id"],
                "poa_number": binding["poa_number"],
                "esign_provider": "docuseal",
                "docuseal_template_id": str(template_id),
                "docuseal_submission_id": submission_id,
                "docuseal_submitters": (submission or {}).get("submitters") if isinstance(submission, dict) else [],
                "docuseal_status": "sent",
                "docuseal_sent_at": now,
                "status": "sent",
                "updated_at": now,
                "sign_links": links,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return packet_id


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
    # Before POA suggestion and before the DocuSeal submit.
    from dashboard.services.identity_verification_service import require_verified_indemnitors

    await require_verified_indemnitors(
        parties=[{"role": "indemnitor", "name": name}],
        bond_data=payload,
        bond_case_id=str(payload.get("bond_case_id") or ""),
        booking_number=str(payload.get("booking_number") or ""),
        packet_id=str(payload.get("packet_id") or ""),
    )
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
    details = pre.get("details") if isinstance(pre.get("details"), dict) else {}
    if str(details.get("poa_number") or "").strip() != poa_number:
        raise PacketStartError("poa_not_available")
    binding = await _authoritative_binding(
        booking_number=prepared["defendant"]["booking_number"],
        bond_case_id=str(details.get("bond_case_id") or payload.get("bond_case_id") or ""),
        surety_id=prepared["surety_id"],
        poa_number=poa_number,
        staff_email=email,
    )
    from dashboard.services.docuseal_service import resolve_template_id_for_surety
    from dashboard.tenancy.context import current_tenant_id

    template_id = resolve_template_id_for_surety(prepared["surety_id"], current_tenant_id())
    if not template_id:
        raise PacketStartError("template_unavailable")
    packet_id, version = await _reserve_packet_id(binding["bond_case_id"])
    submit_fn = submit or _default_submit
    submission = await submit_fn(
        template_id=template_id,
        packet_id=packet_id,
        bond_data={
            "bond_case_id": binding["bond_case_id"],
            "match_id": binding["match_id"],
            "match_status": binding["match_status"],
            "defendant_id": binding["defendant_id"],
            "indemnitor_id": binding["indemnitor_id"],
            "case_number": binding["case_number"],
            "poa_number": binding["poa_number"],
            "booking_number": binding["booking_number"],
            "surety_id": binding["surety_id"],
            "defendant_name": binding["defendant_name"],
            "indemnitor_name": binding["indemnitor_name"],
            "indemnitor_email": binding["indemnitor_email"],
        },
        indemnitors=[binding["indemnitor"]],
        defendant=binding["defendant"],
        send_email=False,
    )
    links = _sign_links(submission)
    packet_id = await _store_packet(
        binding,
        template_id,
        submission if isinstance(submission, dict) else {},
        links,
        packet_id,
        version,
    )
    pay = await _pay_link(prepared["defendant"]["booking_number"])
    return {
        "state": "ready_for_staff_send",
        "sent": False,
        "messages_sent": 0,
        "packet_id": packet_id,
        "signing_url": links[0]["sign_url"] if links else "",
        "sign_links": links,
        "pay_url": pay["pay_url"],
        "pay_status": pay["pay_status"],
        "poa_number": poa_number,
        "poa_status": "available",
        "defendant_name": prepared["defendant"]["defendant_name"],
        "surety_id": prepared["surety_id"],
    }
