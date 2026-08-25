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
    dob = _pick(doc, ("dob", "DOB", "date_of_birth")) or _pick(defendant, ("dob", "DOB"))
    address = _pick(doc, ("address", "Address", "defendant_address")) or _pick(
        defendant, ("address", "Address")
    )
    phone = _pick(doc, _PHONE_FIELDS) or _pick(defendant, ("phone", "Phone"))
    indemnitor_name = _pick(doc, ("indemnitor_name", "IndemnitorName")) or _pick(
        indemnitor, ("name", "full_name", "firstName")
    )
    state = _pick(doc, ("state", "State")) or _pick(defendant, ("state", "State")) or "FL"
    surety = _pick(doc, ("surety_id", "Surety", "surety", "carrier")).lower()
    return {
        "full_name": name,
        "first_name": first,
        "last_name": last,
        "booking_number": identity,
        "county": county,
        "state": state,
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
        "dob": dob,
        "address": address,
        "phone": phone,
        "scraped_at": when,
        "created_at": when,
        "is_past_bond": True,
        "bond_source": source,
        "surety_id": surety if surety in ("osi", "palmetto") else "",
        "indemnitor_name": indemnitor_name,
        "premium_amount": _money(
            doc.get("premium_amount") or doc.get("PremiumAmount") or doc.get("premium")
        ),
        "defendant": {
            "name": name,
            "first_name": first,
            "last_name": last,
            "dob": dob,
            "phone": phone,
            "email": _pick(doc, ("email", "Email", "defendant_email"))
            or _pick(defendant, ("email", "Email")),
            "address": address,
            "city": _pick(doc, ("city", "City")) or _pick(defendant, ("city", "City")),
            "state": state,
            "zip": _pick(doc, ("zip", "Zip", "zip_code")) or _pick(defendant, ("zip", "Zip")),
            "dl": _pick(doc, ("dl", "DL", "dl_number", "defendant_dl"))
            or _pick(defendant, ("dl", "DL", "dl_number")),
            "dl_state": _pick(doc, ("dl_state", "DL_State")) or _pick(defendant, ("dl_state", "DL_State")),
            "ssn": _pick(doc, ("ssn", "SSN")) or _pick(defendant, ("ssn", "SSN")),
            "employer": _pick(doc, ("employer", "Employer", "EmployerInfo"))
            or _pick(defendant, ("employer", "Employer")),
        },
        "indemnitor": {
            "name": indemnitor_name,
            "first_name": _pick(indemnitor, ("first_name", "firstName", "FirstName")),
            "last_name": _pick(indemnitor, ("last_name", "lastName", "LastName")),
            "phone": _pick(doc, ("indemnitor_phone", "IndemnitorPhone"))
            or _pick(indemnitor, ("phone", "Phone")),
            "email": _pick(doc, ("indemnitor_email", "IndemnitorEmail"))
            or _pick(indemnitor, ("email", "Email")),
            "address": _pick(doc, ("indemnitor_address", "IndemnitorAddress"))
            or _pick(indemnitor, ("address", "Address")),
            "city": _pick(indemnitor, ("city", "City")),
            "state": _pick(indemnitor, ("state", "State")),
            "zip": _pick(indemnitor, ("zip", "Zip")),
            "dob": _pick(doc, ("indemnitor_dob", "IndemnitorDOB"))
            or _pick(indemnitor, ("dob", "DOB")),
            "dl": _pick(doc, ("indemnitor_dl", "IndemnitorDL"))
            or _pick(indemnitor, ("dl", "DL", "dl_number")),
            "dl_state": _pick(indemnitor, ("dl_state", "DL_State")),
            "ssn": _pick(doc, ("indemnitor_ssn")) or _pick(indemnitor, ("ssn", "SSN")),
            "employer": _pick(doc, ("indemnitor_employer")) or _pick(indemnitor, ("employer", "Employer")),
            "employer_phone": _pick(indemnitor, ("employer_phone", "employerPhone")),
            "relationship": _pick(doc, ("relationship", "Relationship"))
            or _pick(indemnitor, ("relationship", "relation")),
            "vehicle_year": _pick(indemnitor, ("vehicle_year", "year")),
            "vehicle_make": _pick(indemnitor, ("vehicle_make", "make")),
            "vehicle_model": _pick(indemnitor, ("vehicle_model", "model")),
            "vehicle_color": _pick(indemnitor, ("vehicle_color", "color")),
            "ref1Name": _pick(indemnitor, ("ref1Name", "reference_1_name")),
            "ref1Phone": _pick(indemnitor, ("ref1Phone", "reference_1_phone")),
            "ref1Address": _pick(indemnitor, ("ref1Address", "reference_1_address")),
            "ref1Relation": _pick(indemnitor, ("ref1Relation", "reference_1_relation")),
            "ref2Name": _pick(indemnitor, ("ref2Name", "reference_2_name")),
            "ref2Phone": _pick(indemnitor, ("ref2Phone", "reference_2_phone")),
        },
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


_FILL_DEFENDANT_KEYS = (
    "address", "city", "state", "zip", "phone", "email", "dl", "dl_state",
    "ssn", "employer", "dob",
)
_FILL_INDEMNITOR_KEYS = (
    "name", "first_name", "last_name", "phone", "email", "address", "city",
    "state", "zip", "dob", "dl", "dl_state", "ssn", "employer", "relationship",
    "employer_phone", "vehicle_year", "vehicle_make", "vehicle_model",
    "vehicle_color", "ref1Name", "ref1Phone", "ref1Address", "ref1Relation",
    "ref2Name", "ref2Phone",
)


def _norm_person_name(value: str) -> str:
    text = re.sub(r"[^a-z0-9\s]", " ", (value or "").lower())
    return " ".join(text.split())


def suggest_surety_id(state: str = "", prior_surety: str = "") -> str:
    """Staff still confirms. Palmetto for out-of-Florida; else last surety; else OSI."""
    st = (state or "").strip().upper()
    if st and st not in ("FL", "FLORIDA"):
        return "palmetto"
    prior = (prior_surety or "").strip().lower()
    if prior in ("osi", "palmetto"):
        return prior
    return "osi"


def apply_prior_bond_gaps(
    context: Dict[str, Any],
    prior: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill empty defendant/indemnitor keys from a prior Shamrock bond.

    Never copies POA, case number, booking, bond amount, charges, or clerk flags.
    Live booking facts always win.
    """
    ctx = dict(context or {})
    def_ = dict(ctx.get("defendant") or {})
    ind = dict(ctx.get("indemnitor") or {})
    prior_def = prior.get("defendant") if isinstance(prior.get("defendant"), dict) else {}
    prior_ind = prior.get("indemnitor") if isinstance(prior.get("indemnitor"), dict) else {}
    filled: List[str] = []

    for key in _FILL_DEFENDANT_KEYS:
        if not _text(def_.get(key)) and _text(prior_def.get(key)):
            def_[key] = prior_def[key]
            filled.append(f"defendant.{key}")
    for key in _FILL_INDEMNITOR_KEYS:
        if not _text(ind.get(key)) and _text(prior_ind.get(key)):
            ind[key] = prior_ind[key]
            filled.append(f"indemnitor.{key}")

    ctx["defendant"] = def_
    ctx["indemnitor"] = ind
    state = ctx.get("state") or def_.get("state") or prior.get("state") or "FL"
    suggested = suggest_surety_id(state, prior.get("surety_id") or "")
    sources = ctx.get("sources") or []
    has_bound_surety = "bond" in sources or "packet" in sources
    if not has_bound_surety and suggested in ("osi", "palmetto"):
        if suggested == "palmetto" or not ctx.get("surety_id"):
            ctx["surety_id"] = suggested
            ctx["surety_suggested_from"] = (
                "out_of_state" if suggested == "palmetto" and str(state).upper() not in ("FL", "FLORIDA", "")
                else "prior_bond"
            )
    ctx["prior_bond"] = {
        "found": True,
        "source": prior.get("bond_source") or "",
        "defendant_name": prior.get("full_name") or prior_def.get("name") or "",
        "indemnitor_name": ind.get("name") or prior.get("indemnitor_name") or "",
        "bond_amount": prior.get("bond_amount") or 0,
        "surety_id": prior.get("surety_id") or "",
        "county": prior.get("county") or "",
        "posted_date": prior.get("arrest_date") or prior.get("booking_date") or "",
        "filled_keys": filled,
        "suggested_surety": suggested,
    }
    return ctx


def _name_first_last(value: str) -> tuple[str, str]:
    raw = (value or "").strip()
    if "," in raw:
        last, rest = [part.strip() for part in raw.split(",", 1)]
        first = rest.split()[0] if rest else ""
        return _norm_person_name(first), _norm_person_name(last)
    parts = _norm_person_name(raw).split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], parts[-1]


def _prior_score(lead: Dict[str, Any], name: str, dob: str) -> int:
    score = 0
    want_first, want_last = _name_first_last(name)
    have_first, have_last = _name_first_last(lead.get("full_name") or "")
    if not want_last or not have_last:
        return 0
    if want_last == have_last:
        score += 50
    if want_first and have_first and want_first == have_first:
        score += 20
    want_dob = re.sub(r"[^0-9]", "", dob or "")
    have_dob = re.sub(r"[^0-9]", "", str(lead.get("dob") or (lead.get("defendant") or {}).get("dob") or ""))
    if want_dob and have_dob and (want_dob == have_dob or want_dob[-8:] == have_dob[-8:]):
        score += 30
    if _text((lead.get("indemnitor") or {}).get("name") or lead.get("indemnitor_name")):
        score += 10
    if float(lead.get("bond_amount") or 0) > 0:
        score += 5
    return score


async def find_prior_bond_for_defendant(
    name: str,
    *,
    dob: str = "",
    county: str = "",
    exclude_booking: str = "",
) -> Optional[Dict[str, Any]]:
    """Best prior Shamrock bond for this defendant (name/DOB), excluding the current booking."""
    q = (name or "").strip()
    if len(q) < 2:
        return None
    # Search statewide — returning clients often reappear in a different county.
    hits = await search_past_bonds(q, county="", limit=20)
    exclude = str(exclude_booking or "").strip().lower()
    ranked: List[tuple[int, Dict[str, Any]]] = []
    for lead in hits:
        booking = str(lead.get("booking_number") or "").strip().lower()
        if exclude and booking == exclude:
            continue
        score = _prior_score(lead, q, dob)
        if score < 50:
            continue
        ranked.append((score, lead))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (
        item[0],
        str(item[1].get("scraped_at") or item[1].get("created_at") or ""),
    ), reverse=True)
    return ranked[0][1]
