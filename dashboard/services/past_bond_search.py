"""Search historical and active Shamrock bonds as defendant records.

Past clients live in Mongo (`active_bonds`, GAS `bonds`, OCR `HistoricalBonds`)
but the Defendants tab only queried live jail scrapes. This module maps those
bond documents into the same shape the dashboard already renders.

Search is gated on a query of 2+ characters so browsing the jail roster
does not dump years of history into the newest-first grid.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from dashboard.deps import get_collection
from dashboard.routers.helpers import serialize_doc

logger = logging.getLogger(__name__)

PAST_BOND_COLLECTIONS = (
    "active_bonds",
    "bonds",
    "bond_cases",
    "HistoricalBonds",
    "historical_bonds",
)

_NAME_FIELDS = (
    "defendant_name",
    "DefendantName",
    "FullName",
    "full_name",
    "LastName",
    "last_name",
    "FirstName",
    "first_name",
    "defendant.name",
    "defendant.full_name",
    "defendant.last_name",
)
_BOOKING_FIELDS = (
    "booking_number",
    "BookingNumber",
    "Booking_Number",
    "defendant.booking_number",
)
_CASE_FIELDS = ("case_number", "CaseNumber", "Case_Number", "court_case_number")
_POA_FIELDS = ("poa_number", "PowerNumber", "POA_Number", "power_number", "poa")
_COUNTY_FIELDS = ("county", "County", "defendant_county", "defendant.county")
_CHARGE_FIELDS = ("charges", "Charges", "charge")
_PHONE_FIELDS = (
    "defendant_phone",
    "DefendantPhone",
    "phone",
    "Phone",
    "indemnitor_phone",
    "indemnitor.phone",
)
_INDEMNITOR_FIELDS = (
    "indemnitor_name",
    "IndemnitorName",
    "indemnitor.name",
    "indemnitor.full_name",
)
_AMOUNT_FIELDS = (
    "bond_amount",
    "LiabilityAmount",
    "liability_amount",
    "BondAmount",
    "total_bond_amount",
    "total_bond",
)
_PREMIUM_FIELDS = ("premium_amount", "PremiumAmount", "premium")
_DATE_FIELDS = (
    "posted_date",
    "BondDate",
    "bond_date",
    "created_at",
    "written_at",
    "arrest_date",
)


def _text(*vals: Any) -> str:
    for val in vals:
        if val is None:
            continue
        if isinstance(val, (dict, list, tuple, set, bool)):
            continue
        text = str(val).strip()
        if text and text.lower() not in ("none", "null", "n/a"):
            return text
    return ""


def _money(val: Any) -> float:
    if val is None or isinstance(val, (dict, list, bool)):
        return 0.0
    if isinstance(val, (int, float)):
        try:
            return float(val)
        except (TypeError, ValueError, OverflowError):
            return 0.0
    cleaned = "".join(ch for ch in str(val) if ch.isdigit() or ch in ".-")
    if cleaned in ("", ".", "-", "-."):
        return 0.0
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return 0.0


def _nested(doc: Dict[str, Any], key: str) -> Dict[str, Any]:
    val = doc.get(key)
    return val if isinstance(val, dict) else {}


def _pick(doc: Dict[str, Any], keys: Iterable[str]) -> str:
    vals: List[Any] = []
    for key in keys:
        if "." in key:
            parent, child = key.split(".", 1)
            nested = _nested(doc, parent)
            vals.append(nested.get(child))
        else:
            vals.append(doc.get(key))
    return _text(*vals)


def _charges_text(doc: Dict[str, Any]) -> str:
    raw = doc.get("charges") or doc.get("Charges") or doc.get("charge")
    if isinstance(raw, list):
        parts: List[str] = []
        for item in raw:
            if isinstance(item, dict):
                parts.append(
                    _text(
                        item.get("description"),
                        item.get("charge"),
                        item.get("Charges"),
                        item.get("statute"),
                    )
                )
            else:
                parts.append(_text(item))
        return "; ".join(p for p in parts if p)
    return _text(raw)


def defendant_name_from_bond(doc: Dict[str, Any]) -> str:
    named = _pick(doc, ("defendant_name", "DefendantName", "FullName", "full_name"))
    if not named:
        defendant = _nested(doc, "defendant")
        named = _pick(defendant, ("name", "full_name", "FullName"))
    if named:
        return named
    first = _pick(doc, ("FirstName", "first_name", "firstName")) or _pick(
        _nested(doc, "defendant"), ("first_name", "FirstName", "firstName")
    )
    last = _pick(doc, ("LastName", "last_name", "lastName")) or _pick(
        _nested(doc, "defendant"), ("last_name", "LastName", "lastName")
    )
    if last and first:
        return f"{last}, {first}"
    return " ".join(p for p in (first, last) if p).strip()


def _amount_from_doc(doc: Dict[str, Any]) -> float:
    for key in _AMOUNT_FIELDS:
        amount = _money(doc.get(key))
        if amount > 0:
            return amount
    defendant = _nested(doc, "defendant")
    return _money(defendant.get("bond_amount") or defendant.get("BondAmount"))


def bond_as_lead(doc: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Map a Mongo bond document onto the Defendants-tab lead card shape."""
    name = defendant_name_from_bond(doc)
    amount = _amount_from_doc(doc)
    booking = _pick(doc, _BOOKING_FIELDS)
    case_no = _pick(doc, _CASE_FIELDS)
    poa = _pick(doc, _POA_FIELDS)
    county = _pick(doc, _COUNTY_FIELDS)
    charges = _charges_text(doc)
    when = _pick(doc, _DATE_FIELDS)
    status = _pick(doc, ("status", "Status")) or "past_bond"
    defendant = _nested(doc, "defendant")
    indemnitor = _nested(doc, "indemnitor")
    identity = booking or poa or case_no or ""
    if not identity:
        slug = re.sub(r"[^a-z0-9]+", "-", (name or "unknown").lower()).strip("-")[:40]
        identity = f"past-{source}-{slug}-{int(amount)}"
    first = _pick(doc, ("first_name", "FirstName", "firstName")) or _pick(
        defendant, ("first_name", "FirstName")
    )
    last = _pick(doc, ("last_name", "LastName", "lastName")) or _pick(
        defendant, ("last_name", "LastName")
    )
    if not first or not last:
        parts = re.split(r"\s*,\s*", name, maxsplit=1)
        if len(parts) == 2 and not last:
            last, first = parts[0], parts[1].split()[0] if parts[1] else first
        elif " " in name and not last:
            bits = name.split()
            first = first or bits[0]
            last = last or bits[-1]
    return {
        "full_name": name,
        "first_name": first,
        "last_name": last,
        "booking_number": identity,
        "county": county,
        "state": _pick(doc, ("state", "State")) or _pick(defendant, ("state", "State")) or "FL",
        "charges": charges,
        "bond_amount": amount,
        "bond_type": _pick(doc, ("bond_type", "BondType")) or "SURETY",
        "lead_score": 0,
        "lead_status": "past_bond",
        "status": status if status.lower() not in ("", "unknown") else "Past Bond",
        "arrest_date": when,
        "booking_date": when,
        "court_date": _pick(doc, ("court_date", "CourtDate")) or "",
        "case_number": case_no,
        "poa_number": poa,
        "dob": _pick(doc, ("dob", "DOB", "date_of_birth")) or _pick(defendant, ("dob", "DOB")),
        "address": _pick(doc, ("address", "Address", "defendant_address"))
        or _pick(defendant, ("address", "Address")),
        "phone": _pick(doc, _PHONE_FIELDS) or _pick(defendant, ("phone", "Phone")),
        "scraped_at": when,
        "created_at": when,
        "is_past_bond": True,
        "bond_source": source,
        "surety_id": _pick(doc, ("surety_id", "Surety", "surety", "carrier")),
        "indemnitor_name": _pick(doc, ("indemnitor_name", "IndemnitorName"))
        or _pick(indemnitor, ("name", "full_name", "firstName")),
        "premium_amount": _money(
            doc.get("premium_amount") or doc.get("PremiumAmount") or doc.get("premium")
        ),
    }


def _parsed_amount(query_str: str) -> Optional[float]:
    cleaned = (query_str or "").replace("$", "").replace(",", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        return None
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _search_filter(query_str: str, county: str = "") -> Dict[str, Any]:
    escaped = re.escape((query_str or "").strip())
    regex = {"$regex": escaped, "$options": "i"}
    clauses: List[Dict[str, Any]] = [
        {field: regex}
        for field in (
            *_NAME_FIELDS,
            *_BOOKING_FIELDS,
            *_CASE_FIELDS,
            *_POA_FIELDS,
            *_CHARGE_FIELDS,
            *_PHONE_FIELDS,
            *_INDEMNITOR_FIELDS,
        )
    ]
    amount = _parsed_amount(query_str)
    if amount is not None:
        as_int = int(amount) if amount == int(amount) else None
        for field in (*_AMOUNT_FIELDS, *_PREMIUM_FIELDS):
            clauses.append({field: amount})
            if as_int is not None:
                clauses.append({field: as_int})
            clauses.append({field: regex})
    filt: Dict[str, Any] = {"$or": clauses}
    if county:
        name = county.split("(")[0].strip()
        name = re.sub(r"\s+County$", "", name, flags=re.I)
        if name:
            county_re = {"$regex": f"^{re.escape(name)}(?:\\s+County)?$", "$options": "i"}
            filt = {"$and": [filt, {"$or": [{field: county_re} for field in _COUNTY_FIELDS]}]}
    return filt


def _dedupe_key(lead: Dict[str, Any]) -> str:
    booking = str(lead.get("booking_number") or "").strip().lower()
    if booking and not booking.startswith("past-"):
        return f"bk:{booking}"
    poa = str(lead.get("poa_number") or "").strip().lower()
    if poa:
        return f"poa:{poa}"
    name = str(lead.get("full_name") or "").strip().lower()
    when = str(lead.get("scraped_at") or "")[:10]
    county = str(lead.get("county") or "").strip().lower()
    return f"name:{name}:{when}:{lead.get('bond_amount')}:{county}"


async def search_past_bonds(
    query_str: str,
    *,
    county: str = "",
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """Return past/active Shamrock bonds matching a defendant search string."""
    q = (query_str or "").strip()
    if len(q) < 2:
        return []
    mongo_filter = _search_filter(q, county=county)
    found: List[Dict[str, Any]] = []
    seen: set[str] = set()
    per_col = max(8, min(limit, 40))
    for name in PAST_BOND_COLLECTIONS:
        try:
            col = get_collection(name)
        except Exception:
            continue
        try:
            cursor = col.find(mongo_filter).limit(per_col)
            async for doc in cursor:
                lead = bond_as_lead(serialize_doc(doc), name)
                if not lead.get("full_name") and not lead.get("poa_number"):
                    continue
                if not lead.get("full_name"):
                    lead["full_name"] = lead.get("poa_number") or "Past Bond"
                key = _dedupe_key(lead)
                if key in seen:
                    continue
                seen.add(key)
                found.append(lead)
                if len(found) >= limit:
                    return found
        except Exception as exc:
            logger.debug("past bond search skipped collection %s: %s", name, type(exc).__name__)
    return found


def unique_past_count(live: List[Dict[str, Any]], past: List[Dict[str, Any]]) -> int:
    """How many past-bond hits are not already on the live jail page."""
    seen = {_dedupe_key(row) for row in live}
    n = 0
    for row in past:
        key = _dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        n += 1
    return n


def merge_leads_with_past_bonds(
    live: List[Dict[str, Any]],
    past: List[Dict[str, Any]],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    """Unique past-bond hits first, then live jail scrapes.

    Live records that share a booking/POA with a past bond are kept (not
    replaced), so a current booking is never buried by history.
    """
    live_keys = {_dedupe_key(row) for row in live}
    extra: List[Dict[str, Any]] = []
    seen: set[str] = set(live_keys)
    for row in past:
        key = _dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        extra.append(row)
    out = extra + list(live)
    return out[:limit]
