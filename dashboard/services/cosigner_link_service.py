"""
One co-signer link: ID scan, then DocuSeal, then the down payment.

The scan uses the same name rule as Write Bond (#165). O'Neill does not
match O'Neal. Signing stays locked until the scan passed, the name matches
the co-signer, and the issuer is a US state, DC, or territory. A failed
scan, a wrong name, and a country-only scan (US/USA) do not store ``id_ocr``.
The full ID number is never stored. When the co-signer is a different person
from the indemnitor, only the co-signer's scan unlocks this link.

Pay-before-sign is not offered. Spanish and English share this flow.
Customer texting stays on BlueBubbles behind ``SWIPESIMPLE_DISPATCH_LIVE``.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional
from urllib.parse import urlparse

from dashboard.extensions import get_collection
from dashboard.services.docuseal_service import DEFAULT_DOCUSEAL_URL
from dashboard.services.identity_verification_service import (
    OPEN_BOND_CASE_STATUSES,
    _jurisdiction,
    _reject_full_id,
    names_match,
)
from dashboard.services.payment_links import case_invoice_link
from dashboard.services.premium_confirmation_service import invoice_due_cents
from dashboard.services.swipesimple_invoice_service import dispatch_live_enabled
from dashboard.tenancy.context import current_tenant_id
from dashboard.tenancy.flag import multi_tenant_enabled

logger = logging.getLogger(__name__)

LINKS = "cosigner_links"
_LINK_TTL = timedelta(days=7)
SCAN_ATTEMPT_CAP = 5
SCAN_COOLDOWN = timedelta(seconds=30)
_SIGNED = frozenset({"signed", "completed", "complete"})
_FAIL_STATUS = frozenset({"failed", "fail", "error", "unreadable", "rejected"})
_CLOSED_PACKET = frozenset({
    "void",
    "voided",
    "cancelled",
    "canceled",
    "superseded",
})
_AUDIT_STATE_KEYS = (
    "step",
    "id_verified",
    "signed",
    "paid",
    "payment_reversed",
    "revoked",
    "timeline",
    "packet_status",
)
_COSIGNER_KEYS = (
    "cosigner_name",
    "co_signer_name",
    "coindemnitor_name",
    "co_indemnitor_name",
)
_COSIGNER_ROLES = frozenset({
    "cosigner",
    "co_signer",
    "co-signer",
    "coindemnitor",
    "co-indemnitor",
    "co_indemnitor",
})

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


def _person_name(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("name") or raw.get("full_name") or "").strip()
    return str(raw or "").strip()


def _indemnitor_name(packet: dict, bond: Optional[dict]) -> str:
    for doc in (packet, bond or {}):
        if not isinstance(doc, dict):
            continue
        for key in ("indemnitor_name", "indemnitor"):
            name = _person_name(doc.get(key))
            if name:
                return name
    return ""


def _listed_people(doc: dict) -> list:
    rows = []
    for key in ("indemnitors", "parties", "submitters"):
        raw = doc.get(key)
        if isinstance(raw, list):
            rows.extend(item for item in raw if isinstance(item, dict))
    return rows


def _cosigner_name(packet: dict, bond: Optional[dict]) -> str:
    """The co-signer when the packet names one. Otherwise empty."""
    for doc in (packet, bond or {}):
        if not isinstance(doc, dict):
            continue
        for key in _COSIGNER_KEYS:
            name = _person_name(doc.get(key))
            if name:
                return name
        for person in _listed_people(doc):
            role = str(person.get("role") or person.get("party_role") or "").strip().lower()
            if role in _COSIGNER_ROLES:
                name = _person_name(person)
                if name:
                    return name
        for key in ("coindemnitor", "co_indemnitor", "cosigner", "co_signer"):
            name = _person_name(doc.get(key))
            if name:
                return name
    return ""


def _expected_signer(packet: dict, bond: Optional[dict]) -> tuple[str, str]:
    """Who this link is for. A named co-signer is not the indemnitor."""
    cosigner = _cosigner_name(packet, bond)
    if cosigner:
        return cosigner, "co_indemnitor"
    return _indemnitor_name(packet, bond), "indemnitor"


def _party_role(row: dict) -> str:
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    raw = str(
        row.get("role") or row.get("party_role") or meta.get("party_role") or ""
    ).strip().lower().replace("-", "_")
    if raw in {"coindemnitor", "co_indemnitor", "cosigner", "co_signer"}:
        return "co_indemnitor"
    if raw == "indemnitor":
        return "indemnitor"
    return raw


def _configured_docuseal_origin() -> str:
    """Public DocuSeal origin from ``DOCUSEAL_URL``, otherwise the app default."""
    raw = (os.getenv("DOCUSEAL_URL") or DEFAULT_DOCUSEAL_URL).strip()
    if raw and "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return ""
    return f"https://{host}"


def _url_on_configured_docuseal_host(url: str) -> str:
    """A direct ``/s/{slug}`` link on the configured DocuSeal host, or empty."""
    raw = str(url or "").strip()
    origin = _configured_docuseal_origin()
    if not raw or not origin:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or f"https://{host}" != origin
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or len(parts) != 2
        or parts[0] != "s"
        or not parts[1]
    ):
        return ""
    return raw


def _row_submission_id(row: dict) -> str:
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    direct = str(row.get("submission_id") or "").strip()
    nested = str(meta.get("submission_id") or "").strip()
    if direct and nested and direct != nested:
        return ""
    return direct or nested


def _external_id_matches(packet_id: str, role: str, external_id: str) -> bool:
    if role not in {"indemnitor", "co_indemnitor"} or not external_id:
        return False
    return external_id.startswith(f"{packet_id}:indemnitor:")


def _row_meta(row: dict) -> dict:
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return meta


def _declared_party_role(row: dict) -> str:
    """A party role stored beside the DocuSeal role. Blank when the row has none."""
    meta = _row_meta(row)
    raw = str(row.get("party_role") or meta.get("party_role") or "").strip()
    if not raw:
        return ""
    return _party_role({"role": raw})


def _row_signer_name(row: dict) -> str:
    meta = _row_meta(row)
    return str(row.get("name") or row.get("full_name") or meta.get("name") or "").strip()


def _row_signer_email(row: dict) -> str:
    meta = _row_meta(row)
    return str(row.get("email") or meta.get("email") or "").strip().lower()


def _submitter_matches_signer(
    row: dict,
    role: str,
    signer_name: str,
    signer_email: str = "",
) -> bool:
    """The row's name, and its role or email when stored, must be this signer."""
    if _party_role(row) != role:
        return False
    declared = _declared_party_role(row)
    if declared and declared != role:
        return False
    if not names_match(_row_signer_name(row), signer_name):
        return False
    row_email = _row_signer_email(row)
    expected = str(signer_email or "").strip().lower()
    if row_email and expected and row_email != expected:
        return False
    return True


def _signer_email(packet: dict, bond: Optional[dict], role: str) -> str:
    """Email stored for this signer. Blank when the case has none."""
    keys = (
        ("co_signer_email", "cosigner_email", "co_indemnitor_email")
        if role == "co_indemnitor"
        else ("indemnitor_email",)
    )
    for doc in (bond, packet):
        if not isinstance(doc, dict):
            continue
        for key in keys:
            text = str(doc.get(key) or "").strip()
            if text:
                return text
        people = doc.get("indemnitors")
        if not isinstance(people, list):
            continue
        for person in people:
            if isinstance(person, dict) and _party_role(person) == role:
                text = str(person.get("email") or "").strip()
                if text:
                    return text
    return ""


def _bound_submitter_url(packet: dict, row: dict, role: str) -> str:
    """Return this row's URL only when it is bound to this packet and role."""
    packet_id = str(packet.get("packet_id") or "").strip()
    expected_submission = str(packet.get("docuseal_submission_id") or "").strip()
    if not packet_id or not expected_submission or _party_role(row) != role:
        return ""
    meta = _row_meta(row)
    if str(meta.get("packet_id") or "").strip() != packet_id:
        return ""
    submission_id = _row_submission_id(row)
    if not submission_id or submission_id != expected_submission:
        return ""
    if not _external_id_matches(packet_id, role, str(row.get("external_id") or "").strip()):
        return ""
    stored = str(row.get("sign_url") or "").strip() or str(row.get("embed_src") or "").strip()
    return _url_on_configured_docuseal_host(stored)


def _sign_url(
    packet: dict,
    role: str,
    signer_name: str,
    signer_email: str = "",
) -> tuple[str, str]:
    """The bound DocuSeal URL for this signer, or an error code and no URL.

    A row for someone else is ``sign_link_mismatch``. A row that is not bound
    to this packet is ``sign_link_unbound``.
    """
    wanted = "co_indemnitor" if role == "co_indemnitor" else "indemnitor"
    mismatch = False
    for key in ("docuseal_submitters", "parties", "submitters"):
        rows = packet.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or _party_role(row) != wanted:
                continue
            if not _submitter_matches_signer(row, wanted, signer_name, signer_email):
                mismatch = True
                continue
            url = _bound_submitter_url(packet, row, wanted)
            if url:
                return url, ""
    if mismatch:
        return "", "sign_link_mismatch"
    return "", "sign_link_unbound"


def _signed(packet: dict) -> bool:
    status = str(packet.get("status") or packet.get("docuseal_status") or "").strip().lower()
    return status in _SIGNED


def _link_paid(link: dict) -> bool:
    """The down-payment stamp. A reversal reopens the pay step."""
    if link.get("payment_reversed") is True:
        return False
    if str(link.get("paid_transaction_id") or "").strip():
        return True
    rows = link.get("timeline") or []
    return any(isinstance(row, dict) and row.get("step") == "paid" for row in rows)


def _paid(bond: Optional[dict]) -> bool:
    """The full premium flag only. An approved charge is not this down payment."""
    return isinstance(bond, dict) and bond.get("premium_paid") is True


def _as_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _link_block(link: dict) -> str:
    """Expired or revoked bearer links are not a public OCR endpoint."""
    status = str(link.get("status") or "").strip().lower()
    if (
        link.get("revoked") is True
        or status in {"revoked", "expired"}
        or str(link.get("revoked_at") or "").strip()
    ):
        return "link_revoked" if status != "expired" else "link_expired"
    if link.get("expires_at"):
        expires = _as_utc(link.get("expires_at"))
        if expires is None or expires <= _now():
            return "link_expired"
    return ""


def _packet_closed(packet: Optional[dict]) -> bool:
    """A voided, canceled, or superseded packet is not signable."""
    if not isinstance(packet, dict) or not packet:
        return False
    if packet.get("voided") is True or packet.get("superseded") is True:
        return True
    for key in ("status", "docuseal_status", "packet_status"):
        if str(packet.get(key) or "").strip().lower() in _CLOSED_PACKET:
            return True
    return False


def _audit_state(link: dict, packet: Optional[dict] = None) -> dict:
    """Workflow snapshot. Full ID numbers are never copied onto the audit."""
    timeline = [
        str(row.get("step"))
        for row in (link.get("timeline") or [])
        if isinstance(row, dict) and row.get("step")
    ]
    verified = bool(link.get("id_verified"))
    signed = "signed" in timeline
    reversed_pay = link.get("payment_reversed") is True
    paid = not reversed_pay and (
        "paid" in timeline or bool(str(link.get("paid_transaction_id") or "").strip())
    )
    if paid and signed:
        step = "paid"
    elif signed:
        step = "pay"
    elif verified:
        step = "sign"
    else:
        step = "scan"
    state = {
        "step": step,
        "id_verified": verified,
        "signed": signed,
        "paid": paid,
        "payment_reversed": reversed_pay,
        "revoked": link.get("revoked") is True,
        "timeline": timeline,
        "packet_status": str((packet or {}).get("status") or ""),
    }
    return {key: state[key] for key in _AUDIT_STATE_KEYS}


async def _find_packet(packet_id: str) -> Optional[dict]:
    query = _scope({"packet_id": packet_id})
    if query is None:
        raise CosignerLinkError("tenant_required", 403)
    return await get_collection("paperwork_packets").find_one(query)


async def _matching_bonds(query: dict) -> list:
    scoped = _scope(query)
    if scoped is None:
        raise CosignerLinkError("tenant_required", 403)
    found = []
    for name in ("bond_cases", "active_bonds"):
        cursor = get_collection(name).find(scoped)
        async for doc in _iter(cursor):
            doc["_collection"] = name
            found.append(doc)
    return found


async def _find_bond(packet: dict) -> Optional[dict]:
    """The packet's case id wins. Booking is used only when no case id exists.

    Two bond rows that share a booking number are not a guess. That fallback
    fails closed.
    """
    booking = str(packet.get("booking_number") or "").strip()
    case_id = str(packet.get("bond_case_id") or "").strip()
    if case_id:
        for name in ("bond_cases", "active_bonds"):
            scoped = _scope({"bond_case_id": case_id})
            if scoped is None:
                raise CosignerLinkError("tenant_required", 403)
            doc = await get_collection(name).find_one(scoped)
            if doc:
                doc["_collection"] = name
                return doc
        return None
    if not booking:
        return None
    matches = await _matching_bonds({"booking_number": booking})
    if len(matches) > 1:
        raise CosignerLinkError("bond_ambiguous", 409)
    return matches[0] if matches else None


def _timeline_push(link: dict, step: str, now: datetime) -> bool:
    rows = link.get("timeline")
    if not isinstance(rows, list):
        rows = []
        link["timeline"] = rows
    if any(isinstance(row, dict) and row.get("step") == step for row in rows):
        return False
    rows.append({"step": step, "at": now.isoformat()})
    return True


async def _audit(
    step: str,
    link: dict,
    now: datetime,
    *,
    old_state: dict,
    new_state: dict,
    actor: str = "cosigner_link",
    session=None,
) -> None:
    kwargs = {"session": session} if session is not None else {}
    await get_collection("audit_events").insert_one({
        "event_type": step,
        "action": step,
        "entity_type": "cosigner_link",
        "entity_id": link.get("packet_id") or "",
        "actor": actor or "cosigner_link",
        "agent_name": "cosigner_link",
        "timestamp": now,
        "created_at": now.isoformat(),
        "booking_number": link.get("booking_number") or "",
        "packet_id": link.get("packet_id") or "",
        "reason": step,
        "old_state": _audit_state_only(old_state),
        "new_state": _audit_state_only(new_state),
        "confidence": 1.0,
    }, **kwargs)


def _audit_state_only(state: dict) -> dict:
    """Keep the workflow delta. Drop anything that could be an ID number."""
    if not isinstance(state, dict):
        return {}
    clean = {}
    for key in _AUDIT_STATE_KEYS:
        if key not in state:
            continue
        value = state[key]
        if key == "timeline":
            clean[key] = [str(step) for step in value] if isinstance(value, list) else []
        elif key in {"id_verified", "signed", "paid", "payment_reversed", "revoked"}:
            clean[key] = bool(value)
        else:
            clean[key] = str(value or "")
    return clean


def _bond_field(bond: dict, *keys: str) -> str:
    if not isinstance(bond, dict):
        return ""
    for key in keys:
        text = str(bond.get(key) or "").strip()
        if text:
            return text
    return ""


def _bond_issuance_gap(bond: dict) -> str:
    """A public link needs a validated match, a case number, a surety, and a POA."""
    status = _bond_field(bond, "match_status", "Match_Status").lower()
    if status != "validated":
        return "match_unvalidated"
    if not _bond_field(bond, "case_number", "Case_Number"):
        return "case_number_required"
    if not _bond_field(bond, "surety_id", "Surety_ID"):
        return "surety_required"
    if not _bond_field(bond, "poa_number", "POA_Number", "power_number"):
        return "poa_required"
    return ""


async def create_cosigner_link(*, actor: str, packet_id: str, lang: str = "en") -> dict:
    packet = await _find_packet(packet_id)
    if not packet:
        raise CosignerLinkError("packet_not_found", 404)
    if _packet_closed(packet):
        raise CosignerLinkError("packet_inactive", 409)
    bond = await _find_bond(packet)
    if not bond:
        raise CosignerLinkError("bond_required", 409)
    # The packet and the bonded case must name the same signer. The packet
    # does not win a disagreement, and a missing bond does not get a token.
    packet_name, packet_role = _expected_signer(packet, None)
    bond_name, bond_role = _expected_signer(bond, None)
    if (
        packet_role != bond_role
        or not packet_name
        or not bond_name
        or not names_match(packet_name, bond_name)
    ):
        raise CosignerLinkError("signer_mismatch", 409)
    if _bond_closed(bond):
        raise CosignerLinkError("bond_inactive", 409)
    # A signed packet cannot accept the ID scan this link requires.
    if _signed(packet):
        raise CosignerLinkError("packet_signed", 409)
    gap = _bond_issuance_gap(bond)
    if gap:
        raise CosignerLinkError(gap, 409)
    name, role = packet_name, packet_role
    now = _now()
    token = secrets.token_urlsafe(24)
    # The packet may have no case id. The bond resolved above is the case
    # the payment lookup has to find later.
    doc = {
        "token": token,
        "packet_id": packet_id,
        "booking_number": str(bond.get("booking_number") or packet.get("booking_number") or ""),
        "bond_case_id": str(bond.get("bond_case_id") or packet.get("bond_case_id") or ""),
        "indemnitor_name": _indemnitor_name(packet, bond) or name,
        "signer_name": name,
        "signer_role": role,
        "lang": _lang(lang),
        "created_by": actor,
        "created_at": now.isoformat(),
        "expires_at": (now + _LINK_TTL).isoformat(),
        "timeline": [],
        "id_verified": False,
    }
    if multi_tenant_enabled():
        doc["tenant_id"] = current_tenant_id()
    await get_collection(LINKS).insert_one(doc)
    await _audit(
        "link_issued",
        doc,
        now,
        old_state=_blank_audit_state(packet),
        new_state=_audit_state(doc, packet),
        actor=actor,
    )
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
    blocked = _link_block(link)
    if blocked:
        status = 410
        raise CosignerLinkError(blocked, status)
    return link


def _scan_locked(link: dict, packet: dict) -> bool:
    """A passing scan, or a signed packet, does not accept another upload."""
    return bool(link.get("id_verified")) or _signed(packet)


def _bond_closed(bond: Optional[dict]) -> bool:
    """Open is the kanban set. A missing or unknown status is closed."""
    if not isinstance(bond, dict):
        return True
    status = str(bond.get("status") or "").strip().lower()
    return status not in OPEN_BOND_CASE_STATUSES


def _blank_audit_state(packet: Optional[dict] = None) -> dict:
    """No link yet. Issuance records the transition into the scan step."""
    return {
        "step": "",
        "id_verified": False,
        "signed": False,
        "paid": False,
        "payment_reversed": False,
        "revoked": False,
        "timeline": [],
        "packet_status": str((packet or {}).get("status") or ""),
    }


def _link_role(link: dict) -> str:
    role = str(link.get("signer_role") or "indemnitor").strip()
    return role if role == "co_indemnitor" else "indemnitor"


async def _require_current_bond(link: dict, packet: dict) -> dict:
    """The bond must still exist, still be open, and still name this signer."""
    bond = await _find_bond(packet)
    if not bond:
        raise CosignerLinkError("bond_required", 409)
    if _bond_closed(bond):
        raise CosignerLinkError("bond_inactive", 409)
    packet_name, packet_role = _expected_signer(packet, None)
    bond_name, bond_role = _expected_signer(bond, None)
    if (
        packet_role != bond_role
        or packet_role != _link_role(link)
        or not names_match(packet_name, bond_name)
        or not names_match(bond_name, str(link.get("signer_name") or ""))
    ):
        raise CosignerLinkError("signer_mismatch", 409)
    if _issued_case_drifted(link, packet, bond):
        raise CosignerLinkError("bond_mismatch", 409)
    return bond


def _issued_case_drifted(link: dict, packet: dict, bond: dict) -> bool:
    """The packet or bond is no longer the case this link was issued for."""
    stored_case = str(link.get("bond_case_id") or "").strip()
    stored_booking = str(link.get("booking_number") or "").strip()
    bond_case = str(bond.get("bond_case_id") or "").strip()
    bond_booking = str(bond.get("booking_number") or "").strip()
    packet_case = str(packet.get("bond_case_id") or "").strip()
    packet_booking = str(packet.get("booking_number") or "").strip()
    if stored_case and bond_case != stored_case:
        return True
    if stored_case and packet_case and packet_case != stored_case:
        return True
    if stored_booking and bond_booking != stored_booking:
        return True
    if stored_booking and packet_booking and packet_booking != stored_booking:
        return True
    return False


async def _held_by_other(packet: dict, token: str) -> bool:
    """Another link already claimed this packet's verification."""
    owner = str(packet.get("cosigner_verified_token") or "").strip()
    if owner and owner != str(token or "").strip():
        return True
    query = _scope({"packet_id": str(packet.get("packet_id") or "")})
    if query is None:
        raise CosignerLinkError("tenant_required", 403)
    cursor = get_collection(LINKS).find(query)
    async for other in _iter(cursor):
        if str(other.get("token") or "") == str(token or ""):
            continue
        if other.get("id_verified"):
            return True
    return False


async def _claim_verification(packet: dict, token: str) -> bool:
    """One token owns the packet scan. A second token does not overwrite it."""
    token = str(token or "").strip()
    # Empty to owned only. A second request for this same token does not
    # pass because the token is already stored.
    result = await get_collection("paperwork_packets").update_one(
        {
            "packet_id": packet.get("packet_id"),
            "cosigner_verified_token": {"$in": [None, ""]},
        },
        {"$set": {"cosigner_verified_token": token}},
    )
    if int(getattr(result, "modified_count", 0) or 0) > 0:
        packet["cosigner_verified_token"] = token
        return True
    return False


def _party_slot(role: str) -> str:
    return "co_indemnitor" if role == "co_indemnitor" else "indemnitor"


def _scan_owner(doc: Optional[dict]) -> str:
    """Role that currently owns the top-level scan. Empty when there is none."""
    if not isinstance(doc, dict):
        return ""
    role = str(doc.get("id_ocr_role") or "").strip()
    ocr = doc.get("id_ocr")
    if role and isinstance(ocr, dict) and ocr:
        return role
    return ""


def _with_party_scan(existing: Any, role: str, scan: dict) -> dict:
    if isinstance(existing, dict):
        party = dict(existing)
    elif isinstance(existing, str) and existing.strip():
        party = {"name": existing.strip()}
    else:
        party = {}
    party["role"] = role
    party["id_ocr_role"] = role
    party["id_ocr"] = scan
    return party


async def _write_party_scan(
    collection: str,
    filt: dict,
    doc: dict,
    role: str,
    scan: dict,
    stored: list,
) -> None:
    """Store this party's scan without erasing another party's top-level scan."""
    slot = _party_slot(role)
    prior_party = doc.get(slot)
    wrote_top = _scan_owner(doc) in {"", role}
    party = _with_party_scan(prior_party, role, scan)
    patch = {slot: party}
    prior_top = None
    if wrote_top:
        prior_top = {
            "id_ocr": doc.get("id_ocr"),
            "id_ocr_role": doc.get("id_ocr_role"),
        }
        patch["id_ocr"] = scan
        patch["id_ocr_role"] = role
    stored.append({
        "collection": collection,
        "filter": filt,
        "doc": doc,
        "slot": slot,
        "prior_party": prior_party,
        "wrote_top": wrote_top,
        "prior_top": prior_top,
    })
    await get_collection(collection).update_one(filt, {"$set": patch})
    doc[slot] = party
    if wrote_top:
        doc["id_ocr"] = scan
        doc["id_ocr_role"] = role


async def _clear_stored_scan(stored: list) -> None:
    """Undo scans from a verification that did not finish.

    A party scan written beside an existing scan is removed. The other
    party's top-level ``id_ocr`` is put back.
    """
    for snap in reversed(stored):
        doc = snap.get("doc") if isinstance(snap.get("doc"), dict) else {}
        update: dict = {}
        unset: dict = {}
        slot = snap["slot"]
        prior = snap.get("prior_party")
        if prior is None:
            unset[slot] = ""
            doc.pop(slot, None)
        else:
            update[slot] = prior
            doc[slot] = prior
        if snap.get("wrote_top"):
            prior_top = snap.get("prior_top") or {}
            if isinstance(prior_top.get("id_ocr"), dict) and prior_top.get("id_ocr"):
                update["id_ocr"] = prior_top["id_ocr"]
                update["id_ocr_role"] = prior_top.get("id_ocr_role") or ""
                doc["id_ocr"] = update["id_ocr"]
                doc["id_ocr_role"] = update["id_ocr_role"]
            else:
                unset["id_ocr"] = ""
                unset["id_ocr_role"] = ""
                doc.pop("id_ocr", None)
                doc.pop("id_ocr_role", None)
        payload: dict = {}
        if update:
            payload["$set"] = update
        if unset:
            payload["$unset"] = unset
        if payload:
            await get_collection(snap["collection"]).update_one(snap["filter"], payload)


async def _restore_sibling_links(priors: list) -> None:
    for prior in priors:
        await get_collection(LINKS).update_one(
            {"token": prior.get("token")},
            {"$set": {
                "revoked": prior.get("revoked") is True,
                "status": prior.get("status") or "",
                "revoked_at": prior.get("revoked_at") or "",
            }},
        )


async def _drop_partial_audits(packet_id: str, created_at: str) -> None:
    """Remove audits for a verification that did not finish."""
    coll = get_collection("audit_events")
    for event_type in ("id_verified", "link_revoked"):
        query = {
            "event_type": event_type,
            "packet_id": packet_id,
            "created_at": created_at,
        }
        while int(getattr(await coll.delete_one(query), "deleted_count", 0) or 0):
            continue


async def _release_verification_claim(packet: dict, token: str) -> None:
    """Drop a claim that did not finish verification, so the scan can be retried."""
    token = str(token or "").strip()
    result = await get_collection("paperwork_packets").update_one(
        {
            "packet_id": packet.get("packet_id"),
            "cosigner_verified_token": token,
        },
        {"$set": {"cosigner_verified_token": ""}},
    )
    if int(getattr(result, "modified_count", 0) or 0) > 0:
        packet["cosigner_verified_token"] = ""


async def _revoke_sibling_links(
    packet_id: str,
    token: str,
    now: datetime,
    priors: Optional[list] = None,
) -> None:
    query = _scope({"packet_id": packet_id})
    if query is None:
        return
    cursor = get_collection(LINKS).find(query)
    async for other in _iter(cursor):
        if str(other.get("token") or "") == token or other.get("revoked") is True:
            continue
        if priors is not None:
            priors.append({
                "token": other.get("token"),
                "revoked": other.get("revoked") is True,
                "status": other.get("status") or "",
                "revoked_at": other.get("revoked_at") or "",
            })
        before = _audit_state(other, None)
        other["revoked"] = True
        other["status"] = "revoked"
        other["revoked_at"] = now.isoformat()
        await get_collection(LINKS).update_one(
            {"token": other.get("token")},
            {"$set": {
                "revoked": True,
                "status": "revoked",
                "revoked_at": other["revoked_at"],
            }},
        )
        await _audit(
            "link_revoked",
            other,
            now,
            old_state=before,
            new_state=_audit_state(other, None),
            actor="cosigner_link",
        )


async def require_cosigner_scan(token: str) -> dict:
    """Resolve the bearer link before any upload is read or scanned."""
    link = await _link_by_token(token)
    packet = await _find_packet(str(link.get("packet_id") or ""))
    if not packet:
        raise CosignerLinkError("packet_not_found", 404)
    if _packet_closed(packet):
        raise CosignerLinkError("packet_inactive", 409)
    if _scan_locked(link, packet):
        raise CosignerLinkError("scan_closed", 409)
    await _require_current_bond(link, packet)
    if str(packet.get("cosigner_verified_token") or "").strip():
        raise CosignerLinkError("scan_closed", 409)
    if await _held_by_other(packet, token):
        raise CosignerLinkError("scan_closed", 409)
    return link


async def reserve_scan_attempt(token: str) -> None:
    """Count this upload before OCR. The cap and the cooldown both refuse.

    The increment is one conditional update. A failed scan still counts,
    because the count is stored before the scanner runs. A full cap writes
    an audit row and does not scan.
    """
    now = _now()
    cutoff = now - SCAN_COOLDOWN
    result = await get_collection(LINKS).update_one(
        {
            "token": token,
            "$and": [
                {"$or": [
                    {"scan_attempts": {"$exists": False}},
                    {"scan_attempts": {"$lt": SCAN_ATTEMPT_CAP}},
                ]},
                {"$or": [
                    {"scan_attempted_at": {"$exists": False}},
                    {"scan_attempted_at": None},
                    {"scan_attempted_at": ""},
                    {"scan_attempted_at": {"$lte": cutoff}},
                ]},
            ],
        },
        {
            "$inc": {"scan_attempts": 1},
            "$set": {"scan_attempted_at": now},
        },
    )
    matched = getattr(result, "matched_count", None)
    if matched is None:
        matched = getattr(result, "modified_count", 0)
    if matched:
        return
    link = await _link_by_token(token)
    try:
        attempts = int(link.get("scan_attempts") or 0)
    except (TypeError, ValueError):
        attempts = SCAN_ATTEMPT_CAP
    if attempts >= SCAN_ATTEMPT_CAP:
        await _audit(
            "scan_attempt_cap",
            link,
            now,
            old_state=_audit_state(link, None),
            new_state=_audit_state(link, None),
        )
        raise CosignerLinkError("scan_attempt_cap", 429)
    raise CosignerLinkError("scan_cooldown", 429)


async def _save_link(link: dict) -> None:
    """Save scan, language, and timeline fields.

    Payment fields are written only by the compare-and-set inside the
    payment lock. A stale copy from a scan or a state request must not
    clear or restore them.
    """
    await get_collection(LINKS).update_one(
        {"token": link.get("token")},
        {"$set": {
            "timeline": link.get("timeline") or [],
            "id_verified": bool(link.get("id_verified")),
            "lang": link.get("lang") or "en",
            "id_verified_at": link.get("id_verified_at") or "",
        }},
    )


def _passing_scan(name: str, state: str, role: str) -> dict:
    return {
        "success": True,
        "role": role,
        "extracted": {
            "full_name": name,
            "dl_state": state,
        },
    }


async def _store_scan(
    packet: dict,
    bond: Optional[dict],
    name: str,
    state: str,
    role: str,
    stored: list,
) -> None:
    scan = _passing_scan(name, state, role)
    await _write_party_scan(
        "paperwork_packets",
        {"packet_id": packet.get("packet_id")},
        packet,
        role,
        scan,
        stored,
    )
    if not bond:
        return
    filt = {"bond_case_id": bond.get("bond_case_id")} if bond.get("bond_case_id") else {
        "booking_number": bond.get("booking_number"),
    }
    collection = str(bond.get("_collection") or "bond_cases")
    if collection not in {"bond_cases", "active_bonds"}:
        collection = "bond_cases"
    await _write_party_scan(collection, filt, bond, role, scan, stored)


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
    if _packet_closed(packet):
        raise CosignerLinkError("packet_inactive", 409)
    if _scan_locked(link, packet):
        raise CosignerLinkError("scan_closed", 409)
    await _require_current_bond(link, packet)
    if await _held_by_other(packet, token):
        raise CosignerLinkError("scan_closed", 409)
    name = str(body.get("full_name") or body.get("name") or "").strip()
    expected = str(link.get("signer_name") or link.get("indemnitor_name") or "")
    role = str(link.get("signer_role") or "indemnitor")
    if role != "co_indemnitor":
        role = "indemnitor"
    status = str(body.get("status") or body.get("scan_status") or "").strip().lower()
    passed = body.get("success") is True or body.get("passed") is True
    failed = (
        body.get("success") is False
        or body.get("passed") is False
        or status in _FAIL_STATUS
        or not passed
    )
    if failed:
        logger.info("[cosigner] scan failed packet_id=%s", link.get("packet_id"))
        await _save_link(link)
        raise CosignerLinkError("scan_failed", 409)
    if not names_match(name, expected):
        logger.info("[cosigner] scan locked packet_id=%s", link.get("packet_id"))
        await _save_link(link)
        raise CosignerLinkError("name_mismatch", 409)
    state = _jurisdiction(body)
    if not state:
        logger.info("[cosigner] no issuing state packet_id=%s", link.get("packet_id"))
        await _save_link(link)
        raise CosignerLinkError("no_issuing_state", 400)
    bond = await _require_current_bond(link, packet)
    if not await _claim_verification(packet, token):
        raise CosignerLinkError("scan_closed", 409)
    priors: list = []
    stored: list = []
    started = ""
    verified_local = False
    try:
        now = _now()
        started = now.isoformat()
        await _revoke_sibling_links(
            str(packet.get("packet_id") or ""), token, now, priors,
        )
        await _store_scan(packet, bond, name, state, role, stored)
        before = _audit_state(link, packet)
        link["id_verified"] = True
        link["id_verified_at"] = now.isoformat()
        verified_local = True
        if _timeline_push(link, "id_verified", now):
            await _audit(
                "id_verified", link, now,
                old_state=before, new_state=_audit_state(link, packet),
            )
        await _save_link(link)
    except Exception:
        try:
            if verified_local:
                link["id_verified"] = False
                link["id_verified_at"] = ""
                link["timeline"] = [
                    row for row in (link.get("timeline") or [])
                    if not (isinstance(row, dict) and row.get("step") == "id_verified")
                ]
            await _clear_stored_scan(stored)
            await _restore_sibling_links(priors)
            if started:
                await _drop_partial_audits(str(packet.get("packet_id") or ""), started)
            await _release_verification_claim(packet, token)
        except Exception:
            logger.info("[cosigner] scan rollback failed")
        raise
    logger.info("[cosigner] id verified packet_id=%s", link.get("packet_id"))
    return await cosigner_state(token, lang=link.get("lang"))


async def _with_payment_lock(txn_key: str, work) -> None:
    """Run ``work`` inside ``payment_txn_locks`` for this transaction.

    The provider recorder takes the same lock. The link write waits for it,
    re-reads the payment rows, then does a conditional update. No client
    means nothing is written.
    """
    from dashboard.extensions import get_db
    from dashboard.services import payment_log_claims as payment_log

    txn_key = str(txn_key or "").strip()
    if not txn_key:
        return
    try:
        db = get_db()
        client = await payment_log._transaction_client()
    except Exception:
        logger.info("[cosigner] payment lock unavailable")
        return
    if client is None or db is None:
        logger.info("[cosigner] payment lock unavailable")
        return
    try:
        await payment_log.ensure_txn_lock_index(db[payment_log.LOCKS_COLLECTION])
    except Exception:
        logger.info("[cosigner] payment lock index unavailable")
        return

    async def wrapped(session):
        await payment_log.acquire_txn_lock(db[payment_log.LOCKS_COLLECTION], txn_key, session)
        await work(db, session)

    try:
        await payment_log._run_apply(client, wrapped)
    except Exception:
        logger.info("[cosigner] payment lock write skipped txn=%s", txn_key)


async def _payment_rows(db, txn_key: str, session) -> Optional[list]:
    """Payment rows for this transaction, or None when they cannot be read."""
    from dashboard.services.payment_log_claims import _read_rows

    try:
        coll = db["payments"]
    except Exception:
        return None
    if not hasattr(coll, "find"):
        return None
    try:
        return await _read_rows(db, txn_key, session)
    except Exception:
        logger.info("[cosigner] payment rows unreadable")
        return None


async def _charge_row_blocks_down_payment(db, transaction_id: str, session) -> bool:
    """True when the stored charge must not mark the co-signer pay step.

    A committed charge can still be ``needs_review`` (the refund arrived
    first, or the booking is not one bond). A refund or failure row on the
    same transaction blocks the stamp too. A read error, a missing row, or
    an empty row set blocks it as well: the charge was just recorded under
    this lock, so no readable charge row is not a pass.
    """
    from dashboard.services.payment_log_claims import (
        SETTLED_CHARGE_STATUSES,
        has_reversal_sibling,
        row_kind,
    )

    rows = await _payment_rows(db, str(transaction_id or "").strip(), session)
    if not rows:
        logger.info("[cosigner] paid refused unreadable payment rows")
        return True
    if has_reversal_sibling(rows):
        return True
    charges = [row for row in rows if row_kind(row) == "charge"]
    if not charges:
        return True
    charge_status = str(charges[0].get("status") or "").strip().lower()
    if charge_status not in SETTLED_CHARGE_STATUSES:
        return True
    return any(row.get("needs_review") for row in charges)


def _unpaid_link_query(token: str) -> dict:
    """Compare-and-set filter: this link is not already stamped paid."""
    return {
        "token": token,
        "$or": [
            {"paid_transaction_id": {"$exists": False}},
            {"paid_transaction_id": ""},
            {"paid_transaction_id": None},
        ],
    }


def _timeline_has(link: dict, step: str) -> bool:
    return any(
        isinstance(row, dict) and row.get("step") == step
        for row in (link.get("timeline") or [])
    )


async def _cas_mark_paid(link: dict, packet: Optional[dict], txn: str, now: datetime, session) -> bool:
    """Conditional paid stamp. A lost race writes no audit."""
    token = str(link.get("token") or "")
    if not token:
        return False
    before = _audit_state(link, packet)
    update = {
        "$set": {
            "paid_transaction_id": txn,
            "payment_reversed": False,
        },
    }
    if not _timeline_has(link, "paid"):
        update["$push"] = {"timeline": {"step": "paid", "at": now.isoformat()}}
    result = await get_collection(LINKS).update_one(
        _unpaid_link_query(token),
        update,
        session=session,
    )
    if not getattr(result, "modified_count", 0):
        return False
    fresh = await get_collection(LINKS).find_one({"token": token}) or link
    await _audit(
        "paid", fresh, now,
        old_state=before, new_state=_audit_state(fresh, packet), session=session,
    )
    return True


async def _cas_mark_reversed(link: dict, txn: str, now: datetime, session) -> bool:
    """Conditional reopen. A replay matches nothing and writes no audit."""
    token = str(link.get("token") or "")
    if not token:
        return False
    packet = await _find_packet(str(link.get("packet_id") or ""))
    before = _audit_state(link, packet)
    result = await get_collection(LINKS).update_one(
        {
            "token": token,
            "paid_transaction_id": txn,
            "payment_reversed": {"$ne": True},
        },
        {
            "$set": {
                "payment_reversed": True,
                "paid_transaction_id": "",
            },
            "$push": {
                "timeline": {"step": "payment_reversed", "at": now.isoformat()},
            },
        },
        session=session,
    )
    if not getattr(result, "modified_count", 0):
        return False
    fresh = await get_collection(LINKS).find_one({"token": token}) or link
    await _audit(
        "payment_reversed", fresh, now,
        old_state=before, new_state=_audit_state(fresh, packet), session=session,
    )
    return True


def _dollars_to_cents(amount: Any) -> Optional[int]:
    try:
        quantized = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return None
    return int(quantized * 100)


def _due_cents(packet: Optional[dict], bond: Optional[dict]) -> Optional[int]:
    due = invoice_due_cents(packet) if packet else None
    if due is None and bond:
        due = invoice_due_cents(bond)
    return due


async def _one_case_id(booking: str) -> str:
    """Bond case id when this booking is exactly one case. Otherwise empty."""
    from dashboard.services.payment_log_claims import _case_id, _logical_bond_groups

    found = []
    for name in ("bond_cases", "active_bonds"):
        query = _scope({"booking_number": booking})
        if query is None:
            return ""
        cursor = get_collection(name).find(query)
        async for doc in _iter(cursor):
            found.append((name, doc))
    groups = _logical_bond_groups(found)
    if len(groups) != 1:
        return ""
    return _case_id(groups[0][0][1])


def _packet_on_case(packet: dict, case_id: str, booking: str) -> bool:
    """The packet belongs to this case, including the booking-only fallback."""
    packet_case = str(packet.get("bond_case_id") or "").strip()
    if packet_case:
        return packet_case == case_id
    return str(packet.get("booking_number") or "").strip() == booking


async def _open_down_payment_links(booking: str) -> list:
    """Links on the one open packet for this booking's bond case.

    Booking number alone is not the scope. Another open packet on the same
    case is not stamped. Several links on that one packet are.
    """
    case_id = await _one_case_id(booking)
    if not case_id:
        logger.info("[cosigner] paid refused ambiguous case")
        return []
    # A link issued through the booking fallback stores the resolved case id.
    # An older link may still have an empty case id and the same booking.
    query = _scope({"$or": [
        {"bond_case_id": case_id},
        {"booking_number": booking, "bond_case_id": {"$in": ["", None]}},
    ]})
    if query is None:
        return []
    grouped: dict = {}
    cursor = get_collection(LINKS).find(query)
    async for link in _iter(cursor):
        packet_id = str(link.get("packet_id") or "").strip()
        packet = await _find_packet(packet_id)
        if not packet or _packet_closed(packet):
            continue
        if not _packet_on_case(packet, case_id, booking):
            continue
        grouped.setdefault(packet_id, []).append((link, packet))
    if len(grouped) != 1:
        if len(grouped) > 1:
            logger.info("[cosigner] paid refused ambiguous packets")
        return []
    return next(iter(grouped.values()))


async def note_cosigner_paid(
    booking_number: str,
    transaction_id: str,
    amount: Any = None,
    *,
    status: Any = None,
) -> None:
    """Stamp paid when the charge equals the confirmed down payment. Idempotent.

    A smaller charge is not the down payment. Nothing is stamped, and the
    full premium stays unpaid. The write takes ``payment_txn_locks`` and
    updates the link only when it is not already stamped, so one charge
    writes one paid audit. The links are the open packet on this bond case,
    not every link that shares the booking number.
    """
    booking = str(booking_number or "").strip()
    txn = str(transaction_id or "").strip()
    if not booking or not txn:
        return
    if status is not None:
        from dashboard.services.payment_log_claims import SETTLED_CHARGE_STATUSES

        if str(status).strip().lower() not in SETTLED_CHARGE_STATUSES:
            logger.info("[cosigner] paid refused unsettled status")
            return
    paid_cents = _dollars_to_cents(amount)

    async def work(db, session):
        if await _charge_row_blocks_down_payment(db, txn, session):
            logger.info("[cosigner] paid refused review hold")
            return
        targets = await _open_down_payment_links(booking)
        now = _now()
        for link, packet in targets:
            bond = await _find_bond(packet) if packet else None
            due = _due_cents(packet, bond)
            if paid_cents is None or due is None or paid_cents != due:
                logger.info("[cosigner] paid amount refused packet_id=%s", link.get("packet_id"))
                continue
            await _cas_mark_paid(link, packet, txn, now, session)

    await _with_payment_lock(txn, work)


async def note_cosigner_reversed(booking_number: str, transaction_id: str) -> None:
    """Reopen the pay step when the stamped down payment is refunded or fails.

    The link is found by ``paid_transaction_id``, scoped to the tenant, inside
    ``payment_txn_locks``. A missing reference or a different booking on the
    refund does not keep the step paid. A different transaction leaves the
    stamp in place. A replay does not audit twice.
    """
    txn = str(transaction_id or "").strip()
    if not txn:
        return
    query = _scope({"paid_transaction_id": txn})
    if query is None:
        return

    async def work(db, session):
        rows = await _payment_rows(db, txn, session)
        if rows is not None and rows and not _rows_include_reversal(rows):
            return
        now = _now()
        cursor = get_collection(LINKS).find(query)
        async for link in _iter(cursor):
            if str(link.get("paid_transaction_id") or "").strip() != txn:
                continue
            await _cas_mark_reversed(link, txn, now, session)

    await _with_payment_lock(txn, work)


def _rows_include_reversal(rows: list) -> bool:
    from dashboard.services.payment_log_claims import REVERSAL_KINDS, row_kind

    return any(row_kind(row) in REVERSAL_KINDS for row in rows)


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
    link = case_invoice_link(packet) or case_invoice_link(bond) or ""
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
    packet = await _find_packet(str(link.get("packet_id") or ""))
    if not packet:
        raise CosignerLinkError("packet_not_found", 404)
    if _packet_closed(packet):
        raise CosignerLinkError("packet_inactive", 409)
    bond = await _require_current_bond(link, packet)
    language = _lang(link.get("lang"))
    verified = bool(link.get("id_verified"))
    signed = _signed(packet)
    paid = _link_paid(link) or _paid(bond)
    # A confirmed down payment of zero has nothing to collect. A missing
    # confirmation stays on the pay step.
    nothing_due = _due_cents(packet, bond) == 0
    now = _now()
    changed = False
    before = _audit_state(link, packet)
    if signed and _timeline_push(link, "signed", now):
        await _audit(
            "signed", link, now,
            old_state=before, new_state=_audit_state(link, packet),
        )
        before = _audit_state(link, packet)
        changed = True
    if paid and _timeline_push(link, "paid", now):
        await _audit(
            "paid", link, now,
            old_state=before, new_state=_audit_state(link, packet),
        )
        changed = True
    if changed or lang:
        await _save_link(link)
    # Paid money does not skip the signature. The terminal paid step needs
    # the scan, the signature, and either the down payment or a confirmed
    # zero amount due.
    if (paid or nothing_due) and signed and verified:
        step = "paid"
    elif signed and verified:
        step = "pay"
    elif verified:
        step = "sign"
    else:
        step = "scan"
    sign_url = None
    if verified:
        found, problem = _sign_url(
            packet,
            str(link.get("signer_role") or "indemnitor"),
            str(link.get("signer_name") or ""),
            _signer_email(packet, bond, str(link.get("signer_role") or "indemnitor")),
        )
        # Signing is still ahead. A bad URL must not be shown. Once the
        # packet is signed, the pay step stays open with no sign link.
        if problem and step == "sign":
            raise CosignerLinkError(problem, 409)
        sign_url = found or None
    payment = _payment_view(packet, bond) if step in {"pay", "paid"} else None
    if nothing_due and isinstance(payment, dict):
        payment = {**payment, "amount": "0.00", "url": None, "dispatched": False}
    return {
        "lang": language,
        "strings": STRINGS[language],
        "step": step,
        "id_verified": verified,
        "sign_url": sign_url,
        "signing_locked": not verified,
        "payment": payment,
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
    elif step == "sign":
        body = f"<h1>{strings['sign_title']}</h1><p>{strings['locked']}</p>"
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
