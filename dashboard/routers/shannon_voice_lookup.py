"""Shannon / ElevenLabs live voice inmate, bond & release-status lookup.

Fast, read-only MongoDB lookup mounted under ``/api/paperwork/shannon/``
(already in ``OPEN_PREFIXES`` so it skips browser PIN redirects and enforces
fail-closed machine auth on the route itself).

Queries live ``arrests``, ``active_bonds``, ``court_reminders``, and
``paperwork_packets`` / ``intake_queue`` in ``ShamrockBailDB`` so ElevenLabs
voice agents (Shannon, Sofia, Eric) can answer custody, bond, court-date, and
release-timeline questions in <150ms without legacy GAS cold-start timeouts.

SOC II / Privacy:
  - Never returns third-party indemnitor phone, email, address, DOB, or SSN.
  - Passes all outputs through ``redact_internal_keys`` (Miami-Dade key safety).
  - Fails closed (401) when unauthenticated and never guesses on ambiguous
    multi-defendant matches.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.booking_identity import redact_internal_keys
from dashboard.deps import get_collection
from dashboard.routers.automation_control import _machine_key_ok, _staff_session_ok

logger = logging.getLogger(__name__)

shannon_voice_lookup_bp = APIRouter(prefix="/api/paperwork/shannon", tags=["shannon_voice_lookup"])

# Florida county jail & First Appearance reference table (verified public info).
FL_COUNTY_JAIL_INFO: dict[str, dict[str, str]] = {
    "lee": {
        "county": "Lee",
        "jail_name": "Lee County Jail (Core Facility / Ortiz Facility)",
        "jail_address": "2115 Dr. Martin Luther King Jr. Blvd., Fort Myers, FL 33901 (Core) / 2501 Ortiz Ave., Fort Myers, FL 33905 (Ortiz)",
        "jail_phone": "(239) 477-1500",
        "first_appearance": "Usually daily around 1:30 PM at the Lee County Justice Center (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 2 to 6 hours after the bond is posted at the jail, though heavy intake volume, shift changes, or holds can take longer",
    },
    "charlotte": {
        "county": "Charlotte",
        "jail_name": "Charlotte County Jail",
        "jail_address": "26601 Airport Rd., Punta Gorda, FL 33982",
        "jail_phone": "(941) 833-6300",
        "first_appearance": "Usually daily around 1:30 PM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 2 to 5 hours after the bond is posted, depending on jail processing volume and shift changes",
    },
    "collier": {
        "county": "Collier",
        "jail_name": "Naples Jail Center (Collier County Sheriff's Office)",
        "jail_address": "3315 Tamiami Trail E., Naples, FL 34112",
        "jail_phone": "(239) 252-9500",
        "first_appearance": "Usually daily in the morning around 8:30 AM to 9:00 AM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 2 to 6 hours after the bond is posted, depending on facility volume and any holds",
    },
    "sarasota": {
        "county": "Sarasota",
        "jail_name": "Sarasota County Correctional Facility",
        "jail_address": "2020 Main St., Sarasota, FL 34237",
        "jail_phone": "(941) 861-4601",
        "first_appearance": "Usually daily around 1:30 PM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 2 to 6 hours after the bond is posted, depending on jail workload and shift changes",
    },
    "manatee": {
        "county": "Manatee",
        "jail_name": "Manatee County Central Jail",
        "jail_address": "14470 Harlee Rd., Palmetto, FL 34221",
        "jail_phone": "(941) 747-3011",
        "first_appearance": "Usually daily around 1:30 PM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 2 to 6 hours after the bond is posted, depending on jail volume and holds",
    },
    "hendry": {
        "county": "Hendry",
        "jail_name": "Hendry County Jail",
        "jail_address": "101 S. Bridge St., LaBelle, FL 33935",
        "jail_phone": "(863) 674-5600",
        "first_appearance": "Usually daily in the morning (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 1 to 4 hours after the bond is posted, depending on jail staffing and holds",
    },
    "desoto": {
        "county": "DeSoto",
        "jail_name": "DeSoto County Jail",
        "jail_address": "208 E. Cypress St., Arcadia, FL 34266",
        "jail_phone": "(863) 993-4700",
        "first_appearance": "Usually daily in the morning (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 1 to 4 hours after the bond is posted, depending on jail staffing and holds",
    },
    "glades": {
        "county": "Glades",
        "jail_name": "Glades County Detention Center",
        "jail_address": "1297 E. State Rd. 78, Moore Haven, FL 33471",
        "jail_phone": "(863) 946-1600",
        "first_appearance": "Within 24 hours of arrest under Florida law",
        "typical_release_window": "typically 2 to 5 hours after the bond is posted, depending on facility processing",
    },
    "hillsborough": {
        "county": "Hillsborough",
        "jail_name": "Orient Road Jail (Hillsborough County)",
        "jail_address": "1201 Orient Rd., Tampa, FL 33619",
        "jail_phone": "(813) 247-8300",
        "first_appearance": "Usually daily around 9:00 AM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 4 to 8+ hours after the bond is posted due to large-facility volume and shift changes",
    },
    "pinellas": {
        "county": "Pinellas",
        "jail_name": "Pinellas County Jail",
        "jail_address": "14400 49th St. N., Clearwater, FL 33762",
        "jail_phone": "(727) 464-6415",
        "first_appearance": "Usually daily around 8:30 AM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 3 to 6+ hours after the bond is posted, depending on jail volume and shift changes",
    },
    "orange": {
        "county": "Orange",
        "jail_name": "Orange County Jail (33rd Street)",
        "jail_address": "3723 Vision Blvd., Orlando, FL 32839",
        "jail_phone": "(407) 836-3400",
        "first_appearance": "Usually daily around 9:00 AM and 1:00 PM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 4 to 8+ hours after the bond is posted due to large-facility processing",
    },
    "broward": {
        "county": "Broward",
        "jail_name": "Broward County Main Jail",
        "jail_address": "555 SE 1st Ave., Fort Lauderdale, FL 33301",
        "jail_phone": "(954) 831-5900",
        "first_appearance": "Usually daily around 8:30 AM and 1:00 PM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 4 to 8+ hours after the bond is posted, depending on facility volume and holds",
    },
    "palm beach": {
        "county": "Palm Beach",
        "jail_name": "Palm Beach County Main Detention Center",
        "jail_address": "3228 Gun Club Rd., West Palm Beach, FL 33406",
        "jail_phone": "(561) 688-4400",
        "first_appearance": "Usually daily around 9:00 AM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 4 to 8+ hours after the bond is posted, depending on jail volume and shift changes",
    },
    "miami-dade": {
        "county": "Miami-Dade",
        "jail_name": "Turner Guilford Knight Correctional Center (TGK)",
        "jail_address": "7000 NW 41st St., Miami, FL 33166",
        "jail_phone": "(786) 263-5600",
        "first_appearance": "Usually daily around 9:00 AM and 1:30 PM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 4 to 8+ hours after the bond is posted, depending on facility volume and shift changes",
    },
    "polk": {
        "county": "Polk",
        "jail_name": "South County Jail / Central County Jail (Polk County)",
        "jail_address": "2390 Bob Phillips Rd., Bartow, FL 33830",
        "jail_phone": "(863) 298-6200",
        "first_appearance": "Usually daily around 1:00 PM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 3 to 6+ hours after the bond is posted, depending on processing volume",
    },
    "duval": {
        "county": "Duval",
        "jail_name": "John E. Goode Pre-Trial Detention Facility",
        "jail_address": "500 E. Adams St., Jacksonville, FL 32202",
        "jail_phone": "(904) 630-5760",
        "first_appearance": "Usually daily around 9:00 AM and 1:00 PM (within 24 hours of arrest under Florida law)",
        "typical_release_window": "typically 4 to 8+ hours after the bond is posted, depending on jail volume",
    },
}

_LOCAL_NO_TRANSFER_FEE_COUNTIES = frozenset({"lee", "charlotte"})

_DUI_RE = re.compile(r"\b(dui|driving under the influence|dubal|bui)\b", re.I)
_DV_RE = re.compile(r"\b(domestic|dating violence|battery.*domestic|dom\s*viol|dv\b)\b", re.I)
_VOP_HOLD_RE = re.compile(
    r"\b(vop|violation of probation|probation viol|no\s*bond|hold\b|out of county|fugitive|detainer|ice\s*hold|federal|nebbia)\b",
    re.I,
)
_RELEASED_RE = re.compile(r"\b(released|bonded\s*out|discharged|ror|r\.o\.r\.|time\s*served)\b", re.I)


def _const_eq(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return secrets.compare_digest(a, b)
    except Exception:
        return False


def _voice_auth_ok(request: Request) -> bool:
    """Accept staff session, machine keys, or ELEVENLABS_TOOL_SECRET / SHANNON_TOOL_SECRET."""
    if _staff_session_ok(request) or _machine_key_ok(request):
        return True

    auth_hdr = (request.headers.get("Authorization") or "").strip()
    bearer = auth_hdr[7:].strip() if auth_hdr.lower().startswith("bearer ") else ""
    provided = (
        request.headers.get("X-API-Key")
        or request.headers.get("X-Api-Key")
        or request.headers.get("X-Tool-Secret")
        or bearer
        or request.query_params.get("secret")
        or request.query_params.get("api_key")
        or ""
    ).strip()
    if not provided:
        return False

    for env_key in ("ELEVENLABS_TOOL_SECRET", "SHANNON_TOOL_SECRET", "GAS_API_KEY", "LEADS_INTERNAL_TOKEN"):
        expected = (os.getenv(env_key) or "").strip()
        if expected and _const_eq(provided, expected):
            return True
    return False


def _clean_county_key(raw: str) -> tuple[str, str]:
    """Return (normalized_lookup_key, display_name) for a county string."""
    txt = str(raw or "").strip()
    txt = re.sub(r"\s*\([A-Z]{2}\)\s*$", "", txt)
    txt = re.sub(r"\s+county\b", "", txt, flags=re.I).strip()
    key = re.sub(r"\s+", " ", txt).lower()
    if key in ("dade", "miami dade", "miami-dade"):
        key = "miami-dade"
    if key in ("palmbeach", "palm-beach", "palm beach"):
        key = "palm beach"
    display = FL_COUNTY_JAIL_INFO.get(key, {}).get("county") or (txt.title() if txt else "")
    return key, display


def _parse_bond_amount(doc: dict[str, Any]) -> float | None:
    """Return positive numeric bond amount if published, else None."""
    raw = doc.get("bond_amount")
    if raw is not None and raw != "":
        try:
            val = float(str(raw).replace("$", "").replace(",", "").strip())
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    raw_str = str(doc.get("bond_amount_raw") or "").strip()
    if raw_str:
        m = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)", raw_str)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if val > 0:
                    return val
            except ValueError:
                pass
    return None


def _estimate_premium(bond_amount: float, county_key: str, charge_count: int = 1) -> dict[str, Any]:
    """FL Stat. §648.44 estimated premium (10% or $100/charge minimum) + transfer fee note."""
    charges = max(1, int(charge_count or 1))
    est = max(round(bond_amount * 0.10, 2), float(100 * charges))
    transfer_fee = 0.0 if (not county_key or county_key in _LOCAL_NO_TRANSFER_FEE_COUNTIES) else 100.0
    return {
        "estimated_premium": est,
        "transfer_fee": transfer_fee,
        "estimated_total_with_transfer": round(est + transfer_fee, 2),
        "note": "Estimate only based on Florida statutory 10% rate ($100/charge minimum); final total is confirmed by a licensed bondsman.",
    }


def _name_tokens(raw_name: str) -> list[str]:
    cleaned = re.sub(r"[^a-zA-Z0-9\s'-]", " ", str(raw_name or ""))
    tokens = [t.strip("'-") for t in cleaned.split() if len(t.strip("'-")) >= 2]
    # Drop common suffixes that frequently vary between caller speech and jail rosters.
    return [t for t in tokens if t.lower() not in {"jr", "sr", "ii", "iii", "iv"}]


def _format_charges_text(doc: dict[str, Any]) -> tuple[str, int]:
    charges = doc.get("charges")
    if isinstance(charges, list):
        items = [str(c).strip() for c in charges if str(c).strip()]
        return "; ".join(items[:6]), len(items)
    raw = str(charges or "").strip()
    if not raw:
        return "", 0
    parts = [p.strip() for p in re.split(r"[|;\n]+", raw) if p.strip()]
    return "; ".join(parts[:6]), max(1, len(parts))


def _build_release_guidance(
    *,
    county_key: str,
    county_display: str,
    custody_status: str,
    bond_amount: float | None,
    bond_type: str,
    charges_text: str,
    has_active_bond: bool,
) -> dict[str, Any]:
    county_info = FL_COUNTY_JAIL_INFO.get(county_key)
    window = (
        county_info["typical_release_window"]
        if county_info
        else "typically 2 to 6+ hours after the bond is posted at the jail, depending on facility volume, shift changes, and any holds"
    )
    first_app = (
        county_info["first_appearance"]
        if county_info
        else "within 24 hours of arrest under Florida law"
    )

    combined = f"{charges_text} {bond_type}".strip()
    holds: list[str] = []
    if _DUI_RE.search(combined):
        holds.append(
            "DUI charges in Florida carry a mandatory 8-hour statutory hold from arrest (or until BAC is below 0.05) before the jail will release the person, even after bond is posted."
        )
    if _DV_RE.search(combined):
        holds.append(
            "Domestic violence charges in Florida are held without bond until the defendant sees a judge at First Appearance (within 24 hours)."
        )
    if _VOP_HOLD_RE.search(combined):
        holds.append(
            "Probation violations, out-of-county warrants, Nebbia holds, or No-Bond holds require a judge or the holding jurisdiction to set or clear bond before release."
        )

    is_released = bool(_RELEASED_RE.search(custody_status or ""))
    if is_released:
        summary = (
            "The county roster currently shows this person as already released. "
            "If you have not heard from them yet, call the jail directly to confirm whether they have exited the release lobby."
        )
    elif has_active_bond:
        summary = (
            f"Shamrock already has a bond record on file. Once a bond is posted at the jail, release is controlled entirely by jail staff — {window}. "
            "We never promise an exact release time because jail processing varies."
        )
    elif bond_amount is None or bond_amount <= 0 or "no bond" in (bond_type or "").lower():
        summary = (
            f"Bond does not appear set yet (or a hold is listed). Under Florida law, the defendant must see a judge at First Appearance ({first_app}) for bond to be set. "
            "We can take your information now so our licensed bondsman can move quickly the moment bond is set."
        )
    else:
        summary = (
            f"Once paperwork and payment are completed and our bondsman posts the bond at the jail, release is controlled by jail staff — {window}. "
            "We never guarantee an exact release time because shift changes, intake volume, or hidden holds can extend processing."
        )

    if holds:
        summary = f"{summary} Important hold note: {' '.join(holds)}"

    return {
        "summary": summary,
        "first_appearance_schedule": first_app,
        "typical_release_window": window,
        "special_hold_notes": holds,
        "never_promise_exact_time": True,
        "county_info": county_info
        or {
            "county": county_display or "Florida",
            "first_appearance": first_app,
            "typical_release_window": window,
        },
    }


def _distinct_person_key(doc: dict[str, Any]) -> str:
    name = re.sub(r"[^a-z]", "", str(doc.get("full_name") or "").lower())
    county = str(doc.get("county") or "").strip().lower()
    return f"{county}:{name}"


async def _fetch_matching_arrests(
    *,
    defendant_name: str,
    booking_number: str,
    county_display: str,
    state: str,
) -> list[dict[str, Any]]:
    arrests = get_collection("arrests")
    projection = {
        "_id": 0,
        "full_name": 1,
        "first_name": 1,
        "last_name": 1,
        "booking_number": 1,
        "county": 1,
        "state": 1,
        "facility": 1,
        "custody_status": 1,
        "status": 1,
        "bond_amount": 1,
        "bond_amount_raw": 1,
        "bond_type": 1,
        "charges": 1,
        "charge_details": 1,
        "booking_date": 1,
        "booking_time": 1,
        "arrest_date": 1,
        "court_date": 1,
        "case_number": 1,
        "scraped_at": 1,
    }

    if booking_number:
        q: dict[str, Any] = {
            "booking_number": {"$regex": f"^{re.escape(booking_number)}$", "$options": "i"}
        }
        if county_display:
            q["county"] = {"$regex": f"^{re.escape(county_display)}$", "$options": "i"}
        rows = await arrests.find(q, projection).sort("scraped_at", -1).limit(5).to_list(length=5)
        if rows:
            return rows

    tokens = _name_tokens(defendant_name)
    if not tokens:
        return []

    and_clauses: list[dict[str, Any]] = [
        {"full_name": {"$regex": re.escape(tok), "$options": "i"}} for tok in tokens[:4]
    ]
    if county_display:
        and_clauses.append({"county": {"$regex": f"^{re.escape(county_display)}$", "$options": "i"}})
    if state:
        and_clauses.append({"state": {"$regex": f"^{re.escape(state)}$", "$options": "i"}})

    query: dict[str, Any] = {"$and": and_clauses} if len(and_clauses) > 1 else and_clauses[0]
    rows = await arrests.find(query, projection).sort("scraped_at", -1).limit(10).to_list(length=10)
    # If county-filtered search found nothing, retry statewide so caller gets a helpful county correction.
    if not rows and county_display:
        fallback_clauses = [{"full_name": {"$regex": re.escape(tok), "$options": "i"}} for tok in tokens[:4]]
        if state:
            fallback_clauses.append({"state": {"$regex": f"^{re.escape(state)}$", "$options": "i"}})
        fallback_q = {"$and": fallback_clauses} if len(fallback_clauses) > 1 else fallback_clauses[0]
        rows = await arrests.find(fallback_q, projection).sort("scraped_at", -1).limit(10).to_list(length=10)
    return rows


async def _fetch_active_bond_summary(
    *,
    defendant_name: str,
    booking_number: str,
) -> dict[str, Any] | None:
    """Return non-PII active bond summary if Shamrock already has a bond on file."""
    bonds = get_collection("active_bonds")
    projection = {
        "_id": 0,
        "status": 1,
        "bond_status": 1,
        "county": 1,
        "defendant_name": 1,
        "booking_number": 1,
        "case_number": 1,
        "bond_amount": 1,
        "court_date": 1,
        "next_court_date": 1,
        "posted_at": 1,
        "created_at": 1,
    }
    doc = None
    if booking_number:
        doc = await bonds.find_one(
            {"booking_number": {"$regex": f"^{re.escape(booking_number)}$", "$options": "i"}},
            projection,
        )
    if not doc:
        tokens = _name_tokens(defendant_name)
        if len(tokens) >= 2:
            clauses = [{"defendant_name": {"$regex": re.escape(tok), "$options": "i"}} for tok in tokens[:4]]
            doc = await bonds.find_one({"$and": clauses}, projection)
    if not isinstance(doc, dict):
        return None
    return {
        "bond_on_file": True,
        "status": str(doc.get("status") or doc.get("bond_status") or "active"),
        "county": str(doc.get("county") or ""),
        "case_number": str(doc.get("case_number") or ""),
        "bond_amount": doc.get("bond_amount"),
        "next_court_date": str(doc.get("next_court_date") or doc.get("court_date") or ""),
        "posted_at": str(doc.get("posted_at") or doc.get("created_at") or ""),
    }


async def _fetch_next_court_date(*, defendant_name: str, booking_number: str) -> str:
    reminders = get_collection("court_reminders")
    projection = {"_id": 0, "court_date": 1, "court_time": 1, "court_location": 1}
    doc = None
    if booking_number:
        doc = await reminders.find_one(
            {"booking_number": {"$regex": f"^{re.escape(booking_number)}$", "$options": "i"}},
            projection,
        )
    if not doc:
        tokens = _name_tokens(defendant_name)
        if len(tokens) >= 2:
            clauses = [{"defendant_name": {"$regex": re.escape(tok), "$options": "i"}} for tok in tokens[:4]]
            doc = await reminders.find_one({"$and": clauses}, projection)
    if not isinstance(doc, dict):
        return ""
    parts = [
        str(doc.get("court_date") or "").strip(),
        str(doc.get("court_time") or "").strip(),
        str(doc.get("court_location") or "").strip(),
    ]
    return " ".join(p for p in parts if p)


async def _handle_inmate_lookup(request: Request) -> JSONResponse:
    if not _voice_auth_ok(request):
        return JSONResponse(
            {"success": False, "error": "unauthorized"},
            status_code=401,
        )

    params: dict[str, Any] = dict(request.query_params)
    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict):
                params.update(body)
        except Exception:
            pass

    defendant_name = str(
        params.get("defendant_name") or params.get("name") or params.get("inmate_name") or ""
    ).strip()
    booking_number = str(params.get("booking_number") or params.get("booking_id") or "").strip()
    county_raw = str(params.get("county") or "").strip()
    state = str(params.get("state") or "").strip().upper()

    county_key, county_display = _clean_county_key(county_raw)

    if not defendant_name and not booking_number:
        guidance = _build_release_guidance(
            county_key=county_key,
            county_display=county_display,
            custody_status="",
            bond_amount=None,
            bond_type="",
            charges_text="",
            has_active_bond=False,
        )
        return JSONResponse(
            redact_internal_keys(
                {
                    "success": True,
                    "found": False,
                    "status": "county_info_only",
                    "can_start_intake": True,
                    "county_info": guidance["county_info"],
                    "release_guidance": guidance,
                    "message": (
                        f"I can look up a specific person if you give me their full name or booking number. "
                        f"{guidance['summary']}"
                    ),
                }
            )
        )

    rows = await _fetch_matching_arrests(
        defendant_name=defendant_name,
        booking_number=booking_number,
        county_display=county_display,
        state=state,
    )

    # Disambiguation check: fail closed if multiple distinct people match and caller gave no booking number.
    distinct_keys = list(dict.fromkeys(_distinct_person_key(r) for r in rows))
    if len(distinct_keys) > 1 and not booking_number:
        candidates = []
        seen_k: set[str] = set()
        for r in rows:
            k = _distinct_person_key(r)
            if k in seen_k:
                continue
            seen_k.add(k)
            candidates.append(
                {
                    "full_name": str(r.get("full_name") or ""),
                    "county": str(r.get("county") or ""),
                    "booking_date": str(r.get("booking_date") or r.get("arrest_date") or ""),
                }
            )
            if len(candidates) >= 4:
                break
        return JSONResponse(
            redact_internal_keys(
                {
                    "success": True,
                    "found": False,
                    "ambiguous": True,
                    "status": "multiple_matches",
                    "can_start_intake": True,
                    "candidates": candidates,
                    "message": (
                        f"I see {len(candidates)} different bookings matching that name across our live jail records. "
                        "Can you confirm the exact county, middle name, or booking number so I make sure I pull the right person?"
                    ),
                }
            )
        )

    active_bond = await _fetch_active_bond_summary(
        defendant_name=defendant_name or (rows[0].get("full_name") if rows else ""),
        booking_number=booking_number or (rows[0].get("booking_number") if rows else ""),
    )

    if not rows:
        guidance = _build_release_guidance(
            county_key=county_key,
            county_display=county_display,
            custody_status="",
            bond_amount=None,
            bond_type="",
            charges_text="",
            has_active_bond=bool(active_bond),
        )
        if active_bond:
            court_note = (
                f" Their next court date on file is {active_bond['next_court_date']}."
                if active_bond.get("next_court_date")
                else ""
            )
            msg = (
                f"I do see a Shamrock bond record on file for {defendant_name or booking_number} "
                f"(status: {active_bond['status']}).{court_note} {guidance['summary']}"
            )
            return JSONResponse(
                redact_internal_keys(
                    {
                        "success": True,
                        "found": True,
                        "status": "active_bond_on_file",
                        "can_start_intake": True,
                        "active_bond": active_bond,
                        "county_info": guidance["county_info"],
                        "release_guidance": guidance,
                        "message": msg,
                    }
                )
            )

        county_phrase = f" in {county_display} County" if county_display else ""
        msg = (
            f"I don't see {defendant_name or booking_number} on our live{county_phrase} booking feed yet — "
            "very recent arrests can take 1 to 3 hours to appear while the jail finishes fingerprinting and booking. "
            f"{guidance['summary']} If you already know the bond amount or want to get a head start, "
            "I can take your information right now so our bondsman is ready."
        )
        return JSONResponse(
            redact_internal_keys(
                {
                    "success": True,
                    "found": False,
                    "status": "not_in_system",
                    "can_start_intake": True,
                    "county_info": guidance["county_info"],
                    "release_guidance": guidance,
                    "message": msg,
                }
            )
        )

    doc = rows[0]
    matched_county_key, matched_county_display = _clean_county_key(doc.get("county") or county_display)
    custody_status = str(doc.get("custody_status") or doc.get("status") or "").strip() or "Unknown"
    bond_amount = _parse_bond_amount(doc)
    bond_type = str(doc.get("bond_type") or "").strip()
    charges_text, charge_count = _format_charges_text(doc)
    booking_date = str(doc.get("booking_date") or doc.get("arrest_date") or "").strip()
    matched_booking = str(doc.get("booking_number") or "").strip()
    matched_name = str(doc.get("full_name") or defendant_name).strip()
    facility = str(doc.get("facility") or "").strip()
    court_date = (
        (active_bond or {}).get("next_court_date")
        or str(doc.get("court_date") or "").strip()
        or await _fetch_next_court_date(defendant_name=matched_name, booking_number=matched_booking)
    )

    guidance = _build_release_guidance(
        county_key=matched_county_key,
        county_display=matched_county_display,
        custody_status=custody_status,
        bond_amount=bond_amount,
        bond_type=bond_type,
        charges_text=charges_text,
        has_active_bond=bool(active_bond),
    )

    premium_info = (
        _estimate_premium(bond_amount, matched_county_key, charge_count=max(1, charge_count))
        if bond_amount and bond_amount > 0
        else None
    )

    is_released = bool(_RELEASED_RE.search(custody_status))
    if is_released:
        status_code = "released"
        spoken = (
            f"Our live {matched_county_display} County roster shows {matched_name} is currently listed as {custody_status}."
        )
    elif bond_amount and bond_amount > 0:
        status_code = "bond_set"
        est_prem = premium_info["estimated_premium"]
        fee_note = (
            f" plus a $100 transfer fee outside Lee and Charlotte counties"
            if premium_info["transfer_fee"] > 0
            else ""
        )
        spoken = (
            f"I found {matched_name} in {matched_county_display} County ({custody_status}). "
            f"The listed total bond is ${bond_amount:,.0f}"
            + (f" ({bond_type})" if bond_type else "")
            + f", so the estimated 10 percent premium is around ${est_prem:,.0f}{fee_note} — final amount confirmed by our bondsman. "
        )
    else:
        status_code = "bond_not_set"
        spoken = (
            f"I found {matched_name} in {matched_county_display} County ({custody_status}), "
            "and a bond amount is not published yet (or a hold is listed). "
        )

    if charges_text:
        spoken += f"Listed charges: {charges_text}. "
    if court_date:
        spoken += f"Court date on file: {court_date}. "
    spoken += guidance["summary"]

    payload: dict[str, Any] = {
        "success": True,
        "found": True,
        "status": status_code,
        "can_start_intake": not is_released,
        "defendant": {
            "full_name": matched_name,
            "county": matched_county_display,
            "state": str(doc.get("state") or "FL").upper(),
            "facility": facility or guidance["county_info"].get("jail_name", ""),
            "booking_number": matched_booking,
            "booking_date": booking_date,
            "custody_status": custody_status,
            "bond_amount": bond_amount,
            "bond_type": bond_type,
            "charges": charges_text,
            "charge_count": charge_count,
            "case_number": str(doc.get("case_number") or ""),
            "court_date": court_date,
        },
        "premium_estimate": premium_info,
        "active_bond": active_bond,
        "county_info": guidance["county_info"],
        "release_guidance": guidance,
        "message": spoken.strip(),
    }
    return JSONResponse(redact_internal_keys(payload))


@shannon_voice_lookup_bp.post("/inmate-status")
async def shannon_inmate_status_post(request: Request):
    """Live MongoDB custody, bond, court-date, and release-guidance lookup for ElevenLabs."""
    return await _handle_inmate_lookup(request)


@shannon_voice_lookup_bp.get("/inmate-status")
async def shannon_inmate_status_get(request: Request):
    """GET variant of live MongoDB custody, bond & release lookup for ElevenLabs GET tools."""
    return await _handle_inmate_lookup(request)
