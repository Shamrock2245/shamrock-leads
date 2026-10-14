"""
One co-signer link: ID scan, then DocuSeal, then the down payment.

The scan uses the same name rule as Write Bond (#165). O'Neill does not
match O'Neal. A failed or mismatched scan does not unlock signing and does
not store a passing ``id_ocr``. The full ID number is never stored.

Pay-before-sign is not offered. Spanish and English share this flow.
Customer texting stays on BlueBubbles behind ``SWIPESIMPLE_DISPATCH_LIVE``.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from dashboard.extensions import get_collection
from dashboard.services.identity_verification_service import (
    _reject_full_id,
    names_match,
)
from dashboard.services.premium_confirmation_service import invoice_due_cents
from dashboard.services.swipesimple_invoice_service import dispatch_live_enabled
from dashboard.tenancy.context import current_tenant_id
from dashboard.tenancy.flag import multi_tenant_enabled

logger = logging.getLogger(__name__)

LINKS = "cosigner_links"
_SIGNED = frozenset({"signed", "completed", "complete"})

STRINGS = {
    "en": {
        "title": "Sign and pay",
        "scan_title": "Scan your ID",
        "scan_help": "Signing stays locked until the name on your ID matches this bond.",
        "sign_title": "Sign the bond",
        "pay_title": "Pay the down payment",
        "pay_dry_run": "The payment link is not being texted. Amount due",
        "mismatch": "That ID does not match the co-signer on this bond.",
        "paid": "Payment received",
        "locked": "Signing is locked until the ID matches.",
    },
    "es": {
        "title": "Firmar y pagar",
        "scan_title": "Escanee su identificación",
        "scan_help": "La firma permanece bloqueada hasta que el nombre de su identificación coincida con esta fianza.",
        "sign_title": "Firme la fianza",
        "pay_title": "Pague el enganche",
        "pay_dry_run": "El enlace de pago no se envía por mensaje. Monto a pagar",
        "mismatch": "Esa identificación no coincide con el cofirmante de esta fianza.",
        "paid": "Pago recibido",
        "locked": "La firma está bloqueada hasta que la identificación coincida.",
    },
}


class CosignerLinkError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _scope(query: dict) -> Optional[dict]:
    if not multi_tenant_enabled():
        return query
    tenant = str(current_tenant_id() or "").strip()
    if not tenant:
        return None
    return {**query, "tenant_id": tenant}


def _lang(value: Any) -> str:
    return "es" if str(value or "").strip().lower().startswith("es") else "en"


def _indemnitor_name(packet: dict, bond: Optional[dict]) -> str:
    for doc in (packet, bond or {}):
        if not isinstance(doc, dict):
            continue
        for key in ("indemnitor_name", "indemnitor"):
            raw = doc.get(key)
            if isinstance(raw, dict):
                name = str(raw.get("name") or raw.get("full_name") or "").strip()
                if name:
                    return name
            elif str(raw or "").strip():
                return str(raw).strip()
    return ""


def _sign_url(packet: dict) -> str:
    for key in ("docuseal_sign_url", "sign_url"):
        url = str(packet.get(key) or "").strip()
        if url.startswith("http"):
            return url
    parties = packet.get("parties") or packet.get("submitters") or []
    if isinstance(parties, list):
        for row in parties:
            if isinstance(row, dict):
                url = str(row.get("sign_url") or row.get("embed_src") or "").strip()
                if url.startswith("http"):
                    return url
    return ""


def _signed(packet: dict) -> bool:
    status = str(packet.get("status") or packet.get("docuseal_status") or "").strip().lower()
    return status in _SIGNED


def _paid(bond: Optional[dict]) -> bool:
    if not isinstance(bond, dict):
        return False
    if bond.get("premium_paid") is True:
        return True
    status = str(bond.get("payment_status") or bond.get("last_payment_status") or "").strip().lower()
    return status in {"paid", "approved", "completed", "complete"}


async def _find_packet(packet_id: str) -> Optional[dict]:
    query = _scope({"packet_id": packet_id})
    if query is None:
        raise CosignerLinkError("tenant_required", 403)
    return await get_collection("paperwork_packets").find_one(query)


async def _find_bond(packet: dict) -> Optional[dict]:
    booking = str(packet.get("booking_number") or "").strip()
    case_id = str(packet.get("bond_case_id") or "").strip()
    query = {"$or": []}
    if booking:
        query["$or"].append({"booking_number": booking})
    if case_id:
        query["$or"].append({"bond_case_id": case_id})
    if not query["$or"]:
        return None
    scoped = _scope(query)
    if scoped is None:
        raise CosignerLinkError("tenant_required", 403)
    return await get_collection("bond_cases").find_one(scoped)


def _timeline_push(link: dict, step: str, now: datetime) -> bool:
    rows = link.get("timeline")
    if not isinstance(rows, list):
        rows = []
        link["timeline"] = rows
    if any(isinstance(row, dict) and row.get("step") == step for row in rows):
        return False
    rows.append({"step": step, "at": now.isoformat()})
    return True


async def _audit(step: str, link: dict, now: datetime) -> None:
    await get_collection("audit_events").insert_one({
        "event_type": step,
        "action": step,
        "entity_type": "cosigner_link",
        "entity_id": link.get("packet_id") or "",
        "actor": "cosigner_link",
        "agent_name": "cosigner_link",
        "timestamp": now,
        "created_at": now.isoformat(),
        "booking_number": link.get("booking_number") or "",
        "packet_id": link.get("packet_id") or "",
        "reason": step,
        "confidence": 1.0,
    })


async def create_cosigner_link(*, actor: str, packet_id: str, lang: str = "en") -> dict:
    packet = await _find_packet(packet_id)
    if not packet:
        raise CosignerLinkError("packet_not_found", 404)
    bond = await _find_bond(packet)
    name = _indemnitor_name(packet, bond)
    if not name:
        raise CosignerLinkError("indemnitor_name_required", 400)
    now = _now()
    token = secrets.token_urlsafe(24)
    doc = {
        "token": token,
        "packet_id": packet_id,
        "booking_number": str(packet.get("booking_number") or ""),
        "bond_case_id": str(packet.get("bond_case_id") or ""),
        "indemnitor_name": name,
        "lang": _lang(lang),
        "created_by": actor,
        "created_at": now.isoformat(),
        "timeline": [],
        "id_verified": False,
    }
    if multi_tenant_enabled():
        doc["tenant_id"] = current_tenant_id()
    await get_collection(LINKS).insert_one(doc)
    logger.info("[cosigner] link created packet_id=%s", packet_id)
    return {
        "token": token,
        "url": f"/cosigner/{token}",
        "packet_id": packet_id,
        "lang": doc["lang"],
    }


async def _link_by_token(token: str) -> dict:
    query = _scope({"token": token})
    if query is None:
        raise CosignerLinkError("tenant_required", 403)
    link = await get_collection(LINKS).find_one(query)
    if not link:
        raise CosignerLinkError("link_not_found", 404)
    return link


async def _save_link(link: dict) -> None:
    await get_collection(LINKS).update_one(
        {"token": link.get("token")},
        {"$set": {
            "timeline": link.get("timeline") or [],
            "id_verified": bool(link.get("id_verified")),
            "lang": link.get("lang") or "en",
            "id_verified_at": link.get("id_verified_at") or "",
            "paid_transaction_id": link.get("paid_transaction_id") or "",
        }},
    )


def _passing_scan(name: str, state: str) -> dict:
    return {
        "success": True,
        "role": "indemnitor",
        "extracted": {
            "full_name": name,
            "dl_state": state,
        },
    }


async def _store_scan(packet: dict, bond: Optional[dict], name: str, state: str) -> None:
    patch = {
        "id_ocr_role": "indemnitor",
        "id_ocr": _passing_scan(name, state),
    }
    await get_collection("paperwork_packets").update_one(
        {"packet_id": packet.get("packet_id")},
        {"$set": patch},
    )
    if bond:
        filt = {"bond_case_id": bond.get("bond_case_id")} if bond.get("bond_case_id") else {
            "booking_number": bond.get("booking_number"),
        }
        await get_collection("bond_cases").update_one(filt, {"$set": patch})


async def submit_cosigner_scan(token: str, body: dict) -> dict:
    """Accept a scan only when it matches the co-signer. Otherwise the link stays locked."""
    if not isinstance(body, dict):
        raise CosignerLinkError("invalid_scan", 400)
    rejected = _reject_full_id(body)
    if rejected:
        raise CosignerLinkError(rejected, 400)
    link = await _link_by_token(token)
    if body.get("lang"):
        link["lang"] = _lang(body.get("lang"))
    packet = await _find_packet(str(link.get("packet_id") or ""))
    if not packet:
        raise CosignerLinkError("packet_not_found", 404)
    success = body.get("success") is True or body.get("passed") is True
    name = str(body.get("full_name") or body.get("name") or "").strip()
    state = str(body.get("issuing_state") or body.get("dl_state") or "").strip()
    expected = str(link.get("indemnitor_name") or "")
    if not success or not names_match(name, expected):
        logger.info("[cosigner] scan locked packet_id=%s", link.get("packet_id"))
        await _save_link(link)
        raise CosignerLinkError("name_mismatch", 409)
    if not state:
        raise CosignerLinkError("issuing_state_required", 400)
    bond = await _find_bond(packet)
    await _store_scan(packet, bond, name, state.upper())
    now = _now()
    link["id_verified"] = True
    link["id_verified_at"] = now.isoformat()
    if _timeline_push(link, "id_verified", now):
        await _audit("id_verified", link, now)
    await _save_link(link)
    logger.info("[cosigner] id verified packet_id=%s", link.get("packet_id"))
    return await cosigner_state(token, lang=link.get("lang"))


async def note_cosigner_paid(booking_number: str, transaction_id: str) -> None:
    """Stamp paid on every open link for this booking. Idempotent."""
    booking = str(booking_number or "").strip()
    if not booking:
        return
    query = _scope({"booking_number": booking})
    if query is None:
        return
    cursor = get_collection(LINKS).find(query)
    now = _now()
    async for link in _iter(cursor):
        if _timeline_push(link, "paid", now):
            link["paid_transaction_id"] = transaction_id
            await _save_link(link)
            await _audit("paid", link, now)


async def _iter(cursor):
    if hasattr(cursor, "to_list"):
        for row in await cursor.to_list(length=50):
            if isinstance(row, dict):
                yield row
        return
    if hasattr(cursor, "__aiter__"):
        async for row in cursor:
            if isinstance(row, dict):
                yield row
        return


def _payment_view(packet: dict, bond: Optional[dict]) -> dict:
    cents = invoice_due_cents(packet)
    if cents is None and bond:
        cents = invoice_due_cents(bond)
    amount = f"{(cents or 0) / 100:.2f}" if cents is not None else ""
    link = ""
    for doc in (packet, bond or {}):
        raw = str((doc or {}).get("swipesimple_payment_link") or "").strip()
        if raw.startswith("http"):
            link = raw
            break
    dry_run = not dispatch_live_enabled()
    return {
        "amount": amount,
        "dry_run": dry_run,
        "dispatched": False if dry_run else bool(link),
        "url": link or None,
    }


async def cosigner_state(token: str, lang: Optional[str] = None) -> dict:
    link = await _link_by_token(token)
    if lang:
        link["lang"] = _lang(lang)
    packet = await _find_packet(str(link.get("packet_id") or "")) or {}
    bond = await _find_bond(packet) if packet else None
    language = _lang(link.get("lang"))
    verified = bool(link.get("id_verified"))
    signed = _signed(packet)
    paid = _paid(bond)
    now = _now()
    changed = False
    if signed and _timeline_push(link, "signed", now):
        await _audit("signed", link, now)
        changed = True
    if paid and _timeline_push(link, "paid", now):
        await _audit("paid", link, now)
        changed = True
    if changed or lang:
        await _save_link(link)
    if paid and verified:
        step = "paid"
    elif signed and verified:
        step = "pay"
    elif verified:
        step = "sign"
    else:
        step = "scan"
    sign_url = _sign_url(packet) if verified else None
    return {
        "lang": language,
        "strings": STRINGS[language],
        "step": step,
        "id_verified": verified,
        "sign_url": sign_url,
        "signing_locked": not verified,
        "payment": _payment_view(packet, bond) if step in {"pay", "paid"} else None,
        "timeline": [row.get("step") for row in (link.get("timeline") or []) if isinstance(row, dict)],
    }


def render_cosigner_page(state: dict, token: str) -> str:
    """One page. The language toggle reloads the same token in English or Spanish."""
    strings = state.get("strings") or STRINGS["en"]
    lang = state.get("lang") or "en"
    other = "es" if lang == "en" else "en"
    other_label = "Español" if other == "es" else "English"
    step = state.get("step")
    sign_url = state.get("sign_url") or ""
    payment = state.get("payment") or {}
    if step == "scan":
        body = (
            f"<h1>{strings['scan_title']}</h1>"
            f"<p>{strings['scan_help']}</p>"
            f"<p>{strings['locked']}</p>"
            "<form method='post' enctype='multipart/form-data' action='/api/cosigner/"
            + token + "/scan'>"
            "<input type='hidden' name='lang' value='" + lang + "'>"
            "<label>ID photo <input type='file' name='id_image' accept='image/*' required></label>"
            "<button type='submit'>Continue</button></form>"
        )
    elif step == "sign" and sign_url:
        body = (
            f"<h1>{strings['sign_title']}</h1>"
            f"<p><a href='{sign_url}'>DocuSeal</a></p>"
        )
    elif step == "pay":
        note = strings["pay_dry_run"] if payment.get("dry_run") else strings["pay_title"]
        url = payment.get("url") or ""
        pay_link = f"<p><a href='{url}'>Pay</a></p>" if url else ""
        body = (
            f"<h1>{strings['pay_title']}</h1>"
            f"<p>{note} {payment.get('amount') or ''}</p>"
            f"{pay_link}"
        )
    else:
        body = f"<h1>{strings['paid']}</h1><p>{payment.get('amount') or ''}</p>"
    return (
        "<!DOCTYPE html><html lang='" + lang + "'><head><meta charset='utf-8'>"
        f"<title>{strings['title']}</title></head><body>"
        f"<p><a href='/cosigner/{token}?lang={other}'>{other_label}</a></p>"
        f"{body}</body></html>"
    )
