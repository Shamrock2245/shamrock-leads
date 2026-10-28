"""
Contact Discovery Service — ShamrockLeads co-signer candidates.

A new Lee (FL) booking is queued after the arrest write. The dashboard cron
reads Shamrock's own rows and, only when enabled, public HTTPS sources.
A candidate is kept only when a name is paired with a phone, a DOB, or a
normalized address + ZIP. Nothing in this module calls, texts, or emails.
"""

import asyncio
import hashlib
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote, urlencode, urlparse

log = logging.getLogger("shamrock.contact_discovery")

# FL County Property Appraiser public search endpoints
COUNTY_APPRAISER_URLS = {
    "lee":       "https://www.leepa.org/Search/SearchAddress.aspx",
    "collier":   "https://www.collierappraiser.com/search/commonsearch.aspx",
    "charlotte": "https://www.ccappraiser.com/search/commonsearch.aspx",
    "hendry":    "https://www.hendrypa.com/search/commonsearch.aspx",
    "desoto":    "https://www.desotopafl.com/search/commonsearch.aspx",
    "manatee":   "https://www.manateepao.gov/search/commonsearch.aspx",
    "sarasota":  "https://www.sc-pa.com/search/commonsearch.aspx",
}

class ContactDiscoveryService:
    """Discovers potential indemnitor contacts for a defendant via OSINT."""

    def __init__(self, db):
        self.db = db

    async def discover(self, booking_number: str, full_name: str, county: str = None,
                       address: str = None, dob: str = None, state: str = None):
        """Refresh co-signer candidates for one booking. Staff-initiated, so force=True.

        A name by itself is never stored. HTTP and skip-trace stay off unless
        their env flags are set. This does not text, call, or email anyone.
        """
        result = await run_discovery(
            self.db,
            booking_number=booking_number,
            full_name=full_name or "",
            county=county or "",
            address=address or "",
            dob=dob or "",
            state=state or "",
            force=True,
        )
        return {
            "success": True,
            "cached": bool(result.get("cached")),
            "contacts": result.get("contacts") or [],
            "discovery_status": result.get("discovery_status") or "complete",
        }


# ── Lee indemnitor discovery (name + anchor, no outreach) ────────────────────
#
# A candidate is stored only when the name is paired with at least one anchor:
# phone, dob, or normalized address+ZIP. Confidence is the sum of the anchor
# weights below, capped at 0.99. Name alone adds nothing and is dropped.
#
# INDEMNITOR_DISCOVERY_HTTP and the skip-trace pair default off. Public HTTP
# uses scrapers.plain_http (honest UA, verified TLS, typed block/TLS errors).
# Sheriff hosts also honor scrapers.lee_rate_limit. Nothing here sends a
# message.

ANCHOR_WEIGHTS = {"phone": 0.45, "dob": 0.35, "address_zip": 0.30}
UNREVIEWED_TTL_DAYS = 30
DISCOVERY_BATCH_LIMIT = 5
RECENT_MISSING_HOURS = 24
STALE_RUNNING_MINUTES = 15
DEFER_MINUTES = 15
MAX_DEFERRALS = 4
# A run that raised (e.g. a transient Mongo error) is retried with backoff
# RETRY_BASE_MINUTES * 2**(attempt-1), up to MAX_DISCOVERY_ATTEMPTS runs in
# total. After that the booking is terminal ``failed`` and logged.
MAX_DISCOVERY_ATTEMPTS = 3
RETRY_BASE_MINUTES = 15
_HTTP_GAP_S = 2.0
_last_http_monotonic = 0.0
_http_gap_lock: Optional[asyncio.Lock] = None

# Only these review states are usable outside the review drawer (outreach).
REVIEWED_STATUSES = frozenset({"reviewed", "approved"})
# Provider statuses that mean "try again later", not "nothing found".
DEFERRABLE_STATUSES = frozenset({"lee_rate_limited", "discovery_http_gap"})

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


class DiscoveryHttpError(Exception):
    """Typed public-source failure. ``code`` is safe to store and log."""

    code = "discovery_http_error"

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DiscoveryRateLimited(DiscoveryHttpError):
    def __init__(self, code: str = "lee_rate_limited"):
        super().__init__(code)


class ContactsLegacyAmbiguous(Exception):
    """A booking-only legacy contacts doc cannot be tied to one arrest.

    Nothing is written. ``code`` is safe to store and log.
    """

    code = "contacts_legacy_ambiguous"

    def __init__(self) -> None:
        super().__init__(self.code)


class PriorReadFailed(Exception):
    """A Mongo read inside a provider failed. The run must not count as done."""

    def __init__(self, collection: str, error_type: str):
        self.collection = collection
        self.error_type = error_type
        super().__init__(f"{collection}:{error_type}")


def canonical_place(county: Any, state: Any) -> tuple:
    """(County, ST) in one canonical spelling, or ("", "") parts when unknown.

    ``lee``, ``LEE``, ``Lee County`` and ``Lee (FL)`` all give ``Lee``;
    ``Florida``, ``fla`` and ``fl`` give ``FL``. A state that is not a known
    name or a two-letter code gives "" so the caller fails closed. Already
    canonical input (``Lee`` / ``FL``) is returned unchanged, so contacts ids
    built from canonical values are the same as before.
    """
    from dashboard.services.place_identity import parse_place

    st = _norm_state(state)
    name, parsed_st = parse_place(county, st if re.fullmatch(r"[A-Z]{2}", st or "") else "")
    st = st or _norm_state(parsed_st)
    if not re.fullmatch(r"[A-Z]{2}", st or ""):
        st = ""
    name = re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), " ".join(name.split()))
    return name, st


def contact_identity(state: Any, county: Any, booking_number: Any) -> Optional[Dict[str, str]]:
    """Full arrest identity for a ``contacts`` doc, or None when any part is blank.

    The same booking number can exist in two counties, so a contacts doc is
    always keyed and read on state + county + booking_number. County and state
    are canonicalized first (see :func:`canonical_place`), so ``lee`` /
    ``Lee County`` / ``Florida`` give the same identity and ``_id`` as
    ``Lee`` / ``FL``.
    """
    co, st = canonical_place(county, state)
    bk = str(booking_number or "").strip()
    if not (st and co and bk):
        return None
    return {"state": st, "county": co, "booking_number": bk}


def contact_doc_id(identity: Dict[str, str]) -> str:
    """Deterministic ``_id`` for a new contacts doc, built from its full identity.

    Mongo's ``_id`` index is always unique, so two concurrent upserts of the
    same arrest cannot both insert. No extra index is needed. Legacy docs keep
    their ObjectId; they are still found by the identity fields, so a doc
    that already exists is updated, never inserted again.
    """
    raw = "\x1f".join((identity["state"], identity["county"], identity["booking_number"]))
    return "contacts:v1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _is_duplicate_key(exc: BaseException) -> bool:
    return getattr(exc, "code", None) == 11000 or type(exc).__name__ == "DuplicateKeyError"


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


_LEGACY_SCAN_LIMIT = 25


async def _legacy_booking_only_docs(contacts: Any, identity: Dict[str, str]) -> List[Dict[str, Any]]:
    """Contacts docs for this booking number that lack county or state.

    A doc whose present county or state names another place is not this
    arrest's and is ignored. Read errors propagate (the caller fails closed).
    """
    query = {
        "booking_number": identity["booking_number"],
        "$or": [{"state": {"$in": [None, ""]}}, {"county": {"$in": [None, ""]}}],
    }
    rows = await _find_list(contacts, query, limit=_LEGACY_SCAN_LIMIT)
    if len(rows) >= _LEGACY_SCAN_LIMIT:
        # Too many to see them all: one we did not read could be this arrest's.
        log.warning("[indemnitor_discovery] contacts write skipped code=%s", ContactsLegacyAmbiguous.code)
        raise ContactsLegacyAmbiguous()
    found: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        r_county, r_state = canonical_place(row.get("county"), row.get("state"))
        if not _blank(row.get("county")) and r_county.lower() != identity["county"].lower():
            continue
        if not _blank(row.get("state")) and r_state != identity["state"]:
            continue
        found.append(row)
    return found


async def _booking_is_unique(arrests: Any, booking_number: str) -> bool:
    """True only when exactly one arrest has this booking number. Unknown = False."""
    if arrests is None:
        return False
    try:
        rows = await _find_list(arrests, {"booking_number": booking_number}, limit=2)
    except Exception:
        return False
    return len([row for row in rows if isinstance(row, dict)]) == 1


async def find_contact_doc(
    contacts: Any,
    identity: Dict[str, str],
    *,
    arrests: Any = None,
) -> Optional[Dict[str, Any]]:
    """The contacts doc this arrest's writes go to, or None for a new one.

    The doc on the full identity wins. Otherwise the one legacy doc keyed by
    booking only, when exactly one exists and exactly one arrest has the
    booking number. Any doubt raises :class:`ContactsLegacyAmbiguous`.
    """
    current = await contacts.find_one(identity)
    if current is not None:
        return current
    legacy = await _legacy_booking_only_docs(contacts, identity)
    if not legacy:
        return None
    if len(legacy) != 1 or not await _booking_is_unique(arrests, identity["booking_number"]):
        log.warning("[indemnitor_discovery] contacts write skipped code=%s", ContactsLegacyAmbiguous.code)
        raise ContactsLegacyAmbiguous()
    return legacy[0]


async def upsert_contact_doc(
    contacts: Any,
    identity: Dict[str, str],
    update: Dict[str, Any],
    *,
    arrests: Any = None,
) -> str:
    """Idempotent contacts upsert on state + county + booking_number.

    1. A doc already on the full identity is updated (``"updated"``).
    2. Otherwise a legacy doc keyed by booking only (no county or state) is
       updated in place and given the identity fields (``"legacy_updated"``).
       That happens only when there is exactly one such doc and exactly one
       arrest has the booking number. Any doubt raises
       :class:`ContactsLegacyAmbiguous` and nothing is written.
    3. Otherwise a new doc gets the deterministic ``_id`` (``"upserted"``). If
       a concurrent writer inserted it first, the unique ``_id`` index rejects
       the second insert, and that write is applied as a plain update.
    """
    plain = {key: value for key, value in update.items() if key != "$setOnInsert"}
    target = await find_contact_doc(contacts, identity, arrests=arrests)
    if target is not None and all(target.get(key) == value for key, value in identity.items()):
        await contacts.update_one(identity, plain, upsert=False)
        return "updated"
    if target is not None:
        in_place = dict(plain)
        in_place["$set"] = {**(plain.get("$set") or {}), **identity}
        await contacts.update_one({"_id": target.get("_id")}, in_place, upsert=False)
        return "legacy_updated"
    first = dict(update)
    first["$setOnInsert"] = {**(update.get("$setOnInsert") or {}), "_id": contact_doc_id(identity)}
    try:
        await contacts.update_one(identity, first, upsert=True)
        return "upserted"
    except Exception as exc:
        if not _is_duplicate_key(exc):
            raise
    await contacts.update_one(identity, plain, upsert=False)
    return "updated"


def is_reviewed(candidate: Any) -> bool:
    return isinstance(candidate, dict) and candidate.get("review_status") in REVIEWED_STATUSES


def discovery_enabled() -> bool:
    raw = os.getenv("INDEMNITOR_DISCOVERY_ENABLED", "1").strip().lower()
    return raw not in _FALSY


def discovery_http_enabled() -> bool:
    return os.getenv("INDEMNITOR_DISCOVERY_HTTP", "").strip().lower() in _TRUTHY


def skiptrace_configured() -> bool:
    return bool(
        os.getenv("INDEMNITOR_SKIPTRACE_URL", "").strip()
        and os.getenv("INDEMNITOR_SKIPTRACE_API_KEY", "").strip()
    )


def name_key(full_name: str) -> str:
    """Exact identity key. 'Last, First' and 'First Last' match. Substrings do not."""
    text = re.sub(r"[^a-z0-9, ]", "", (full_name or "").lower())
    text = " ".join(text.split())
    if not text:
        return ""
    if "," in text:
        last, rest = [part.strip() for part in text.split(",", 1)]
        parts = rest.split() + ([last] if last else [])
    else:
        parts = text.split()
    return " ".join(parts)


def exact_name_variants(full_name: str) -> List[str]:
    raw = " ".join((full_name or "").split())
    if not raw:
        return []
    found = {raw, raw.lower(), raw.upper(), raw.title()}
    if "," in raw:
        last, rest = [part.strip() for part in raw.split(",", 1)]
        flipped = " ".join((rest.split() + [last]) if last and rest else [])
    else:
        parts = raw.split()
        flipped = f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) >= 2 else ""
    if flipped:
        found.update({flipped, flipped.lower(), flipped.upper(), flipped.title()})
    return [item for item in found if item]


def names_equal(left: str, right: str) -> bool:
    key = name_key(left)
    return bool(key) and key == name_key(right)


def normalize_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def normalize_dob(value: Any) -> str:
    text = str(value or "").strip()
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return text
    us = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if us:
        return f"{int(us.group(3)):04d}-{int(us.group(1)):02d}-{int(us.group(2)):02d}"
    return ""


def normalize_zip(value: Any) -> str:
    match = re.search(r"\d{5}", str(value or ""))
    return match.group(0) if match else ""


def normalize_street(value: Any) -> str:
    text = re.sub(r"[^a-z0-9 ]", " ", str(value or "").lower())
    return " ".join(text.split())


def address_key(address: Any, zip_code: Any) -> str:
    street = normalize_street(address)
    zip5 = normalize_zip(zip_code)
    if not street or not zip5:
        return ""
    return f"{street}|{zip5}"


def confidence_for(anchors: Sequence[str]) -> float:
    seen: List[str] = []
    for anchor in anchors:
        if anchor in ANCHOR_WEIGHTS and anchor not in seen:
            seen.append(anchor)
    if not seen:
        return 0.0
    total = sum(ANCHOR_WEIGHTS[anchor] for anchor in seen)
    return round(min(0.99, total), 2)


def _anchors_from(values: Sequence[str]) -> List[str]:
    seen: List[str] = []
    for anchor in values:
        if anchor in ANCHOR_WEIGHTS and anchor not in seen:
            seen.append(anchor)
    return seen


def make_candidate(
    *,
    booking_number: str,
    name: str,
    anchors: Sequence[str],
    source: str,
    relationship: str,
    phone: str = "",
    email: str = "",
    address: str = "",
    defendant_name: str = "",
) -> Optional[Dict[str, Any]]:
    """Return a candidate dict, or None when the name lacks a real anchor."""
    clean_name = " ".join(str(name or "").split())
    kept = _anchors_from(anchors)
    if not clean_name or not kept:
        return None
    if defendant_name and names_equal(clean_name, defendant_name):
        return None
    score = confidence_for(kept)
    if score <= 0:
        return None
    discovered = datetime.now(timezone.utc).isoformat()
    return {
        "candidate_id": str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{booking_number}|{source}|{name_key(clean_name)}",
        )),
        "name": clean_name,
        "relationship": relationship or "possible_indemnitor",
        "phone": normalize_phone(phone) or None,
        "email": (str(email).strip() or None) if email else None,
        "address": (str(address).strip() or None) if address else None,
        "source": source,
        "confidence": score,
        "matched_anchors": kept,
        "discovered_at": discovered,
        "review_status": "unreviewed",
    }


def _first(row: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _as_names(value: Any) -> List[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def _row_booking(row: Dict[str, Any]) -> str:
    return _first(row, "booking_number", "Booking_Number", "matched_booking_number")


def _contact_anchors(row: Dict[str, Any]) -> List[str]:
    """Anchors present on the co-signer fields of one stored row."""
    anchors: List[str] = []
    phone = _first(row, "indemnitor_phone", "relative_phone", "phone", "emergency_phone", "employer_phone")
    if normalize_phone(phone):
        anchors.append("phone")
    dob = _first(row, "indemnitor_dob", "relative_dob", "emergency_dob")
    if normalize_dob(dob):
        anchors.append("dob")
    address = _first(row, "indemnitor_address", "relative_address", "emergency_address", "employer_address", "address")
    zip_code = _first(row, "indemnitor_zip", "relative_zip", "emergency_zip", "employer_zip", "zip")
    if address_key(address, zip_code):
        anchors.append("address_zip")
    return _anchors_from(anchors)


def shared_defendant_anchors(booking: Dict[str, Any], row: Dict[str, Any]) -> List[str]:
    """Anchors that tie this booking's defendant to a stored defendant-side row.

    The co-signer's own phone is not an anchor here. A shared display name is not either.
    """
    anchors: List[str] = []
    booking_dob = normalize_dob(booking.get("dob"))
    row_dob = normalize_dob(_first(row, "defendant_dob"))
    if booking_dob and row_dob and booking_dob == row_dob:
        anchors.append("dob")
    booking_phone = normalize_phone(_first(booking, "phone", "defendant_phone"))
    row_phone = normalize_phone(_first(row, "defendant_phone"))
    if booking_phone and row_phone and booking_phone == row_phone:
        anchors.append("phone")
    booking_addr = address_key(booking.get("address"), booking.get("zip"))
    row_addr = address_key(
        _first(row, "defendant_address"),
        _first(row, "defendant_zip", "defendant_zip_code"),
    )
    if booking_addr and row_addr and booking_addr == row_addr:
        anchors.append("address_zip")
    return _anchors_from(anchors)


def _candidate_name(row: Dict[str, Any]) -> str:
    named = _first(row, "indemnitor_name", "relative_name", "emergency_name", "employer_name", "name")
    if named:
        return named
    first = _first(row, "first_name", "firstName")
    last = _first(row, "last_name", "lastName")
    return " ".join(part for part in (first, last) if part)


def _relationship(row: Dict[str, Any], default: str) -> str:
    return _first(row, "relationship", "indemnitor_relationship", "relation", "relation_type") or default


def _contact_fields(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "phone": _first(row, "indemnitor_phone", "relative_phone", "phone", "emergency_phone", "employer_phone"),
        "email": _first(row, "indemnitor_email", "relative_email", "email", "emergency_email"),
        "address": _first(row, "indemnitor_address", "relative_address", "address", "emergency_address", "employer_address"),
    }


async def _find_list(collection: Any, query: Dict[str, Any], limit: int = 25) -> List[Dict[str, Any]]:
    cursor = collection.find(query)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit)
    if hasattr(cursor, "to_list"):
        rows = cursor.to_list(length=limit)
        if asyncio.iscoroutine(rows):
            rows = await rows
        return list(rows or [])
    return []


def _merge_candidates(
    existing: Sequence[Dict[str, Any]],
    fresh: Sequence[Dict[str, Any]],
    keep_sources: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Merge a refresh into the stored list.

    A reviewed/approved prior row keeps its review state and id, and it is
    kept even when this run did not return it. A prior unreviewed row from a
    provider that did not complete this run (``keep_sources``) is kept too;
    the 30-day purge still applies to it.
    """
    def _key(row: Dict[str, Any]):
        return (name_key(str(row.get("name") or "")), str(row.get("source") or ""))

    prior = {_key(row): row for row in existing if isinstance(row, dict)}
    merged: List[Dict[str, Any]] = []
    seen = set()
    for row in fresh:
        key = _key(row)
        if not key[0] or key in seen:
            continue
        seen.add(key)
        previous = prior.get(key)
        if is_reviewed(previous):
            row = dict(row)
            row["review_status"] = previous["review_status"]
            for field in ("candidate_id", "reviewed_at", "reviewed_by"):
                if previous.get(field):
                    row[field] = previous[field]
        merged.append(row)
    keep = set(keep_sources or ())
    for key, row in prior.items():
        if key in seen or not key[0]:
            continue
        if is_reviewed(row) or str(row.get("source") or "") in keep:
            seen.add(key)
            merged.append(row)
    return merged


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def unreviewed_expire_at(candidates: Sequence[Dict[str, Any]], now: datetime) -> Optional[datetime]:
    """TTL date for a doc whose candidates are all still unreviewed.

    A reviewed candidate keeps the document. The purge job removes expired
    unreviewed rows inside a mixed document. Mongo TTL cannot expire a subdocument.
    """
    if not candidates:
        return None
    if any(row.get("review_status") != "unreviewed" for row in candidates):
        return None
    # Mongo TTL deletes the whole document, so it may only expire once the
    # newest candidate has aged out. The oldest row would drop fresh rows.
    stamps = [_parse_time(row.get("discovered_at")) or now for row in candidates]
    return max(stamps) + timedelta(days=UNREVIEWED_TTL_DAYS)


def _norm_county(value: Any) -> str:
    text = " ".join(str(value or "").lower().split())
    return text[:-7].strip() if text.endswith(" county") else text


_US_STATE_CODES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA",
    "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA",
    "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "FLA": "FL",
}


def _norm_state(value: Any) -> str:
    text = " ".join(str(value or "").strip().upper().replace(".", "").split())
    return _US_STATE_CODES.get(text, text)


def _same_place(booking: Dict[str, Any], county: Any, state: Any = "") -> bool:
    """False only when the row names a different county or state than the booking.

    Both two-letter codes and full state names are compared ("Georgia" is GA),
    so a same-name county in another state never counts as the same place.
    """
    b_county, r_county = _norm_county(booking.get("county")), _norm_county(county)
    if b_county and r_county and b_county != r_county:
        return False
    b_state, r_state = _norm_state(booking.get("state")), _norm_state(state)
    if b_state and r_state and b_state != r_state:
        return False
    return True


def _arrest_scope_matches(booking: Dict[str, Any], county: Any, state: Any) -> bool:
    """True only when the row names this arrest's county AND state.

    Booking numbers are not unique across counties, so a row matched on the
    booking number counts only when it carries both place fields and they
    equal the arrest's (after canonicalization). A missing value on either
    side is doubt, and doubt does not match.
    """
    b_county, b_state = canonical_place(booking.get("county"), booking.get("state"))
    r_county, r_state = canonical_place(county, state)
    if not (b_county and b_state and r_county and r_state):
        return False
    return b_county.lower() == r_county.lower() and b_state == r_state


# Fields on indemnitors / intakes / family rows that describe the arrest, never
# the co-signer's or relative's own address.
_ARREST_COUNTY_FIELDS = ("defendant_county", "matched_county", "bond_county", "booking_county")
_ARREST_STATE_FIELDS = ("defendant_state", "matched_state", "bond_state", "booking_state")


def _generic_prior_rows(rows: Sequence[Any], booking: Dict[str, Any]) -> List[Dict[str, Any]]:
    """indemnitors / intakes rows (flat indemnitor_* or name/phone fields)."""
    defendant = _first(booking, "full_name", "defendant_name")
    booking_number = _first(booking, "booking_number")
    found: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Only arrest-scoped place fields count. The bare ``state`` / ``county``
        # on an indemnitor row is the co-signer's home address, not the arrest.
        # Both must be present: a legacy row without them does not match.
        same_case = (
            bool(booking_number)
            and _row_booking(row) == booking_number
            and _arrest_scope_matches(
                booking,
                _first(row, *_ARREST_COUNTY_FIELDS),
                _first(row, *_ARREST_STATE_FIELDS),
            )
        )
        defendant_names = _as_names(row.get("defendant_name")) + _as_names(row.get("defendants_bonded"))
        name_hit = any(names_equal(defendant, item) for item in defendant_names)
        anchors: List[str] = []
        if same_case:
            anchors.extend(_contact_anchors(row))
        if name_hit:
            anchors.extend(shared_defendant_anchors(booking, row))
        candidate = make_candidate(
            booking_number=booking_number,
            name=_candidate_name(row),
            anchors=anchors,
            source="prior_shamrock",
            relationship=_relationship(row, "prior_indemnitor"),
            defendant_name=defendant,
            **_contact_fields(row),
        )
        if candidate:
            found.append(candidate)
    return found


def _intake_queue_rows(rows: Sequence[Any], booking: Dict[str, Any]) -> List[Dict[str, Any]]:
    """intake_queue: booking is defendant_booking_number / matched_booking_number;
    the co-signer is the nested ``indemnitor`` block (flat indemnitor_* mirror)."""
    defendant = _first(booking, "full_name", "defendant_name")
    booking_number = _first(booking, "booking_number")
    found: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ind = row.get("indemnitor") if isinstance(row.get("indemnitor"), dict) else {}
        dfd = row.get("defendant") if isinstance(row.get("defendant"), dict) else {}
        same_case = bool(booking_number) and (
            (
                _first(row, "defendant_booking_number") == booking_number
                and _arrest_scope_matches(
                    booking,
                    _first(row, "defendant_county") or _first(dfd, "county"),
                    _first(row, "defendant_state") or _first(dfd, "state") or _first(row, "state"),
                )
            )
            or (
                _first(row, "matched_booking_number") == booking_number
                and _arrest_scope_matches(
                    booking,
                    _first(row, "matched_county"),
                    _first(row, "matched_state") or _first(row, "state"),
                )
            )
        )
        name = _first(row, "indemnitor_name") or " ".join(
            part for part in (_first(ind, "firstName"), _first(ind, "lastName")) if part
        )
        phone = _first(row, "indemnitor_phone") or _first(ind, "phone")
        email = _first(row, "indemnitor_email") or _first(ind, "email")
        address = _first(ind, "address")
        zip_code = _first(ind, "zip")
        anchors: List[str] = []
        if same_case:
            if normalize_phone(phone):
                anchors.append("phone")
            if normalize_dob(_first(ind, "dob")):
                anchors.append("dob")
            if address_key(address, zip_code):
                anchors.append("address_zip")
        defendant_names = _as_names(row.get("defendant_name")) + _as_names(_first(dfd, "name"))
        if any(names_equal(defendant, item) for item in defendant_names):
            anchors.extend(shared_defendant_anchors(booking, {
                "defendant_dob": _first(dfd, "dob"),
                "defendant_phone": _first(dfd, "phone"),
                "defendant_address": _first(dfd, "address"),
                "defendant_zip": _first(dfd, "zip"),
            }))
        candidate = make_candidate(
            booking_number=booking_number,
            name=name,
            anchors=anchors,
            source="prior_shamrock",
            relationship=_first(ind, "relationship") or "intake_indemnitor",
            phone=phone,
            email=email,
            address=" ".join(part for part in (address, zip_code) if part),
            defendant_name=defendant,
        )
        if candidate:
            found.append(candidate)
    return found


def _family_rows(rows: Sequence[Any], booking: Dict[str, Any], collection_name: str) -> List[Dict[str, Any]]:
    """family_graph (defendant_name -> relative_name/relative_phone) and
    family_relationships from FamilyTreeService (person_name/person_id ->
    relative_name, relation_type, phone). The candidate is the linked relative;
    its anchors are the relative's own phone / DOB / address+ZIP.

    A row is linked to this arrest only by its full arrest identity: the same
    booking number plus arrest-scoped county AND state. A matching defendant
    name or name slug never links a row on its own, because two defendants
    can share a name. Rows without that scope (FamilyTreeService rows carry
    only a person name/id today) are skipped."""
    defendant = _first(booking, "full_name", "defendant_name")
    booking_number = _first(booking, "booking_number")
    found: List[Dict[str, Any]] = []
    if not booking_number:
        return found
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").lower() in {"soft_deleted", "deleted"}:
            continue
        linked = _row_booking(row) == booking_number and _arrest_scope_matches(
            booking,
            _first(row, *_ARREST_COUNTY_FIELDS),
            _first(row, *_ARREST_STATE_FIELDS),
        )
        if not linked:
            continue
        phone = _first(row, "relative_phone", "phone")
        dob = _first(row, "relative_dob", "dob")
        address = _first(row, "relative_address", "address")
        zip_code = _first(row, "relative_zip", "zip")
        anchors: List[str] = []
        if normalize_phone(phone):
            anchors.append("phone")
        if normalize_dob(dob):
            anchors.append("dob")
        if address_key(address, zip_code):
            anchors.append("address_zip")
        candidate = make_candidate(
            booking_number=booking_number,
            name=_first(row, "relative_name"),
            anchors=anchors,
            source="prior_shamrock",
            relationship=_first(row, "relation_type", "relationship") or "relative",
            phone=phone,
            email=_first(row, "relative_email", "email"),
            address=" ".join(part for part in (address, zip_code) if part),
            defendant_name=defendant,
        )
        if candidate:
            found.append(candidate)
    return found


async def _provider_prior(db: Any, booking: Dict[str, Any]) -> List[Dict[str, Any]]:
    defendant = _first(booking, "full_name", "defendant_name")
    booking_number = _first(booking, "booking_number")
    variants = exact_name_variants(defendant)
    by_booking = [{"booking_number": booking_number}] if booking_number else []
    queries: List[tuple] = []
    queries.append(("indemnitors", by_booking + (
        [{"defendant_name": {"$in": variants}}, {"defendants_bonded": {"$in": variants}}] if variants else []
    )))
    queries.append(("intakes", by_booking + ([{"defendant_name": {"$in": variants}}] if variants else [])))
    queries.append(("intake_queue", (
        [{"defendant_booking_number": booking_number}, {"matched_booking_number": booking_number}]
        if booking_number else []
    ) + ([{"defendant_name": {"$in": variants}}] if variants else [])))
    # Family rows are linked only by booking + arrest place (see _family_rows),
    # so they are read by booking number only; name-only rows are not fetched.
    queries.append(("family_graph", by_booking))
    queries.append(("family_relationships", by_booking))
    found: List[Dict[str, Any]] = []
    for collection_name, clauses in queries:
        if not clauses:
            continue
        try:
            rows = await _find_list(db[collection_name], {"$or": clauses}, limit=25)
        except Exception as exc:
            # A partial read must not look like "nothing found": the provider
            # fails, its prior rows are kept, and the booking is retried.
            log.warning(
                "[indemnitor_discovery] prior read failed collection=%s error_type=%s",
                collection_name,
                type(exc).__name__,
            )
            raise PriorReadFailed(collection_name, type(exc).__name__) from exc
        if collection_name == "intake_queue":
            found.extend(_intake_queue_rows(rows, booking))
        elif collection_name in {"family_graph", "family_relationships"}:
            found.extend(_family_rows(rows, booking, collection_name))
        else:
            found.extend(_generic_prior_rows(rows, booking))
    return found


async def _provider_same_address(db: Any, booking: Dict[str, Any]) -> List[Dict[str, Any]]:
    defendant = _first(booking, "full_name", "defendant_name")
    booking_number = _first(booking, "booking_number")
    key = address_key(booking.get("address"), booking.get("zip"))
    zip5 = normalize_zip(booking.get("zip"))
    if not key or not zip5:
        return []
    query = {"zip": {"$in": [zip5, booking.get("zip")]}}
    found: List[Dict[str, Any]] = []
    for collection_name, name_fields in (
        ("arrests", ("full_name", "defendant_name")),
        ("active_bonds", ("defendant_name", "full_name")),
    ):
        try:
            rows = await _find_list(db[collection_name], query, limit=25)
        except Exception as exc:
            log.warning(
                "[indemnitor_discovery] address read failed collection=%s error_type=%s",
                collection_name,
                type(exc).__name__,
            )
            raise PriorReadFailed(collection_name, type(exc).__name__) from exc
        for row in rows:
            if not isinstance(row, dict):
                continue
            if address_key(row.get("address"), row.get("zip")) != key:
                continue
            if (
                booking_number
                and _row_booking(row) == booking_number
                and _same_place(booking, row.get("county"), row.get("state"))
            ):
                continue  # the defendant's own row
            other = _first(row, *name_fields)
            anchors = ["address_zip"]
            if normalize_phone(_first(row, "phone", "defendant_phone")) and normalize_phone(
                _first(booking, "phone", "defendant_phone")
            ) and normalize_phone(_first(row, "phone", "defendant_phone")) == normalize_phone(
                _first(booking, "phone", "defendant_phone")
            ):
                anchors.append("phone")
            candidate = make_candidate(
                booking_number=booking_number,
                name=other,
                anchors=anchors,
                source="same_address",
                relationship="household",
                phone=_first(row, "phone", "defendant_phone"),
                address=str(row.get("address") or ""),
                defendant_name=defendant,
            )
            if candidate:
                found.append(candidate)
    return found


def _provider_booking_fields(booking: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Emergency contact or employer already stored on the arrest. Address alone is not a person."""
    defendant = _first(booking, "full_name", "defendant_name")
    booking_number = _first(booking, "booking_number")
    blocks: List[Dict[str, Any]] = []
    extra = booking.get("extra_data") if isinstance(booking.get("extra_data"), dict) else {}
    for raw in (
        booking.get("emergency_contact"),
        extra.get("emergency_contact"),
        booking.get("employer_contact"),
        extra.get("employer_contact"),
    ):
        if isinstance(raw, dict):
            blocks.append(raw)
    flat_emergency = {
        "name": _first(booking, "emergency_contact_name", "emergency_name"),
        "phone": _first(booking, "emergency_contact_phone", "emergency_phone"),
        "address": _first(booking, "emergency_contact_address", "emergency_address"),
        "zip": _first(booking, "emergency_contact_zip", "emergency_zip"),
        "dob": _first(booking, "emergency_contact_dob", "emergency_dob"),
        "relationship": "emergency_contact",
    }
    flat_employer = {
        "name": _first(booking, "employer_name"),
        "phone": _first(booking, "employer_phone"),
        "address": _first(booking, "employer_address"),
        "zip": _first(booking, "employer_zip"),
        "relationship": "employer",
    }
    if flat_emergency["name"]:
        blocks.append(flat_emergency)
    if flat_employer["name"]:
        blocks.append(flat_employer)
    found: List[Dict[str, Any]] = []
    for block in blocks:
        anchors: List[str] = []
        if normalize_phone(block.get("phone")):
            anchors.append("phone")
        if normalize_dob(block.get("dob")):
            anchors.append("dob")
        if address_key(block.get("address"), block.get("zip")):
            anchors.append("address_zip")
        candidate = make_candidate(
            booking_number=booking_number,
            name=str(block.get("name") or ""),
            anchors=anchors,
            source="booking_fields",
            relationship=str(block.get("relationship") or "emergency_contact"),
            phone=str(block.get("phone") or ""),
            email=str(block.get("email") or ""),
            address=str(block.get("address") or ""),
            defendant_name=defendant,
        )
        if candidate:
            found.append(candidate)
    return found


def _gap_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("INDEMNITOR_DISCOVERY_HTTP_GAP_S", str(_HTTP_GAP_S))))
    except ValueError:
        return _HTTP_GAP_S


async def _await_http_gap() -> None:
    """Sleep out the gap between public calls (asyncio.sleep, never time.sleep).

    The call is delayed, not skipped, so a booking is never completed early
    just because two providers ran back to back.
    """
    global _last_http_monotonic, _http_gap_lock
    if _http_gap_lock is None:
        _http_gap_lock = asyncio.Lock()
    async with _http_gap_lock:
        gap = _gap_seconds()
        if gap > 0 and _last_http_monotonic:
            wait = gap - (time.monotonic() - _last_http_monotonic)
            if wait > 0:
                await asyncio.sleep(wait)
        _last_http_monotonic = time.monotonic()


def public_request(method: str, url: str):
    """One plain HTTPS call (blocking). Sheriff hosts stop while the Lee cooldown is active.

    Raises DiscoveryRateLimited, EgressBlocked, or SourceTLSInvalid. The response
    body is not logged. Async callers use :func:`public_request_async`.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise DiscoveryHttpError("insecure_url")
    host = (parsed.hostname or "").lower()
    from scrapers.lee_rate_limit import is_cooled_down, record_429
    from scrapers.plain_http import make_session, request
    from scrapers.scraper_resilience import EgressBlocked

    if "sheriffleefl.org" in host and is_cooled_down():
        raise DiscoveryRateLimited("lee_rate_limited")
    session = make_session()
    try:
        response = request(session, method, url, county="Lee", timeout=10)
    except EgressBlocked as exc:
        if "HTTP 429" in str(exc) and "sheriffleefl.org" in host:
            record_429("HTTP 429")
            raise DiscoveryRateLimited("lee_rate_limited") from exc
        raise
    return response


async def public_request_async(method: str, url: str):
    """Gap, then run the blocking request on a worker thread (off the event loop)."""
    await _await_http_gap()
    return await asyncio.to_thread(public_request, method, url)


def _people_from_html(html: str, defendant: str) -> List[Dict[str, str]]:
    """Pull other people only when the same snippet has a phone or an address+ZIP."""
    people: List[Dict[str, str]] = []
    for match in re.finditer(
        r"([A-Za-z][A-Za-z' -]{1,40},\s+[A-Za-z][A-Za-z' -]{1,40})",
        html or "",
    ):
        name = " ".join(match.group(1).split())
        if names_equal(name, defendant):
            continue
        window = (html or "")[match.start():match.start() + 240]
        phone = ""
        phone_match = re.search(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", window)
        if phone_match:
            phone = phone_match.group(0)
        address = ""
        zip_code = ""
        addr_match = re.search(r"(\d+\s+[A-Za-z0-9 .]{3,60}?)\s+(\d{5})", window)
        if addr_match:
            address = addr_match.group(1).strip()
            zip_code = addr_match.group(2)
        if not phone and not (address and zip_code):
            continue
        people.append({"name": name, "phone": phone, "address": address, "zip": zip_code})
    return people


def _html_candidates(html: str, booking: Dict[str, Any], source: str, relationship: str) -> List[Dict[str, Any]]:
    defendant = _first(booking, "full_name", "defendant_name")
    booking_number = _first(booking, "booking_number")
    found: List[Dict[str, Any]] = []
    for person in _people_from_html(html, defendant):
        anchors: List[str] = []
        if normalize_phone(person.get("phone")):
            anchors.append("phone")
        if address_key(person.get("address"), person.get("zip")):
            anchors.append("address_zip")
        candidate = make_candidate(
            booking_number=booking_number,
            name=person.get("name") or "",
            anchors=anchors,
            source=source,
            relationship=relationship,
            phone=person.get("phone") or "",
            address=" ".join(part for part in (person.get("address"), person.get("zip")) if part),
            defendant_name=defendant,
        )
        if candidate:
            found.append(candidate)
    return found


async def _provider_property(booking: Dict[str, Any]) -> Dict[str, Any]:
    if not discovery_http_enabled():
        return {"provider": "lee_property_appraiser", "status": "skipped_disabled", "count": 0}
    from dashboard.services.place_identity import is_lee_florida

    if not is_lee_florida(booking.get("county"), booking.get("state") or "FL"):
        return {"provider": "lee_property_appraiser", "status": "skipped", "count": 0}
    defendant = _first(booking, "full_name", "defendant_name")
    variants = exact_name_variants(defendant)
    if not variants:
        return {"provider": "lee_property_appraiser", "status": "ok", "count": 0, "candidates": []}
    base = COUNTY_APPRAISER_URLS.get("lee")
    search_name = variants[0]
    url = f"{base}?{urlencode({'mode': 'owner', 'OwnerName': search_name})}"
    try:
        response = await public_request_async("GET", url)
        html = response.text or ""
    except DiscoveryHttpError as exc:
        return {"provider": "lee_property_appraiser", "status": exc.code, "count": 0}
    except Exception as exc:
        from scrapers.plain_http import SourceTLSInvalid
        from scrapers.scraper_resilience import EgressBlocked

        if isinstance(exc, SourceTLSInvalid):
            return {"provider": "lee_property_appraiser", "status": "tls_cert_invalid", "count": 0}
        if isinstance(exc, EgressBlocked):
            return {"provider": "lee_property_appraiser", "status": "egress_block", "count": 0}
        log.warning(
            "[indemnitor_discovery] property provider failed error_type=%s",
            type(exc).__name__,
        )
        return {"provider": "lee_property_appraiser", "status": "failed", "count": 0, "error_type": type(exc).__name__}
    candidates = _html_candidates(html, booking, "lee_property_appraiser", "property_associate")
    return {"provider": "lee_property_appraiser", "status": "ok", "count": len(candidates), "candidates": candidates}


async def _provider_clerk(booking: Dict[str, Any]) -> Dict[str, Any]:
    if not discovery_http_enabled():
        return {"provider": "lee_clerk_parties", "status": "skipped_disabled", "count": 0}
    case_number = _first(booking, "case_number")
    if not case_number:
        return {"provider": "lee_clerk_parties", "status": "skipped", "count": 0}
    url = f"https://matrix.leeclerk.org/Home/Search?query={quote(case_number)}"
    try:
        response = await public_request_async("GET", url)
        html = response.text or ""
    except DiscoveryHttpError as exc:
        return {"provider": "lee_clerk_parties", "status": exc.code, "count": 0}
    except Exception as exc:
        from scrapers.plain_http import SourceTLSInvalid
        from scrapers.scraper_resilience import EgressBlocked

        if isinstance(exc, SourceTLSInvalid):
            return {"provider": "lee_clerk_parties", "status": "tls_cert_invalid", "count": 0}
        if isinstance(exc, EgressBlocked):
            return {"provider": "lee_clerk_parties", "status": "egress_block", "count": 0}
        log.warning(
            "[indemnitor_discovery] clerk provider failed error_type=%s",
            type(exc).__name__,
        )
        return {"provider": "lee_clerk_parties", "status": "failed", "count": 0, "error_type": type(exc).__name__}
    if len(html) < 800:
        return {"provider": "lee_clerk_parties", "status": "skipped_unparsed", "count": 0}
    candidates = _html_candidates(html, booking, "lee_clerk_parties", "case_party")
    status = "ok" if candidates or "Party:" not in html else "ok"
    return {"provider": "lee_clerk_parties", "status": status, "count": len(candidates), "candidates": candidates}


def skiptrace_anchors(row: Dict[str, Any]) -> List[str]:
    """Anchors recomputed from the returned fields. A vendor claim counts only
    when the matching field is present and valid; a field counts only when the
    vendor also claims it matched."""
    claimed = set(row.get("matched_anchors") or []) if isinstance(row.get("matched_anchors"), list) else set()
    anchors: List[str] = []
    if "phone" in claimed and normalize_phone(row.get("phone")):
        anchors.append("phone")
    if "dob" in claimed and normalize_dob(row.get("dob")):
        anchors.append("dob")
    if "address_zip" in claimed and address_key(row.get("address"), row.get("zip") or row.get("address")):
        anchors.append("address_zip")
    return anchors


async def _provider_skiptrace(booking: Dict[str, Any]) -> Dict[str, Any]:
    url = os.getenv("INDEMNITOR_SKIPTRACE_URL", "").strip()
    key = os.getenv("INDEMNITOR_SKIPTRACE_API_KEY", "").strip()
    if not url or not key:
        return {"provider": "skiptrace", "status": "skipped_disabled", "count": 0}
    if not url.lower().startswith("https://"):
        return {"provider": "skiptrace", "status": "insecure_url", "count": 0}
    defendant = _first(booking, "full_name", "defendant_name")
    payload = {
        "booking_number": _first(booking, "booking_number"),
        "name": defendant,
        "dob": normalize_dob(booking.get("dob")),
        "address": str(booking.get("address") or ""),
        "zip": normalize_zip(booking.get("zip")),
    }
    def _call() -> Any:
        from scrapers.plain_http import make_session, request

        session = make_session({"Authorization": f"Bearer {key}", "Accept": "application/json"})
        response = request(session, "POST", url, county="Lee", timeout=10, json=payload)
        return response.json() if response.content else {}

    try:
        await _await_http_gap()
        body = await asyncio.to_thread(_call)
    except DiscoveryHttpError as exc:
        return {"provider": "skiptrace", "status": exc.code, "count": 0}
    except Exception as exc:
        from scrapers.plain_http import SourceTLSInvalid
        from scrapers.scraper_resilience import EgressBlocked

        if isinstance(exc, SourceTLSInvalid):
            return {"provider": "skiptrace", "status": "tls_cert_invalid", "count": 0}
        if isinstance(exc, EgressBlocked):
            return {"provider": "skiptrace", "status": "egress_block", "count": 0}
        log.warning(
            "[indemnitor_discovery] skiptrace failed error_type=%s",
            type(exc).__name__,
        )
        return {"provider": "skiptrace", "status": "failed", "count": 0, "error_type": type(exc).__name__}
    found: List[Dict[str, Any]] = []
    rows = body.get("candidates") if isinstance(body, dict) else None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        candidate = make_candidate(
            booking_number=_first(booking, "booking_number"),
            name=str(row.get("name") or ""),
            anchors=skiptrace_anchors(row),
            source="skiptrace",
            relationship=str(row.get("relationship") or "skiptrace"),
            phone=str(row.get("phone") or ""),
            email=str(row.get("email") or ""),
            address=str(row.get("address") or ""),
            defendant_name=defendant,
        )
        if candidate:
            found.append(candidate)
    return {"provider": "skiptrace", "status": "ok", "count": len(found), "candidates": found}


def _public_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    summary = {
        "provider": result.get("provider"),
        "status": result.get("status"),
        "count": int(result.get("count") or 0),
    }
    if result.get("error_type"):
        summary["error_type"] = result["error_type"]
    return summary


async def _resolve_arrest(
    db: Any, booking_number: str, county: str, state: str
) -> Optional[Dict[str, Any]]:
    """The one arrest for this booking. None when it is missing or ambiguous
    (the same booking number in two counties and no county/state to pick)."""
    from dashboard.services.place_identity import mongo_place_clause, mongo_state_clause

    query: Dict[str, Any] = {"booking_number": booking_number}
    c_county, c_state = canonical_place(county, state)
    if county:
        # Matches "Lee", "lee", "Lee County" in the stored arrest.
        query = {"$and": [query, mongo_place_clause(c_county or county, c_state)]}
    elif state:
        query = {"$and": [query, mongo_state_clause(c_state or state)]}
    try:
        rows = await _find_list(db["arrests"], query, limit=2)
    except Exception as exc:
        log.warning("[indemnitor_discovery] arrest read failed error_type=%s", type(exc).__name__)
        return None
    rows = [row for row in rows if isinstance(row, dict)]
    return rows[0] if len(rows) == 1 else None


async def run_discovery(
    db: Any,
    *,
    booking_number: str,
    full_name: str = "",
    county: str = "",
    address: str = "",
    dob: str = "",
    zip_code: str = "",
    state: str = "",
    force: bool = False,
    arrest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Find co-signer candidates for one booking and store them on ``contacts``.

    The contacts doc is keyed on state + county + booking_number. When the
    caller does not give all three and the arrests collection does not pin
    exactly one row, nothing is read or written (``identity_ambiguous``).

    Completed runs are reused unless ``force`` is set. Provider failures are
    recorded as status codes. A rate-limited provider makes the run
    ``deferred`` so the booking is retried. Never raises for a provider error.
    """
    booking_number = str(booking_number or "").strip()
    contacts = db["contacts"]

    stored = dict(arrest or {})
    if not stored and booking_number and not (state and county):
        found = await _resolve_arrest(db, booking_number, county, state)
        if found is None:
            return {
                "cached": False,
                "contacts": [],
                "discovery_status": "identity_ambiguous",
                "providers_run": [],
            }
        stored = dict(found)
    elif not stored and booking_number:
        found = await _resolve_arrest(db, booking_number, county, state)
        if isinstance(found, dict):
            stored = dict(found)
    if full_name:
        stored["full_name"] = full_name
    if county:
        stored["county"] = county
    if address:
        stored["address"] = address
    if dob:
        stored["dob"] = dob
    if zip_code:
        stored["zip"] = zip_code
    if state:
        stored["state"] = state
    stored["booking_number"] = booking_number or stored.get("booking_number") or ""
    identity = contact_identity(stored.get("state"), stored.get("county"), stored["booking_number"])
    if identity is None:
        return {
            "cached": False,
            "contacts": [],
            "discovery_status": "identity_ambiguous",
            "providers_run": [],
        }

    try:
        # Resolve the target doc before any provider runs: an ambiguous legacy
        # doc means nothing is read for, or written to, this booking.
        await find_contact_doc(contacts, identity, arrests=db["arrests"])
    except ContactsLegacyAmbiguous as exc:
        return {"cached": False, "contacts": [], "discovery_status": exc.code, "providers_run": []}

    if not force:
        existing = await contacts.find_one(identity)
        if existing and existing.get("discovery_status") == "complete":
            return {
                "cached": True,
                "contacts": list(existing.get("discovered_contacts") or []),
                "discovery_status": "complete",
                "providers_run": list(existing.get("providers_run") or []),
            }

    candidates: List[Dict[str, Any]] = []
    providers_run: List[Dict[str, Any]] = []
    # Mongo-backed providers. A read failure in any of their collections means
    # the run is incomplete: the booking goes to retry, never to complete.
    read_failed = False
    try:
        prior = await _provider_prior(db, stored)
        candidates.extend(prior)
        providers_run.append({"provider": "prior_shamrock", "status": "ok", "count": len(prior)})
    except Exception as exc:
        read_failed = True
        log.warning("[indemnitor_discovery] prior provider failed error_type=%s", type(exc).__name__)
        providers_run.append({"provider": "prior_shamrock", "status": "failed", "count": 0, "error_type": type(exc).__name__})
    try:
        household = await _provider_same_address(db, stored)
        candidates.extend(household)
        providers_run.append({"provider": "same_address", "status": "ok", "count": len(household)})
    except Exception as exc:
        read_failed = True
        log.warning("[indemnitor_discovery] address provider failed error_type=%s", type(exc).__name__)
        providers_run.append({"provider": "same_address", "status": "failed", "count": 0, "error_type": type(exc).__name__})
    try:
        booked = _provider_booking_fields(stored)
        candidates.extend(booked)
        providers_run.append({"provider": "booking_fields", "status": "ok", "count": len(booked)})
    except Exception as exc:
        log.warning("[indemnitor_discovery] booking provider failed error_type=%s", type(exc).__name__)
        providers_run.append({"provider": "booking_fields", "status": "failed", "count": 0, "error_type": type(exc).__name__})

    for runner in (_provider_property, _provider_clerk, _provider_skiptrace):
        try:
            result = await runner(stored)
        except Exception as exc:
            log.warning("[indemnitor_discovery] provider crashed error_type=%s", type(exc).__name__)
            result = {"provider": getattr(runner, "__name__", "provider"), "status": "failed", "count": 0, "error_type": type(exc).__name__}
        candidates.extend(result.get("candidates") or [])
        providers_run.append(_public_summary(result))

    try:
        # A legacy booking-only doc is merged too, so its reviewed rows survive.
        previous = await find_contact_doc(contacts, identity, arrests=db["arrests"])
    except ContactsLegacyAmbiguous as exc:
        return {"cached": False, "contacts": [], "discovery_status": exc.code, "providers_run": providers_run}
    merged_existing: List[Dict[str, Any]] = []
    if isinstance(previous, dict):
        merged_existing = list(previous.get("discovered_contacts") or [])
    # Sources whose provider did not finish this run keep their prior rows.
    unfinished = [
        str(row.get("provider") or "") for row in providers_run
        if row.get("status") not in {"ok", "skipped", "skipped_unparsed"}
    ]
    merged = _merge_candidates(merged_existing, candidates, keep_sources=unfinished)
    hard_failures = [
        row for row in providers_run
        if row.get("status") not in {"ok", "skipped", "skipped_disabled", "skipped_unparsed"}
    ]
    attempted = [row for row in providers_run if row.get("status") != "skipped_disabled"]
    deferred = [row for row in providers_run if row.get("status") in DEFERRABLE_STATUSES]
    error_code: Optional[str] = None
    if read_failed:
        status = "failed"
        error_code = "prior_read_failed"
    elif deferred:
        status = "deferred"
    elif attempted and hard_failures and all(row.get("status") not in {"ok", "skipped", "skipped_unparsed"} for row in attempted):
        status = "failed"
        error_code = "providers_failed"
    else:
        status = "complete"
    now = datetime.now(timezone.utc)
    expire = unreviewed_expire_at(merged, now)
    payload = {
        **identity,
        "defendant_name": _first(stored, "full_name", "defendant_name"),
        "discovery_status": status,
        "discovery_error": error_code,
        "discovered_at": now,
        "providers_run": providers_run,
        "discovered_contacts": merged,
    }
    update: Dict[str, Any] = {"$set": payload}
    if expire:
        update["$set"]["unreviewed_expire_at"] = expire
    else:
        update["$unset"] = {"unreviewed_expire_at": ""}
    try:
        await upsert_contact_doc(contacts, identity, update, arrests=db["arrests"])
    except ContactsLegacyAmbiguous as exc:
        # Nothing written to contacts; the caller decides what to do with the arrest.
        return {
            "cached": False,
            "contacts": [],
            "discovery_status": exc.code,
            "providers_run": providers_run,
        }
    marker: Dict[str, Any] = {
        "indemnitor_discovery.status": status,
        "indemnitor_discovery.candidate_count": len(merged),
        "indemnitor_discovery.updated_at": now,
        "indemnitor_discovery.error": error_code,
    }
    if status == "deferred":
        marker["indemnitor_discovery.retry_after"] = now + timedelta(minutes=DEFER_MINUTES)
    arrest_filter = {"_id": stored["_id"]} if stored.get("_id") is not None else identity
    try:
        await db["arrests"].update_one(arrest_filter, {"$set": marker})
    except Exception as exc:
        log.warning("[indemnitor_discovery] status write failed error_type=%s", type(exc).__name__)
    log.info(
        "[indemnitor_discovery] status=%s candidates=%s providers=%s",
        status,
        len(merged),
        len(providers_run),
    )
    return {
        "cached": False,
        "contacts": merged,
        "discovery_status": status,
        "discovery_error": error_code,
        "providers_run": providers_run,
    }


def _lee_claim_query(now: datetime) -> Dict[str, Any]:
    from dashboard.services.place_identity import mongo_place_clause

    recent = now - timedelta(hours=RECENT_MISSING_HOURS)
    stale = now - timedelta(minutes=STALE_RUNNING_MINUTES)
    return {
        "$and": [
            mongo_place_clause("Lee", "FL"),
            {"$or": [
                {"indemnitor_discovery.status": "queued"},
                {
                    # Rate-limited last time: retry after the cooldown.
                    "indemnitor_discovery.status": "deferred",
                    "indemnitor_discovery.retry_after": {"$lte": now},
                },
                {
                    # Transient failure last time: retry after the backoff.
                    "indemnitor_discovery.status": "retry",
                    "indemnitor_discovery.retry_after": {"$lte": now},
                },
                {
                    "indemnitor_discovery.status": "running",
                    "indemnitor_discovery.updated_at": {"$lte": stale},
                },
                {
                    "indemnitor_discovery.status": {"$exists": False},
                    "first_seen_at": {"$gte": recent},
                },
            ]},
        ]
    }


def retry_backoff(attempt: int) -> timedelta:
    """Wait before retry number ``attempt`` (1-based): 15, 30, 60 ... minutes."""
    return timedelta(minutes=RETRY_BASE_MINUTES * (2 ** max(0, attempt - 1)))


async def _mark_retry_or_failed(
    arrests: Any, arrest: Dict[str, Any], marker: Dict[str, Any], error: str, now: datetime
) -> bool:
    """Leave the booking retryable after a transient failure, or terminal
    ``failed`` once MAX_DISCOVERY_ATTEMPTS runs have failed. True = will retry."""
    attempts = int(marker.get("attempts") or 0) + 1
    retrying = attempts < MAX_DISCOVERY_ATTEMPTS
    fields: Dict[str, Any] = {
        "indemnitor_discovery.status": "retry" if retrying else "failed",
        "indemnitor_discovery.attempts": attempts,
        "indemnitor_discovery.updated_at": datetime.now(timezone.utc),
        "indemnitor_discovery.error": error,
    }
    if retrying:
        fields["indemnitor_discovery.retry_after"] = now + retry_backoff(attempts)
    else:
        log.warning(
            "[indemnitor_discovery] retry limit reached attempts=%s error_type=%s",
            attempts,
            error,
        )
    try:
        await arrests.update_one({"_id": arrest["_id"]}, {"$set": fields})
    except Exception as write_exc:
        log.warning(
            "[indemnitor_discovery] failure mark skipped error_type=%s",
            type(write_exc).__name__,
        )
    return retrying


async def _mark_terminal(arrests: Any, arrest: Dict[str, Any], code: str) -> None:
    log.warning("[indemnitor_discovery] booking not resolvable code=%s", code)
    try:
        await arrests.update_one({"_id": arrest["_id"]}, {"$set": {
            "indemnitor_discovery.status": "failed",
            "indemnitor_discovery.error": code,
            "indemnitor_discovery.updated_at": datetime.now(timezone.utc),
        }})
    except Exception as exc:
        log.warning("[indemnitor_discovery] failure mark skipped error_type=%s", type(exc).__name__)


async def run_lee_discovery_batch(db: Any, *, limit: int = DISCOVERY_BATCH_LIMIT, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Claim a few Lee bookings, run discovery, then purge expired unreviewed candidates."""
    now = now or datetime.now(timezone.utc)
    purged = await purge_unreviewed_candidates(db, now=now)
    summary = {
        "enabled": discovery_enabled(),
        "claimed": 0,
        "completed": 0,
        "deferred": 0,
        "retry": 0,
        "failed": 0,
        "purged": purged.get("purged_candidates", 0),
    }
    if not discovery_enabled():
        log.info("[indemnitor_discovery] disabled purged=%s", summary["purged"])
        return summary
    arrests = db["arrests"]
    try:
        cursor = arrests.find(_lee_claim_query(now))
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("first_seen_at", 1)
        if hasattr(cursor, "limit"):
            cursor = cursor.limit(limit)
        if hasattr(cursor, "to_list"):
            rows = cursor.to_list(length=limit)
            if asyncio.iscoroutine(rows):
                rows = await rows
            pending = list(rows or [])
        else:
            pending = []
    except Exception as exc:
        log.warning("[indemnitor_discovery] claim query failed error_type=%s", type(exc).__name__)
        summary["failed"] = 1
        return summary

    for arrest in pending:
        if not isinstance(arrest, dict) or not arrest.get("_id"):
            continue
        marker = arrest.get("indemnitor_discovery") or {}
        status = marker.get("status")
        deferrals = int(marker.get("deferrals") or 0)
        if status in {"queued", "running", "deferred", "retry"}:
            claim_filter: Dict[str, Any] = {
                "_id": arrest["_id"],
                "indemnitor_discovery.status": status,
            }
        else:
            claim_filter = {
                "_id": arrest["_id"],
                "indemnitor_discovery.status": {"$exists": False},
            }
        try:
            claimed = await arrests.update_one(
                claim_filter,
                {"$set": {
                    "indemnitor_discovery.status": "running",
                    "indemnitor_discovery.updated_at": now,
                    "indemnitor_discovery.deferrals": deferrals + (1 if status == "deferred" else 0),
                }},
            )
        except Exception as exc:
            log.warning("[indemnitor_discovery] claim failed error_type=%s", type(exc).__name__)
            summary["failed"] += 1
            continue
        modified = getattr(claimed, "modified_count", 0)
        if modified != 1:
            continue
        summary["claimed"] += 1
        try:
            result = await run_discovery(
                db,
                booking_number=str(arrest.get("booking_number") or ""),
                force=True,
                arrest=arrest,
            )
            outcome = result.get("discovery_status")
            if outcome == "deferred" and deferrals + 1 >= MAX_DEFERRALS:
                # Stop retrying a booking the source keeps rate-limiting.
                await arrests.update_one(
                    {"_id": arrest["_id"]},
                    {"$set": {
                        "indemnitor_discovery.status": "complete",
                        "indemnitor_discovery.error": "deferred_limit",
                    }},
                )
                outcome = "complete"
            if outcome == "deferred":
                summary["deferred"] += 1
            elif outcome == "failed":
                # A Mongo read failed or every provider failed: transient, retry.
                error = str(result.get("discovery_error") or "providers_failed")
                retrying = await _mark_retry_or_failed(arrests, arrest, marker, error, now)
                summary["retry" if retrying else "failed"] += 1
            elif outcome in {"identity_ambiguous", ContactsLegacyAmbiguous.code}:
                # Cannot be resolved by retrying. Terminal, so a "running"
                # marker is not re-claimed forever. Code only, no booking.
                await _mark_terminal(arrests, arrest, str(outcome))
                summary["failed"] += 1
            else:
                summary["completed"] += 1
        except Exception as exc:
            log.warning("[indemnitor_discovery] run failed error_type=%s", type(exc).__name__)
            retrying = await _mark_retry_or_failed(arrests, arrest, marker, type(exc).__name__, now)
            summary["retry" if retrying else "failed"] += 1
    log.info(
        "[indemnitor_discovery] claimed=%s completed=%s retry=%s failed=%s purged=%s",
        summary["claimed"],
        summary["completed"],
        summary["retry"],
        summary["failed"],
        summary["purged"],
    )
    return summary


def _expired_unreviewed_query(cutoff: datetime) -> Dict[str, Any]:
    """Contacts docs holding at least one unreviewed row discovered at or before
    ``cutoff``. ``discovered_at`` is a UTC ISO string (make_candidate); legacy
    rows may hold a BSON date, so both forms are matched."""
    def _row(stamp: Any) -> Dict[str, Any]:
        return {"discovered_contacts": {"$elemMatch": {
            "review_status": "unreviewed",
            "discovered_at": {"$lte": stamp},
        }}}
    return {"$or": [_row(cutoff.isoformat()), _row(cutoff)]}


async def purge_unreviewed_candidates(
    db: Any, *, now: Optional[datetime] = None, limit: int = 200, max_pages: int = 50
) -> Dict[str, int]:
    """Drop unreviewed candidates older than 30 days. Reviewed rows stay.

    The query selects only docs with an expired unreviewed row, a page of
    ``limit`` at a time. A purged doc stops matching, and every doc already
    read is excluded, so each page is new. Pages repeat until one comes back
    short (or ``max_pages``; the rest is picked up on the next cron run).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=UNREVIEWED_TTL_DAYS)
    contacts = db["contacts"]
    purged = 0
    docs = 0
    seen: List[Any] = []
    for _page in range(max(1, max_pages)):
        query = _expired_unreviewed_query(cutoff)
        if seen:
            query = {"$and": [query, {"_id": {"$nin": list(seen)}}]}
        try:
            cursor = contacts.find(query)
            if hasattr(cursor, "limit"):
                cursor = cursor.limit(limit)
            if hasattr(cursor, "to_list"):
                rows = cursor.to_list(length=limit)
                if asyncio.iscoroutine(rows):
                    rows = await rows
                stored = list(rows or [])
            else:
                stored = []
        except Exception as exc:
            log.warning("[indemnitor_discovery] purge read failed error_type=%s", type(exc).__name__)
            break
        seen.extend(doc.get("_id") for doc in stored if isinstance(doc, dict))
        page_purged, page_docs = await _purge_docs(db, contacts, stored, cutoff, now)
        purged += page_purged
        docs += page_docs
        if len(stored) < limit:
            break
    if purged:
        log.info("[indemnitor_discovery] purged_candidates=%s docs=%s", purged, docs)
    return {"purged_candidates": purged, "docs": docs}


async def _purge_docs(
    db: Any, contacts: Any, stored: Sequence[Any], cutoff: datetime, now: datetime
) -> tuple:
    purged = 0
    docs = 0
    for doc in stored:
        if not isinstance(doc, dict):
            continue
        kept: List[Dict[str, Any]] = []
        dropped = 0
        for row in doc.get("discovered_contacts") or []:
            if not isinstance(row, dict):
                continue
            stamp = _parse_time(row.get("discovered_at"))
            if row.get("review_status") == "unreviewed" and stamp and stamp <= cutoff:
                dropped += 1
                continue
            kept.append(row)
        if not dropped:
            continue
        expire = unreviewed_expire_at(kept, now)
        update: Dict[str, Any] = {"$set": {"discovered_contacts": kept}}
        if expire:
            update["$set"]["unreviewed_expire_at"] = expire
        else:
            update["$unset"] = {"unreviewed_expire_at": ""}
        try:
            await contacts.update_one({"_id": doc.get("_id")}, update)
            identity = contact_identity(doc.get("state"), doc.get("county"), doc.get("booking_number"))
            if identity:
                await db["arrests"].update_one(
                    identity,
                    {"$set": {
                        "indemnitor_discovery.candidate_count": len(kept),
                        "indemnitor_discovery.updated_at": now,
                    }},
                )
        except Exception as exc:
            log.warning("[indemnitor_discovery] purge write failed error_type=%s", type(exc).__name__)
            continue
        purged += dropped
        docs += 1
    return purged, docs
