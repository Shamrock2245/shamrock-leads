"""Self-serve ID confirm → draft BondCase → DocuSeal prefill.

Flag ``PAPERWORK_SELF_SERVE_PREFILL`` (default off). Off leaves the portal
on today's "agent will text you" / "no packet on file" behavior.

On, a confirmed scan:

1. Reuses a BondCase for the same phone, name, and date of birth, or inserts
   one draft via ``ensure_self_serve_bondcase`` (which calls
   ``ensure_match_bondcase`` only when a booking is already on file).
2. Builds the signer's packet with ``DocuSealService.prefill_values_from_bond``
   and ``DocuSealService.create_submission`` (``send_email`` and ``send_sms``
   false). A case that already has a validated match, POA, case number, and
   premium uses ``create_submission_for_packet`` instead.
3. If that build fails, keeps the confirmed fields and returns no signing URL.

Leads Ops' later auto-packet builder should call
``get_pending_id_prefill(case_id, role, person_id)`` and can replace the
DocuSeal call inside ``build_self_serve_packet``. The lookup key is the
BondCase id, the signer role, and the person id. It does not include a POA
number.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dashboard.services.exclusive_insert import insert_new

logger = logging.getLogger(__name__)

_FLAG = "PAPERWORK_SELF_SERVE_PREFILL"
_ROLES = ("indemnitor", "coindemnitor", "defendant")
_VOID_STATUSES = frozenset({"voided", "cancelled", "canceled"})
_FINAL_STATUSES = frozenset({"signed", "completed", "filed", "archived"})
_DEAD_SIGNER = frozenset({"voided", "cancelled", "canceled", "declined", "unverified"})
_HOLD_MESSAGE = (
    "We saved your information. A signing link is not available yet. "
    "Call (239) 332-2245."
)
_READY_MESSAGE = "Your paperwork is ready to sign."
_OCR_KEYS = (
    "full_name", "first_name", "last_name", "dob", "address", "city", "state",
    "zip", "dl_number", "dl_state", "issuing_state", "expiration_date", "sex",
)

_BOND_KEYS = frozenset({
    "bond_amount", "Bond_Amount", "numeric_full_bond_amount",
    "numeric_full_bond_amount_dollar", "bond_amount_numeric",
    "bond_amount_words", "bond_amount_written", "full_bond_amount_words",
})
_PREMIUM_KEYS = frozenset({
    "premium", "premium_amount", "numeric_premium", "numeric_premium_dollar",
    "premium_numeric", "premium_words", "premium_written", "written_premium",
    "premium_down_payment_numeric", "premium_down_payment_written",
    "numeric_downpayment", "numeric_balance_due", "numeric_misc_charges",
    "numeric_total_charges", "down_payment_amount", "balance_financed_amount",
    "number_of_payments", "payment_amount",
})
_POA_KEYS = frozenset({
    "poa_number", "POA_Number", "PowerNum", "all_poa_numbers", "poa_numbers",
    "bond_numbers", "BondNumbers", "collateral_receipt_number",
})


def self_serve_prefill_enabled() -> bool:
    return str(os.getenv(_FLAG) or "").strip().lower() in {"1", "true", "yes", "on"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _col(name: str):
    from dashboard.extensions import get_collection
    return get_collection(name)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive(value: Any) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _prefix(role: str) -> str:
    return {"defendant": "defendant_", "coindemnitor": "coindemnitor_"}.get(role, "indemnitor_")


def role_fields_from_scan(extracted: Optional[Dict[str, Any]], role: str) -> Dict[str, str]:
    """Map one ID read onto that role's paperwork fields. Other roles are omitted."""
    role = role if role in _ROLES else ""
    if not role:
        return {}
    extracted = extracted if isinstance(extracted, dict) else {}
    from dashboard.services.id_ocr_service import normalize_person_name

    full_name = normalize_person_name(
        extracted.get("full_name")
        or " ".join(part for part in (extracted.get("first_name"), extracted.get("last_name")) if part)
    )
    first = normalize_person_name(extracted.get("first_name") or "")
    last = normalize_person_name(extracted.get("last_name") or "")
    if full_name and not first:
        parts = full_name.split()
        first = parts[0] if parts else ""
        last = " ".join(parts[1:]) if len(parts) > 1 else last
    address = _text(extracted.get("address"))
    city = _text(extracted.get("city"))
    state = _text(extracted.get("state") or extracted.get("address_state"))
    zip_code = _text(extracted.get("zip"))
    dob = _text(extracted.get("dob"))
    dl_number = _text(extracted.get("dl_number") or extracted.get("dl"))
    dl_state = _text(extracted.get("dl_state") or extracted.get("issuing_state"))
    expiration = _text(extracted.get("expiration_date") or extracted.get("dl_expiration"))
    sex = _text(extracted.get("sex")).upper()[:1]
    prefix = _prefix(role)
    raw: Dict[str, str] = {
        prefix + "name": full_name,
        prefix + "dob": dob,
        prefix + "address": address,
        prefix + "city": city,
        prefix + "state": state,
        prefix + "zip": zip_code,
        prefix + "dl": dl_number,
        prefix + "dl_state": dl_state,
        prefix + "dl_expiration": expiration,
        prefix + "sex": sex,
    }
    if role == "defendant":
        raw.update({
            "DefName": full_name,
            "DefFirstName": first,
            "DefLastName": last,
            "DefDOB": dob,
            "DefAddress": address,
            "DefCity": city,
            "DefState": state,
            "DefZip": zip_code,
            "DefDL": dl_number,
            "DefDLState": dl_state,
            "DefSex": sex,
            "defendant_license_number": dl_number,
            "defendant_license_state": dl_state,
        })
    elif role == "coindemnitor":
        raw["Ind2Name"] = full_name
        raw["coindemnitor_first_name"] = first
        raw["coindemnitor_last_name"] = last
    else:
        raw.update({
            "IndemnitorName": full_name,
            "IndName": full_name,
            "FullName": full_name,
            "indemnitor_first_name": first,
            "indemnitor_last_name": last,
            "IndDOB": dob,
            "IndAddress": address,
            "IndCity": city,
            "IndState": state,
            "IndZip": zip_code,
            "IndDL": dl_number,
        })
    if city or state or zip_code:
        raw[prefix + "city_state_zip"] = ", ".join(
            part for part in (city, f"{state} {zip_code}".strip()) if part
        )
        if role == "indemnitor":
            raw["IndCityStateZip"] = raw[prefix + "city_state_zip"]
    return {key: value for key, value in raw.items() if _text(value)}


def restrict_to_role(fields: Optional[Dict[str, Any]], role: str) -> Dict[str, str]:
    """Drop keys that belong to a different signer role."""
    from dashboard.routers.pin_portal import _sanitize_client_fields

    role = role if role in _ROLES else ""
    clean = _sanitize_client_fields(fields)
    if not role:
        return {}
    prefix = _prefix(role)
    allowed_aliases = {
        "defendant": {"DefName", "DefFirstName", "DefLastName", "DefDOB", "DefAddress", "DefCity", "DefState", "DefZip", "DefDL", "DefDLState", "DefSex", "defendant_license_number", "defendant_license_state"},
        "coindemnitor": {"Ind2Name", "coindemnitor_first_name", "coindemnitor_last_name"},
        "indemnitor": {"IndemnitorName", "IndName", "FullName", "indemnitor_first_name", "indemnitor_last_name", "IndDOB", "IndAddress", "IndCity", "IndState", "IndZip", "IndDL", "IndCityStateZip"},
    }[role]
    kept: Dict[str, str] = {}
    for key, value in clean.items():
        if key.startswith(prefix) or key in allowed_aliases:
            kept[key] = value
    return kept


def merge_confirmed_fields(ocr_fields: Dict[str, str], edits: Optional[Dict[str, Any]], role: str) -> Dict[str, str]:
    """Signer edits overlay the OCR map. Keys for another role are ignored."""
    base = restrict_to_role(ocr_fields, role)
    edited = restrict_to_role(edits, role)
    return {**base, **edited}


def _backed(case: Dict[str, Any], *keys: str) -> bool:
    return any(_positive(case.get(key)) for key in keys)


def _has_poa(case: Dict[str, Any]) -> bool:
    return bool(_text(case.get("poa_number") or case.get("POA_Number")))


def strip_unbacked_amounts(values: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    """Remove bond, premium, and POA prefill that the case does not already have.

    ``prefill_values_from_bond`` fills a premium at 10% of the bond when the
    premium is blank. A self-serve draft must not keep that figure.
    """
    cleaned = dict(values)
    if not _backed(case, "bond_amount", "Bond_Amount"):
        for key in list(cleaned):
            if key in _BOND_KEYS or key.startswith("bond_amount_") or key.startswith("numeric_bond_amount_"):
                cleaned[key] = ""
    if not _backed(case, "premium", "premium_amount", "Premium"):
        for key in list(cleaned):
            if key in _PREMIUM_KEYS or key.startswith("premium_") or key.startswith("payment_amount") or key.startswith("payment_due_date"):
                cleaned[key] = ""
    if not _has_poa(case):
        for key in list(cleaned):
            if key in _POA_KEYS or key.startswith("poa_number_") or key.startswith("poa_"):
                cleaned[key] = ""
    return cleaned


def _case_fully_bound(case: Dict[str, Any]) -> bool:
    from dashboard.services.surety_registry import is_supported_surety

    required = (
        "bond_case_id", "match_id", "defendant_id", "indemnitor_id",
        "case_number", "poa_number", "booking_number",
    )
    if any(not _text(case.get(key)) for key in required):
        return False
    if _text(case.get("match_status")).lower() != "validated":
        return False
    if not is_supported_surety(_text(case.get("surety_id"))):
        return False
    if not _backed(case, "bond_amount", "Bond_Amount"):
        return False
    if not _backed(case, "premium", "premium_amount", "Premium"):
        return False
    return True


def _packet_voided(doc: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(doc, dict):
        return True
    if doc.get("voided") is True:
        return True
    return _text(doc.get("status")).lower() in _VOID_STATUSES


def _packet_finalized(doc: Optional[Dict[str, Any]]) -> bool:
    """Signed, completed, filed, or archived packets are not edited in place."""
    if not isinstance(doc, dict):
        return False
    status = _text(doc.get("status")).lower()
    remote = _text(doc.get("docuseal_status")).lower()
    return status in _FINAL_STATUSES or remote in _FINAL_STATUSES


def _safe_sign_url(url: str) -> str:
    """A signing URL only. A pay link is never returned."""
    text = _text(url)
    if not text.startswith("http"):
        return ""
    lowered = text.lower()
    if "swipesimple" in lowered or "/pay" in lowered:
        return ""
    return text


def _digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""


def self_serve_packet_id(case_id: str, role: str, person_id: str, prior_ids: List[str]) -> str:
    """One packet id per signer, plus a new id after a finalized packet.

    Both workers compute the same id. The packet row is inserted with that
    value as ``_id``, so the second insert conflicts on MongoDB's existing
    unique ``_id`` index. No new index is created.
    """
    basis = ",".join(sorted({item for item in prior_ids if item}))
    raw = f"shamrock-self-serve-packet-v1:{case_id}:{role}:{person_id}:{basis}"
    return "PKT-SS-" + uuid.uuid5(uuid.NAMESPACE_URL, raw).hex


def _signer_blocked(party: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(party, dict):
        return True
    if party.get("voided") is True or party.get("verified") is False:
        return True
    return _text(party.get("status")).lower() in _DEAD_SIGNER


def _role_submitter(packet: Dict[str, Any], role: str) -> Optional[Dict[str, Any]]:
    from dashboard.services.paperwork_signers import normalize_role

    want = normalize_role(role)
    for item in packet.get("docuseal_submitters") or []:
        if isinstance(item, dict) and normalize_role(item.get("role")) == want:
            return item
    return None


def _sign_url(packet: Dict[str, Any], role: str) -> str:
    party = _role_submitter(packet, role)
    if _packet_voided(packet) or _packet_finalized(packet) or _signer_blocked(party):
        return ""
    return _safe_sign_url((party or {}).get("sign_url") or packet.get("sign_url") or "")


def _packet_signer_bound(
    packet: Dict[str, Any],
    role: str,
    person_id: str,
    phone: str,
    email: str,
) -> bool:
    """True only when this packet is bound to this person, phone, and email."""
    party = _role_submitter(packet, role)
    if not isinstance(party, dict) or _signer_blocked(party):
        return False
    bound = False
    packet_person = _text(packet.get("person_id"))
    if packet_person:
        if packet_person != _text(person_id):
            return False
        bound = True
    party_phone = _digits(party.get("phone"))
    if party_phone:
        if party_phone != _digits(phone):
            return False
        bound = True
    party_email = _text(party.get("email")).lower()
    if party_email:
        if not email or party_email != _text(email).lower():
            return False
        bound = True
    return bound


async def _rows(name: str, query: Dict[str, Any]) -> list:
    cursor = _col(name).find(query)
    if hasattr(cursor, "to_list"):
        rows = await cursor.to_list(length=20)
        return [row for row in rows if isinstance(row, dict)]
    found = []
    async for row in cursor:
        if isinstance(row, dict):
            found.append(row)
    return found


async def _latest_pending(case_id: str, role: str, person_id: str) -> Dict[str, Any]:
    case = await _col("bond_cases").find_one({"$or": [
        {"bond_case_id": case_id},
        {"Bond_Case_ID": case_id},
    ]})
    if not isinstance(case, dict):
        return {}
    rows = case.get("pending_id_prefills")
    if not isinstance(rows, list):
        return {}
    chosen = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _text(row.get("role")) == role and _text(row.get("person_id")) == person_id:
            chosen = row
    if not chosen:
        return {}
    return {
        "case_id": case_id,
        "role": role,
        "person_id": person_id,
        "scan_id": _text(chosen.get("scan_id")),
        "confirm_id": _text(chosen.get("confirm_id")),
        "fields": dict(chosen.get("fields") or {}),
    }


async def get_pending_id_prefill(case_id: str, role: str, person_id: str) -> Dict[str, Any]:
    """Fallback read for the auto-packet builder.

    Returns the latest confirmed ID fields for this BondCase, role, and
    person. An empty dict means there is nothing to apply. The POA number
    is not part of the key.
    """
    case_id = _text(case_id)
    role = _text(role).lower()
    person_id = _text(person_id)
    if not case_id or role not in _ROLES or not person_id:
        return {}
    return await _latest_pending(case_id, role, person_id)


async def _push_pending(case_id: str, record: Dict[str, Any]) -> None:
    await _col("bond_cases").update_one(
        {"bond_case_id": case_id},
        {"$push": {"pending_id_prefills": record}},
    )


def _bond_data_for_prefill(case: Dict[str, Any], role: str, fields: Dict[str, str], email: str, phone: str) -> Dict[str, Any]:
    data = {
        "bond_case_id": case.get("bond_case_id") or "",
        "match_id": case.get("match_id") or "",
        "match_status": case.get("match_status") or "",
        "defendant_id": case.get("defendant_id") or "",
        "indemnitor_id": case.get("indemnitor_id") or "",
        "case_number": case.get("case_number") or "",
        "booking_number": case.get("booking_number") or "",
        "surety_id": _text(case.get("surety_id") or case.get("Surety_ID")),
        "in_person": True,
        "in_person_scan": True,
        "source": "self_serve",
    }
    if _has_poa(case):
        data["poa_number"] = case.get("poa_number") or case.get("POA_Number")
    if _backed(case, "bond_amount", "Bond_Amount"):
        data["bond_amount"] = case.get("bond_amount") if case.get("bond_amount") not in (None, "") else case.get("Bond_Amount")
    if _backed(case, "premium", "premium_amount", "Premium"):
        data["premium"] = case.get("premium") if case.get("premium") not in (None, "") else case.get("premium_amount")
    data.update(fields)
    if email:
        data[_prefix(role) + "email"] = email
    if phone:
        data[_prefix(role) + "phone"] = phone
    if role == "indemnitor":
        data["indemnitor"] = {
            "name": fields.get("indemnitor_name") or "",
            "email": email,
            "phone": phone,
            "dob": fields.get("indemnitor_dob") or "",
            "dl": fields.get("indemnitor_dl") or "",
            "dl_state": fields.get("indemnitor_dl_state") or "",
            "address": fields.get("indemnitor_address") or "",
            "city": fields.get("indemnitor_city") or "",
            "state": fields.get("indemnitor_state") or "",
            "zip": fields.get("indemnitor_zip") or "",
        }
    elif role == "coindemnitor":
        data["coindemnitor"] = {
            "name": fields.get("coindemnitor_name") or "",
            "email": email,
            "phone": phone,
        }
        data["coindemnitor_email"] = email
        data["coindemnitor_phone"] = phone
    else:
        data["defendant"] = {
            "name": fields.get("defendant_name") or "",
            "email": email,
            "phone": phone,
            "dob": fields.get("defendant_dob") or "",
            "dl": fields.get("defendant_dl") or "",
            "dl_state": fields.get("defendant_dl_state") or "",
            "sex": fields.get("defendant_sex") or "",
            "address": fields.get("defendant_address") or "",
            "city": fields.get("defendant_city") or "",
            "state": fields.get("defendant_state") or "",
            "zip": fields.get("defendant_zip") or "",
        }
        data["defendant_email"] = email
        data["defendant_phone"] = phone
    return data


def _overlay_role(values: Dict[str, Any], role: str, fields: Dict[str, str], email: str, phone: str) -> Dict[str, Any]:
    merged = dict(values)
    merged.update(fields)
    if email:
        merged[_prefix(role) + "email"] = email
    if phone:
        merged[_prefix(role) + "phone"] = phone
        if role == "indemnitor":
            merged["IndPhone"] = phone
        if role == "defendant":
            merged["DefPhone"] = phone
    if role != "defendant" and not _text(merged.get("defendant_name")):
        for key in ("defendant_name", "DefendantName", "DefName", "DefFirstName", "DefLastName"):
            if _text(merged.get(key)).lower().startswith("to be named"):
                merged[key] = ""
    return merged


async def _case_packets(case_id: str, key: str) -> List[Dict[str, Any]]:
    return await _rows("paperwork_packets", {"$or": [
        {"self_serve_packet_key": key},
        {"bond_case_id": case_id},
    ]})


def _open_packets(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [row for row in rows if not _packet_voided(row) and not _packet_finalized(row)]


async def _existing_packet(
    case_id: str,
    role: str,
    person_id: str,
    phone: str,
    email: str,
) -> Optional[Dict[str, Any]]:
    """The open packet for this exact signer, or None.

    An exact ``self_serve_packet_key`` wins. Any other row must match
    person id and, where the packet has them, phone and email. A role
    match alone is not enough. Several matches and no exact key means
    no packet, so the caller does not return a URL.
    """
    key = f"{case_id}|{role}|{person_id}"
    rows = _open_packets(await _case_packets(case_id, key))
    exact = [
        row for row in rows
        if _text(row.get("self_serve_packet_key")) == key and _sign_url(row, role)
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        exact.sort(key=lambda row: str(row.get("created_at") or ""))
        return exact[0]
    bound = [
        row for row in rows
        if _packet_signer_bound(row, role, person_id, phone, email) and _sign_url(row, role)
    ]
    if len(bound) == 1:
        return bound[0]
    return None


async def _apply_to_packet(
    packet: Dict[str, Any],
    role: str,
    person_id: str,
    fields: Dict[str, str],
    email: str,
    phone: str,
) -> str:
    from dashboard.services.docuseal_service import DocuSealService
    from dashboard.services.docuseal_signing_ux import IDENTITY_READONLY_FIELD_NAMES, submission_fields_from_values

    key = f"{packet.get('bond_case_id')}|{role}|{person_id}"
    exact_key = _text(packet.get("self_serve_packet_key")) == key
    party = _role_submitter(packet, role)
    if (
        _packet_voided(packet)
        or _packet_finalized(packet)
        or _signer_blocked(party)
        or not (exact_key or _packet_signer_bound(packet, role, person_id, phone, email))
    ):
        return ""
    values = dict(fields)
    if email:
        values[_prefix(role) + "email"] = email
    if phone:
        values[_prefix(role) + "phone"] = phone
    if role == "coindemnitor":
        for key, value in list(fields.items()):
            if key.startswith("coindemnitor_"):
                values["indemnitor_" + key[len("coindemnitor_"):]] = value
    submitter_id = (party or {}).get("id")
    if submitter_id:
        await DocuSealService().update_submitter(
            submitter_id,
            name=fields.get(_prefix(role) + "name") or None,
            values=values,
            fields=submission_fields_from_values(values, extra_readonly=IDENTITY_READONLY_FIELD_NAMES),
            send_email=False,
            send_sms=False,
        )
    existing = packet.get("client_fields") if isinstance(packet.get("client_fields"), dict) else {}
    await _col("paperwork_packets").update_one(
        {"packet_id": packet.get("packet_id")},
        {"$set": {
            "client_fields": {**existing, **fields},
            "client_fields_updated_at": _now(),
            "id_ocr_pushed_at": _now(),
            "self_serve_packet_key": packet.get("self_serve_packet_key") or f"{packet.get('bond_case_id')}|{role}|{person_id}",
        }},
    )
    return _sign_url(packet, role)


async def _identity_ok(case: Dict[str, Any], role: str, fields: Dict[str, str], packet_id: str) -> bool:
    if role == "defendant":
        return True
    from dashboard.services.identity_verification_service import require_verified_indemnitors

    person_id = case.get("co_indemnitor_id") if role == "coindemnitor" else case.get("indemnitor_id")
    party_role = "co_indemnitor" if role == "coindemnitor" else "indemnitor"
    name = fields.get(_prefix(role) + "name") or ""
    await require_verified_indemnitors(
        bond_data=case,
        parties=[{"role": party_role, "name": name, "indemnitor_id": _text(person_id)}],
        bond_case_id=_text(case.get("bond_case_id")),
        booking_number=_text(case.get("booking_number")),
        packet_id=packet_id,
        staff_test_case=False,
    )
    return True


async def _mark_packet_build_failed(packet_id: str) -> None:
    await _col("paperwork_packets").update_one(
        {"_id": packet_id},
        {"$set": {"status": "build_failed", "docuseal_started": True}},
    )


async def _wait_for_claimed_packet(packet_id: str, role: str) -> Dict[str, Any]:
    """Another worker owns this packet id. Do not create a second submission."""
    for _ in range(40):
        row = await _col("paperwork_packets").find_one({"_id": packet_id})
        if isinstance(row, dict):
            if row.get("status") == "build_failed":
                raise RuntimeError("packet_incomplete")
            url = _sign_url(row, role)
            if url:
                return {"packet_id": packet_id, "signing_link": url, "created": False}
        await asyncio.sleep(0.05)
    row = await _col("paperwork_packets").find_one({"_id": packet_id})
    if isinstance(row, dict) and row.get("docuseal_started") and not _sign_url(row, role):
        raise RuntimeError("packet_incomplete")
    raise RuntimeError("packet_in_progress")


async def build_self_serve_packet(
    *,
    case: Dict[str, Any],
    role: str,
    person_id: str,
    fields: Dict[str, str],
    email: str,
    phone: str,
    scan_id: str = "",
) -> Dict[str, Any]:
    """Build or reuse one packet. Raises when DocuSeal or the identity gate refuses."""
    from dashboard.services.docuseal_service import (
        ROLE_BONDSMAN,
        ROLE_CO_INDEMNITOR,
        ROLE_DEFENDANT,
        ROLE_INDEMNITOR,
        DocuSealService,
        resolve_template_id_for_surety,
    )
    from dashboard.services.docuseal_signing_ux import submission_fields_from_values

    if not _text(scan_id):
        raise RuntimeError("scan_required")
    case_id = _text(case.get("bond_case_id"))
    packet_key = f"{case_id}|{role}|{person_id}"
    return await _build_self_serve_packet_locked(
            case=case,
            role=role,
            person_id=person_id,
            fields=fields,
            email=email,
            phone=phone,
            scan_id=_text(scan_id),
            case_id=case_id,
            packet_key=packet_key,
            role_constants=(ROLE_BONDSMAN, ROLE_CO_INDEMNITOR, ROLE_DEFENDANT, ROLE_INDEMNITOR),
            service_cls=DocuSealService,
            resolve_template=resolve_template_id_for_surety,
            submission_fields=submission_fields_from_values,
        )


async def _build_self_serve_packet_locked(
    *,
    case: Dict[str, Any],
    role: str,
    person_id: str,
    fields: Dict[str, str],
    email: str,
    phone: str,
    scan_id: str,
    case_id: str,
    packet_key: str,
    role_constants: tuple,
    service_cls: Any,
    resolve_template: Any,
    submission_fields: Any,
) -> Dict[str, Any]:
    role_bondsman, role_co, role_defendant, role_indemnitor = role_constants
    existing = await _existing_packet(case_id, role, person_id, phone, email)
    if existing:
        url = await _apply_to_packet(existing, role, person_id, fields, email, phone)
        if not url:
            raise RuntimeError("existing_packet_not_signable")
        return {"packet_id": existing.get("packet_id") or "", "signing_link": url, "created": False}

    prior_final = [
        row for row in await _case_packets(case_id, packet_key)
        if _packet_finalized(row) and (
            _text(row.get("self_serve_packet_key")) == packet_key
            or _packet_signer_bound(row, role, person_id, phone, email)
        )
    ]
    packet_id = self_serve_packet_id(
        case_id,
        role,
        person_id,
        [str(row.get("packet_id") or "") for row in prior_final],
    )
    await _identity_ok(case, role, fields, packet_id)
    from dashboard.services.surety_registry import is_supported_surety

    bond_data = _bond_data_for_prefill(case, role, fields, email, phone)
    if not is_supported_surety(_text(bond_data.get("surety_id"))):
        raise RuntimeError("surety_unassigned")
    unvalidated = not _case_fully_bound(case)
    now = _now()
    shell = {
        "_id": packet_id,
        "packet_id": packet_id,
        "bond_case_id": case_id,
        "self_serve_packet_key": packet_key,
        "person_id": person_id,
        "role": role,
        "scan_id": scan_id,
        "status": "building",
        "docuseal_started": False,
        "sign_url": "",
        "voided": False,
        "source": "self_serve",
        "surety_id": bond_data.get("surety_id") or "",
        "self_serve_unvalidated_draft": unvalidated,
        "created_at": now,
    }
    await insert_new(_col("paperwork_packets"), shell)
    claim = await _col("paperwork_packets").update_one(
        {"_id": packet_id, "docuseal_started": {"$ne": True}},
        {"$set": {"docuseal_started": True, "claimed_at": now}},
    )
    if int(getattr(claim, "matched_count", 0) or 0) != 1:
        return await _wait_for_claimed_packet(packet_id, role)
    svc = service_cls()
    try:
        if not unvalidated:
            template_id = resolve_template(bond_data.get("surety_id"))
            if not template_id:
                raise RuntimeError("template_unavailable")
            result = await svc.create_submission_for_packet(
                template_id=template_id,
                packet_id=packet_id,
                bond_data=bond_data,
                send_email=False,
                include_defendant=role == "defendant" or bool(_text(case.get("defendant_name"))),
                staff_test_case=False,
            )
        else:
            template_id = resolve_template(bond_data.get("surety_id"))
            if not template_id:
                raise RuntimeError("template_unavailable")
            prefill = svc.prefill_values_from_bond(bond_data, template_id=template_id)
            values = _overlay_role(strip_unbacked_amounts(prefill, case), role, fields, email, phone)
            role_name = {"defendant": role_defendant, "coindemnitor": role_co}.get(role, role_indemnitor)
            submitter_values = dict(values)
            if role == "coindemnitor":
                for key, value in list(fields.items()):
                    if key.startswith("coindemnitor_"):
                        submitter_values["indemnitor_" + key[len("coindemnitor_"):]] = value
            fields_payload = submission_fields(submitter_values)
            submitters = [
                svc.build_submitter(
                    role=role_bondsman,
                    email=os.getenv("BOND_AGENT_EMAIL", "admin@shamrockbailbonds.biz"),
                    name="Brendan O'Neal",
                    external_id=f"{packet_id}:bondsman",
                    values=submitter_values,
                    send_email=False,
                    fields=fields_payload,
                    metadata={"packet_id": packet_id, "party_role": "bondsman"},
                ),
                svc.build_submitter(
                    role=role_name,
                    email=email,
                    name=fields.get(_prefix(role) + "name") or "",
                    phone=phone,
                    external_id=f"{packet_id}:{role}",
                    values=submitter_values,
                    send_email=False,
                    fields=fields_payload,
                    metadata={"packet_id": packet_id, "party_role": role},
                ),
            ]
            raw = await svc.create_submission(
                template_id=template_id,
                submitters=submitters,
                send_email=False,
                send_sms=False,
            )
            result = svc.normalize_create_response(raw)
    except Exception:
        await _mark_packet_build_failed(packet_id)
        raise
    submitters = list(result.get("submitters") or [])
    from dashboard.services.paperwork_signers import normalize_role

    want = normalize_role(role)
    signer = next((item for item in submitters if normalize_role((item or {}).get("role")) == want), None)
    if _signer_blocked(signer):
        await _mark_packet_build_failed(packet_id)
        raise RuntimeError("signer_not_verified")
    url = _safe_sign_url((signer or {}).get("sign_url") or "")
    if not url:
        await _mark_packet_build_failed(packet_id)
        raise RuntimeError("signing_link_missing")
    packet_doc = {
        "packet_id": packet_id,
        "bond_case_id": case_id,
        "self_serve_packet_key": packet_key,
        "person_id": person_id,
        "role": role,
        "scan_id": scan_id,
        "status": "pending_owner_review" if unvalidated else "pending_signature",
        "self_serve_unvalidated_draft": unvalidated,
        "owner_review": "pending_owner_review" if unvalidated else "",
        "voided": False,
        "source": "self_serve",
        "surety_id": bond_data.get("surety_id") or "",
        "poa_number": bond_data.get("poa_number") or "",
        "client_fields": dict(fields),
        "docuseal_submitters": submitters,
        "sign_url": url,
        "send_email": False,
        "send_sms": False,
        "created_at": result.get("created_at") or _now(),
    }
    await _col("paperwork_packets").update_one({"_id": packet_id}, {"$set": packet_doc})
    if unvalidated:
        audit_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"shamrock-self-serve-sign-audit-v1:{packet_id}"))
        await insert_new(_col("audit_events"), {
            "_id": audit_id,
            "event_id": audit_id,
            "event_type": "self_serve_draft_signing_url",
            "action": "self_serve_draft_signing_url",
            "entity_type": "paperwork_packet",
            "entity_id": packet_id,
            "packet_id": packet_id,
            "bond_case_id": case_id,
            "actor": "auto",
            "agent_name": "paperwork_self_serve",
            "role": role,
            "person_id": person_id,
            "scan_id": scan_id,
            "reason": "signing_url_issued_for_unvalidated_draft",
            "created_at": packet_doc["created_at"],
            "old_state": {},
            "new_state": {
                "status": "pending_owner_review",
                "self_serve_unvalidated_draft": True,
                "signing_url_issued": True,
            },
        })
    if prior_final:
        repl_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"shamrock-self-serve-repl-audit-v1:{packet_id}"))
        await insert_new(_col("audit_events"), {
            "_id": repl_id,
            "event_id": repl_id,
            "event_type": "self_serve_packet_replacement",
            "action": "self_serve_packet_replacement",
            "entity_type": "paperwork_packet",
            "entity_id": packet_id,
            "packet_id": packet_id,
            "bond_case_id": case_id,
            "actor": "auto",
            "agent_name": "paperwork_self_serve",
            "role": role,
            "person_id": person_id,
            "reason": "prior_packet_finalized",
            "created_at": packet_doc["created_at"],
            "old_state": {"packet_id": [row.get("packet_id") for row in prior_final]},
            "new_state": {"packet_id": packet_id, "status": packet_doc["status"]},
        })
    return {"packet_id": packet_id, "signing_link": url, "created": True}


def _scan_on_session(session: Dict[str, Any], scan_id: str) -> Optional[Dict[str, Any]]:
    rows = session.get("id_scan_records")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and _text(row.get("scan_id")) == scan_id:
            return row
    return None


def _snapshot(session: Dict[str, Any], scan_id: str) -> Dict[str, Any]:
    rows = session.get("id_ocr_snapshots")
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, dict) and _text(row.get("scan_id")) == scan_id:
            extracted = row.get("extracted")
            return dict(extracted) if isinstance(extracted, dict) else {}
    return {}


async def accept_confirmed_id(
    *,
    session: Dict[str, Any],
    scan_id: str,
    role: str,
    edits: Optional[Dict[str, Any]],
    email: str,
    phone: str,
) -> Dict[str, Any]:
    """Confirm one scan and open a packet, or hold the fields with no URL."""
    role = _text(role).lower()
    scan = _scan_on_session(session, _text(scan_id))
    if role not in _ROLES or not scan or _text(scan.get("role")) != role:
        return {"ok": False, "error": "scan_required", "signing_link": "", "message": _HOLD_MESSAGE}
    if scan.get("success") is not True:
        return {"ok": False, "error": "scan_required", "signing_link": "", "message": _HOLD_MESSAGE}
    ocr = _snapshot(session, _text(scan_id))
    if not ocr:
        ocr = {
            "full_name": _text((scan.get("extracted") or {}).get("full_name")),
            "dl_state": _text((scan.get("extracted") or {}).get("dl_state")),
        }
    fields = merge_confirmed_fields(role_fields_from_scan(ocr, role), edits, role)
    if email:
        fields[_prefix(role) + "email"] = email
    if phone:
        fields[_prefix(role) + "phone"] = phone
    confirm_id = uuid.uuid4().hex
    now = _now()
    confirmed = {
        "confirm_id": confirm_id,
        "scan_id": _text(scan_id),
        "role": role,
        "fields": dict(fields),
        "ocr_fields": {key: ocr.get(key) for key in _OCR_KEYS if _text(ocr.get(key))},
        "recorded_at": now,
    }
    token = _text(session.get("session_token"))
    await _col("portal_pins").update_one(
        {"session_token": token},
        {"$push": {"id_confirmed_records": confirmed}},
    )
    from dashboard.services.staff_chain_service import ensure_self_serve_bondcase

    ensured = await ensure_self_serve_bondcase(
        phone=phone,
        name=fields.get(_prefix(role) + "name") or _text(ocr.get("full_name")),
        dob=fields.get(_prefix(role) + "dob") or _text(ocr.get("dob")),
        role=role,
        email=email,
        identity=fields,
        scan_blob={
            "scan_id": _text(scan_id),
            "full_name": _text(ocr.get("full_name") or fields.get(_prefix(role) + "name")),
            "dl_state": _text(ocr.get("dl_state") or ocr.get("issuing_state") or fields.get(_prefix(role) + "dl_state")),
        },
        booking_number=_text(session.get("booking_number")),
        actor="auto",
    )
    if not ensured.get("success"):
        logger.info("[self-serve] case not opened error=%s", ensured.get("error"))
        return {
            "ok": True,
            "saved": True,
            "fallback": True,
            "signing_link": "",
            "message": _HOLD_MESSAGE,
            "error": ensured.get("error") or "case_unavailable",
            "role": role,
        }
    case = ensured.get("case") if isinstance(ensured.get("case"), dict) else {}
    case_id = _text(ensured.get("bond_case_id"))
    person_id = _text(ensured.get("person_id"))
    pending = {
        "case_id": case_id,
        "role": role,
        "person_id": person_id,
        "scan_id": _text(scan_id),
        "confirm_id": confirm_id,
        "fields": dict(fields),
        "recorded_at": now,
    }
    await _push_pending(case_id, pending)
    try:
        built = await build_self_serve_packet(
            case=case,
            role=role,
            person_id=person_id,
            fields=fields,
            email=email,
            phone=phone,
            scan_id=_text(scan_id),
        )
    except Exception as exc:
        logger.warning(
            "[self-serve] packet build failed case=%s role=%s error_type=%s",
            case_id,
            role,
            type(exc).__name__,
        )
        return {
            "ok": True,
            "saved": True,
            "fallback": True,
            "signing_link": "",
            "bond_case_id": case_id,
            "person_id": person_id,
            "role": role,
            "created_case": bool(ensured.get("created")),
            "reused_case": bool(ensured.get("reused")),
            "message": _HOLD_MESSAGE,
        }
    return {
        "ok": True,
        "saved": True,
        "fallback": False,
        "signing_link": built.get("signing_link") or "",
        "packet_id": built.get("packet_id") or "",
        "bond_case_id": case_id,
        "person_id": person_id,
        "role": role,
        "has_packet": True,
        "created_case": bool(ensured.get("created")),
        "reused_case": bool(ensured.get("reused")),
        "message": _READY_MESSAGE,
    }


def ocr_snapshot(extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Full server OCR kept beside the limited id-upload row."""
    return {key: extracted.get(key) for key in _OCR_KEYS if _text(extracted.get(key))}
