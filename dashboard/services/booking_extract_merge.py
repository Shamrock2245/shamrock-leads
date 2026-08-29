"""Merge a jail-roster bookmarklet extract onto an ArrestLead.

Identity:
  County + Booking_Number is the match key.
  Name mismatch or two+ hits fail closed.
  Never creates a BondCase, packet, POA, DocuSeal submission, or client contact.
  Never invents POA numbers or case numbers (booking-as-case is stripped).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from dashboard.bond_pdf_service import _is_booking_as_case, _split_court_datetime
from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

FIRST_NAME_PREFIX_LEN = 3
_MAX_PAYLOAD_BYTES = 100_000


class BookingExtractError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def bare_county(val: Any) -> str:
    s = str(val or "").strip()
    s = re.sub(r"\s*\([A-Za-z]{2}\)\s*$", "", s)
    s = re.sub(r"\s+county\s*$", "", s, flags=re.I)
    return s.strip()


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().upper())


def parse_name_parts(full_name: str) -> tuple[str, str]:
    """Return (first, last) from LAST, FIRST MIDDLE or FIRST MIDDLE LAST."""
    name = _normalize_name(full_name)
    if not name:
        return ("", "")
    if "," in name:
        last_name, rest = [p.strip() for p in name.split(",", 1)]
        first_name = rest.split()[0] if rest else ""
        return (first_name, last_name)
    parts = name.split()
    if len(parts) == 1:
        return ("", parts[0])
    return (parts[0], parts[-1])


def names_agree(existing_name: str, incoming_name: str) -> bool:
    """Fail closed on a clear last-name clash. Allow empty existing (fill)."""
    incoming = _normalize_name(incoming_name)
    existing = _normalize_name(existing_name)
    if not incoming:
        return False
    if not existing:
        return True
    if existing == incoming:
        return True

    ex_first, ex_last = parse_name_parts(existing)
    in_first, in_last = parse_name_parts(incoming)
    if not ex_last or not in_last:
        return False
    if ex_last != in_last:
        return False
    if not ex_first or not in_first:
        return True
    return _fuzzy_first_name_match(ex_first, in_first)


def _fuzzy_first_name_match(a: str, b: str) -> bool:
    a = _normalize_name(a)
    b = _normalize_name(b)
    if not a or not b:
        return False
    if len(a) < FIRST_NAME_PREFIX_LEN or len(b) < FIRST_NAME_PREFIX_LEN:
        return a == b
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return longer.startswith(shorter[:FIRST_NAME_PREFIX_LEN])


def _g(data: dict, *keys: str) -> str:
    for k in keys:
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _parse_hearing(hearing: str) -> tuple[str, str]:
    raw = str(hearing or "").strip()
    if not raw:
        return ("", "")
    date, time = _split_court_datetime(raw, "")
    if date.upper() in ("TBN", "TBD"):
        return (date.upper(), "")
    return (date, time)


def _safe_amount(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = re.sub(r"[^\d.-]", "", str(val))
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def charge_rows_from_payload(payload: dict, booking_number: str) -> list[dict]:
    """Normalize bookmarklet charges[] / charge_details[] into arrest charge_details."""
    raw = payload.get("charges") or payload.get("charge_details") or payload.get("Charges") or []
    rows: list[dict] = []
    if isinstance(raw, str):
        parts = [p.strip() for p in re.split(r"[|\n;]", raw) if p.strip()]
        raw = [{"description": p} for p in parts]

    if not isinstance(raw, list):
        return rows

    for i, item in enumerate(raw):
        if isinstance(item, str):
            desc = item.strip()
            amt = 0.0
            case_num = ""
            bond_type = "CASH / SURETY"
            court_date, court_time = "", ""
            court_loc = ""
        elif isinstance(item, dict):
            desc = str(
                item.get("description")
                or item.get("charge")
                or item.get("desc")
                or ""
            ).strip()
            amt = _safe_amount(
                item.get("bondAmount") or item.get("bond_amount") or item.get("amount") or item.get("bond")
            )
            case_num = str(item.get("caseNumber") or item.get("case_number") or item.get("caseNum") or "").replace("#", "").strip()
            bond_type = str(item.get("bondType") or item.get("bond_type") or "CASH / SURETY").strip() or "CASH / SURETY"
            hearing = item.get("hearing") or item.get("court_date") or item.get("courtDate") or ""
            court_date, court_time = _parse_hearing(str(hearing or ""))
            if item.get("court_time") or item.get("courtTime"):
                court_time = str(item.get("court_time") or item.get("courtTime")).strip() or court_time
            court_loc = str(item.get("courtLocation") or item.get("court_location") or item.get("courtLoc") or "").strip()
        else:
            continue

        if not desc:
            continue
        if _is_booking_as_case(case_num, booking_number):
            case_num = ""

        row = {
            "charge": desc,
            "bond_amount": amt,
            "bond_type": bond_type,
            "case_number": case_num,
        }
        if court_date:
            row["court_date"] = court_date
        if court_time:
            row["court_time"] = court_time
        if court_loc:
            row["court_location"] = court_loc
        rows.append(row)
    return rows


def merge_charge_details(
    existing_rows: list,
    incoming_rows: list,
    booking_number: str,
) -> list[dict]:
    """Incoming booking-page charges win for amount/case/court. Preserve POA by description or index."""
    existing = [r for r in (existing_rows or []) if isinstance(r, dict)]
    by_desc: dict[str, dict] = {}
    for r in existing:
        key = _normalize_name(str(r.get("charge") or r.get("description") or ""))
        if key and key not in by_desc:
            by_desc[key] = r

    merged: list[dict] = []
    for i, incoming in enumerate(incoming_rows or []):
        if not isinstance(incoming, dict):
            continue
        desc = str(incoming.get("charge") or "").strip()
        if not desc:
            continue
        prior = by_desc.get(_normalize_name(desc))
        if prior is None and i < len(existing):
            prior = existing[i]

        poa = ""
        if isinstance(prior, dict):
            poa = str(prior.get("poa_number") or prior.get("poa_full") or "").strip()

        case_num = str(incoming.get("case_number") or "").strip()
        if _is_booking_as_case(case_num, booking_number):
            case_num = ""
        if not case_num and isinstance(prior, dict):
            prior_case = str(prior.get("case_number") or "").strip()
            if prior_case and not _is_booking_as_case(prior_case, booking_number):
                case_num = prior_case

        row = {
            "charge": desc,
            "bond_amount": _safe_amount(incoming.get("bond_amount")),
            "bond_type": str(incoming.get("bond_type") or "CASH / SURETY").strip() or "CASH / SURETY",
            "case_number": case_num,
        }
        if incoming.get("court_date"):
            row["court_date"] = incoming["court_date"]
        elif isinstance(prior, dict) and prior.get("court_date"):
            row["court_date"] = prior["court_date"]
        if incoming.get("court_time"):
            row["court_time"] = incoming["court_time"]
        elif isinstance(prior, dict) and prior.get("court_time"):
            row["court_time"] = prior["court_time"]
        if incoming.get("court_location"):
            row["court_location"] = incoming["court_location"]
        elif isinstance(prior, dict) and prior.get("court_location"):
            row["court_location"] = prior["court_location"]
        if poa:
            row["poa_number"] = poa
        merged.append(row)
    return merged


def normalize_booking_extract(payload: dict) -> dict:
    """Pull a canonical extract from bookmarklet / SLHydrate JSON. No PII logging."""
    if not isinstance(payload, dict):
        raise BookingExtractError("Extract must be a JSON object", 400, "invalid")

    booking = _g(
        payload,
        "defendantArrestNumber",
        "bookingNumber",
        "booking_number",
        "arrest_number",
        "arrestNumber",
        "Booking_Number",
    )
    if not booking:
        raise BookingExtractError("Booking / arrest number is required", 400, "missing_booking")

    county = bare_county(_g(payload, "county", "County", "defCounty")) or "Lee"
    name = _g(
        payload,
        "defendantFullName",
        "full_name",
        "defendant_name",
        "name",
        "DefName",
        "defendantName",
        "Defendant_Name",
    )
    if not name:
        fn = _g(payload, "firstName", "first_name", "DefFirstName")
        ln = _g(payload, "lastName", "last_name", "DefLastName")
        if fn or ln:
            name = ", ".join([p for p in (ln, fn) if p])

    if not name:
        raise BookingExtractError("Defendant name is required", 400, "missing_name")

    dob = _g(payload, "defendantDOB", "dob", "DOB", "defDOB", "defendant_dob")
    if dob and "/" in dob:
        parts = dob.split("/")
        if len(parts) == 3:
            mm, dd, yy = parts[0].zfill(2), parts[1].zfill(2), re.sub(r"\s.*$", "", parts[2])
            if len(yy) == 2:
                yy = ("19" if int(yy) > 30 else "20") + yy
            dob = f"{yy}-{mm}-{dd}"

    street = _g(payload, "defendantStreetAddress", "street", "street_address", "address", "Address")
    city = _g(payload, "defendantCity", "city", "City")
    state = _g(payload, "defendantState", "state", "State") or "FL"
    zip_code = _g(payload, "defendantZip", "zip", "zip_code", "Zip")
    address_parts = [p for p in (street, city, state, zip_code) if p]
    full_address = ", ".join(address_parts)

    charges = charge_rows_from_payload(payload, booking)
    total = sum(_safe_amount(c.get("bond_amount")) for c in charges)
    explicit = _safe_amount(_g(payload, "bondAmount", "bond_amount", "bond", "DefBondAmount", "totalBond"))
    if explicit > 0 and (total == 0 or explicit > total):
        total = explicit

    primary_case = next((c["case_number"] for c in charges if c.get("case_number")), "")
    primary_court = next((c.get("court_date") for c in charges if c.get("court_date")), "") or "TBN"
    primary_time = next((c.get("court_time") for c in charges if c.get("court_time")), "")
    primary_loc = next((c.get("court_location") for c in charges if c.get("court_location")), "")
    charges_raw = " | ".join(c["charge"] for c in charges)

    first, last = parse_name_parts(name)
    return {
        "booking_number": booking,
        "county": county,
        "state": state[:2].upper() if state else "FL",
        "full_name": name,
        "first_name": first.title() if first else "",
        "last_name": last.title() if last else "",
        "dob": dob,
        "race": _g(payload, "defendantRace", "race", "Race"),
        "sex": _g(payload, "defendantSex", "sex", "Sex", "gender", "Gender"),
        "height": _g(payload, "defendantHeight", "height", "Height"),
        "weight": re.sub(r"lbs?", "", _g(payload, "defendantWeight", "weight", "Weight"), flags=re.I).strip(),
        "street": street,
        "city": city,
        "zip": zip_code,
        "address": full_address,
        "facility": _g(payload, "facility", "DefFacility") or f"{county} County Jail",
        "charge_details": charges,
        "charges": charges_raw,
        "bond_amount": total,
        "case_number": primary_case,
        "court_date": primary_court or "TBN",
        "court_time": primary_time if str(primary_court).upper() != "TBN" else "",
        "court_location": primary_loc,
        "detail_url": _g(payload, "sourceUrl", "source_url", "detail_url", "detailUrl"),
    }


def _county_regex(county: str) -> dict:
    bare = re.escape(bare_county(county) or "Lee")
    return {"$regex": rf"^{bare}(\s+county)?(\s*\([A-Za-z]{{2}}\))?$", "$options": "i"}


def _booking_lookup(booking: str, county: str) -> dict:
    return {
        "$and": [
            {"$or": [{"booking_number": booking}, {"Booking_Number": booking}]},
            {"$or": [{"county": _county_regex(county)}, {"County": _county_regex(county)}]},
        ]
    }


def _fill_if_empty(doc: dict, updates: dict, key: str, value: Any) -> None:
    if value in (None, ""):
        return
    current = doc.get(key)
    if current in (None, ""):
        updates[key] = value


def _public_lead(doc: dict) -> dict:
    """Subset returned to the dashboard. Includes fields the write desk needs."""
    keys = (
        "booking_number", "county", "state", "full_name", "first_name", "last_name",
        "dob", "race", "sex", "height", "weight", "address", "city", "zip",
        "charges", "charge_details", "bond_amount", "bond_type",
        "case_number", "court_date", "court_time", "court_location",
        "facility", "status", "lead_score", "lead_status", "detail_url",
        "poa_number",
    )
    out = {k: doc.get(k) for k in keys if k in doc}
    out["booking_number"] = str(out.get("booking_number") or "")
    return out


async def merge_booking_extract(payload: dict, *, actor: str = "dashboard_user") -> dict:
    """Upsert charge-level booking facts onto County + Booking_Number.

    Returns a JSON-safe dict. Raises BookingExtractError on fail-closed paths.
    """
    import json

    raw = json.dumps(payload, default=str) if isinstance(payload, dict) else ""
    if len(raw.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise BookingExtractError("Extract is too large", 413, "too_large")

    extract = normalize_booking_extract(payload)
    booking = extract["booking_number"]
    county = extract["county"]

    arrests = get_collection("arrests")
    cursor = arrests.find(_booking_lookup(booking, county))
    matches = await cursor.to_list(length=5)

    if len(matches) > 1:
        logger.warning("[booking-extract] ambiguous booking=%s county=%s hits=%s", booking, county, len(matches))
        raise BookingExtractError(
            "Multiple arrest records match this booking in that county — will not merge",
            409,
            "ambiguous_identity",
        )

    created = False
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    if not matches:
        alts_cursor = arrests.find({"$or": [{"booking_number": booking}, {"Booking_Number": booking}]})
        alts = await alts_cursor.to_list(length=5)
        if alts:
            logger.warning("[booking-extract] county_mismatch booking=%s county=%s", booking, county)
            raise BookingExtractError(
                "This booking exists in a different county — will not merge",
                409,
                "county_mismatch",
            )
        created = True
        existing: dict = {}
    else:
        existing = matches[0]
        existing_name = str(existing.get("full_name") or existing.get("Full_Name") or "")
        if not names_agree(existing_name, extract["full_name"]):
            logger.warning("[booking-extract] name_mismatch booking=%s county=%s", booking, county)
            raise BookingExtractError(
                "Name on the booking page does not match the arrest on file — will not overwrite",
                409,
                "name_mismatch",
            )

    incoming_rows = extract["charge_details"]
    if not incoming_rows:
        raise BookingExtractError(
            "No charges found on the booking page — nothing to merge",
            400,
            "missing_charges",
        )

    merged_rows = merge_charge_details(
        existing.get("charge_details") or existing.get("Charge_Details") or [],
        incoming_rows,
        booking,
    )
    total_bond = sum(_safe_amount(r.get("bond_amount")) for r in merged_rows)
    if total_bond <= 0:
        total_bond = _safe_amount(existing.get("bond_amount")) or extract["bond_amount"]

    bond_types = {str(r.get("bond_type") or "").strip() for r in merged_rows if r.get("bond_type")}
    primary_bond_type = " / ".join(sorted(t for t in bond_types if t)) or str(existing.get("bond_type") or "Surety")
    charges_raw = " | ".join(r["charge"] for r in merged_rows) or extract["charges"]
    primary_case = next((r["case_number"] for r in merged_rows if r.get("case_number")), "") or extract["case_number"]
    primary_court = next((r.get("court_date") for r in merged_rows if r.get("court_date")), "") or extract["court_date"] or "TBN"
    primary_time = ""
    if str(primary_court).upper() != "TBN":
        primary_time = next((r.get("court_time") for r in merged_rows if r.get("court_time")), "") or extract["court_time"]
    primary_loc = next((r.get("court_location") for r in merged_rows if r.get("court_location")), "") or extract["court_location"]

    from core.models import ArrestRecord
    from scoring.lead_scorer import LeadScorer

    rec_dict = dict(existing) if existing else {}
    rec_dict.update({
        "booking_number": booking,
        "county": existing.get("county") or county,
        "state": existing.get("state") or extract["state"],
        "full_name": existing.get("full_name") or extract["full_name"],
        "bond_amount": f"{total_bond:.2f}",
        "bond_type": primary_bond_type,
        "charges": charges_raw,
        "status": existing.get("status") or "In Custody",
    })
    rec = ArrestRecord.from_mongo_doc(rec_dict)
    LeadScorer().score_and_update(rec)

    updates: dict[str, Any] = {
        "bond_amount": total_bond,
        "bond_type": primary_bond_type,
        "charges": charges_raw,
        "charge_details": merged_rows,
        "lead_score": rec.Lead_Score,
        "lead_status": rec.Lead_Status,
        "updated_at": now,
        "last_checked": now_iso,
        "last_checked_mode": "BOOKMARKLET_EXTRACT",
        "case_number": primary_case,
        "court_date": primary_court,
        "court_time": primary_time,
        "court_location": primary_loc,
    }
    if extract["detail_url"] and not existing.get("detail_url"):
        updates["detail_url"] = extract["detail_url"]

    # Fill identity gaps only — never clobber a populated name/DOB/address.
    _fill_if_empty(existing, updates, "full_name", extract["full_name"])
    _fill_if_empty(existing, updates, "first_name", extract["first_name"])
    _fill_if_empty(existing, updates, "last_name", extract["last_name"])
    _fill_if_empty(existing, updates, "dob", extract["dob"])
    _fill_if_empty(existing, updates, "address", extract["address"])
    _fill_if_empty(existing, updates, "city", extract["city"])
    _fill_if_empty(existing, updates, "zip", extract["zip"])
    _fill_if_empty(existing, updates, "race", extract["race"])
    _fill_if_empty(existing, updates, "sex", extract["sex"])
    _fill_if_empty(existing, updates, "height", extract["height"])
    _fill_if_empty(existing, updates, "weight", extract["weight"])
    _fill_if_empty(existing, updates, "facility", extract["facility"])
    _fill_if_empty(existing, updates, "state", extract["state"])

    has_active_bond = False
    try:
        active = get_collection("active_bonds")
        found = await active.find_one({
            "$or": [{"booking_number": booking}, {"Booking_Number": booking}],
            "status": {"$nin": ["exonerated", "forfeited", "surrendered"]},
        })
        has_active_bond = bool(found)
    except Exception:
        has_active_bond = False

    if created:
        insert_doc = {
            "booking_number": booking,
            "county": county,
            "state": extract["state"],
            "full_name": extract["full_name"],
            "first_name": extract["first_name"],
            "last_name": extract["last_name"],
            "dob": extract["dob"],
            "address": extract["address"],
            "city": extract["city"],
            "zip": extract["zip"],
            "race": extract["race"],
            "sex": extract["sex"],
            "height": extract["height"],
            "weight": extract["weight"],
            "facility": extract["facility"],
            "status": "In Custody",
            "source": "bookmarklet_extract",
            "scraped_at": now,
            "created_at": now,
            **updates,
        }
        if extract["detail_url"]:
            insert_doc["detail_url"] = extract["detail_url"]
        await arrests.insert_one(insert_doc)
        result_doc = insert_doc
        logger.info(
            "[booking-extract] created booking=%s county=%s charges=%s",
            booking, county, len(merged_rows),
        )
    else:
        await arrests.update_one({"_id": existing["_id"]}, {"$set": updates})
        result_doc = {**existing, **updates}
        logger.info(
            "[booking-extract] merged booking=%s county=%s charges=%s",
            booking, county, len(merged_rows),
        )

    try:
        await get_collection("audit_events").insert_one({
            "event_type": "booking_extract_merge",
            "actor": (actor or "dashboard_user")[:80],
            "booking_number": booking,
            "county": county,
            "created": created,
            "charge_count": len(merged_rows),
            "has_active_bond": has_active_bond,
            "timestamp": now_iso,
        })
    except Exception as exc:
        logger.warning("[booking-extract] audit write failed: %s", exc)

    return {
        "success": True,
        "created": created,
        "booking_number": booking,
        "county": county,
        "charge_count": len(merged_rows),
        "total_bond": total_bond,
        "lead_score": rec.Lead_Score,
        "lead_status": rec.Lead_Status,
        "has_active_bond": has_active_bond,
        "lead": _public_lead(result_doc),
    }
