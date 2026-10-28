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

import asyncio
import hashlib
import html
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional
from urllib.parse import urlparse

from pymongo.errors import DuplicateKeyError

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
from dashboard.services.swipesimple_invoice_service import (
    SwipeSimpleInvoiceError,
    _reject_unproven_invoice_amount,
    dispatch_live_enabled,
)
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


def _email_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _signer_email_keys(role: str) -> tuple[str, ...]:
    if role == "co_indemnitor":
        return (
            "co_signer_email",
            "cosigner_email",
            "co_indemnitor_email",
            "coindemnitor_email",
        )
    return ("indemnitor_email",)


def _doc_signer_emails(doc: dict, role: str) -> list[str]:
    """Non-empty emails this document stores for the signer, compared folded."""
    if not isinstance(doc, dict):
        return []
    found: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = _email_key(value)
        if not text or text in seen:
            return
        seen.add(text)
        found.append(text)

    for key in _signer_email_keys(role):
        add(doc.get(key))
    people = doc.get("indemnitors")
    if isinstance(people, list):
        for person in people:
            if isinstance(person, dict) and _party_role(person) == role:
                add(person.get("email"))
    return found


def _signer_email(packet: dict, bond: Optional[dict], role: str) -> str:
    """Email stored for this signer. Blank when the case has none.

    The bond does not win when the packet has a different email. Two
    non-empty values that differ after trimming and lowercasing are a conflict.
    """
    found: list[str] = []
    seen: set[str] = set()
    for doc in (bond, packet):
        for text in _doc_signer_emails(doc if isinstance(doc, dict) else {}, role):
            if text in seen:
                continue
            seen.add(text)
            found.append(text)
    if len(found) > 1:
        raise CosignerLinkError("signer_email_mismatch", 409)
    return found[0] if found else ""


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
    """A real down-payment transaction. A reversal reopens the pay step.

    A confirmed zero amount is not this stamp. The page reads that amount
    again on each load.
    """
    if link.get("payment_reversed") is True:
        return False
    return bool(str(link.get("paid_transaction_id") or "").strip())


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
    paid = not reversed_pay and bool(str(link.get("paid_transaction_id") or "").strip())
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


async def _case_rows(collection: str, case_id: str) -> list:
    scoped = _scope({"$or": [
        {"bond_case_id": case_id},
        {"Bond_Case_ID": case_id},
    ]})
    if scoped is None:
        raise CosignerLinkError("tenant_required", 403)
    found = []
    cursor = get_collection(collection).find(scoped)
    async for doc in _iter(cursor):
        doc["_collection"] = collection
        found.append(doc)
    return found


def _bond_status(bond: dict) -> str:
    return str((bond or {}).get("status") or "").strip().casefold()


def _mirror_text(bond: dict, *keys: str) -> str:
    return _bond_field(bond, *keys)


def _mirror_key(bond: dict, *keys: str) -> str:
    """Trimmed and casefolded, so ``OSI`` and `` osi `` are one surety."""
    return _mirror_text(bond, *keys).casefold()


def _mirror_name_key(value: str) -> str:
    """Casefold, drop punctuation, collapse whitespace."""
    letters = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in str(value or ""))
    return " ".join(letters.casefold().split())


def _mirror_names_agree(left: str, right: str) -> bool:
    """Same person on both rows. A blank or a different name is not."""
    left_key, right_key = _mirror_name_key(left), _mirror_name_key(right)
    if not left_key or not right_key:
        return False
    return left_key == right_key or names_match(left, right)


def _mirror_disagrees(case_row: dict, mirror: dict) -> bool:
    """The case and its active mirror must be the same bonded case.

    Status, signer, booking, defendant, match, case number, surety, POA,
    and the defendant and indemnitor ids are compared. A cleared or
    reassigned field is a different case. Case and surrounding whitespace
    are not a difference: status, surety, booking, case number, POA, and
    match status compare trimmed and casefolded, and names compare without
    case, punctuation, or extra spaces. ``match_id`` and the party ids
    compare trimmed only. A POA number on the selected bond is not, by
    itself, a mirror disagreement.
    """
    if _bond_status(case_row) != _bond_status(mirror):
        return True
    case_name, case_role = _expected_signer(case_row, None)
    mirror_name, mirror_role = _expected_signer(mirror, None)
    if case_role != mirror_role:
        return True
    if not _mirror_names_agree(case_name, mirror_name):
        return True
    for keys in (
        ("booking_number", "Booking_Number"),
        ("case_number", "Case_Number"),
        ("surety_id", "Surety_ID"),
        ("poa_number", "POA_Number", "power_number"),
        ("match_status", "Match_Status"),
    ):
        if _mirror_key(case_row, *keys) != _mirror_key(mirror, *keys):
            return True
    for keys in (
        ("match_id", "Match_ID"),
        ("defendant_id", "Defendant_ID"),
        ("indemnitor_id", "Indemnitor_ID"),
    ):
        if _mirror_text(case_row, *keys) != _mirror_text(mirror, *keys):
            return True
    case_defendant = _mirror_text(case_row, "defendant_name", "Defendant_Name", "defendant")
    mirror_defendant = _mirror_text(mirror, "defendant_name", "Defendant_Name", "defendant")
    if case_defendant or mirror_defendant:
        if not _mirror_names_agree(case_defendant, mirror_defendant):
            return True
    return False


async def _find_bond(packet: dict) -> Optional[dict]:
    """The packet's case id wins. Booking is used only when no case id exists.

    Two bond rows that share a booking number are not a guess. A mirror pair
    with the same case id is reconciled. Any other pair fails closed. A case
    id with more than one ``bond_cases`` row, or an ``active_bonds`` mirror
    that does not match, also fails closed.
    """
    booking = str(packet.get("booking_number") or "").strip()
    case_id = str(packet.get("bond_case_id") or "").strip()
    if case_id:
        cases = await _case_rows("bond_cases", case_id)
        mirrors = await _case_rows("active_bonds", case_id)
        if len(cases) > 1 or len(mirrors) > 1:
            raise CosignerLinkError("bond_ambiguous", 409)
        if cases and mirrors and _mirror_disagrees(cases[0], mirrors[0]):
            raise CosignerLinkError("bond_mirror_mismatch", 409)
        if cases:
            return cases[0]
        return mirrors[0] if mirrors else None
    if not booking:
        return None
    matches = await _matching_bonds({"booking_number": booking})
    if not matches:
        return None
    # A case stored in both collections is one case when the case ids agree.
    # Reconcile that pair the same way the case-id path does. Two logical
    # cases, or two rows in one collection, are not a guess.
    from dashboard.services.payment_log_claims import _logical_bond_groups

    groups = _logical_bond_groups([
        (str(doc.get("_collection") or ""), doc) for doc in matches
    ])
    if len(groups) != 1:
        raise CosignerLinkError("bond_ambiguous", 409)
    group = groups[0]
    if len(group) == 1:
        return group[0][1]
    case_row, mirror = group[0][1], group[1][1]
    if _mirror_disagrees(case_row, mirror):
        raise CosignerLinkError("bond_mirror_mismatch", 409)
    return case_row


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
    reason: str = "",
    extra: Optional[dict] = None,
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
        "reason": reason or step,
        "old_state": _audit_state_only(old_state),
        "new_state": _audit_state_only(new_state),
        "confidence": 1.0,
        **(extra or {}),
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


def _party_ids(doc: dict) -> tuple[str, str]:
    """Defendant and indemnitor ids, under either field spelling."""
    if not isinstance(doc, dict):
        return "", ""
    return (
        _bond_field(doc, "defendant_id", "Defendant_ID"),
        _bond_field(doc, "indemnitor_id", "Indemnitor_ID"),
    )


def _party_id_gap(packet: dict, bond: dict) -> str:
    """The packet and the bond must name the same defendant and indemnitor."""
    packet_defendant, packet_indemnitor = _party_ids(packet)
    bond_defendant, bond_indemnitor = _party_ids(bond)
    if not packet_defendant or not bond_defendant:
        return "defendant_required"
    if packet_defendant != bond_defendant:
        return "party_mismatch"
    if not packet_indemnitor or not bond_indemnitor:
        return "indemnitor_required"
    if packet_indemnitor != bond_indemnitor:
        return "party_mismatch"
    return ""


def _co_indemnitor_ids(doc: dict) -> list[str]:
    """Ids on co-indemnitor rows. The primary indemnitor id is not one of these."""
    if not isinstance(doc, dict):
        return []
    found: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        found.append(text)

    for person in _listed_people(doc):
        role = str(person.get("role") or person.get("party_role") or "").strip().lower()
        role = role.replace("-", "_")
        if role not in _COSIGNER_ROLES and role != "co_indemnitor":
            continue
        add(_bond_field(person, "indemnitor_id", "Indemnitor_ID", "id"))
    for key in ("coindemnitor", "co_indemnitor", "cosigner", "co_signer"):
        nested = doc.get(key)
        if isinstance(nested, dict):
            add(_bond_field(nested, "indemnitor_id", "Indemnitor_ID", "id"))
    add(_bond_field(doc, "co_indemnitor_id", "coindemnitor_id", "Co_Indemnitor_ID"))
    return found


def _collapsed_name(value: Any) -> str:
    """Case-insensitive full name with the whitespace collapsed."""
    return " ".join(str(value or "").split()).casefold()


def _indemnitor_record_name(doc: dict) -> str:
    if not isinstance(doc, dict):
        return ""
    for key in (
        "name",
        "full_name",
        "fullName",
        "Full_Name",
        "indemnitor_name",
        "Indemnitor_Name",
    ):
        text = str(doc.get(key) or "").strip()
        if text:
            return text
    return ""


def _co_indemnitor_gap(packet: dict, bond: dict) -> str:
    """The co-signer needs one id of their own, the same on the packet and the bond."""
    packet_ids = _co_indemnitor_ids(packet)
    bond_ids = _co_indemnitor_ids(bond)
    if len(packet_ids) > 1 or len(bond_ids) > 1:
        return "co_indemnitor_ambiguous"
    if len(packet_ids) != 1 or len(bond_ids) != 1:
        return "co_indemnitor_required"
    if packet_ids[0] != bond_ids[0]:
        return "co_indemnitor_mismatch"
    primary_packet, primary_bond = _party_ids(packet)[1], _party_ids(bond)[1]
    if packet_ids[0] in {primary_packet, primary_bond}:
        return "co_indemnitor_mismatch"
    return ""


def _co_row_name_gap(doc: dict, co_id: str, expected: str) -> str:
    """A row that carries this id must name the selected signer."""
    wanted = _collapsed_name(expected)
    if not wanted:
        return "co_indemnitor_mismatch"
    people: list[dict] = list(_listed_people(doc))
    for key in ("coindemnitor", "co_indemnitor", "cosigner", "co_signer"):
        nested = doc.get(key)
        if isinstance(nested, dict):
            people.append(nested)
    for person in people:
        role = str(person.get("role") or person.get("party_role") or "").strip().lower()
        role = role.replace("-", "_")
        named_nested = person in (
            doc.get(key) for key in ("coindemnitor", "co_indemnitor", "cosigner", "co_signer")
            if isinstance(doc.get(key), dict)
        )
        if role not in _COSIGNER_ROLES and role != "co_indemnitor" and not named_nested:
            continue
        if _bond_field(person, "indemnitor_id", "Indemnitor_ID", "id") != co_id:
            continue
        row_name = _person_name(person)
        if row_name and _collapsed_name(row_name) != wanted:
            return "co_indemnitor_mismatch"
    return ""


async def _load_indemnitor(indemnitor_id: str) -> Optional[dict]:
    query = _scope({"$or": [
        {"indemnitor_id": indemnitor_id},
        {"Indemnitor_ID": indemnitor_id},
    ]})
    if query is None:
        raise CosignerLinkError("tenant_required", 403)
    found = await get_collection("indemnitors").find_one(query)
    return found if isinstance(found, dict) else None


async def _indemnitor_missing(indemnitor_id: str) -> bool:
    return not await _load_indemnitor(indemnitor_id)


async def _parties_unresolved(defendant_id: str, indemnitor_id: str) -> str:
    """Both party rows must already exist. This does not create them."""
    defendant_query = _scope({"$or": [
        {"defendant_id": defendant_id},
        {"Defendant_ID": defendant_id},
    ]})
    if defendant_query is None:
        raise CosignerLinkError("tenant_required", 403)
    defendant = await get_collection("defendants").find_one(defendant_query)
    if not defendant or await _indemnitor_missing(indemnitor_id):
        return "party_unresolved"
    return ""


async def _require_party_binding(packet: dict, bond: dict, *, role: str) -> None:
    gap = _party_id_gap(packet, bond)
    if gap:
        raise CosignerLinkError(gap, 409)
    defendant_id, indemnitor_id = _party_ids(bond)
    missing = await _parties_unresolved(defendant_id, indemnitor_id)
    if missing:
        raise CosignerLinkError(missing, 409)
    if role != "co_indemnitor":
        return
    co_gap = _co_indemnitor_gap(packet, bond)
    if co_gap:
        raise CosignerLinkError(co_gap, 409)
    co_id = _co_indemnitor_ids(bond)[0]
    expected = _cosigner_name(packet, bond)
    for doc in (packet, bond):
        row_gap = _co_row_name_gap(doc, co_id, expected)
        if row_gap:
            raise CosignerLinkError(row_gap, 409)
    record = await _load_indemnitor(co_id)
    if not record:
        raise CosignerLinkError("co_indemnitor_unresolved", 409)
    # The id has to be this signer. A different person's record is not a match.
    if _collapsed_name(_indemnitor_record_name(record)) != _collapsed_name(expected):
        raise CosignerLinkError("co_indemnitor_mismatch", 409)


async def _replace_stale_verification_claim(packet: dict) -> None:
    """A live claim blocks a second link. A dead claim is cleared.

    The clear touches only ``cosigner_verified_token``. Stored ID scans stay.
    A claim whose link is still open is left in place, and no new link is
    inserted. A missing, expired, or revoked claim can be replaced.
    """
    claim = str(packet.get("cosigner_verified_token") or "").strip()
    if not claim:
        return
    query = _scope({"token": claim})
    if query is None:
        raise CosignerLinkError("tenant_required", 403)
    prior = await get_collection(LINKS).find_one(query)
    if prior and not _link_block(prior):
        raise CosignerLinkError("verification_open", 409)
    result = await get_collection("paperwork_packets").update_one(
        {
            "packet_id": packet.get("packet_id"),
            "cosigner_verified_token": claim,
        },
        {"$set": {"cosigner_verified_token": ""}},
    )
    if int(getattr(result, "modified_count", 0) or 0) <= 0:
        raise CosignerLinkError("verification_open", 409)
    packet["cosigner_verified_token"] = ""


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
    await _require_party_binding(packet, bond, role=packet_role)
    # A packet email and a bond email for this signer have to agree before
    # a bearer exists. The bond does not silently win.
    _signer_email(packet, bond, packet_role)
    await _replace_stale_verification_claim(packet)
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
    try:
        await _audit(
            "link_issued",
            doc,
            now,
            old_state=_blank_audit_state(packet),
            new_state=_audit_state(doc, packet),
            actor=actor,
        )
    except Exception:
        await _drop_unaudited_link(doc, now)
        raise
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
    """The bond must still exist, still be open, and still name this signer.

    The same completeness check used at issuance runs again here. A link
    that was valid when it was created is refused once the match, case
    number, surety, or POA is no longer on the bond. The defendant and
    indemnitor ids must still agree and still resolve to stored rows.
    """
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
    gap = _bond_issuance_gap(bond)
    if gap:
        raise CosignerLinkError(gap, 409)
    await _require_party_binding(packet, bond, role=_link_role(link))
    return bond


def _recorded_case_ids(doc: dict) -> set[str]:
    """Case ids stored under either bond-case field. Blanks are not ids."""
    found: set[str] = set()
    if not isinstance(doc, dict):
        return found
    for key in ("bond_case_id", "Bond_Case_ID"):
        text = str(doc.get(key) or "").strip()
        if text:
            found.add(text)
    return found


def _issued_case_drifted(link: dict, packet: dict, bond: dict) -> bool:
    """The packet or bond is no longer the case this link was issued for.

    A case id matches when it is stored as ``bond_case_id`` or
    ``Bond_Case_ID``, and the bond's booking when it is stored as
    ``booking_number`` or ``Booking_Number``. A POA number is not part of
    this comparison.
    """
    stored_case = _bond_field(link, "bond_case_id", "Bond_Case_ID")
    stored_booking = str(link.get("booking_number") or "").strip()
    bond_cases = _recorded_case_ids(bond)
    bond_booking = _bond_field(bond, "booking_number", "Booking_Number")
    packet_cases = _recorded_case_ids(packet)
    packet_booking = str(packet.get("booking_number") or "").strip()
    if stored_case and stored_case not in bond_cases:
        return True
    if stored_case and packet_cases and stored_case not in packet_cases:
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
        # An expired or revoked link no longer holds the scan. A live
        # verified link still does.
        if other.get("id_verified") and not _link_block(other):
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


def _scan_record(doc: dict, role: str, scan: dict, packet_id: str) -> dict:
    """One stored scan. Callers append it and never update it."""
    extracted = scan.get("extracted") if isinstance(scan.get("extracted"), dict) else {}
    name = str(extracted.get("full_name") or "").strip()
    state = str(extracted.get("dl_state") or "").strip()
    return {
        "scan_id": secrets.token_hex(8),
        "role": role,
        "signer_name": name,
        "bond_case_id": _bond_field(doc, "bond_case_id", "Bond_Case_ID"),
        "booking_number": _bond_field(doc, "booking_number", "Booking_Number"),
        "packet_id": _bond_field(doc, "packet_id") or str(packet_id or "").strip(),
        "recorded_at": _now().isoformat(),
        "success": True,
        "extracted": {"full_name": name, "dl_state": state},
    }


def _scan_pointer(record: dict) -> dict:
    """The current-scan pointer. The history row stays a separate object."""
    return {
        "scan_id": record["scan_id"],
        "success": True,
        "role": record["role"],
        "recorded_at": record["recorded_at"],
        "extracted": dict(record["extracted"]),
    }


def _copy_scan_pointer(blob: Any) -> Optional[dict]:
    """A pointer snapshot. Later writes must not change this copy."""
    if not isinstance(blob, dict) or not blob:
        return None
    copied = dict(blob)
    extracted = copied.get("extracted")
    if isinstance(extracted, dict):
        copied["extracted"] = dict(extracted)
    return copied


def _top_pointer_filter(filt: dict, role: str) -> dict:
    """Set the top-level pointer only while this role still owns it."""
    return {
        **filt,
        "$or": [
            {"id_ocr_role": {"$exists": False}},
            {"id_ocr_role": ""},
            {"id_ocr_role": None},
            {"id_ocr_role": role},
        ],
    }


async def _write_party_scan(
    collection: str,
    filt: dict,
    doc: dict,
    role: str,
    scan: dict,
    stored: list,
    packet_id: str,
) -> None:
    """Append this scan. A pointer may move. The stored row is not replaced."""
    record = _scan_record(doc, role, scan, packet_id)
    pointer = _scan_pointer(record)
    slot = _party_slot(role)
    prior_party = doc.get(slot)
    prior_party_pointer = None
    prior_party_role = ""
    if isinstance(prior_party, dict):
        prior_party_pointer = _copy_scan_pointer(prior_party.get("id_ocr"))
        prior_party_role = str(prior_party.get("id_ocr_role") or "").strip()
    prior_top = None
    if str(doc.get("id_ocr_role") or "").strip() == role:
        prior_top_pointer = _copy_scan_pointer(doc.get("id_ocr"))
        if prior_top_pointer is not None:
            prior_top = {"id_ocr": prior_top_pointer, "id_ocr_role": role}
    snap = {
        "collection": collection,
        "filter": filt,
        "doc": doc,
        "slot": slot,
        "role": role,
        "scan_id": record["scan_id"],
        "prior_party": prior_party,
        "prior_party_pointer": prior_party_pointer,
        "prior_party_role": prior_party_role,
        "prior_top": prior_top,
        "wrote_top": False,
    }
    stored.append(snap)
    await get_collection(collection).update_one(
        filt, {"$push": {"id_scan_records": record}},
    )
    records = doc.get("id_scan_records")
    if not isinstance(records, list):
        records = []
        doc["id_scan_records"] = records
    if record not in records:
        records.append(record)
    party = _with_party_scan(prior_party, role, pointer)
    await get_collection(collection).update_one(filt, {"$set": {slot: party}})
    doc[slot] = party
    snap["wrote_top"] = True
    top = await get_collection(collection).update_one(
        _top_pointer_filter(filt, role),
        {"$set": {"id_ocr": pointer, "id_ocr_role": role}},
    )
    wrote_top = int(getattr(top, "modified_count", 0) or 0) > 0
    snap["wrote_top"] = wrote_top
    if wrote_top:
        doc["id_ocr"] = pointer
        doc["id_ocr_role"] = role


async def _clear_stored_scan(stored: list) -> None:
    """Restore or remove pointers this attempt added. Scan rows stay."""
    for snap in reversed(stored):
        scan_id = str(snap.get("scan_id") or "")
        if not scan_id:
            continue
        slot = snap["slot"]
        filt = dict(snap.get("filter") or {})
        coll = get_collection(snap["collection"])
        owned = {**filt, f"{slot}.id_ocr.scan_id": scan_id}
        prior_pointer = snap.get("prior_party_pointer")
        if snap.get("prior_party") is None:
            await coll.update_one(owned, {"$unset": {slot: ""}})
        elif isinstance(prior_pointer, dict):
            await coll.update_one(owned, {"$set": {
                f"{slot}.id_ocr": prior_pointer,
                f"{slot}.id_ocr_role": snap.get("prior_party_role") or snap.get("role") or "",
            }})
        else:
            await coll.update_one(owned, {"$unset": {
                f"{slot}.id_ocr": "",
                f"{slot}.id_ocr_role": "",
            }})
        if snap.get("wrote_top"):
            prior_top = snap.get("prior_top") if isinstance(snap.get("prior_top"), dict) else None
            if isinstance((prior_top or {}).get("id_ocr"), dict):
                await coll.update_one(
                    {**filt, "id_ocr.scan_id": scan_id},
                    {"$set": {
                        "id_ocr": prior_top["id_ocr"],
                        "id_ocr_role": prior_top.get("id_ocr_role") or "",
                    }},
                )
            else:
                await coll.update_one(
                    {**filt, "id_ocr.scan_id": scan_id},
                    {"$unset": {"id_ocr": "", "id_ocr_role": ""}},
                )


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


def _bearer_ref(token: str) -> str:
    """A reference for a bearer token. The token itself is not stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _drop_unaudited_link(doc: dict, now: datetime) -> None:
    """A link whose issuance audit did not commit is not a bearer.

    The link row is removed. If that delete fails, the row is revoked so
    the token cannot be used. The rollback audit stores a hash, not the token.
    """
    token = str(doc.get("token") or "")
    removed = False
    try:
        result = await get_collection(LINKS).delete_one({"token": token})
        removed = int(getattr(result, "deleted_count", 0) or 0) > 0
    except Exception:
        logger.info("[cosigner] unaudited link delete failed")
    if not removed and token:
        try:
            revoked_at = now.isoformat()
            await get_collection(LINKS).update_one(
                {"token": token},
                {"$set": {
                    "revoked": True,
                    "status": "revoked",
                    "revoked_at": revoked_at,
                }},
            )
        except Exception:
            logger.info("[cosigner] unaudited link revoke failed")
    try:
        await get_collection("audit_events").insert_one({
            "event_type": "link_issuance_rolled_back",
            "action": "link_issuance_rolled_back",
            "entity_type": "cosigner_link",
            "entity_id": doc.get("packet_id") or "",
            "actor": "cosigner_link",
            "agent_name": "cosigner_link",
            "timestamp": now,
            "created_at": now.isoformat(),
            "booking_number": doc.get("booking_number") or "",
            "packet_id": doc.get("packet_id") or "",
            "reason": "link_issuance_rolled_back",
            "link_ref": _bearer_ref(token) if token else "",
            "old_state": _blank_audit_state(None),
            "new_state": _blank_audit_state(None),
            "confidence": 1.0,
        })
    except Exception:
        logger.info("[cosigner] issuance rollback audit failed")


async def _drop_partial_audits(
    packet_id: str,
    created_at: str,
    restored: Optional[list] = None,
) -> None:
    """Keep this attempt's audits and record that the verification rolled back."""
    siblings = []
    for prior in restored or []:
        if not isinstance(prior, dict):
            continue
        token = str(prior.get("token") or "").strip()
        if not token:
            continue
        siblings.append({
            "link_ref": _bearer_ref(token),
            "revoked": prior.get("revoked") is True,
            "status": str(prior.get("status") or ""),
        })
    now = _now()
    await get_collection("audit_events").insert_one({
        "event_type": "verification_rolled_back",
        "action": "verification_rolled_back",
        "entity_type": "cosigner_link",
        "entity_id": packet_id,
        "actor": "cosigner_link",
        "agent_name": "cosigner_link",
        "timestamp": now,
        "created_at": now.isoformat(),
        "packet_id": packet_id,
        "reason": "verification_rolled_back",
        "attempted_at": created_at,
        "restored_links": siblings,
        "old_state": {},
        "new_state": {"restored_links": [row["link_ref"] for row in siblings]},
        "confidence": 1.0,
    })


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


def _bond_scan_filter(bond: dict) -> dict:
    """Update the selected case. Either case-id field is the key.

    Booking is only the filter when the bond has no case id. A shared
    booking must not choose a different case.
    """
    case_id = _bond_field(bond, "bond_case_id", "Bond_Case_ID")
    if case_id:
        return {"$and": [{"$or": [
            {"bond_case_id": case_id},
            {"Bond_Case_ID": case_id},
        ]}]}
    return {"booking_number": bond.get("booking_number")}


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
    packet_id = str(packet.get("packet_id") or "")
    await _write_party_scan(
        "paperwork_packets",
        {"packet_id": packet.get("packet_id")},
        packet,
        role,
        scan,
        stored,
        packet_id,
    )
    if not bond:
        return
    filt = _bond_scan_filter(bond)
    collection = str(bond.get("_collection") or "bond_cases")
    if collection not in {"bond_cases", "active_bonds"}:
        collection = "bond_cases"
    await _write_party_scan(collection, filt, bond, role, scan, stored, packet_id)


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
                await _drop_partial_audits(
                    str(packet.get("packet_id") or ""), started, priors,
                )
        except Exception:
            logger.info("[cosigner] scan rollback failed")
        finally:
            try:
                await _release_verification_claim(packet, token)
            except Exception:
                logger.info("[cosigner] verification claim release failed")
        raise
    logger.info("[cosigner] id verified packet_id=%s", link.get("packet_id"))
    return await cosigner_state(token, lang=link.get("lang"))


# Outcomes of one locked link write.
LOCK_OK = "ok"  # the transaction committed
LOCK_REFUSED = "refused"  # the work raised a co-signer refusal; retrying will not change it
LOCK_UNAVAILABLE = "unavailable"  # no transaction support: nothing is ever written
LOCK_FAILED = "failed"  # every attempt raised; the caller records a pending transition

# Bounded retry inside the lock for errors that are not refusals. The
# driver's ``with_transaction`` already retries TransientTransactionError;
# this covers a dropped connection or a failed commit that it gives up on.
_LOCK_ATTEMPTS = 3
_LOCK_RETRY_DELAYS = (0.05, 0.2)


async def _with_payment_lock(txn_key: str, work) -> str:
    """Run ``work`` inside ``payment_txn_locks`` for this transaction.

    The provider recorder takes the same lock. The link write waits for it,
    re-reads the payment rows, then does a conditional update. No client
    means nothing is written. A failed attempt rolls back and is retried, up
    to ``_LOCK_ATTEMPTS`` times; ``work`` is conditional, so a retry after a
    commit that did land does not write twice. Returns one of the ``LOCK_*``
    outcomes.
    """
    from dashboard.extensions import get_db
    from dashboard.services import payment_log_claims as payment_log

    txn_key = str(txn_key or "").strip()
    if not txn_key:
        return LOCK_UNAVAILABLE
    try:
        db = get_db()
        client = await payment_log._transaction_client()
    except Exception:
        logger.info("[cosigner] payment lock unavailable")
        return LOCK_FAILED
    if client is None or db is None:
        logger.info("[cosigner] payment lock unavailable")
        return LOCK_UNAVAILABLE

    async def wrapped(session):
        await payment_log.acquire_txn_lock(db[payment_log.LOCKS_COLLECTION], txn_key, session)
        await work(db, session)

    for attempt in range(_LOCK_ATTEMPTS):
        try:
            await payment_log.ensure_txn_lock_index(db[payment_log.LOCKS_COLLECTION])
            await payment_log._run_apply(client, wrapped)
            return LOCK_OK
        except payment_log.TransactionsUnavailable:
            logger.info("[cosigner] payment lock unavailable")
            return LOCK_UNAVAILABLE
        except CosignerLinkError as exc:
            logger.info("[cosigner] payment lock write refused txn=%s code=%s", txn_key, exc.code)
            return LOCK_REFUSED
        except Exception as exc:
            logger.warning(
                "[cosigner] payment lock write failed txn=%s attempt=%d/%d error=%s",
                txn_key, attempt + 1, _LOCK_ATTEMPTS, type(exc).__name__,
            )
            if attempt + 1 < _LOCK_ATTEMPTS:
                delays = _LOCK_RETRY_DELAYS or (0,)
                await asyncio.sleep(delays[min(attempt, len(delays) - 1)])
    return LOCK_FAILED


class PaymentRowsUnreadable(Exception):
    """The payment rows could not be read inside the lock. Not a refusal."""


async def _payment_rows(db, txn_key: str, session) -> Optional[list]:
    """Payment rows for this transaction, or None when they cannot be read.

    Scoped to the active tenant when multi-tenancy is on: payment rows are
    tenant-tagged and the claims allow the same transaction id in two
    tenants, so another tenant's charge or refund never decides this link.
    With the flag on and no tenant, the rows count as unreadable.
    """
    from dashboard.services.payment_log_claims import _read_rows

    query = _scope({"transaction_id": txn_key})
    if query is None:
        logger.info("[cosigner] payment rows need a tenant")
        return None
    try:
        coll = db["payments"]
    except Exception:
        return None
    if not hasattr(coll, "find"):
        return None
    try:
        return await _read_rows(db, txn_key, session, query=query)
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

    A read error is not a refusal: it raises ``PaymentRowsUnreadable`` so
    the lock reports ``LOCK_FAILED`` and a pending paid transition is kept
    (a heal does not drop its marker on a read it could not make).
    """
    from dashboard.services.payment_log_claims import (
        SETTLED_CHARGE_STATUSES,
        has_reversal_sibling,
        row_kind,
    )

    rows = await _payment_rows(db, str(transaction_id or "").strip(), session)
    if rows is None:
        # Not a decision. Raising makes the locked write fail, so the paid
        # transition stays pending and is replayed once the rows read.
        logger.info("[cosigner] paid deferred unreadable payment rows")
        raise PaymentRowsUnreadable("payment rows unreadable")
    if not rows:
        logger.info("[cosigner] paid refused empty payment rows")
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
    # The update is uncommitted until this transaction ends. The audit has
    # to read that write, so the read uses the same session.
    fresh = await get_collection(LINKS).find_one({"token": token}, session=session) or link
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
    fresh = await get_collection(LINKS).find_one({"token": token}, session=session) or link
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
    """Bond case id when this booking is exactly one case. Otherwise empty.

    A bond stored with ``Booking_Number`` and no ``booking_number`` is found
    too (a co-signer link can be issued for it through its case id). More
    than one case under either field is not one case, so nothing is
    returned and the paid stamp fails closed.
    """
    from dashboard.services.payment_log_claims import _case_id, _logical_bond_groups

    found = []
    for name in ("bond_cases", "active_bonds"):
        query = _scope({"$or": [
            {"booking_number": booking},
            {"Booking_Number": booking},
        ]})
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
    targets, _blocked = await _down_payment_scope(booking)
    return targets


async def _down_payment_scope(booking: str) -> tuple[list, list]:
    """``(targets, blocked)`` for this booking's down payment.

    ``targets`` is what ``_open_down_payment_links`` returns. ``blocked`` is
    the candidate links when more than one open packet claims the case, so
    the refusal can be flagged for review. It is empty otherwise.
    """
    case_id = await _one_case_id(booking)
    if not case_id:
        logger.info("[cosigner] paid refused ambiguous case")
        return [], []
    # A link issued through the booking fallback stores the resolved case id.
    # An older link may still have an empty case id and the same booking.
    query = _scope({"$or": [
        {"bond_case_id": case_id},
        {"booking_number": booking, "bond_case_id": {"$in": ["", None]}},
    ]})
    if query is None:
        return [], []
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
            return [], [pair for pairs in grouped.values() for pair in pairs]
        return [], []
    return next(iter(grouped.values())), []


_PAID_BLOCK_REVIEW_CODES = frozenset({"bond_mirror_mismatch", "bond_ambiguous"})
_PACKET_AMBIGUOUS = "packet_ambiguous"
_PAID_BLOCK_REASONS = _PAID_BLOCK_REVIEW_CODES | {_PACKET_AMBIGUOUS}


async def _blocked_charge_is_the_down_payment(
    packet: Optional[dict], paid_cents: Optional[int],
) -> bool:
    """The held charge is exactly this packet's down payment.

    The bond rows cannot be trusted on this path, so the packet's own amount
    due is used. Without one, a single ``bond_cases`` row for the packet's
    case supplies it. Anything else is not flagged, the same as the amount
    refusal on the paid path.
    """
    if paid_cents is None or not isinstance(packet, dict):
        return False
    due = invoice_due_cents(packet)
    if due is None:
        case_id = str(packet.get("bond_case_id") or "").strip()
        if case_id:
            try:
                rows = await _case_rows("bond_cases", case_id)
            except CosignerLinkError:
                rows = []
            if len(rows) == 1:
                due = invoice_due_cents(rows[0])
    return due is not None and int(due) == int(paid_cents)


def _version_match(version: int):
    """Filter value for ``paid_block_version``. A link never held has no field."""
    return {"$in": [0, None]} if not version else version


def _active_holds(doc: Optional[dict]) -> list:
    """Held charges on this link that no paid stamp has released yet."""
    doc = doc or {}
    released = set(doc.get("paid_block_released_transaction_ids") or [])
    return [
        str(txn) for txn in (doc.get("paid_blocked_transaction_ids") or [])
        if str(txn) not in released
    ]


def _hold_reason(doc: dict, txn: str, default: str = "") -> str:
    for hold in reversed(doc.get("paid_blocked_holds") or []):
        if isinstance(hold, dict) and str(hold.get("transaction_id") or "") == txn:
            return str(hold.get("reason") or default)
    return default


def _review_view(doc: Optional[dict]) -> dict:
    """The review values an audit records before and after a hold change."""
    doc = doc or {}
    return {
        "needs_review": bool(doc.get("needs_review")),
        "needs_review_reason": str(doc.get("needs_review_reason") or ""),
        "held_transaction_ids": _active_holds(doc),
    }


_HOLD_WRITE_ATTEMPTS = 3


async def _flag_paid_blocked(
    link: dict,
    packet: Optional[dict],
    txn: str,
    code: str,
    paid_cents: Optional[int],
    now: datetime,
    session,
) -> bool:
    """Leave a staff-visible review mark when a settled charge cannot stamp.

    The payment row is already recorded. The bond rows disagree, or more than
    one open packet claims the case, so the link stays unpaid. Only a charge
    equal to the packet's down payment is flagged. The link gets
    ``needs_review`` with the reason, the transaction is added to
    ``paid_blocked_transaction_ids`` and a ``paid_blocked_holds`` entry keeps
    its own reason. One ``paid_blocked`` audit is written per transaction,
    with the review values before and after, so a hold that replaces
    another one leaves both in the history. The update is conditional on
    the transaction not being in the list and on ``paid_block_version``, so
    a replay does not audit again and a concurrent hold is not lost.
    Nothing is sent anywhere.
    """
    token = str(link.get("token") or "").strip()
    if not token or not txn:
        return False
    if not await _blocked_charge_is_the_down_payment(packet, paid_cents):
        logger.info("[cosigner] paid hold not flagged amount packet_id=%s", link.get("packet_id"))
        return False
    coll = get_collection(LINKS)
    for _ in range(_HOLD_WRITE_ATTEMPTS):
        fresh = await coll.find_one({"token": token}, session=session)
        if not fresh:
            return False
        if txn in [str(t) for t in (fresh.get("paid_blocked_transaction_ids") or [])]:
            return False
        version = int(fresh.get("paid_block_version") or 0)
        before = _review_view(fresh)
        result = await coll.update_one(
            {
                "token": token,
                "paid_blocked_transaction_ids": {"$ne": txn},
                "paid_block_version": _version_match(version),
            },
            {
                "$set": {
                    "needs_review": True,
                    "needs_review_reason": code,
                    "paid_blocked_transaction_id": txn,
                    "paid_blocked_at": now.isoformat(),
                    "paid_block_version": version + 1,
                },
                "$push": {
                    "paid_blocked_transaction_ids": txn,
                    "paid_blocked_holds": {
                        "transaction_id": txn,
                        "reason": code,
                        "at": now.isoformat(),
                    },
                },
            },
            session=session,
        )
        if not getattr(result, "modified_count", 0):
            continue
        after = {
            "needs_review": True,
            "needs_review_reason": code,
            "held_transaction_ids": before["held_transaction_ids"] + [txn],
        }
        state = _audit_state(fresh, packet)
        await _audit(
            "paid_blocked", fresh, now,
            old_state=state, new_state=state, session=session,
            reason=code,
            extra={
                "needs_review": True,
                "transaction_id": txn,
                "amount_cents": paid_cents,
                "review_before": before,
                "review_after": after,
            },
        )
        logger.info(
            "[cosigner] paid blocked for review packet_id=%s reason=%s",
            link.get("packet_id"), code,
        )
        return True
    logger.info("[cosigner] paid hold write lost the race packet_id=%s", link.get("packet_id"))
    return False


async def _clear_paid_block(
    link: dict, packet: Optional[dict], txn: str, now: datetime, session,
) -> bool:
    """Release this charge's hold now that it stamped paid.

    Only ``txn`` is released (``paid_block_released_transaction_ids``). When
    another held charge is still recorded on the link, ``needs_review`` stays
    set and the reason becomes that charge's reason: staff still have to look
    at a second down payment. When none is left and the reason is a paid
    hold, the review mark is cleared. A reason staff set for something else
    is left alone. One audit (``paid_block_cleared``, or ``paid_hold_released``
    when the hold is kept) records the review values before and after, in
    the same payment transaction.
    """
    token = str(link.get("token") or "").strip()
    if not token or not txn:
        return False
    coll = get_collection(LINKS)
    for _ in range(_HOLD_WRITE_ATTEMPTS):
        fresh = await coll.find_one({"token": token}, session=session)
        if not fresh:
            return False
        held = _active_holds(fresh)
        if txn not in held:
            return False
        remaining = [t for t in held if t != txn]
        reason = str(fresh.get("needs_review_reason") or "")
        before = _review_view(fresh)
        version = int(fresh.get("paid_block_version") or 0)
        fields = {
            "paid_block_cleared_at": now.isoformat(),
            "paid_block_version": version + 1,
        }
        if remaining:
            fields["needs_review"] = True
            fields["needs_review_reason"] = _hold_reason(fresh, remaining[-1], reason)
        elif reason in _PAID_BLOCK_REASONS:
            fields["needs_review"] = False
            fields["needs_review_reason"] = ""
        result = await coll.update_one(
            {"token": token, "paid_block_version": _version_match(version)},
            {
                "$set": fields,
                "$push": {"paid_block_released_transaction_ids": txn},
            },
            session=session,
        )
        if not getattr(result, "modified_count", 0):
            continue
        after = {
            "needs_review": bool(fields.get("needs_review", before["needs_review"])),
            "needs_review_reason": str(fields.get("needs_review_reason", before["needs_review_reason"])),
            "held_transaction_ids": remaining,
        }
        state = _audit_state(fresh, packet)
        await _audit(
            "paid_hold_released" if remaining else "paid_block_cleared",
            fresh, now,
            old_state=state, new_state=state, session=session,
            reason=_hold_reason(fresh, txn, reason),
            extra={
                "transaction_id": txn,
                "hold_kept": bool(remaining),
                "review_before": before,
                "review_after": after,
            },
        )
        return True
    logger.info("[cosigner] paid hold release lost the race packet_id=%s", link.get("packet_id"))
    return False


async def note_cosigner_paid(
    booking_number: str,
    transaction_id: str,
    amount: Any = None,
    *,
    status: Any = None,
    _heal: bool = True,
) -> bool:
    """Stamp paid when the charge equals the confirmed down payment. Idempotent.

    A smaller charge is not the down payment. Nothing is stamped, and the
    full premium stays unpaid. The write takes ``payment_txn_locks`` and
    updates the link only when it is not already stamped, so one charge
    writes one paid audit. The links are the open packet on this bond case,
    not every link that shares the booking number.

    Returns True when the decision was made (stamped, refused by a rule, or
    a no-op replay). When every locked attempt fails, the unpaid links on
    the case get a durable pending transition and False is returned; the
    next event on the booking or the next page read reconciles it from the
    payment rows (``reconcile_cosigner_payment_state``).
    """
    booking = str(booking_number or "").strip()
    txn = str(transaction_id or "").strip()
    if not booking or not txn:
        return False
    if status is not None:
        from dashboard.services.payment_log_claims import SETTLED_CHARGE_STATUSES

        if str(status).strip().lower() not in SETTLED_CHARGE_STATUSES:
            logger.info("[cosigner] paid refused unsettled status")
            return True
    paid_cents = _dollars_to_cents(amount)

    async def work(db, session):
        if await _charge_row_blocks_down_payment(db, txn, session):
            logger.info("[cosigner] paid refused review hold")
            return
        targets, blocked = await _down_payment_scope(booking)
        now = _now()
        for link, packet in blocked:
            if not str(link.get("paid_transaction_id") or "").strip():
                await _flag_paid_blocked(
                    link, packet, txn, _PACKET_AMBIGUOUS, paid_cents, now, session,
                )
        for link, packet in targets:
            try:
                bond = await _find_bond(packet) if packet else None
            except CosignerLinkError as exc:
                if exc.code not in _PAID_BLOCK_REVIEW_CODES:
                    raise
                await _flag_paid_blocked(
                    link, packet, txn, exc.code, paid_cents, now, session,
                )
                continue
            due = _due_cents(packet, bond)
            if paid_cents is None or due is None or paid_cents != due:
                logger.info("[cosigner] paid amount refused packet_id=%s", link.get("packet_id"))
                continue
            marked = await _cas_mark_paid(link, packet, txn, now, session)
            if marked and (
                _active_holds(link)
                or str(link.get("needs_review_reason") or "") in _PAID_BLOCK_REASONS
            ):
                await _clear_paid_block(link, packet, txn, now, session)

    outcome = await _with_payment_lock(txn, work)
    if outcome == LOCK_FAILED:
        await _record_pending_transition(
            "paid", booking, txn, await _paid_pending_links(booking),
        )
        return False
    if outcome == LOCK_OK and _heal:
        await _heal_booking(booking)
    return outcome in (LOCK_OK, LOCK_REFUSED)


async def note_cosigner_reversed(
    booking_number: str, transaction_id: str, *, _heal: bool = True,
) -> bool:
    """Reopen the pay step when the stamped down payment is refunded or fails.

    The link is found by ``paid_transaction_id``, scoped to the tenant, inside
    ``payment_txn_locks``. A missing reference or a different booking on the
    refund does not keep the step paid. A different transaction leaves the
    stamp in place. A replay does not audit twice.

    Returns True when the decision was made. When every locked attempt
    fails, the links still stamped with this transaction get a durable
    pending transition and False is returned.
    """
    txn = str(transaction_id or "").strip()
    if not txn:
        return False
    query = _scope({"paid_transaction_id": txn})
    if query is None:
        return False

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

    outcome = await _with_payment_lock(txn, work)
    booking = str(booking_number or "").strip()
    if outcome == LOCK_FAILED:
        links = []
        try:
            async for link in _iter(get_collection(LINKS).find(query)):
                if str(link.get("paid_transaction_id") or "").strip() == txn:
                    links.append(link)
        except Exception:
            logger.warning("[cosigner] pending reversal lookup failed txn=%s", txn)
        await _record_pending_transition("reversed", booking, txn, links)
        return False
    if outcome == LOCK_OK and _heal and booking:
        await _heal_booking(booking)
    return outcome in (LOCK_OK, LOCK_REFUSED)


def _pending_key(kind: str, txn: str) -> str:
    return f"{kind}:{txn}"


async def _paid_pending_links(booking: str) -> list:
    """Unpaid links a failed paid write could have stamped or flagged."""
    try:
        targets, blocked = await _down_payment_scope(booking)
    except Exception:
        logger.warning("[cosigner] pending paid lookup failed")
        return []
    return [
        link for link, _packet in list(targets) + list(blocked)
        if not str(link.get("paid_transaction_id") or "").strip()
    ]


async def _record_pending_transition(kind: str, booking: str, txn: str, links: list) -> None:
    """Durable marker for a paid or reopen write that failed every attempt.

    Written outside the failed transaction. One ``payment_transition_pending``
    audit per link and transition: the push is conditional on the key not
    being pending yet. The payment row itself is already recorded and is the
    source of truth the reconcile step reads.

    The pending key and an owed-audit marker are pushed in one document
    update, so the state change never lands without a record that its
    audit is due. The audit is then written and the marker pulled. If the
    audit write fails, the marker stays and the next record, reconcile or
    heal on this link writes it (``_flush_owed_audits``).
    """
    key = _pending_key(kind, txn)
    now = _now()
    for link in links:
        token = str(link.get("token") or "").strip()
        if not token:
            continue
        try:
            entry = {
                "key": key,
                "kind": kind,
                "transaction_id": txn,
                "booking_number": booking,
                "at": now.isoformat(),
            }
            coll = get_collection(LINKS)
            result = None
            for _attempt in range(_PENDING_CAS_ATTEMPTS):
                doc = await coll.find_one({"token": token})
                if not isinstance(doc, dict):
                    break
                before = _pending_view(doc)
                if key in before["pending_payment_keys"]:
                    break
                after = {
                    "payment_transition_pending": True,
                    "pending_payment_keys": before["pending_payment_keys"] + [key],
                    "pending_payment_transitions": (
                        before["pending_payment_transitions"] + [dict(entry)]
                    ),
                }
                # Compare-and-set on the keys read: the snapshot stored in
                # the owed marker is exactly this transition's before/after.
                result = await coll.update_one(
                    {"token": token, **_pending_keys_match(doc)},
                    {
                        "$set": {
                            "pending_payment_keys": after["pending_payment_keys"],
                            "pending_payment_transitions": after["pending_payment_transitions"],
                            "payment_transition_pending": True,
                            "payment_transition_pending_at": now.isoformat(),
                            "payment_audit_owed": True,
                        },
                        "$push": {
                            "pending_payment_audits_owed": _owed_audit(
                                "payment_transition_pending", token, entry, now,
                                before=before, after=after,
                            ),
                        },
                    },
                )
                if getattr(result, "modified_count", 0):
                    break
            if result is not None and getattr(result, "modified_count", 0):
                logger.warning(
                    "[cosigner] payment transition pending packet_id=%s kind=%s",
                    link.get("packet_id"), kind,
                )
        except Exception:
            logger.error(
                "[cosigner] pending transition not recorded packet_id=%s kind=%s",
                link.get("packet_id"), kind,
            )
            continue
        await _flush_owed_audits(token)


_PENDING_CAS_ATTEMPTS = 5


def _pending_keys_match(doc: dict) -> dict:
    """Filter that matches only while the pending keys are what was read.

    Keys and transitions are always written together, so an exact match on
    the keys is a compare-and-set for the whole pending state.
    """
    keys = doc.get("pending_payment_keys")
    if isinstance(keys, list):
        return {"pending_payment_keys": list(keys)}
    return {"pending_payment_keys": None}


def _owed_audit(
    step: str, token: str, entry: dict, now: datetime,
    *, before: Optional[dict] = None, after: Optional[dict] = None,
) -> dict:
    """An audit that is due for a pending-state change on this link.

    ``audit_key`` is the same on every retry, so a flush that wrote the
    audit but could not pull the marker does not write it twice.
    ``pending_before``/``pending_after`` are this transition's own
    snapshot, taken by the update that made it, so a later flush does not
    rebuild them from a document other transitions have since changed.
    """
    key = str(entry.get("key") or "")
    at = str(entry.get("at") or "")
    digest = hashlib.sha256(f"{step}|{token}|{key}|{at}".encode()).hexdigest()[:32]
    return {
        "audit_key": f"cosigner-pending:{digest}",
        "step": step,
        "pending_key": key,
        "entry": dict(entry),
        "at": now.isoformat(),
        **({"pending_before": dict(before), "pending_after": dict(after)}
           if isinstance(before, dict) and isinstance(after, dict) else {}),
    }


async def _flush_owed_audits(token: str) -> bool:
    """Write the owed pending/resolved audits on this link. Never raises.

    True when nothing is owed afterwards. Each audit records the
    before/after pending snapshot stored on its marker. Older markers
    without one fall back to the link with this key present (pending) or
    absent (resolved).
    """
    token = str(token or "").strip()
    if not token:
        return True
    coll = get_collection(LINKS)
    try:
        doc = await coll.find_one({"token": token})
    except Exception:
        logger.warning("[cosigner] owed audit lookup failed")
        return False
    if not isinstance(doc, dict):
        return True
    owed = [o for o in (doc.get("pending_payment_audits_owed") or []) if isinstance(o, dict)]
    clean = True
    audits = get_collection("audit_events")
    for item in owed:
        audit_key = str(item.get("audit_key") or "")
        step = str(item.get("step") or "")
        key = str(item.get("pending_key") or "")
        entry = item.get("entry") if isinstance(item.get("entry"), dict) else {}
        kind = str(entry.get("kind") or "")
        txn = str(entry.get("transaction_id") or "")
        try:
            if not audit_key or not step or not key:
                raise ValueError("malformed owed audit")
            # Audits written before audit_key became the _id carry it as a
            # field; those are found here and left as they are.
            if not await audits.find_one({"audit_key": audit_key}):
                view = _pending_view(doc)
                stored_before = item.get("pending_before")
                stored_after = item.get("pending_after")
                if isinstance(stored_before, dict) and isinstance(stored_after, dict):
                    before, after = dict(stored_before), dict(stored_after)
                elif step == "payment_transition_pending":
                    # Markers written before snapshots were stored.
                    base = _pending_without(view, key)
                    after = {
                        "payment_transition_pending": True,
                        "pending_payment_keys": base["pending_payment_keys"] + [key],
                        "pending_payment_transitions": (
                            base["pending_payment_transitions"] + [dict(entry)]
                        ),
                    }
                    before = base
                else:
                    after = _pending_without(view, key)
                    before = _pending_with(after, key, entry)
                state = _audit_state_only(_audit_state(doc, None))
                at = item.get("at")
                try:
                    when = datetime.fromisoformat(str(at))
                except (TypeError, ValueError):
                    when = _now()
                try:
                    # audit_key is the document _id: insert-only, so two
                    # flushes of the same marker cannot both write it.
                    await _audit(
                        step, doc, when,
                        old_state=state, new_state=state,
                        reason=kind,
                        extra={
                            "_id": audit_key,
                            "audit_key": audit_key,
                            "transaction_id": txn, "transition": kind, "pending_key": key,
                            "pending_before": before, "pending_after": after,
                        },
                    )
                except DuplicateKeyError:
                    pass  # already written by a concurrent flush
            await coll.update_one(
                {"token": token},
                {"$pull": {"pending_payment_audits_owed": {"audit_key": audit_key}}},
            )
        except Exception:
            clean = False
            logger.error(
                "[cosigner] pending audit not written packet_id=%s step=%s",
                doc.get("packet_id"), step,
            )
    if clean:
        try:
            await coll.update_one(
                {"token": token, "pending_payment_audits_owed": []},
                {"$set": {"payment_audit_owed": False}},
            )
        except Exception:
            return False
    return clean


def _pending_view(doc: Optional[dict]) -> dict:
    """The pending-transition fields an audit records before and after."""
    doc = doc or {}
    return {
        "payment_transition_pending": bool(doc.get("payment_transition_pending")),
        "pending_payment_keys": [str(k) for k in (doc.get("pending_payment_keys") or [])],
        "pending_payment_transitions": [
            dict(entry) for entry in (doc.get("pending_payment_transitions") or [])
            if isinstance(entry, dict)
        ],
    }


def _pending_without(after: dict, key: str) -> dict:
    """The values before ``key`` was pushed: the same document without it.

    The flag is set with every push and cleared only when no key is left,
    so before the push it was pending exactly when another key was.
    """
    keys = [k for k in after["pending_payment_keys"] if k != key]
    return {
        "payment_transition_pending": bool(keys),
        "pending_payment_keys": keys,
        "pending_payment_transitions": [
            entry for entry in after["pending_payment_transitions"]
            if str(entry.get("key") or "") != key
        ],
    }


def _pending_with(after: dict, key: str, entry: Optional[dict]) -> dict:
    """The values before ``key`` was pulled: it was pending, with its entry."""
    transitions = list(after["pending_payment_transitions"])
    if isinstance(entry, dict):
        transitions.append(dict(entry))
    return {
        "payment_transition_pending": True,
        "pending_payment_keys": list(after["pending_payment_keys"]) + [key],
        "pending_payment_transitions": transitions,
    }


async def _resolve_pending(
    link: dict, key: str, kind: str, txn: str, entry: Optional[dict] = None,
) -> bool:
    """Pull a decided pending key, with its owed resolved audit, in one update.

    The same update sets ``payment_transition_pending`` from the keys that
    are left (a compare-and-set on the keys read), so no later step can
    leave the flag true with nothing pending. The audit is written afterwards by ``_flush_owed_audits``; if that write
    fails the marker stays on the link and is retried on the next read.
    """
    token = str(link.get("token") or "").strip()
    if not token:
        return False
    coll = get_collection(LINKS)
    if entry is None:
        entry = next(
            (e for e in (link.get("pending_payment_transitions") or [])
             if isinstance(e, dict) and str(e.get("key") or "") == key),
            None,
        )
    entry = dict(entry) if isinstance(entry, dict) else {
        "key": key, "kind": kind, "transaction_id": txn,
    }
    entry.setdefault("key", key)
    entry.setdefault("kind", kind)
    entry.setdefault("transaction_id", txn)
    resolved = False
    for _attempt in range(_PENDING_CAS_ATTEMPTS):
        doc = await coll.find_one({"token": token})
        if not isinstance(doc, dict):
            return False
        before = _pending_view(doc)
        if key not in before["pending_payment_keys"]:
            return False
        after = _pending_without(before, key)
        now = _now()
        # One update pulls the key, sets the flag from what is left and
        # records the owed audit, so the flag can never be left stale.
        result = await coll.update_one(
            {"token": token, **_pending_keys_match(doc)},
            {
                "$set": {
                    "pending_payment_keys": after["pending_payment_keys"],
                    "pending_payment_transitions": after["pending_payment_transitions"],
                    "payment_transition_pending": after["payment_transition_pending"],
                    "payment_audit_owed": True,
                },
                "$push": {
                    "pending_payment_audits_owed": _owed_audit(
                        "payment_transition_resolved", token,
                        entry, now, before=before, after=after,
                    ),
                },
            },
        )
        if getattr(result, "modified_count", 0):
            resolved = True
            break
    if not resolved:
        return False
    await _flush_owed_audits(token)
    return True


async def _stored_rows(txn: str) -> Optional[list]:
    from dashboard.extensions import get_db

    try:
        db = get_db()
    except Exception:
        return None
    if db is None:
        return None
    return await _payment_rows(db, txn, None)


async def _heal_paid(booking: str, txn: str) -> bool:
    """Replay a pending paid write from the stored charge row.

    The amount and status come from the payment row, and
    ``note_cosigner_paid`` re-reads the rows under the lock, so the same
    rules apply: settled status, no reversal row, not ``needs_review``, the
    exact ``invoice_due_cents``, the same CAS. Unreadable or missing rows
    keep the transition pending.
    """
    from dashboard.services.payment_log_claims import row_kind

    rows = await _stored_rows(txn)
    if not rows:
        return False
    charges = [row for row in rows if row_kind(row) == "charge"]
    if not charges:
        return True
    charge = charges[0]
    return await note_cosigner_paid(
        booking, txn, charge.get("amount"),
        status=charge.get("status"), _heal=False,
    )


async def _reconcile(link: dict) -> bool:
    if link.get("payment_audit_owed") or link.get("pending_payment_audits_owed"):
        await _flush_owed_audits(link.get("token"))
    booking = str(link.get("booking_number") or "").strip()
    keys = [str(k) for k in (link.get("pending_payment_keys") or [])]
    changed = False
    for entry in list(link.get("pending_payment_transitions") or []):
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "")
        kind = str(entry.get("kind") or "")
        txn = str(entry.get("transaction_id") or "").strip()
        if key not in keys or not txn:
            continue
        entry_booking = str(entry.get("booking_number") or "").strip() or booking
        if kind == "paid":
            decided = await _heal_paid(entry_booking, txn)
        elif kind == "reversed":
            rows = await _stored_rows(txn)
            decided = bool(rows) and await note_cosigner_reversed(
                entry_booking, txn, _heal=False,
            )
        else:
            decided = True
        if decided and await _resolve_pending(link, key, kind, txn, entry):
            changed = True
    # A refund row that is stored while the link still shows paid, with no
    # pending record (the marker write failed too): reopen from the rows.
    paid_txn = str(link.get("paid_transaction_id") or "").strip()
    if paid_txn and not link.get("payment_reversed") and _pending_key("reversed", paid_txn) not in keys:
        rows = await _stored_rows(paid_txn)
        if rows and _rows_include_reversal(rows):
            await note_cosigner_reversed(booking, paid_txn, _heal=False)
            changed = True
    return changed


async def reconcile_cosigner_payment_state(link: Optional[dict]) -> bool:
    """Bring a link's pay step in line with the stored payment rows.

    Runs on the co-signer page read and after each paid or refund event on
    the booking. It replays pending transitions through ``note_cosigner_paid``
    and ``note_cosigner_reversed`` (same lock, same rules, same CAS), so a
    link is stamped once and audited once, and no plan balance or
    ``last_payment`` is touched. Never raises. True when something changed.
    """
    if not isinstance(link, dict):
        return False
    try:
        return await _reconcile(link)
    except Exception:
        logger.warning("[cosigner] payment reconcile failed packet_id=%s", link.get("packet_id"))
        return False


async def _heal_booking(booking: str) -> None:
    """Reconcile links on this booking that still carry a pending transition."""
    query = _scope({"booking_number": booking, "$or": [
        {"payment_transition_pending": True},
        {"payment_audit_owed": True},
    ]})
    if query is None:
        return
    try:
        links = [link async for link in _iter(get_collection(LINKS).find(query))]
    except Exception:
        logger.warning("[cosigner] pending transition lookup failed")
        return
    for link in links[:20]:
        await reconcile_cosigner_payment_state(link)


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


def _swipe_origin(url: str) -> tuple[str, str, int]:
    """Scheme, hostname, and port. A missing port on https is 443."""
    parsed = urlparse(str(url or "").strip())
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if parsed.port is not None:
        port = int(parsed.port)
    else:
        port = 443
    return scheme, host, port


def _invoice_host_rejected(link: str) -> bool:
    """A pay URL must match the configured SwipeSimple origin, including port."""
    from dashboard.services.swipesimple_invoice_service import _DEFAULT_BASE_URL

    base = (os.getenv("SWIPESIMPLE_BASE_URL") or _DEFAULT_BASE_URL).strip()
    expected = _swipe_origin(base)
    got = _swipe_origin(link)
    if not got[1] or not expected[1]:
        return True
    return got != expected


def _invoice_source(packet: dict, bond: Optional[dict]) -> tuple[str, Optional[dict]]:
    """The case invoice URL and the document that stored it."""
    for doc in (packet, bond):
        link = case_invoice_link(doc)
        if link:
            return link, doc if isinstance(doc, dict) else None
    return "", None


def _invoice_not_this_case(doc: Optional[dict], packet: dict, bond: Optional[dict]) -> bool:
    """The stored invoice number and bond id must name this booking and bond.

    A missing number or bond id cannot prove ownership, so the pay URL stays
    hidden. The fields are read from the document that supplied the URL.
    """
    number = str((doc or {}).get("swipesimple_invoice_number") or "").strip()
    stored_bond = str((doc or {}).get("swipesimple_invoice_bond_id") or "").strip()
    booking = _bond_field(bond or {}, "booking_number", "Booking_Number")
    if not booking:
        booking = str((packet or {}).get("booking_number") or "").strip()
    case_id = _bond_field(bond or {}, "bond_case_id", "Bond_Case_ID")
    if not case_id:
        case_id = str((packet or {}).get("bond_case_id") or "").strip()
    if not number or not stored_bond or not booking or not case_id:
        return True
    return number != booking or stored_bond != case_id


def _invoice_amount_unproven(doc: Optional[dict], due_cents: Optional[int]) -> bool:
    """The stored SwipeSimple cents are missing or are not this due amount.

    Uses ``_reject_unproven_invoice_amount`` for a present value: a non-integer
    or a value other than the due cents is unproven. A missing value is also
    unproven on this page, including when that helper would keep an older row
    that has no confirmed premium.
    """
    if not isinstance(doc, dict) or due_cents is None:
        return True
    stored = doc.get("swipesimple_invoice_amount_cents")
    if stored in (None, ""):
        return True
    try:
        _reject_unproven_invoice_amount(doc, int(due_cents))
    except SwipeSimpleInvoiceError:
        return True
    return False


def _payment_view(packet: dict, bond: Optional[dict]) -> dict:
    cents = invoice_due_cents(packet)
    if cents is None and bond:
        cents = invoice_due_cents(bond)
    amount = f"{(cents or 0) / 100:.2f}" if cents is not None else ""
    link, source = _invoice_source(packet, bond)
    unproven = bool(link) and _invoice_amount_unproven(source, cents)
    unbound = False
    off_host = False
    if unproven:
        link = ""
    elif link and _invoice_not_this_case(source, packet, bond):
        link = ""
        unbound = True
    elif link and _invoice_host_rejected(link):
        link = ""
        off_host = True
    dry_run = not dispatch_live_enabled()
    view = {
        "amount": amount,
        "dry_run": dry_run,
        "dispatched": False if dry_run else bool(link),
        "url": link or None,
    }
    if unproven:
        view["error"] = "invoice_amount_unproven"
    elif unbound:
        view["error"] = "invoice_unbound"
    elif off_host:
        view["error"] = "invoice_origin"
    return view


async def cosigner_state(token: str, lang: Optional[str] = None) -> dict:
    link = await _link_by_token(token)
    if await reconcile_cosigner_payment_state(link):
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
    elif (
        nothing_due
        and signed
        and verified
        and _timeline_push(link, "nothing_due", now)
    ):
        # The audit is history. The link is not stored as paid, so a later
        # confirmed amount above zero can still show the pay link.
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
    if step == "sign":
        found, problem = _sign_url(
            packet,
            str(link.get("signer_role") or "indemnitor"),
            str(link.get("signer_name") or ""),
            _signer_email(packet, bond, str(link.get("signer_role") or "indemnitor")),
        )
        # Signing is still ahead. A bad URL must not be shown. Pay and
        # paid do not receive the signing URL.
        if problem:
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


def _html_attr(value: str) -> str:
    """Escape a value placed in a single-quoted HTML attribute."""
    return html.escape(str(value or ""), quote=True)


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
            f"<p><a href='{_html_attr(sign_url)}'>DocuSeal</a></p>"
        )
    elif step == "sign":
        body = f"<h1>{strings['sign_title']}</h1><p>{strings['locked']}</p>"
    elif step == "pay":
        note = strings["pay_dry_run"] if payment.get("dry_run") else strings["pay_title"]
        url = payment.get("url") or ""
        pay_link = f"<p><a href='{_html_attr(url)}'>Pay</a></p>" if url else ""
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
