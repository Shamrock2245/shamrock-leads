"""
ShamrockLeads — POA Inventory API Blueprint
Endpoints: /api/poa/next, /api/poa/assign, /api/poa/inventory,
           /api/poa/list, /api/poa/add, /api/poa/void,
           /api/poa/release, /api/poa/reassign, /api/poa/restore
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from dashboard.extensions import get_collection
from dashboard.services.poa_service import (
    get_poa_tier_for_bond,
    inventory_prefix_query,
    parse_max_bond_from_prefix,
    determine_surety_from_prefix,
)

logger = logging.getLogger(__name__)
poa_bp = APIRouter(prefix="/api", tags=["poa"])
@poa_bp.get("/poa/next")
async def api_poa_next(
    surety: str | None = Query(default=None),
    bond_amount: int = Query(default=0),
    count: int = Query(default=1),
    amounts: str | None = Query(default=None),
):
    """
    Suggest the next available POA number(s) for a given surety + bond amount.
    Query params: surety, bond_amount, count, amounts (comma-separated per-charge)
    """
    poa_inventory = get_collection("poa_inventory")

    surety = (surety or "").lower().strip()
    if surety not in ("osi", "palmetto"):
        return JSONResponse({"error": "surety must be 'osi' or 'palmetto'"}, status_code=400)
    try:
        bond_amount = float(bond_amount or 0)
    except ValueError:
        bond_amount = 0.0
    count = max(1, int(count or 1))

    amt_list: list[float] = []
    if amounts:
        for raw in str(amounts).split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                amt_list.append(float(raw))
            except ValueError:
                continue
    if not amt_list:
        amt_list = [bond_amount] * count

    used_serials: list[str] = []
    suggested = []
    prefixes: list[str] = []
    for amt in amt_list:
        prefix = get_poa_tier_for_bond(surety, amt)
        prefixes.append(prefix)
        q = {
            "surety_id": surety,
            "status": "available",
            **inventory_prefix_query(prefix),
        }
        if used_serials:
            q["poa_number"] = {"$nin": list(used_serials)}
        doc = await poa_inventory.find_one(
            q,
            {"poa_number": 1, "poa_prefix": 1, "poa_full": 1, "_id": 0},
            sort=[("poa_number", 1)],
        )
        if doc:
            serial = str(doc.get("poa_number") or "")
            if serial:
                used_serials.append(serial)
            suggested.append(doc)
        else:
            suggested.append({"poa_number": "", "poa_prefix": prefix, "poa_full": "", "missing": True})

    primary_prefix = prefixes[0] if prefixes else get_poa_tier_for_bond(surety, bond_amount)
    total_available = await poa_inventory.count_documents(
        {"surety_id": surety, "status": "available", **inventory_prefix_query(primary_prefix)}
    )
    total_surety = await poa_inventory.count_documents(
        {"surety_id": surety, "status": "available"}
    )

    missing = sum(1 for s in suggested if s.get("missing"))
    return {
        "surety": surety,
        "prefix": primary_prefix,
        "prefixes": prefixes,
        "bond_amount": bond_amount,
        "amounts": amt_list,
        "available_in_tier": total_available,
        "available_total": total_surety,
        "suggested": suggested,
        "warning": (
            (
                "Low inventory in this tier"
                + ("; some charges have no available POA in tier" if missing else "")
            ) if total_available <= 3
            else ("Some charges have no available POA in tier" if missing else None)
        ),
    }


@poa_bp.post("/poa/assign")
async def api_poa_assign(request: Request):
    """Mark a POA as assigned to a bond case."""
    poa_inventory = get_collection("poa_inventory")

    body = (await request.json()) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    poa_prefix = str(body.get("poa_prefix", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    bond_case_id = body.get("bond_case_id") or body.get("booking_number", "")

    if not poa_number or not surety_id:
        return JSONResponse({"error": "poa_number and surety_id are required"}, status_code=400)

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return JSONResponse({"error": f"POA {poa_number} not found for surety {surety_id}"}, status_code=404)
    if doc.get("status") != "available":
        return JSONResponse({"error": f"POA {poa_number} is already {doc.get('status')} — cannot assign"}, status_code=409)

    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {
            "status": "assigned",
            "bond_case_id": str(bond_case_id),
            "used_at": datetime.now(timezone.utc).isoformat(),
        }},
    )

    remaining = await poa_inventory.count_documents(
        {"surety_id": surety_id, "poa_prefix": doc.get("poa_prefix", poa_prefix), "status": "available"}
    )

    return {
        "success": True,
        "poa_number": poa_number,
        "poa_prefix": doc.get("poa_prefix", poa_prefix),
        "poa_full": doc.get("poa_full", f"{poa_prefix} {poa_number}"),
        "surety_id": surety_id,
        "bond_case_id": str(bond_case_id),
        "remaining_in_tier": remaining,
    }


@poa_bp.get("/poa/inventory")
async def api_poa_inventory(surety: str | None = Query(default=None)):
    """Return a summary of available POA inventory by surety and tier."""
    poa_inventory = get_collection("poa_inventory")

    surety_filter = (surety or "").lower().strip()
    match = {"status": "available"}
    if surety_filter in ("osi", "palmetto"):
        match["surety_id"] = surety_filter

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"surety_id": "$surety_id", "poa_prefix": "$poa_prefix", "max_bond_value": "$max_bond_value"},
            "available": {"$sum": 1},
            "next_serial": {"$min": "$poa_number"},
        }},
        {"$sort": {"_id.surety_id": 1, "_id.max_bond_value": 1}},
    ]
    result = []
    async for r in poa_inventory.aggregate(pipeline):
        result.append({
            "surety_id": r["_id"]["surety_id"],
            "poa_prefix": r["_id"]["poa_prefix"],
            "max_bond_value": r["_id"]["max_bond_value"],
            "available": r["available"],
            "next_serial": r["next_serial"],
            "next_poa_full": f"{r['_id']['poa_prefix']} {r['next_serial']}",
        })

    totals = {
        "osi": sum(r["available"] for r in result if r["surety_id"] == "osi"),
        "palmetto": sum(r["available"] for r in result if r["surety_id"] == "palmetto"),
    }
    return {"tiers": result, "totals": totals}


@poa_bp.get("/poa/inventory-summary")
async def api_poa_inventory_summary():
    """Lightweight summary for the dashboard low-stock alert banner.
    Returns tiers with available count, prefix, and surety label."""
    poa_inventory = get_collection("poa_inventory")

    pipeline = [
        {"$match": {"status": "available"}},
        {"$group": {
            "_id": {"surety_id": "$surety_id", "poa_prefix": "$poa_prefix"},
            "available": {"$sum": 1},
        }},
        {"$sort": {"_id.surety_id": 1, "_id.poa_prefix": 1}},
    ]
    tiers = []
    async for r in poa_inventory.aggregate(pipeline):
        surety_label = "Palmetto Surety" if r["_id"]["surety_id"] == "palmetto" else "OSI"
        tiers.append({
            "prefix": r["_id"]["poa_prefix"],
            "surety": surety_label,
            "surety_id": r["_id"]["surety_id"],
            "available": r["available"],
        })

    return {"tiers": tiers}


@poa_bp.get("/poa/list")
async def api_poa_list(
    request: Request,
    page: int = Query(default=1),
    limit: int = Query(default=50),
    surety: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
):
    """Paginated list of POA powers. Sub-agents only see powers assigned to them."""
    poa_inventory = get_collection("poa_inventory")

    page = max(1, int(page or 1))
    limit = min(200, max(1, int(limit or 50)))
    surety = (surety or "").lower().strip()
    status = (status or "").lower().strip()
    search = (search or "").strip()

    match: dict = {}
    if surety in ("osi", "palmetto"):
        match["surety_id"] = surety
    if status in ("available", "assigned", "voided"):
        match["status"] = status
    if search:
        match["$or"] = [
            {"poa_number": {"$regex": search, "$options": "i"}},
            {"poa_full": {"$regex": search, "$options": "i"}},
            {"bond_case_id": {"$regex": search, "$options": "i"}},
        ]

    try:
        from dashboard.auth.agent_scope import merge_scope, poa_scope_query

        match = merge_scope(match, poa_scope_query(request))
    except Exception:
        pass

    total = await poa_inventory.count_documents(match)
    pages = max(1, (total + limit - 1) // limit)
    skip = (page - 1) * limit

    cursor = poa_inventory.find(
        match,
        {"_id": 0, "poa_number": 1, "poa_full": 1, "poa_prefix": 1,
         "surety_id": 1, "max_bond_value": 1, "status": 1,
         "bond_case_id": 1, "defendant_name": 1, "defendant_first_name": 1,
         "defendant_last_name": 1, "charge": 1, "appearance_bond_number": 1,
         "used_at": 1, "expiration": 1, "date_executed": 1, "bond_amount": 1, "amount": 1,
         "assigned_to_agent": 1, "agent_license": 1},
    ).sort([("surety_id", 1), ("poa_prefix", 1), ("poa_number", 1)]).skip(skip).limit(limit)

    powers = []
    async for doc in cursor:
        powers.append(doc)

    return {"powers": powers, "total": total, "page": page, "pages": pages}




@poa_bp.post("/poa/execute")
async def api_poa_execute(request: Request):
    """
    Execute/record details for a Power of Attorney (POA) number.
    Fields:
    - poa_number (required)
    - poa_prefix (optional/recommended, determines surety on first use)
    - date_executed (optional, defaults to current date YYYY-MM-DD)
    - amount / bond_amount (optional, float)
    - defendant_first_name (optional)
    - defendant_last_name (optional)
    - charge (optional)
    - surety_id (optional, derived from prefix if omitted)
    """
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}

    poa_number = str(body.get("poa_number", "")).strip()
    if not poa_number:
        return JSONResponse({"error": "poa_number is required"}, status_code=400)

    poa_prefix = str(body.get("poa_prefix", "")).strip()
    provided_surety = str(body.get("surety_id", "")).lower().strip()
    surety_id = determine_surety_from_prefix(poa_prefix, provided_surety)

    date_executed = str(body.get("date_executed", "")).strip()
    if not date_executed:
        date_executed = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        raw_amt = body.get("amount") if body.get("amount") is not None else body.get("bond_amount", 0)
        amount = float(raw_amt or 0)
    except (ValueError, TypeError):
        amount = 0.0

    def_first = str(body.get("defendant_first_name", "")).strip()
    def_last = str(body.get("defendant_last_name", "")).strip()
    def_name = str(body.get("defendant_name", "")).strip()
    if not def_name and (def_first or def_last):
        def_name = f"{def_first} {def_last}".strip()

    charge = str(body.get("charge", "")).strip() or None

    now_iso = datetime.now(timezone.utc).isoformat()

    gross_premium = max(100.0, amount * 0.10) if amount > 0 else 0.0
    surety_rate = 7.50 if surety_id == "osi" else 10.00
    surety_owed = round(gross_premium * (surety_rate / 100.0), 2)
    buf_owed = round(gross_premium * (5.00 / 100.0), 2)
    agent_retains = round(gross_premium - surety_owed - buf_owed, 2)

    doc = await poa_inventory.find_one({"poa_number": poa_number})

    update_fields = {
        "status": "assigned",
        "poa_number": poa_number,
        "surety_id": surety_id,
        "date_executed": date_executed,
        "executed_at": date_executed,
        "bond_amount": amount,
        "amount": amount,
        "gross_premium": gross_premium,
        "surety_owed": surety_owed,
        "buf_owed": buf_owed,
        "agent_retains": agent_retains,
        "defendant_first_name": def_first,
        "defendant_last_name": def_last,
        "defendant_name": def_name or None,
        "charge": charge,
        "used_at": now_iso,
    }

    if poa_prefix:
        update_fields["poa_prefix"] = poa_prefix
        update_fields["poa_full"] = f"{poa_prefix} {poa_number}"
    elif doc and doc.get("poa_prefix"):
        update_fields["poa_full"] = f"{doc.get('poa_prefix')} {poa_number}"
    else:
        update_fields["poa_full"] = poa_number

    if doc:
        await poa_inventory.update_one({"_id": doc["_id"]}, {"$set": update_fields})
    else:
        update_fields.update({
            "max_bond_value": amount,
            "received_at": now_iso,
            "book_number": f"manual_exec_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            "assigned_to_agent": "Brendan O'Neal",
            "bond_case_id": f"EXEC-{poa_number}",
        })
        await poa_inventory.insert_one(update_fields)

    # Sync to active_bonds for liability reports and statement calculations
    active_bonds = get_collection("active_bonds")
    booking_key = f"POA-{poa_number}"
    bond_doc = {
        "booking_number": booking_key,
        "poa_number": poa_number,
        "poa_prefix": update_fields.get("poa_prefix", ""),
        "poa_full": update_fields.get("poa_full", poa_number),
        "surety": surety_id.upper(),
        "surety_id": surety_id,
        "insurance_company": "Palmetto Surety Corporation" if surety_id == "palmetto" else "O'Shaughnahill Surety & Insurance",
        "bond_date": date_executed,
        "posted_date": date_executed,
        "created_at": date_executed,
        "bond_amount": amount,
        "amount": amount,
        "gross_premium": gross_premium,
        "surety_owed": surety_owed,
        "buf_owed": buf_owed,
        "agent_retains": agent_retains,
        "defendant_first_name": def_first,
        "defendant_last_name": def_last,
        "defendant_name": def_name or None,
        "charge": charge,
        "status": "active",
        "agent_name": "Brendan O'Neal",
        "updated_at": now_iso,
    }
    await active_bonds.update_one(
        {"$or": [{"poa_number": poa_number}, {"booking_number": booking_key}]},
        {"$set": bond_doc},
        upsert=True,
    )

    return {
        "success": True,
        "poa_number": poa_number,
        "poa_prefix": update_fields.get("poa_prefix", ""),
        "poa_full": update_fields.get("poa_full", poa_number),
        "surety_id": surety_id,
        "date_executed": date_executed,
        "amount": amount,
        "gross_premium": gross_premium,
        "surety_owed": surety_owed,
        "buf_owed": buf_owed,
        "agent_retains": agent_retains,
        "defendant_name": def_name,
        "defendant_first_name": def_first,
        "defendant_last_name": def_last,
        "charge": charge,
    }




# Hard safety rails — a single power-sheet line never issues thousands of POAs.
# (OCR once misread end serial as …-6828 → 6828 phantom powers.)
_MAX_RANGE_PER_LINE = 200
_MAX_TOTAL_POWERS = 500


def _normalize_poa_ocr_text(text: str) -> str:
    """Normalize common OCR mistakes before serial extraction."""
    import re
    if not text:
        return ""
    t = text.replace("\u00a0", " ")

    # Fix OCR typos in dollar amounts / quantities
    t = re.sub(r"\b008\b", r"$3,000", t)
    t = re.sub(r"\b8[\.,]?000\b", r"$6,000", t)
    t = re.sub(r"\b1600\b", r"$16,000", t)
    t = re.sub(r"\b51[\.,]?000\b", r"$51,000", t)

    # Fix OCR typos near OSI / Palmetto prefixes
    t = re.sub(r"\bGSI\-F(\d+)", r"OSI-P\1", t, flags=re.IGNORECASE)
    t = re.sub(r"\bOst\s+P(\d+)", r"OSI-P\1", t, flags=re.IGNORECASE)
    t = re.sub(r"\bO81\-P(\d+)", r"OSI-P\1", t, flags=re.IGNORECASE)
    t = re.sub(r"\b051\-P(\d+)", r"OSI-P\1", t, flags=re.IGNORECASE)
    t = re.sub(r"\bOSL\-6\-", r"OSI-P16-", t, flags=re.IGNORECASE)
    t = re.sub(r"\bOSL\-(\d+)", r"OSI-P\1", t, flags=re.IGNORECASE)
    t = re.sub(r"@S1\-PLO\-", r"OSI-P16-", t, flags=re.IGNORECASE)
    t = re.sub(r"@S1\-PL?O?\-(\d+)", r"OSI-P\1", t, flags=re.IGNORECASE)
    t = re.sub(r"\bO61\-P(\d+)", r"OSI-P\1", t, flags=re.IGNORECASE)
    t = re.sub(r"@st\-PS1\-(\d+)", r"OSI-P\1", t, flags=re.IGNORECASE)

    # Agency/year code typos (136.26, 118.28, 11625, 11626 -> 116-26)
    t = re.sub(r"136[\.\-]26", "116-26", t)
    t = re.sub(r"118[\.\-]28", "116-26", t)
    t = re.sub(r"11625", "116-26", t)
    t = re.sub(r"11626", "116-26", t)

    # Standardize hyphen formatting
    t = re.sub(r"\bOS[l1I][\- ]", "OSI-", t, flags=re.IGNORECASE)
    t = re.sub(r"\b0SI[\- ]", "OSI-", t, flags=re.IGNORECASE)
    t = re.sub(r"\bOS1-P", "OSI-P", t, flags=re.IGNORECASE)
    t = re.sub(r"\bOSI\s+P\s*", "OSI-P", t, flags=re.IGNORECASE)
    t = re.sub(r"\bOSI\-P\s+(\d)", r"OSI-P\1", t, flags=re.IGNORECASE)
    t = re.sub(r"\bPSC\s+(\d)", r"PSC\1", t, flags=re.IGNORECASE)
    # Collapse multi-spaces but keep newlines for line parser
    t = re.sub(r"[ \t]+", " ", t)
    return t


def _is_poa_serial(token: str) -> bool:
    """True if token looks like a real power serial (not a phone/zip fragment)."""
    import re
    t = (token or "").strip().upper()
    if not t:
        return False
    # New OSI book: OSI-P3-116-26-0001
    if re.fullmatch(r"OSI-P\d+(?:-\d+){2,}", t):
        return True
    # Legacy compact: OSI3-20134295 or OSI320134295
    if re.fullmatch(r"OSI\d{1,3}-\d{5,}", t) or re.fullmatch(r"OSI\d{6,}", t):
        return True
    # Palmetto: PSC5-2644670
    if re.fullmatch(r"PSC\d{1,3}-\d{4,}", t) or re.fullmatch(r"PSC\d{6,}", t):
        return True
    return False


def _split_serial_stem_suffix(serial: str) -> tuple[str, str, int] | None:
    """Return (stem_with_delim_prefix, zero_padded_suffix, int_suffix) or None."""
    import re
    s = (serial or "").strip()
    m = re.search(r"^(.*?)[- ]?(\d+)$", s)
    if not m:
        return None
    stem, digits = m.group(1), m.group(2)
    # Prefer hyphen join for OSI-P… stems
    if stem.endswith("-") or stem.endswith(" "):
        join_stem = stem
    elif "-" in s:
        join_stem = stem + "-"
    elif " " in s:
        join_stem = stem + " "
    else:
        join_stem = stem
    return join_stem, digits, int(digits)


def parse_poa_receipt_text(text: str, default_surety: str = "osi") -> list[dict]:
    """
    Parse text from PDF, OCR image, or text file to extract structured POA powers.
    Matches lines like:
    $3,000  17  OSI-P3-116-26-0001 to OSI-P3-116-26-0017  4-Feb-27
    $6,000  13  OSI-P6-116-26-0001 to OSI-P6-116-26-0013  4-Feb-27
    $16,000 16  OSI-P16-116-26-0001 to OSI-P16-116-26-0016 4-Feb-27
    $51,000  4  OSI-P51-116-26-0001 to OSI-P51-116-26-0004 4-Feb-27
    Also supports legacy forms: OSI3 20134295 to 20134324, PSC5 2644670-2644777

    Safety: quantity on the line wins over a mis-OCR'd end serial; ranges are
    hard-capped so a bad end like …-6828 cannot invent thousands of powers.
    """
    import re
    from dashboard.services.poa_service import parse_max_bond_from_prefix, determine_surety_from_prefix

    text = _normalize_poa_ocr_text(text or "")
    results = []
    lines = text.splitlines()

    # Optional global total printed on OSI receipts
    total_claimed = None
    m_total = re.search(
        r"Total\s+Powers?\s+Assigned\s*:\s*(\d{1,4})",
        text,
        re.IGNORECASE,
    )
    if m_total:
        total_claimed = int(m_total.group(1))

    # ONLY "to" / "thru" / en-dash as range separators — never a bare hyphen
    # (bare "-" matches inside serials / phone numbers and invents huge ranges).
    # Require BOTH ends to look like POA serials.
    range_regex = re.compile(
        r"(?:(?P<val>\$[\d,]+)\s+)?"
        r"(?:(?P<qty>\d{1,3})\s+)?"
        r"(?P<start>OSI-P\d+(?:-\d+)+|OSI\d+(?:-\d+)?|PSC\d+(?:-\d+)?)\s+"
        r"(?:to|thru|through|–|—)\s+"
        r"(?P<end>OSI-P\d+(?:-\d+)+|OSI\d+(?:-\d+)?|PSC\d+(?:-\d+)?)"
        r"(?:\s+(?P<exp>\d{1,2}\-[A-Za-z]{3}\-\d{2,4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}\-\d{2}\-\d{2}))?",
        re.IGNORECASE,
    )

    def parse_exp_date(exp_str):
        if not exp_str:
            return None
        exp_clean = exp_str.strip()
        # Parse 4-Feb-27 or 4-Feb-2027
        m = re.match(r"^(\d{1,2})\-([A-Za-z]{3})\-(\d{2,4})$", exp_clean)
        if m:
            d, mon, y = m.groups()
            months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
            m_num = months.get(mon.lower(), 12)
            year_num = int(y)
            if year_num < 100:
                year_num += 2000
            return f"{year_num:04d}-{m_num:02d}-{int(d):02d}"
        return exp_clean

    def expand_range(start_str: str, end_str: str, qty: int | None, val_str: str | None, exp_str: str | None):
        """Expand start→end with qty/caps. Returns list of item dicts."""
        if not _is_poa_serial(start_str):
            return []
        start_parts = _split_serial_stem_suffix(start_str)
        if not start_parts:
            return []
        join_stem, s_digits, s_int = start_parts
        # Always pad from the START serial (end is often OCR garbage / glued date)
        pad_len = len(s_digits)

        e_int = None
        if end_str and _is_poa_serial(end_str):
            end_parts = _split_serial_stem_suffix(end_str)
            if end_parts:
                end_stem, e_digits, e_candidate = end_parts
                # Stems must agree (ignore trailing delim)
                stem_a = join_stem.rstrip("- ").upper()
                stem_b = end_stem.rstrip("- ").upper()
                if stem_a == stem_b or stem_b.startswith(stem_a) or stem_a.startswith(stem_b):
                    # Reject end suffixes that look like start+date glued (…00174)
                    # unless they match qty or are a small sequential bump.
                    e_int = e_candidate

        # Fix OCR noise where start serial suffix (e.g. 0601, 8001, 0061) is larger than qty or end serial
        if qty and (s_int > qty or (e_int is not None and s_int > e_int)):
            s_int = 1

        # Quantity on the receipt line is authoritative when present
        # (OCR frequently corrupts the END serial into garbage like …-6828).
        if qty and 1 <= qty <= _MAX_RANGE_PER_LINE:
            e_int = s_int + qty - 1
        elif e_int is None:
            return []

        if e_int < s_int:
            return []

        span = e_int - s_int + 1
        if span > _MAX_RANGE_PER_LINE:
            # Prefer qty if available; otherwise refuse the exploding range
            if qty and 1 <= qty <= _MAX_RANGE_PER_LINE:
                e_int = s_int + qty - 1
                span = qty
            else:
                logger.warning(
                    "POA range refused: %s → …%s spans %s (cap %s)",
                    start_str, e_int, span, _MAX_RANGE_PER_LINE,
                )
                return []
        max_bond = 0.0
        if val_str:
            try:
                max_bond = float(val_str.replace("$", "").replace(",", ""))
            except ValueError:
                pass
        if not max_bond:
            max_bond = parse_max_bond_from_prefix(start_str)

        surety_id = determine_surety_from_prefix(start_str) or default_surety
        exp_formatted = parse_exp_date(exp_str)
        pfx_match = re.search(r"^(OSI-?P?\d+|PSC\d+|PAL\d+)", start_str, re.IGNORECASE)
        poa_prefix = pfx_match.group(1).upper() if pfx_match else join_stem.rstrip("- ")

        items = []
        for seq in range(s_int, e_int + 1):
            seq_str = f"{seq:0{pad_len}d}"
            poa_num = f"{join_stem}{seq_str}" if join_stem.endswith(("-", " ")) else f"{join_stem}-{seq_str}"
            # normalize double hyphens
            poa_num = re.sub(r"-{2,}", "-", poa_num)
            items.append({
                "poa_number": poa_num.upper(),
                "poa_prefix": poa_prefix,
                "poa_full": poa_num.upper(),
                "max_bond_value": max_bond,
                "surety_id": surety_id,
                "expiration": exp_formatted,
            })
        return items

    for line in lines:
        line_s = line.strip()
        if not line_s:
            continue

        # If a range pattern is present on the line, never fall through to
        # single-serial matching (avoids adding a bogus …-6828 end as a single).
        range_hits = list(range_regex.finditer(line_s))
        if range_hits:
            for m in range_hits:
                qty_raw = m.group("qty")
                qty = int(qty_raw) if qty_raw and qty_raw.isdigit() else None
                # Guard: qty of 0001-style serial fragments mis-captured — only 1–200
                if qty is not None and not (1 <= qty <= _MAX_RANGE_PER_LINE):
                    qty = None
                expanded = expand_range(
                    m.group("start"),
                    m.group("end"),
                    qty,
                    m.group("val"),
                    m.group("exp"),
                )
                if expanded:
                    results.extend(expanded)
            continue

        # Single POA pattern match if line wasn't a range
        # New OSI book: OSI-P3-116-26-0001 · legacy: OSI3-20134295 · PSC5-2644670
        for m_single in re.finditer(
            r"\b(OSI-P\d+(?:-\d+){2,}|OSI\d{1,3}-\d{5,}|OSI\d{6,}|PSC\d{1,3}-\d{4,}|PSC\d{6,})\b",
            line_s,
            re.IGNORECASE,
        ):
            poa_num = m_single.group(1).strip().upper()
            if not _is_poa_serial(poa_num):
                continue
            surety_id = determine_surety_from_prefix(poa_num) or default_surety
            max_bond = parse_max_bond_from_prefix(poa_num)
            pfx_match = re.search(r"^(OSI-?P?\d+|PSC\d+|PAL\d+)", poa_num, re.IGNORECASE)
            poa_prefix = pfx_match.group(1).upper() if pfx_match else "OSI"
            results.append({
                "poa_number": poa_num,
                "poa_prefix": poa_prefix,
                "poa_full": poa_num,
                "max_bond_value": max_bond,
                "surety_id": surety_id,
                "expiration": None,
            })

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for item in results:
        key = (item.get("surety_id"), item.get("poa_number"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    # Global caps — total on sheet or absolute safety limit
    if total_claimed and 1 <= total_claimed <= _MAX_TOTAL_POWERS:
        hard_cap = total_claimed
        if len(deduped) > hard_cap:
            logger.warning(
                "POA parse truncated %s → %s (receipt claims Total Powers=%s)",
                len(deduped), hard_cap, total_claimed,
            )
            deduped = deduped[:hard_cap]
    elif len(deduped) > _MAX_TOTAL_POWERS:
        logger.warning(
            "POA parse truncated %s → %s (absolute cap)",
            len(deduped), _MAX_TOTAL_POWERS,
        )
        deduped = deduped[:_MAX_TOTAL_POWERS]

    return deduped


def _looks_like_text_file(filename: str, content_bytes: bytes) -> bool:
    """True only for real text/CSV uploads — never binary images."""
    name = (filename or "").lower()
    if name.endswith((".txt", ".csv", ".tsv", ".log", ".md")):
        return True
    if name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".pdf")):
        return False
    # Magic-byte guards
    if content_bytes.startswith(b"%PDF") or content_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return False
    if content_bytes[:2] in (b"\xff\xd8", b"II", b"MM") or content_bytes[:4] in (b"RIFF", b"GIF8"):
        return False
    # Heuristic: mostly printable → treat as text
    sample = content_bytes[:2048]
    if not sample:
        return False
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return (printable / len(sample)) > 0.85


def _extract_text_from_pdf(content_bytes: bytes) -> str:
    import io
    text = ""
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(content_bytes))
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
    except Exception as exc:
        logger.warning("pypdf extract failed: %s", exc)
    if len((text or "").strip()) >= 20:
        return text
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
            parts = []
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
            text = "\n".join(parts)
    except Exception as exc:
        logger.warning("pdfplumber extract failed: %s", exc)
    return text or ""


def _tesseract_cli(image_path: str, psm: int = 6) -> str:
    """Run system tesseract binary (dashboard Dockerfile installs it)."""
    import shutil
    import subprocess
    if not shutil.which("tesseract"):
        return ""
    try:
        proc = subprocess.run(
            ["tesseract", image_path, "stdout", "--psm", str(psm), "-l", "eng"],
            capture_output=True,
            timeout=90,
            check=False,
        )
        out = (proc.stdout or b"").decode("utf-8", errors="ignore")
        if proc.returncode != 0 and not out.strip():
            err = (proc.stderr or b"").decode("utf-8", errors="ignore")[:200]
            logger.warning("tesseract exit %s: %s", proc.returncode, err)
        return out
    except Exception as exc:
        logger.warning("tesseract CLI failed: %s", exc)
        return ""


def _extract_text_from_image(content_bytes: bytes) -> str:
    """OCR a receipt image. Prefer tesseract (document OCR); ddddocr is captcha-oriented only."""
    import io
    import os
    import tempfile
    text = ""
    try:
        from PIL import Image, ImageOps, ImageFilter
        img = Image.open(io.BytesIO(content_bytes))
        # HEIC / CMYK / RGBA → RGB for tesseract
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # Light preprocess: grayscale + mild contrast for receipt scans
        gray = ImageOps.grayscale(img)
        gray = ImageOps.autocontrast(gray)
        try:
            gray = gray.filter(ImageFilter.SHARPEN)
        except Exception:
            pass

        with tempfile.TemporaryDirectory(prefix="poa_ocr_") as tmp:
            gray_path = os.path.join(tmp, "gray.png")
            color_path = os.path.join(tmp, "color.png")
            gray.save(gray_path, format="PNG")
            img.save(color_path, format="PNG")

            # System binary first (reliable in Docker; no pytesseract required)
            text = _tesseract_cli(gray_path, psm=6)
            if len((text or "").strip()) < 20:
                alt = _tesseract_cli(color_path, psm=4)
                if len((alt or "").strip()) > len((text or "").strip()):
                    text = alt
            if len((text or "").strip()) < 20:
                alt = _tesseract_cli(gray_path, psm=3)
                if len((alt or "").strip()) > len((text or "").strip()):
                    text = alt

            # Optional python wrapper if installed
            if len((text or "").strip()) < 15:
                try:
                    import pytesseract
                    text = pytesseract.image_to_string(gray, config="--psm 6") or text
                except Exception as tess_exc:
                    logger.debug("pytesseract unavailable/failed: %s", tess_exc)

        if len((text or "").strip()) < 10:
            # ddddocr is for short captchas — last-ditch only
            try:
                import ddddocr
                ocr = ddddocr.DdddOcr(show_ad=False)
                text = ocr.classification(content_bytes) or text
            except Exception as ddd_exc:
                logger.warning("ddddocr fallback failed: %s", ddd_exc)
    except Exception as exc:
        logger.warning("Image OCR error: %s", exc)
    return text or ""


@poa_bp.post("/poa/upload-image")
async def api_poa_upload_image(request: Request):
    """
    Upload and parse a POA inventory receipt (PDF, image, or text file).
    Extracts structured power numbers, max bond values, quantities, and expiration dates.

    IMPORTANT: never UTF-8-decode binary images — that produced garbage text and
    skipped OCR, which caused "No POA serial numbers could be extracted".
    """
    try:
        form = await request.form()
    except Exception as exc:
        return JSONResponse(
            {"success": False, "error": f"Could not read upload form (file too large or invalid): {exc}"},
            status_code=400,
        )

    file_obj = form.get("file")
    surety_id = str(form.get("surety_id", "osi")).lower().strip() or "osi"

    if not file_obj:
        return JSONResponse({"success": False, "error": "No file uploaded"}, status_code=400)

    filename = getattr(file_obj, "filename", "") or "file"
    try:
        content_bytes = await file_obj.read()
    except Exception as exc:
        return JSONResponse(
            {"success": False, "error": f"Failed to read uploaded file: {exc}"},
            status_code=400,
        )

    if not content_bytes:
        return JSONResponse({"success": False, "error": "Uploaded file is empty"}, status_code=400)

    # Soft limit messaging (nginx default used to be 1m → false 413 on 2–3MB photos)
    size_mb = len(content_bytes) / (1024 * 1024)
    if size_mb > 50:
        return JSONResponse(
            {
                "success": False,
                "error": f"File is {size_mb:.1f}MB — max is 50MB. Compress the photo or use a PDF export.",
            },
            status_code=413,
        )

    name_l = filename.lower()
    is_pdf = name_l.endswith(".pdf") or content_bytes.startswith(b"%PDF")
    is_image = (
        name_l.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"))
        or content_bytes.startswith(b"\x89PNG")
        or content_bytes[:2] == b"\xff\xd8"
        or content_bytes[:4] in (b"RIFF", b"GIF8")
    )
    is_text = _looks_like_text_file(filename, content_bytes)

    extracted_text = ""
    method = "none"

    try:
        if is_pdf:
            extracted_text = _extract_text_from_pdf(content_bytes)
            method = "pdf"
            # Scanned PDF with no text layer → rasterize first page via pdf2image if available,
            # else ask user for a photo; also try OCR on embedded images is out of scope.
            if len((extracted_text or "").strip()) < 20:
                # Fallback: some "PDFs" are actually images wrapped poorly — try OCR on raw bytes
                ocr_try = _extract_text_from_image(content_bytes)
                if len((ocr_try or "").strip()) > len((extracted_text or "").strip()):
                    extracted_text = ocr_try
                    method = "pdf+ocr"
        elif is_image:
            extracted_text = _extract_text_from_image(content_bytes)
            method = "ocr"
        elif is_text:
            extracted_text = content_bytes.decode("utf-8", errors="ignore")
            method = "text"
        else:
            # Unknown: try PDF → image → text in that order without treating binary as text first
            if content_bytes.startswith(b"%PDF"):
                extracted_text = _extract_text_from_pdf(content_bytes)
                method = "pdf"
            else:
                try:
                    from PIL import Image
                    import io
                    Image.open(io.BytesIO(content_bytes))
                    extracted_text = _extract_text_from_image(content_bytes)
                    method = "ocr"
                except Exception:
                    if _looks_like_text_file(filename, content_bytes):
                        extracted_text = content_bytes.decode("utf-8", errors="ignore")
                        method = "text"
    except Exception as exc:
        logger.exception("POA upload extract error: %s", exc)
        return JSONResponse(
            {"success": False, "error": f"Failed to process file: {exc}"},
            status_code=500,
        )

    if not (extracted_text or "").strip():
        return JSONResponse(
            {
                "success": False,
                "error": (
                    "Could not read text from this file. "
                    "Use a clear photo/scan of the power receipt (JPG/PNG) or a text PDF. "
                    "HEIC from iPhone may need to be saved as JPEG first if OCR is unavailable."
                ),
                "filename": filename,
                "method": method,
            },
            status_code=400,
        )

    parsed_items = parse_poa_receipt_text(extracted_text, default_surety=surety_id)
    extracted_serials = [item["poa_number"] for item in parsed_items]

    return {
        "success": True,
        "filename": filename,
        "method": method,
        "extracted_count": len(extracted_serials),
        "extracted": extracted_serials,
        "items": parsed_items,
        "raw_text_preview": (extracted_text or "")[:800],
        "message": (
            None
            if extracted_serials
            else "Text was read but no POA serials matched. Check the preview or use manual entry."
        ),
    }


@poa_bp.post("/poa/add")
async def api_poa_add(request: Request):
    """Add one or more POA numbers to inventory (manual replenishment or upload confirmation)."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}

    # Support adding structured items directly (from upload confirmation)
    items = body.get("items", [])
    if items and isinstance(items, list):
        docs = []
        skipped = 0
        now_str = datetime.now(timezone.utc).isoformat()
        for item in items:
            p_num = str(item.get("poa_number", "")).strip()
            s_id = str(item.get("surety_id", body.get("surety_id", "osi"))).lower().strip()
            p_prefix = str(item.get("poa_prefix", body.get("poa_prefix", "OSI-P3"))).strip()
            max_val = float(item.get("max_bond_value") or body.get("max_bond_value") or parse_max_bond_from_prefix(p_prefix) or 3000)
            exp = item.get("expiration") or body.get("expiration")

            if not p_num:
                continue
            existing = await poa_inventory.find_one({"poa_number": p_num, "surety_id": s_id})
            if existing:
                skipped += 1
                continue

            docs.append({
                "surety_id": s_id,
                "poa_prefix": p_prefix,
                "poa_number": p_num,
                "poa_full": item.get("poa_full", p_num),
                "max_bond_value": max_val,
                "status": "available",
                "expiration": exp,
                "book_number": f"upload_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                "assigned_to_agent": "Brendan O'Neal",
                "received_at": now_str,
                "bond_case_id": None,
                "used_at": None,
            })

        if docs:
            await poa_inventory.insert_many(docs, ordered=False)

        return {
            "success": True,
            "count": len(docs),
            "skipped": skipped,
            "surety_id": body.get("surety_id", "osi"),
        }

    # Manual form addition (start & end)
    surety_id = str(body.get("surety_id", "")).lower().strip()
    poa_prefix = str(body.get("poa_prefix", "")).strip()
    start = str(body.get("start", "")).strip()
    end = str(body.get("end", start)).strip()
    max_bond = body.get("max_bond_value", 0)
    if not max_bond:
        max_bond = parse_max_bond_from_prefix(poa_prefix)
    expiration = body.get("expiration")

    if not surety_id or surety_id not in ("osi", "palmetto"):
        return JSONResponse({"error": "surety_id must be 'osi' or 'palmetto'"}, status_code=400)
    if not poa_prefix or not start:
        return JSONResponse({"error": "poa_prefix and start are required"}, status_code=400)

    import re
    # Check if start is full string like OSI-P3-116-26-0001
    m_start = re.search(r"^(.*?)(?:-(\d+)|\s+(\d+)|(\d+))$", start)
    m_end = re.search(r"^(.*?)(?:-(\d+)|\s+(\d+)|(\d+))$", end)

    if m_start and m_end and (m_start.group(2) or m_start.group(3) or m_start.group(4)):
        prefix_stem = m_start.group(1)
        s_digits = m_start.group(2) or m_start.group(3) or m_start.group(4)
        e_digits = m_end.group(2) or m_end.group(3) or m_end.group(4) or s_digits
        start_int = int(s_digits)
        end_int = int(e_digits)
        pad_len = len(s_digits)
        delim = "-" if "-" in start else (" " if " " in start else "")
    else:
        try:
            start_int = int(start)
            end_int = int(end)
            pad_len = len(start)
            prefix_stem = poa_prefix
            delim = " " if not poa_prefix.endswith("-") else ""
        except ValueError:
            return JSONResponse({"error": "start and end serial numbers must be numeric"}, status_code=400)

    if end_int < start_int:
        return JSONResponse({"error": "end must be >= start"}, status_code=400)
    if (end_int - start_int) > 500:
        return JSONResponse({"error": "Cannot add more than 500 at once"}, status_code=400)

    docs = []
    skipped = 0
    now_str = datetime.now(timezone.utc).isoformat()

    for serial in range(start_int, end_int + 1):
        seq_str = f"{serial:0{pad_len}d}"
        if prefix_stem == poa_prefix:
            poa_num = f"{poa_prefix}{delim}{seq_str}".strip()
        else:
            poa_num = f"{prefix_stem}{delim}{seq_str}".strip()

        existing = await poa_inventory.find_one({"poa_number": poa_num, "surety_id": surety_id})
        if existing:
            skipped += 1
            continue

        docs.append({
            "surety_id": surety_id,
            "poa_prefix": poa_prefix,
            "poa_number": poa_num,
            "poa_full": poa_num,
            "max_bond_value": float(max_bond or 0),
            "status": "available",
            "expiration": expiration,
            "book_number": f"manual_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            "assigned_to_agent": "Brendan O'Neal",
            "received_at": now_str,
            "bond_case_id": None,
            "used_at": None,
        })

    if docs:
        await poa_inventory.insert_many(docs, ordered=False)

    return {
        "success": True,
        "count": len(docs),
        "skipped": skipped,
        "surety_id": surety_id,
        "poa_prefix": poa_prefix,
    }


@poa_bp.post("/poa/void")
async def api_poa_void(request: Request):
    """Mark a POA as voided (unusable)."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    reason = body.get("reason", "Manual void")

    if not poa_number or not surety_id:
        return JSONResponse({"error": "poa_number and surety_id required"}, status_code=400)

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return JSONResponse({"error": f"POA {poa_number} not found"}, status_code=404)

    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {
            "status": "voided",
            "voided_at": datetime.now(timezone.utc).isoformat(),
            "void_reason": reason,
        }},
    )
    return {"success": True, "poa_number": poa_number, "message": f"POA {poa_number} voided"}


@poa_bp.post("/poa/release")
async def api_poa_release(request: Request):
    """Release an assigned POA back to available status."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()

    if not poa_number or not surety_id:
        return JSONResponse({"error": "poa_number and surety_id required"}, status_code=400)

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return JSONResponse({"error": f"POA {poa_number} not found"}, status_code=404)
    if doc.get("status") != "assigned":
        return JSONResponse({"error": f"POA {poa_number} is {doc.get('status')}, not assigned"}, status_code=409)

    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {"status": "available", "bond_case_id": None, "used_at": None},
         "$unset": {"voided_at": "", "void_reason": ""}},
    )
    return {"success": True, "poa_number": poa_number, "message": f"POA {poa_number} released back to available"}


@poa_bp.post("/poa/reassign")
async def api_poa_reassign(request: Request):
    """Reassign a POA from one case to another."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    new_booking = str(body.get("new_booking_number", "")).strip()

    if not poa_number or not surety_id or not new_booking:
        return JSONResponse({"error": "poa_number, surety_id, and new_booking_number required"}, status_code=400)

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return JSONResponse({"error": f"POA {poa_number} not found"}, status_code=404)

    old_case = doc.get("bond_case_id", "none")
    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {
            "status": "assigned",
            "bond_case_id": new_booking,
            "used_at": datetime.now(timezone.utc).isoformat(),
            "reassigned_from": old_case,
        }},
    )
    return {
        "success": True, "poa_number": poa_number,
        "message": f"POA {poa_number} reassigned from {old_case} → {new_booking}",
    }


@poa_bp.post("/poa/restore")
async def api_poa_restore(request: Request):
    """Restore a voided POA back to available."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()

    if not poa_number or not surety_id:
        return JSONResponse({"error": "poa_number and surety_id required"}, status_code=400)

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return JSONResponse({"error": f"POA {poa_number} not found"}, status_code=404)
    if doc.get("status") != "voided":
        return JSONResponse({"error": f"POA {poa_number} is {doc.get('status')}, not voided"}, status_code=409)

    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {"status": "available", "bond_case_id": None, "used_at": None},
         "$unset": {"voided_at": "", "void_reason": ""}},
    )
    return {"success": True, "poa_number": poa_number, "message": f"POA {poa_number} restored to available"}


@poa_bp.post("/poa/bulk-assign")
async def api_poa_bulk_assign(request: Request):
    """Assign multiple POAs to a single defendant/case in one operation.

    Supports two formats:

    NEW format (charge-level mapping):
    {
        assignments: [
            { poa_number: "12345", charge: "BATTERY", appearance_bond_number: "26-CF-001234" },
            { poa_number: "12346", charge: "DUI", appearance_bond_number: "26-CF-001235" }
        ],
        surety_id: "osi" | "palmetto",
        bond_case_id: "booking_number or case reference",
        defendant_name: "optional — for audit trail"
    }

    LEGACY format (flat list, no charge data):
    {
        poa_numbers: ["12345", "12346"],
        surety_id: "osi" | "palmetto",
        bond_case_id: "booking_number or case reference",
        defendant_name: "optional"
    }
    """
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}

    surety_id = str(body.get("surety_id", "")).lower().strip()
    bond_case_id = str(body.get("bond_case_id", "")).strip()
    defendant_name = body.get("defendant_name", "")

    # ── Normalize both formats into per-POA assignment dicts ──
    assignments_raw = body.get("assignments", [])
    poa_numbers_legacy = body.get("poa_numbers", [])

    if assignments_raw and isinstance(assignments_raw, list):
        # New format: each entry has poa_number + optional charge/bond info
        work_items = []
        for a in assignments_raw:
            if isinstance(a, dict) and a.get("poa_number"):
                work_items.append({
                    "poa_number": str(a["poa_number"]).strip(),
                    "charge": str(a.get("charge", "")).strip() or None,
                    "appearance_bond_number": str(a.get("appearance_bond_number", "")).strip() or None,
                })
    elif poa_numbers_legacy and isinstance(poa_numbers_legacy, list):
        # Legacy format: flat list, no charge data
        work_items = [{"poa_number": str(n).strip(), "charge": None, "appearance_bond_number": None}
                      for n in poa_numbers_legacy]
    else:
        return JSONResponse({"error": "Either 'assignments' or 'poa_numbers' must be a non-empty array"}, status_code=400)

    if not bond_case_id:
        return JSONResponse({"error": "bond_case_id (booking number) is required"}, status_code=400)
    if len(work_items) > 50:
        return JSONResponse({"error": "Cannot bulk-assign more than 50 POAs at once"}, status_code=400)

    now = datetime.now(timezone.utc).isoformat()
    assigned = []
    skipped = []
    errors = []

    for item in work_items:
        poa_num = item["poa_number"]
        query = {"poa_number": poa_num}
        if surety_id in ("osi", "palmetto"):
            query["surety_id"] = surety_id

        doc = await poa_inventory.find_one(query)
        if not doc:
            errors.append({"poa_number": poa_num, "reason": "not found"})
            continue
        if doc.get("status") != "available":
            skipped.append({
                "poa_number": poa_num,
                "reason": f"already {doc.get('status')}",
                "current_case": doc.get("bond_case_id"),
            })
            continue

        update_fields = {
            "status": "assigned",
            "bond_case_id": bond_case_id,
            "defendant_name": defendant_name or None,
            "used_at": now,
            "bulk_assigned": True,
        }
        # Attach charge-level data when provided
        if item["charge"]:
            update_fields["charge"] = item["charge"]
        if item["appearance_bond_number"]:
            update_fields["appearance_bond_number"] = item["appearance_bond_number"]

        await poa_inventory.update_one(
            {"_id": doc["_id"]},
            {"$set": update_fields},
        )
        assigned.append({
            "poa_number": poa_num,
            "poa_full": doc.get("poa_full", f"{doc.get('poa_prefix', '')} {poa_num}"),
            "poa_prefix": doc.get("poa_prefix", ""),
            "charge": item["charge"],
            "appearance_bond_number": item["appearance_bond_number"],
        })

    return {
        "success": True,
        "bond_case_id": bond_case_id,
        "defendant_name": defendant_name,
        "assigned_count": len(assigned),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "assigned": assigned,
        "skipped": skipped,
        "errors": errors,
    }


@poa_bp.post("/poa/alert-check")
async def api_poa_alert_check():
    """Check all POA tiers and fire Slack alerts for low/critical inventory.

    Called on-demand from the dashboard or on a cron schedule.
    Thresholds: CRITICAL ≤ 2, LOW ≤ 5.
    """
    import os, aiohttp, logging

    poa_inventory = get_collection("poa_inventory")
    LOW = 5
    CRITICAL = 2

    pipeline = [
        {"$match": {"status": "available"}},
        {"$group": {
            "_id": {"surety_id": "$surety_id", "poa_prefix": "$poa_prefix"},
            "available": {"$sum": 1},
            "max_bond_value": {"$max": "$max_bond_value"},
        }},
        {"$sort": {"_id.surety_id": 1, "_id.max_bond_value": 1}},
    ]

    critical_tiers = []
    low_tiers = []
    all_tiers = []

    async for r in poa_inventory.aggregate(pipeline):
        tier = {
            "surety": r["_id"]["surety_id"].upper(),
            "prefix": r["_id"]["poa_prefix"],
            "available": r["available"],
            "max_bond": r.get("max_bond_value", 0),
        }
        all_tiers.append(tier)
        if tier["available"] <= CRITICAL:
            critical_tiers.append(tier)
        elif tier["available"] <= LOW:
            low_tiers.append(tier)

    alerts_sent = 0
    webhook = os.getenv("SLACK_WEBHOOK_LEADS", "")

    if (critical_tiers or low_tiers) and webhook:
        blocks = []
        if critical_tiers:
            lines = [f"• *{t['prefix']}* ({t['surety']}): *{t['available']}* left" for t in critical_tiers]
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"🔴 *CRITICAL POA INVENTORY*\n{'chr(10)'.join(lines)}"},
            })

        if low_tiers:
            lines = [f"• *{t['prefix']}* ({t['surety']}): {t['available']} left" for t in low_tiers]
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"⚠️ *Low POA Stock*\n{'chr(10)'.join(lines)}"},
            })

        payload = {
            "text": f"POA Inventory Alert: {len(critical_tiers)} critical, {len(low_tiers)} low",
            "blocks": blocks,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        alerts_sent = 1
        except Exception as exc:
            logging.getLogger(__name__).warning("POA Slack alert failed: %s", exc)

    return {
        "success": True,
        "critical_count": len(critical_tiers),
        "low_count": len(low_tiers),
        "critical_tiers": critical_tiers,
        "low_tiers": low_tiers,
        "slack_alert_sent": alerts_sent > 0,
    }
