"""
Contact Discovery Service — ShamrockLeads co-signer candidates.

A new Lee (FL) booking is queued after the arrest write. The dashboard cron
reads Shamrock's own rows and, only when enabled, public HTTPS sources.
A candidate is kept only when a name is paired with a phone, a DOB, or a
normalized address + ZIP. Nothing in this module calls, texts, or emails.
"""

import asyncio
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
                       address: str = None, dob: str = None):
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
_HTTP_GAP_S = 2.0
_last_http_monotonic = 0.0

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


def _merge_candidates(existing: Sequence[Dict[str, Any]], fresh: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prior = {
        (name_key(str(row.get("name") or "")), str(row.get("source") or "")): row
        for row in existing
    }
    merged: List[Dict[str, Any]] = []
    seen = set()
    for row in fresh:
        key = (name_key(str(row.get("name") or "")), str(row.get("source") or ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        previous = prior.get(key)
        if previous and previous.get("review_status") == "reviewed":
            row = dict(row)
            row["review_status"] = "reviewed"
            if previous.get("candidate_id"):
                row["candidate_id"] = previous["candidate_id"]
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
    stamps = [_parse_time(row.get("discovered_at")) or now for row in candidates]
    return min(stamps) + timedelta(days=UNREVIEWED_TTL_DAYS)


async def _provider_prior(db: Any, booking: Dict[str, Any]) -> List[Dict[str, Any]]:
    defendant = _first(booking, "full_name", "defendant_name")
    booking_number = _first(booking, "booking_number")
    variants = exact_name_variants(defendant)
    name_clause: List[Dict[str, Any]] = []
    if variants:
        name_clause = [
            {"defendant_name": {"$in": variants}},
            {"defendants_bonded": {"$in": variants}},
            {"person_name": {"$in": variants}},
        ]
    booking_clause = [{"booking_number": booking_number}] if booking_number else []
    query: Dict[str, Any] = {"$or": booking_clause + name_clause} if (booking_clause or name_clause) else {}
    if not query:
        return []
    found: List[Dict[str, Any]] = []
    for collection_name in ("indemnitors", "intakes", "family_graph", "family_relationships"):
        try:
            rows = await _find_list(db[collection_name], query, limit=25)
        except Exception as exc:
            log.warning(
                "[indemnitor_discovery] prior read failed collection=%s error_type=%s",
                collection_name,
                type(exc).__name__,
            )
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            same_case = bool(booking_number) and _row_booking(row) == booking_number
            defendant_names = _as_names(row.get("defendant_name")) + _as_names(row.get("defendants_bonded"))
            defendant_names += _as_names(row.get("person_name"))
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


async def _provider_same_address(db: Any, booking: Dict[str, Any]) -> List[Dict[str, Any]]:
    defendant = _first(booking, "full_name", "defendant_name")
    booking_number = _first(booking, "booking_number")
    key = address_key(booking.get("address"), booking.get("zip"))
    zip5 = normalize_zip(booking.get("zip"))
    if not key or not zip5:
        return []
    query = {"zip": {"$in": [zip5, booking.get("zip")]}, "booking_number": {"$ne": booking_number}}
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
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if address_key(row.get("address"), row.get("zip")) != key:
                continue
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


def _respect_http_gap() -> None:
    global _last_http_monotonic
    gap = float(os.getenv("INDEMNITOR_DISCOVERY_HTTP_GAP_S", str(_HTTP_GAP_S)))
    now = time.monotonic()
    if gap > 0 and _last_http_monotonic and (now - _last_http_monotonic) < gap:
        raise DiscoveryRateLimited("discovery_http_gap")
    _last_http_monotonic = now


def public_request(method: str, url: str):
    """One plain HTTPS call. Sheriff hosts stop while the Lee cooldown is active.

    Raises DiscoveryRateLimited, EgressBlocked, or SourceTLSInvalid. The response
    body is not logged.
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
    _respect_http_gap()
    session = make_session()
    try:
        response = request(session, method, url, county="Lee", timeout=10)
    except EgressBlocked as exc:
        if "HTTP 429" in str(exc) and "sheriffleefl.org" in host:
            record_429("HTTP 429")
            raise DiscoveryRateLimited("lee_rate_limited") from exc
        raise
    return response


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
        response = public_request("GET", url)
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
        response = public_request("GET", url)
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
    try:
        from scrapers.plain_http import make_session, request

        _respect_http_gap()
        session = make_session({"Authorization": f"Bearer {key}", "Accept": "application/json"})
        response = request(session, "POST", url, county="Lee", timeout=10, json=payload)
        body = response.json() if response.content else {}
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
            anchors=row.get("matched_anchors") or [],
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

    Completed runs are reused unless ``force`` is set. Provider failures are
    recorded as status codes. This function does not raise for a provider error.
    """
    booking_number = str(booking_number or "").strip()
    contacts = db["contacts"]
    if not force and booking_number:
        existing = await contacts.find_one({"booking_number": booking_number})
        if existing and existing.get("discovery_status") == "complete":
            return {
                "cached": True,
                "contacts": list(existing.get("discovered_contacts") or []),
                "discovery_status": "complete",
                "providers_run": list(existing.get("providers_run") or []),
            }

    stored = dict(arrest or {})
    if not stored and booking_number:
        try:
            found = await db["arrests"].find_one({"booking_number": booking_number})
        except Exception as exc:
            log.warning("[indemnitor_discovery] arrest read failed error_type=%s", type(exc).__name__)
            found = None
        if isinstance(found, dict):
            stored = found
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

    candidates: List[Dict[str, Any]] = []
    providers_run: List[Dict[str, Any]] = []
    try:
        prior = await _provider_prior(db, stored)
        candidates.extend(prior)
        providers_run.append({"provider": "prior_shamrock", "status": "ok", "count": len(prior)})
    except Exception as exc:
        log.warning("[indemnitor_discovery] prior provider failed error_type=%s", type(exc).__name__)
        providers_run.append({"provider": "prior_shamrock", "status": "failed", "count": 0, "error_type": type(exc).__name__})
    try:
        household = await _provider_same_address(db, stored)
        candidates.extend(household)
        providers_run.append({"provider": "same_address", "status": "ok", "count": len(household)})
    except Exception as exc:
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

    merged_existing: List[Dict[str, Any]] = []
    if booking_number:
        previous = await contacts.find_one({"booking_number": booking_number})
        if isinstance(previous, dict):
            merged_existing = list(previous.get("discovered_contacts") or [])
    merged = _merge_candidates(merged_existing, candidates)
    hard_failures = [
        row for row in providers_run
        if row.get("status") not in {"ok", "skipped", "skipped_disabled", "skipped_unparsed"}
    ]
    attempted = [row for row in providers_run if row.get("status") != "skipped_disabled"]
    if attempted and hard_failures and all(row.get("status") not in {"ok", "skipped", "skipped_unparsed"} for row in attempted):
        status = "failed"
    else:
        status = "complete"
    now = datetime.now(timezone.utc)
    expire = unreviewed_expire_at(merged, now)
    payload = {
        "booking_number": booking_number,
        "county": stored.get("county") or "",
        "state": stored.get("state") or "",
        "defendant_name": _first(stored, "full_name", "defendant_name"),
        "discovery_status": status,
        "discovery_error": None,
        "discovered_at": now,
        "providers_run": providers_run,
        "discovered_contacts": merged,
    }
    update: Dict[str, Any] = {"$set": payload}
    if expire:
        update["$set"]["unreviewed_expire_at"] = expire
        update["$unset"] = {}
    else:
        update["$unset"] = {"unreviewed_expire_at": ""}
    if not update["$unset"]:
        update.pop("$unset", None)
    if booking_number:
        await contacts.update_one({"booking_number": booking_number}, update, upsert=True)
        arrest_filter: Dict[str, Any] = {"booking_number": booking_number}
        if stored.get("state"):
            arrest_filter["state"] = stored.get("state")
        if stored.get("county"):
            arrest_filter["county"] = stored.get("county")
        try:
            await db["arrests"].update_one(
                arrest_filter,
                {"$set": {
                    "indemnitor_discovery.status": status,
                    "indemnitor_discovery.candidate_count": len(merged),
                    "indemnitor_discovery.updated_at": now,
                    "indemnitor_discovery.error": None,
                }},
            )
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


async def run_lee_discovery_batch(db: Any, *, limit: int = DISCOVERY_BATCH_LIMIT, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Claim a few Lee bookings, run discovery, then purge expired unreviewed candidates."""
    now = now or datetime.now(timezone.utc)
    purged = await purge_unreviewed_candidates(db, now=now)
    summary = {
        "enabled": discovery_enabled(),
        "claimed": 0,
        "completed": 0,
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
        status = ((arrest.get("indemnitor_discovery") or {}).get("status"))
        if status in {"queued", "running"}:
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
            if result.get("discovery_status") == "failed":
                summary["failed"] += 1
            else:
                summary["completed"] += 1
        except Exception as exc:
            log.warning("[indemnitor_discovery] run failed error_type=%s", type(exc).__name__)
            summary["failed"] += 1
            try:
                await arrests.update_one(
                    {"_id": arrest["_id"]},
                    {"$set": {
                        "indemnitor_discovery.status": "failed",
                        "indemnitor_discovery.updated_at": datetime.now(timezone.utc),
                        "indemnitor_discovery.error": type(exc).__name__,
                    }},
                )
            except Exception as write_exc:
                log.warning(
                    "[indemnitor_discovery] failure mark skipped error_type=%s",
                    type(write_exc).__name__,
                )
    log.info(
        "[indemnitor_discovery] claimed=%s completed=%s failed=%s purged=%s",
        summary["claimed"],
        summary["completed"],
        summary["failed"],
        summary["purged"],
    )
    return summary


async def purge_unreviewed_candidates(db: Any, *, now: Optional[datetime] = None, limit: int = 200) -> Dict[str, int]:
    """Drop unreviewed candidates older than 30 days. Reviewed rows stay."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=UNREVIEWED_TTL_DAYS)
    contacts = db["contacts"]
    purged = 0
    docs = 0
    try:
        cursor = contacts.find({"discovered_contacts.review_status": "unreviewed"})
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
        return {"purged_candidates": 0, "docs": 0}
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
            await contacts.update_one({"_id": doc.get("_id"), "booking_number": doc.get("booking_number")}, update)
            if doc.get("booking_number"):
                await db["arrests"].update_one(
                    {"booking_number": doc.get("booking_number")},
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
    if purged:
        log.info("[indemnitor_discovery] purged_candidates=%s docs=%s", purged, docs)
    return {"purged_candidates": purged, "docs": docs}
