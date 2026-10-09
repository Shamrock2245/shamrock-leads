"""Fail-closed indemnitor identity for Write Bond (OSI and Palmetto).

Verified means one of:

(a) A passed US state ID scan on the case whose first and last name match
    the indemnitor or co-indemnitor. The pass/fail flag is the scanner
    ``success`` boolean (``passed`` and ``status`` / ``scan_status`` are
    accepted when a caller stored those instead). A bare extract with no
    flag is a pass only when a successful read was what got stored
    (``id_extracted``, ``id_ocr``, ``id_ocr_fields``, ``id_scan``,
    ``license_scan``, ``dl_scan``). ``success: false`` is a failed scan.
    An empty object is no scan. A scan with no US issuing state, DC, or
    territory is not a pass.

(b) A staff in-person attestation on ``audit_events`` (``action``
    ``staff_id_attestation``) for this case and this person. The row stores
    the session user, ID type, US issuing state, the last 4 of the ID
    number, and a UTC timestamp. The full ID number is never stored.

Name rule: ``normalize_person_name`` (case, whitespace, apostrophes).
``Last, First`` is reordered to first-last. Suffixes Jr/Sr/II/III/IV/V are
ignored. Middle names and initials are ignored. First and last must both
match on letters only (``name_letters_key``). O'Neal does not match O'Neill.

Self-indemnitor: a passed defendant-role scan verifies the indemnitor only
when the packet or case is explicitly marked ``self_indemnitor``. The mark
is the boolean set by ``apply_self_indemnitor`` on the packet context, and
the same field saved on ``paperwork_packets``, ``bond_cases``, and
``active_bonds``. Accepted values are true, 1, and the strings true / yes /
y / 1. The scanned first and last name must still match the indemnitor.
The gate does not infer the mark from matching names, from relationship
"Self", or from ``self_indemnitor_authorized_at`` alone. Co-indemnitors are
not covered. Every other packet ignores defendant-role scans.

Where scans live today:

- ``intake_queue.id_extracted`` plus ``id_scanned_at`` (intake)
- ``portal_pins.id_extracted`` plus ``id_scanned_at`` (kiosk / portal)
- ``paperwork_packets.id_ocr`` and ``id_ocr_fields`` (Shannon, desktop, kiosk confirm)
- ``indemnitors.id_scan`` (indemnitor form, including mobile / tablet)
- ``active_bonds`` / ``prospective_bonds`` / ``bond_cases`` nested
  ``indemnitor`` / ``indemnitors[].id_scan`` or ``id_extracted``

Synthetic staff test packets skip this gate. They are ``staff_test_case``
with a ``TEST-`` booking and a ``PKT-TEST-`` packet id, so they never use a
live power or a real signer. ``validate_docuseal_packet_binding`` still
runs. A real booking is never skipped.

Open BondCases (count script) use the kanban statuses that are not terminal:
``active``, ``monitoring``, ``alert``, ``reinstated``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from dashboard.services.docuseal_service import DocuSealPacketValidationError
from dashboard.services.id_ocr_service import US_STATES, name_letters_key, normalize_person_name
from dashboard.services.staff_test_case import is_test_booking, is_test_packet_id

logger = logging.getLogger(__name__)

IDENTITY_UNVERIFIED = "indemnitor_identity_unverified"
ATTESTATION_ACTION = "staff_id_attestation"
ATTESTATION_NUMBER_REJECTED = "attestation_id_number_rejected"
ATTESTATION_INVALID = "attestation_invalid"

OPEN_BOND_CASE_STATUSES = frozenset({"active", "monitoring", "alert", "reinstated"})

# 50 states plus DC and US territories that issue photo IDs.
_US_JURISDICTIONS = set(US_STATES) | {"DC", "PR", "GU", "VI", "AS", "MP"}
_JURISDICTION_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
    "puerto rico": "PR", "guam": "GU", "virgin islands": "VI",
    "american samoa": "AS", "northern mariana islands": "MP",
    "usa": "US", "u.s.a.": "US", "united states": "US", "united states of america": "US",
}
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
_FAIL_STATUS = frozenset({"failed", "fail", "error", "unreadable", "rejected"})
_SCAN_FIELDS = (
    "id_extracted",
    "id_scan",
    "id_ocr",
    "id_ocr_fields",
    "license_scan",
    "dl_scan",
)
_SCAN_COLLECTIONS = (
    "intake_queue",
    "portal_pins",
    "paperwork_packets",
    "indemnitors",
    "active_bonds",
    "prospective_bonds",
    "bond_cases",
)
_FULL_ID_KEYS = frozenset({
    "id_number",
    "dl_number",
    "license_number",
    "document_number",
    "full_id",
    "id_full",
    "identification_number",
    "drivers_license_number",
    "driver_license_number",
    "passport_number",
})
_DIGIT_RUN = re.compile(r"\d{5,}")
_LAST4 = re.compile(r"^\d{4}$")
_ID_TYPE = re.compile(r"^[a-z][a-z0-9 _-]{0,39}$")

# A blocked case lands in exactly one bucket, most specific first.
_REASON_PRIORITY = ("lookup_failed", "scan_failed", "name_mismatch", "no_scan")


class IndemnitorIdentityError(DocuSealPacketValidationError):
    """Write Bond refused because an indemnitor is not verified."""

    def __init__(self, message: str, issues: Sequence[Mapping[str, str]]):
        self.code = IDENTITY_UNVERIFIED
        self.issues = [dict(item) for item in issues]
        super().__init__(message)


class IdentityLookupError(Exception):
    """Mongo (or the collection handle) failed. Callers fail closed."""

    def __init__(self, error_type: str):
        self.error_type = error_type
        super().__init__(error_type)


def synthetic_staff_test_exempt(
    *,
    staff_test_case: bool,
    booking_number: Any,
    packet_id: Any,
) -> bool:
    """True only for a synthetic TEST- / PKT-TEST- staff packet.

    Those packets never reserve a live power and never contact a real signer.
    The binding gate still runs. A real booking is not exempt.
    """
    if not staff_test_case:
        return False
    if not is_test_booking(booking_number):
        return False
    if not is_test_packet_id(packet_id):
        return False
    return True


def _tokens(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    if "," in raw:
        last, _, rest = raw.partition(",")
        raw = f"{rest} {last}"
    tokens: list[str] = []
    for tok in normalize_person_name(raw).split():
        key = name_letters_key(tok)
        if not key or key in _SUFFIXES:
            continue
        tokens.append(tok)
    return tokens


def names_match(left: Any, right: Any) -> bool:
    """First and last name match. Middle names, initials, and suffixes do not."""
    a = _tokens(left)
    b = _tokens(right)
    if len(a) < 2 or len(b) < 2:
        return False
    return (
        name_letters_key(a[0]) == name_letters_key(b[0])
        and name_letters_key(a[-1]) == name_letters_key(b[-1])
    )


def _text(doc: Mapping[str, Any] | None, *keys: str) -> str:
    if not isinstance(doc, Mapping):
        return ""
    for key in keys:
        value = str(doc.get(key) or "").strip()
        if value:
            return value
    return ""


def party_display_name(party: Mapping[str, Any] | None) -> str:
    if not isinstance(party, Mapping):
        return ""
    full = _text(party, "name", "full_name", "fullName", "indemnitor_name")
    if full:
        return full
    return " ".join(
        part for part in (
            _text(party, "first_name", "firstName"),
            _text(party, "last_name", "lastName"),
        ) if part
    )


def packet_parties(
    bond_data: Mapping[str, Any] | None = None,
    indemnitors: Sequence[Any] | None = None,
) -> list[dict[str, str]]:
    """Indemnitor and co-indemnitor rows on the packet. The defendant is not included."""
    data = bond_data if isinstance(bond_data, Mapping) else {}
    source: Sequence[Any] | None = indemnitors
    if source is None and isinstance(data.get("indemnitors"), list):
        source = data.get("indemnitors")
    rows: list[dict[str, str]] = []
    if source:
        for index, item in enumerate(source):
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role") or item.get("party_role") or "").strip().lower()
            if role in {"defendant"}:
                continue
            if not role:
                role = "indemnitor" if index == 0 else "co_indemnitor"
            if role in {"coindemnitor", "co-indemnitor", "co_indemnitor"}:
                role = "co_indemnitor"
            elif role != "co_indemnitor":
                role = "indemnitor" if index == 0 else "co_indemnitor"
            rows.append({
                "role": role,
                "name": party_display_name(item),
                "indemnitor_id": _text(item, "indemnitor_id", "Indemnitor_ID", "id"),
            })
    if not rows:
        ind = data.get("indemnitor") if isinstance(data.get("indemnitor"), Mapping) else {}
        rows.append({
            "role": "indemnitor",
            "name": party_display_name(ind) or _text(data, "indemnitor_name"),
            "indemnitor_id": _text(data, "indemnitor_id", "Indemnitor_ID"),
        })
    primary_id = _text(data, "indemnitor_id", "Indemnitor_ID")
    if primary_id and rows and not rows[0].get("indemnitor_id") and rows[0]["role"] == "indemnitor":
        rows[0]["indemnitor_id"] = primary_id
    co = data.get("coindemnitor") or data.get("co_indemnitor")
    if isinstance(co, Mapping):
        co_name = party_display_name(co)
        if co_name and not any(names_match(co_name, row["name"]) for row in rows if row.get("name")):
            rows.append({
                "role": "co_indemnitor",
                "name": co_name,
                "indemnitor_id": _text(co, "indemnitor_id", "Indemnitor_ID", "id"),
            })
    return rows


def _jurisdiction(blob: Mapping[str, Any] | None) -> str:
    if not isinstance(blob, Mapping):
        return ""
    raw = _text(
        blob,
        "dl_state",
        "dlState",
        "issuing_state",
        "issuing_jurisdiction",
        "id_state",
        "state",
        "issuing_country",
        "country",
    )
    if not raw:
        return ""
    token = raw.strip().upper()
    if token in _US_JURISDICTIONS or token == "US":
        return token
    named = _JURISDICTION_NAMES.get(raw.strip().lower())
    return named or ""


def _scan_name(blob: Mapping[str, Any], role: str = "") -> str:
    for key in ("full_name", "fullName", "name"):
        if _text(blob, key):
            return _text(blob, key)
    lowered = role.lower()
    if "defend" in lowered and _text(blob, "defendant_name"):
        return _text(blob, "defendant_name")
    for key in ("indemnitor_name", "coindemnitor_name", "co_indemnitor_name"):
        if _text(blob, key):
            return _text(blob, key)
    return " ".join(
        part for part in (
            _text(blob, "first_name", "firstName"),
            _text(blob, "last_name", "lastName"),
        ) if part
    )


def _meaningful(blob: Mapping[str, Any]) -> bool:
    for value in blob.values():
        if isinstance(value, Mapping) and _meaningful(value):
            return True
        if value not in (None, "", [], {}):
            return True
    return False


def _outcome(blob: Mapping[str, Any]) -> str:
    """Return passed, failed, or absent. Does not compare names."""
    if not _meaningful(blob):
        return "absent"
    if blob.get("success") is False or blob.get("passed") is False:
        return "failed"
    status = str(blob.get("status") or blob.get("scan_status") or "").strip().lower()
    if status in _FAIL_STATUS:
        return "failed"
    inner = blob.get("extracted") if isinstance(blob.get("extracted"), Mapping) else blob
    role = _text(blob, "role", "party_role", "id_ocr_role")
    name = _scan_name(inner, role) or _scan_name(blob, role)
    jurisdiction = _jurisdiction(inner) or _jurisdiction(blob)
    explicit = blob.get("success") is True or blob.get("passed") is True
    bare = (
        "success" not in blob
        and "passed" not in blob
        and "status" not in blob
        and "scan_status" not in blob
    )
    if not explicit and not bare:
        return "absent"
    if len(_tokens(name)) < 2 or not jurisdiction:
        return "failed"
    return "passed"


def _link_ids(doc: Mapping[str, Any]) -> dict[str, str]:
    return {
        "bond_case_id": _text(doc, "bond_case_id", "Bond_Case_ID"),
        "booking_number": _text(doc, "booking_number", "Booking_Number", "defendant_booking_number"),
        "packet_id": _text(doc, "packet_id", "Packet_ID"),
        "indemnitor_id": _text(doc, "indemnitor_id", "Indemnitor_ID"),
    }


def _normalize_scan(blob: Mapping[str, Any], parent: Mapping[str, Any], role: str = "") -> dict[str, str] | None:
    outcome = _outcome(blob)
    if outcome == "absent":
        return None
    inner = blob.get("extracted") if isinstance(blob.get("extracted"), Mapping) else blob
    scan_role = role or _text(blob, "role", "party_role", "id_ocr_role") or _text(parent, "id_ocr_role", "role")
    name = _scan_name(inner, scan_role) or _scan_name(blob, scan_role)
    links = _link_ids(parent)
    nested_id = _text(blob, "indemnitor_id", "Indemnitor_ID")
    if nested_id:
        links["indemnitor_id"] = nested_id
    return {
        "name": name,
        "passed": "1" if outcome == "passed" else "0",
        "role": scan_role.lower(),
        **links,
    }


def scans_from_document(doc: Mapping[str, Any]) -> list[dict[str, str]]:
    """Pull normalized scans off one stored document. ID numbers are dropped."""
    if not isinstance(doc, Mapping):
        return []
    found: list[dict[str, str]] = []
    role = _text(doc, "id_ocr_role", "role", "party_role")
    for field in _SCAN_FIELDS:
        blob = doc.get(field)
        if isinstance(blob, Mapping):
            row = _normalize_scan(blob, doc, role)
            if row:
                found.append(row)
    for key in ("indemnitor", "coindemnitor", "co_indemnitor"):
        nested = doc.get(key)
        if isinstance(nested, Mapping):
            found.extend(scans_from_document({**_link_ids(doc), **nested}))
    people = doc.get("indemnitors")
    if isinstance(people, list):
        for person in people:
            if isinstance(person, Mapping):
                found.extend(scans_from_document({**_link_ids(doc), **person}))
    defendant = doc.get("defendant")
    if isinstance(defendant, Mapping):
        nested = {**_link_ids(doc), **{key: value for key, value in defendant.items() if key != "defendant"}}
        if not _text(nested, "id_ocr_role", "role", "party_role"):
            nested["role"] = "defendant"
        found.extend(scans_from_document(nested))
    return found


def _case_linked(row: Mapping[str, str], *, bond_case_id: str, booking_number: str, packet_id: str, indemnitor_id: str) -> bool:
    if bond_case_id and row.get("bond_case_id") == bond_case_id:
        return True
    if booking_number and row.get("booking_number") == booking_number:
        return True
    if packet_id and row.get("packet_id") == packet_id:
        return True
    if indemnitor_id and row.get("indemnitor_id") == indemnitor_id:
        return True
    return False


def _is_defendant_scan(row: Mapping[str, str]) -> bool:
    return "defend" in str(row.get("role") or "")


def explicit_self_indemnitor(doc: Mapping[str, Any] | None) -> bool:
    """True only when ``self_indemnitor`` is set on this packet or case.

    The field is the packet-context flag from ``apply_self_indemnitor``,
    stored on ``paperwork_packets.self_indemnitor`` and, when present, on
    ``bond_cases`` / ``active_bonds``. Matching names and relationship
    "Self" are not a mark.
    """
    if not isinstance(doc, Mapping):
        return False
    value = doc.get("self_indemnitor")
    if value is True or value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in {"true", "yes", "y", "1"}:
        return True
    return False


def attestation_matches_party(
    row: Mapping[str, Any],
    party: Mapping[str, str],
    *,
    bond_case_id: str,
    booking_number: str,
    packet_id: str,
) -> bool:
    if not isinstance(row, Mapping):
        return False
    action = str(row.get("action") or row.get("event_type") or "")
    if action and action != ATTESTATION_ACTION:
        return False
    details = row.get("details") if isinstance(row.get("details"), Mapping) else row
    last4 = str(details.get("id_last4") or "")
    if not _LAST4.match(last4):
        return False
    if not _text(details, "id_type") or not _jurisdiction(details):
        return False
    actor = str(row.get("actor") or details.get("staff_user") or "").strip()
    if not actor:
        return False
    if not (row.get("timestamp") or details.get("timestamp")):
        return False
    if not names_match(_text(details, "indemnitor_name", "name"), party.get("name")):
        return False
    linked = {
        "bond_case_id": _text(details, "bond_case_id", "Bond_Case_ID") or _text(row, "bond_case_id"),
        "booking_number": _text(details, "booking_number", "Booking_Number") or _text(row, "booking_number"),
        "packet_id": _text(details, "packet_id") or _text(row, "packet_id"),
        "indemnitor_id": _text(details, "indemnitor_id", "Indemnitor_ID") or _text(row, "entity_id"),
    }
    return _case_linked(
        linked,
        bond_case_id=bond_case_id,
        booking_number=booking_number,
        packet_id=packet_id,
        indemnitor_id=str(party.get("indemnitor_id") or ""),
    )


def _role_family(role: str) -> str:
    text = str(role or "").lower()
    if "co" in text and "indemn" in text:
        return "co_indemnitor"
    if "indemn" in text:
        return "indemnitor"
    return ""


def _tied_to_party(row: Mapping[str, str], party: Mapping[str, str], *, sole: bool) -> bool:
    """A scan is this person's when the id or role says so.

    A case-level scan with no id and no role is tied only when this packet
    has one indemnitor. That is the passed-scan-for-a-different-name case.
    Another person's scan on a multi-party packet is not a name mismatch.
    """
    indemnitor_id = str(party.get("indemnitor_id") or "")
    if indemnitor_id and row.get("indemnitor_id") == indemnitor_id:
        return True
    scan_role = _role_family(str(row.get("role") or ""))
    party_role = _role_family(str(party.get("role") or ""))
    if scan_role and party_role and scan_role == party_role:
        return True
    if sole and not row.get("indemnitor_id") and not scan_role:
        return True
    return False


def evaluate_parties(
    parties: Sequence[Mapping[str, str]],
    scans: Sequence[Mapping[str, str]],
    attestations: Sequence[Mapping[str, Any]],
    *,
    bond_case_id: str = "",
    booking_number: str = "",
    packet_id: str = "",
    self_indemnitor: bool = False,
) -> dict[str, Any]:
    """Pure decision. ``ok`` is true only when every party is verified.

    ``self_indemnitor`` must already be the explicit flag. A defendant-role
    scan then verifies only the indemnitor, and only when the name matches.
    """
    issues: list[dict[str, str]] = []
    methods: list[str] = []
    if not parties:
        issues.append({"role": "indemnitor", "name": "", "reason": "no_scan"})
    sole = len(list(parties)) == 1
    for party in parties:
        role = str(party.get("role") or "indemnitor")
        name = str(party.get("name") or "")
        indemnitor_id = str(party.get("indemnitor_id") or "")
        linked = [
            row for row in scans
            if not _is_defendant_scan(row)
            and _case_linked(
                row,
                bond_case_id=bond_case_id,
                booking_number=booking_number,
                packet_id=packet_id,
                indemnitor_id=indemnitor_id,
            )
        ]
        defendant_rows: list[Mapping[str, str]] = []
        if self_indemnitor and role == "indemnitor":
            defendant_rows = [
                row for row in scans
                if _is_defendant_scan(row)
                and _case_linked(
                    row,
                    bond_case_id=bond_case_id,
                    booking_number=booking_number,
                    packet_id=packet_id,
                    indemnitor_id=indemnitor_id,
                )
            ]
        considered = [*linked, *defendant_rows]
        if any(str(row.get("passed")) == "1" and names_match(name, row.get("name")) for row in considered):
            methods.append("scan")
            continue
        attested = any(
            attestation_matches_party(
                row,
                party,
                bond_case_id=bond_case_id,
                booking_number=booking_number,
                packet_id=packet_id,
            )
            for row in attestations
        )
        if attested:
            methods.append("attestation")
            continue
        tied = [row for row in linked if _tied_to_party(row, party, sole=sole)]
        tied.extend(defendant_rows)
        if any(str(row.get("passed")) == "1" for row in tied):
            reason = "name_mismatch"
        elif any(str(row.get("passed")) == "0" for row in tied):
            reason = "scan_failed"
        else:
            reason = "no_scan"
        issues.append({"role": role, "name": name, "reason": reason})
    return {"ok": not issues, "issues": issues, "methods": methods}


def _role_label(role: str) -> str:
    if role == "co_indemnitor":
        return "Co-indemnitor"
    return "Indemnitor"


def _reason_text(reason: str) -> str:
    if reason == "scan_failed":
        return "scan failed"
    if reason == "name_mismatch":
        return "name mismatch"
    if reason == "lookup_failed":
        return "identity lookup failed"
    return "no scan and no attestation"


def message_for_issues(issues: Sequence[Mapping[str, str]]) -> str:
    parts = []
    for issue in issues:
        name = str(issue.get("name") or "").strip() or "(name missing)"
        parts.append(f"{_role_label(str(issue.get('role') or 'indemnitor'))} {name}: {_reason_text(str(issue.get('reason') or 'no_scan'))}.")
    return " ".join(parts) or "Indemnitor (name missing): no scan and no attestation."


def _link_filter(
    *,
    bond_case_id: str,
    booking_number: str,
    packet_id: str,
    indemnitor_ids: Iterable[str],
) -> dict[str, Any] | None:
    ors: list[dict[str, str]] = []
    if bond_case_id:
        ors.append({"bond_case_id": bond_case_id})
        ors.append({"Bond_Case_ID": bond_case_id})
    if booking_number:
        ors.append({"booking_number": booking_number})
        ors.append({"Booking_Number": booking_number})
        ors.append({"defendant_booking_number": booking_number})
    if packet_id:
        ors.append({"packet_id": packet_id})
    for indemnitor_id in indemnitor_ids:
        if not indemnitor_id:
            continue
        ors.append({"indemnitor_id": indemnitor_id})
        ors.append({"Indemnitor_ID": indemnitor_id})
    if not ors:
        return None
    return {"$or": ors}


async def _collect(cursor: Any) -> list[dict]:
    if cursor is None:
        return []
    if hasattr(cursor, "to_list"):
        rows = await cursor.to_list(length=500)
        return [row for row in rows if isinstance(row, Mapping)]
    if hasattr(cursor, "__await__"):
        cursor = await cursor
    return [row for row in list(cursor or []) if isinstance(row, Mapping)]


async def _find_docs(name: str, filt: dict[str, Any]) -> list[dict]:
    from dashboard.extensions import get_collection

    try:
        collection = get_collection(name)
    except KeyError:
        return []
    try:
        return await _collect(collection.find(filt, {"_id": 0}))
    except KeyError:
        return []
    except Exception as exc:
        logger.warning(
            "identity lookup failed closed collection=%s error_type=%s",
            name,
            type(exc).__name__,
        )
        raise IdentityLookupError(type(exc).__name__) from exc


async def gather_identity_evidence(
    *,
    bond_case_id: str = "",
    booking_number: str = "",
    packet_id: str = "",
    indemnitor_ids: Sequence[str] = (),
) -> dict[str, list]:
    filt = _link_filter(
        bond_case_id=bond_case_id,
        booking_number=booking_number,
        packet_id=packet_id,
        indemnitor_ids=indemnitor_ids,
    )
    if filt is None:
        return {"scans": [], "attestations": []}
    scans: list[dict[str, str]] = []
    self_indemnitor = False
    for name in _SCAN_COLLECTIONS:
        for doc in await _find_docs(name, filt):
            if explicit_self_indemnitor(doc):
                self_indemnitor = True
            scans.extend(scans_from_document(doc))
    audit_filt = {"$and": [{"action": ATTESTATION_ACTION}, filt]}
    try:
        attestations = await _find_docs("audit_events", audit_filt)
    except IdentityLookupError:
        raise
    return {"scans": scans, "attestations": attestations, "self_indemnitor": self_indemnitor}


async def require_verified_indemnitors(
    *,
    bond_data: Mapping[str, Any] | None = None,
    indemnitors: Sequence[Any] | None = None,
    parties: Sequence[Mapping[str, str]] | None = None,
    bond_case_id: str = "",
    booking_number: str = "",
    packet_id: str = "",
    staff_test_case: bool = False,
) -> dict[str, Any]:
    """Raise IndemnitorIdentityError before a packet may be built or sent.

    Returns the decision when every indemnitor and co-indemnitor is verified.
    Lookup errors fail closed with the same machine code.
    """
    if synthetic_staff_test_exempt(
        staff_test_case=staff_test_case,
        booking_number=booking_number or _text(bond_data, "booking_number", "Booking_Number"),
        packet_id=packet_id,
    ):
        return {"ok": True, "exempt": "synthetic_staff_test", "issues": [], "methods": []}
    resolved = list(parties) if parties is not None else packet_parties(bond_data, indemnitors)
    ids = [str(row.get("indemnitor_id") or "") for row in resolved if row.get("indemnitor_id")]
    try:
        evidence = await gather_identity_evidence(
            bond_case_id=bond_case_id,
            booking_number=booking_number,
            packet_id=packet_id,
            indemnitor_ids=ids,
        )
    except IdentityLookupError:
        issue = {"role": "indemnitor", "name": str((resolved or [{}])[0].get("name") or ""), "reason": "lookup_failed"}
        raise IndemnitorIdentityError(message_for_issues([issue]), [issue]) from None
    decision = evaluate_parties(
        resolved,
        evidence.get("scans") or [],
        evidence.get("attestations") or [],
        bond_case_id=bond_case_id,
        booking_number=booking_number,
        packet_id=packet_id,
        self_indemnitor=explicit_self_indemnitor(bond_data) or bool(evidence.get("self_indemnitor")),
    )
    if not decision["ok"]:
        raise IndemnitorIdentityError(message_for_issues(decision["issues"]), decision["issues"])
    return decision


def _reject_full_id(body: Mapping[str, Any]) -> str | None:
    """Return a machine code when the body carries more than the last 4."""
    for key, value in body.items():
        if str(key).lower() in _FULL_ID_KEYS and str(value or "").strip():
            return ATTESTATION_NUMBER_REJECTED
    last4 = body.get("id_last4", body.get("last4"))
    text = str(last4 or "").strip()
    if _DIGIT_RUN.search(text) or (text and not _LAST4.match(text)):
        return ATTESTATION_NUMBER_REJECTED
    for key, value in body.items():
        if str(key).lower() in {"id_last4", "last4"}:
            continue
        if str(key).lower() in _FULL_ID_KEYS:
            continue
        if isinstance(value, str) and _DIGIT_RUN.search(value) and "id" in str(key).lower():
            return ATTESTATION_NUMBER_REJECTED
    return None


def validate_attestation_body(body: Mapping[str, Any], actor: str) -> dict[str, str] | str:
    """Return the stored fields, or a machine code. Does not write."""
    if not isinstance(body, Mapping):
        return ATTESTATION_INVALID
    rejected = _reject_full_id(body)
    if rejected:
        return rejected
    name = str(body.get("indemnitor_name") or body.get("name") or "").strip()
    if len(_tokens(name)) < 2:
        return ATTESTATION_INVALID
    id_type = str(body.get("id_type") or "").strip().lower()
    if not _ID_TYPE.match(id_type):
        return ATTESTATION_INVALID
    issuing = _jurisdiction({"issuing_state": body.get("issuing_state") or body.get("state")})
    if issuing not in _US_JURISDICTIONS:
        return ATTESTATION_INVALID
    last4 = str(body.get("id_last4") or body.get("last4") or "").strip()
    if not last4:
        return ATTESTATION_INVALID
    if not _LAST4.match(last4):
        return ATTESTATION_NUMBER_REJECTED
    bond_case_id = str(body.get("bond_case_id") or body.get("Bond_Case_ID") or "").strip()
    booking_number = str(body.get("booking_number") or body.get("Booking_Number") or "").strip()
    packet_id = str(body.get("packet_id") or "").strip()
    indemnitor_id = str(body.get("indemnitor_id") or body.get("Indemnitor_ID") or "").strip()
    if not any((bond_case_id, booking_number, packet_id, indemnitor_id)):
        return ATTESTATION_INVALID
    if not actor.strip():
        return ATTESTATION_INVALID
    role = str(body.get("role") or "indemnitor").strip().lower()
    if role in {"coindemnitor", "co-indemnitor", "co_indemnitor"}:
        role = "co_indemnitor"
    else:
        role = "indemnitor"
    return {
        "indemnitor_name": normalize_person_name(name),
        "id_type": id_type,
        "issuing_state": issuing,
        "id_last4": last4,
        "bond_case_id": bond_case_id,
        "booking_number": booking_number,
        "packet_id": packet_id,
        "indemnitor_id": indemnitor_id,
        "role": role,
        "staff_user": actor.strip(),
    }


async def record_staff_id_attestation(actor: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Write one audit_events row. ``actor`` is the session user, never the body.

    A full ID number is rejected and nothing is written.
    """
    cleaned = validate_attestation_body(body, actor)
    if isinstance(cleaned, str):
        return {"ok": False, "error": cleaned}
    from dashboard.extensions import get_collection

    now = datetime.now(timezone.utc).isoformat()
    entity_id = cleaned["indemnitor_id"] or cleaned["bond_case_id"] or cleaned["booking_number"] or cleaned["packet_id"]
    doc = {
        "entity_type": "indemnitor",
        "entity_id": entity_id,
        "action": ATTESTATION_ACTION,
        "actor": cleaned["staff_user"],
        "actor_type": "staff",
        "timestamp": now,
        "details": {
            "indemnitor_name": cleaned["indemnitor_name"],
            "role": cleaned["role"],
            "id_type": cleaned["id_type"],
            "issuing_state": cleaned["issuing_state"],
            "id_last4": cleaned["id_last4"],
            "bond_case_id": cleaned["bond_case_id"],
            "booking_number": cleaned["booking_number"],
            "packet_id": cleaned["packet_id"],
            "indemnitor_id": cleaned["indemnitor_id"],
            "timestamp": now,
            "staff_user": cleaned["staff_user"],
        },
    }
    await get_collection("audit_events").insert_one(doc)
    return {"ok": True, "action": ATTESTATION_ACTION}


def _open_status(doc: Mapping[str, Any]) -> str:
    return str(doc.get("status") or doc.get("Status") or "").strip().lower()


def _case_key(doc: Mapping[str, Any]) -> str:
    return (
        _text(doc, "bond_case_id", "Bond_Case_ID")
        or _text(doc, "booking_number", "Booking_Number")
        or ""
    )


def _is_synthetic_case(doc: Mapping[str, Any]) -> bool:
    if doc.get("is_test") is True or doc.get("test_case") is True:
        return True
    return is_test_booking(_text(doc, "booking_number", "Booking_Number"))


def summarize_open_bond_cases(
    cases: Sequence[Mapping[str, Any]],
    related_docs: Sequence[Mapping[str, Any]] | None = None,
    attestations: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, int]:
    """Count open BondCases. Counts only. No names, ids, or ID numbers.

    Open is a kanban status of active, monitoring, alert, or reinstated.
    The same bond case id in ``bond_cases`` and ``active_bonds`` counts once.
    ``bond_cases`` wins when both are present. Synthetic TEST- / is_test rows
    are excluded. A defendant-role scan verifies the indemnitor only when
    this case, or a linked packet, has an explicit ``self_indemnitor`` mark.
    """
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for doc in cases:
        if not isinstance(doc, Mapping) or _is_synthetic_case(doc):
            continue
        key = _case_key(doc)
        if not key:
            continue
        source = str(doc.get("_collection") or "")
        slot = grouped.get(key)
        if slot is None:
            grouped[key] = {"doc": doc, "source": source}
            order.append(key)
            continue
        if source == "bond_cases" and slot["source"] != "bond_cases":
            grouped[key] = {"doc": doc, "source": source}
    counts = {
        "total_open": 0,
        "verified_by_scan": 0,
        "verified_by_attestation": 0,
        "blocked_no_scan": 0,
        "blocked_scan_failed": 0,
        "blocked_name_mismatch": 0,
        "blocked_lookup_failed": 0,
    }
    related = list(related_docs or [])
    audits = list(attestations or [])
    for key in order:
        doc = grouped[key]["doc"]
        if _open_status(doc) not in OPEN_BOND_CASE_STATUSES:
            continue
        counts["total_open"] += 1
        bond_case_id = _text(doc, "bond_case_id", "Bond_Case_ID")
        booking_number = _text(doc, "booking_number", "Booking_Number")
        packet_id = _text(doc, "packet_id")
        parties = packet_parties(doc)
        scans = scans_from_document(doc)
        self_indemnitor = explicit_self_indemnitor(doc)
        for extra in related:
            linked_extra = _case_linked(
                {**_link_ids(extra), "passed": "0"},
                bond_case_id=bond_case_id,
                booking_number=booking_number,
                packet_id=packet_id,
                indemnitor_id=_text(doc, "indemnitor_id", "Indemnitor_ID"),
            ) or any(
                _case_linked(
                    {**_link_ids(extra), "passed": "0"},
                    bond_case_id=bond_case_id,
                    booking_number=booking_number,
                    packet_id=packet_id,
                    indemnitor_id=str(party.get("indemnitor_id") or ""),
                )
                for party in parties
            )
            if not linked_extra:
                continue
            if explicit_self_indemnitor(extra):
                self_indemnitor = True
            scans.extend(scans_from_document(extra))
        decision = evaluate_parties(
            parties,
            scans,
            audits,
            bond_case_id=bond_case_id,
            booking_number=booking_number,
            packet_id=packet_id,
            self_indemnitor=self_indemnitor,
        )
        if decision["ok"]:
            if decision["methods"] and all(method == "scan" for method in decision["methods"]):
                counts["verified_by_scan"] += 1
            else:
                counts["verified_by_attestation"] += 1
            continue
        reasons = {str(issue.get("reason") or "no_scan") for issue in decision["issues"]}
        chosen = "no_scan"
        for reason in _REASON_PRIORITY:
            if reason in reasons:
                chosen = reason
                break
        counts[f"blocked_{chosen}"] += 1
    return counts
