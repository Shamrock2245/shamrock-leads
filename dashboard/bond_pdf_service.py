"""
ShamrockLeads — Bond PDF Generation Service
Fills official OSI and Palmetto Appearance Bond PDF templates
with arrest record data using PyMuPDF (fitz).

Identity rules (non-negotiable)
-------------------------------
1. **One appearance bond PDF per charge** for a defendant.
2. Every charge (and its appearance bond) is tied to a **case number**.
   A defendant may have **multiple case numbers** (e.g. 26-CF-001 and 26-MM-002).
3. **Exactly one POA number per charge** — never re-use the same POA across
   charges. If only one POA is supplied for N charges, only charge 0 receives
   it; remaining bonds generate with an empty POA (must be filled before print).

Packet composition (surety folders) is separate — see paperwork_pdf_service.py.
"""
from __future__ import annotations

import io
import logging
import os
import re
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


# ── Template Paths ──
# Check Docker path first (/app/templates/), then relative to this file
_DOCKER_TEMPLATES = Path("/app/templates")
_LOCAL_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
TEMPLATES_DIR = _DOCKER_TEMPLATES if _DOCKER_TEMPLATES.exists() else _LOCAL_TEMPLATES
OSI_TEMPLATE = TEMPLATES_DIR / "osi" / "Appearance Bond blank.pdf"
PALMETTO_TEMPLATE = TEMPLATES_DIR / "palmetto" / "Shamrock Palmetto Official Appearance Bond.pdf"

# ── Static Agent Info (pre-filled in templates but we enforce consistency) ──
# Always use the authentic Shamrock Bail Bonds values as defined in AGENTS.md / GEMINI.md
AGENT_NAME = "Brendan O'Neal"
AGENT_LICENSE = "P139768"
AGENCY_DETAILS = "Shamrock Bail Bonds\r1528 Broadway\rFort Myers, FL 33901\r239-332-2245\rshamrockbailbonds.biz"
AGENCY_NAME = "Shamrock Bail Bonds"


def _safe_float(val) -> float:
    """Safely convert a value to float, handling currencies and None."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return 0.0
    # Strip currency signs, commas, and non-numeric formatting
    s = re.sub(r'[^\d.-]', '', s)
    try:
        return float(s)
    except ValueError:
        return 0.0


def _normalize_charges_and_amounts(charges_input, bond_amount_input) -> list[dict]:
    """
    Normalizes diverse shapes of charges and bond amounts into a structured list of dicts:
    [{'charge': str, 'amount': float}]
    
    Supports:
      - List of dicts (Wix intake / scraped format)
      - List of strings
      - Delimited strings (pipe, semicolon, newline, and conditionally comma)
    """
    # 1. Parse bond amounts into a list of floats
    amounts = []
    if isinstance(bond_amount_input, list):
        amounts = [_safe_float(x) for x in bond_amount_input]
    elif isinstance(bond_amount_input, (int, float)):
        amounts = [float(bond_amount_input)]
    elif isinstance(bond_amount_input, str):
        b_str = bond_amount_input.strip()
        if not b_str:
            amounts = []
        elif "|" in b_str:
            amounts = [_safe_float(x) for x in b_str.split("|")]
        elif "\n" in b_str:
            amounts = [_safe_float(x) for x in b_str.split("\n")]
        elif ";" in b_str:
            amounts = [_safe_float(x) for x in b_str.split(";")]
        elif "," in b_str:
            amounts = [_safe_float(x) for x in b_str.split(",")]
        else:
            amounts = [_safe_float(b_str)]
    else:
        amounts = [_safe_float(bond_amount_input)] if bond_amount_input is not None else []

    # 2. Extract charge descriptions and amounts from charges_input
    charges = []
    amounts_from_charges = []

    if isinstance(charges_input, list):
        for item in charges_input:
            if isinstance(item, dict):
                desc = item.get("charge") or item.get("description") or item.get("charge_desc") or ""
                amt = item.get("bond_amount") or item.get("amount") or item.get("bond")
                amt_val = _safe_float(amt) if amt is not None else None
                charges.append(str(desc).strip())
                amounts_from_charges.append(amt_val)
            else:
                charges.append(str(item).strip())
                amounts_from_charges.append(None)
    elif isinstance(charges_input, str):
        c_str = charges_input.strip()
        if not c_str:
            charges = []
        elif "|" in c_str:
            charges = [c.strip() for c in c_str.split("|")]
        elif "\n" in c_str:
            charges = [c.strip() for c in c_str.split("\n")]
        elif ";" in c_str:
            charges = [c.strip() for c in c_str.split(";")]
        else:
            # Handle potential comma split only if we have matching amounts
            comma_split = [c.strip() for c in c_str.split(",")]
            if len(comma_split) > 1 and len(amounts) == len(comma_split):
                charges = comma_split
            else:
                charges = [c_str]
        amounts_from_charges = [None] * len(charges)
    else:
        # Generic fallback
        charges = [str(charges_input).strip()] if charges_input is not None else []
        amounts_from_charges = [None] * len(charges)

    # Filter out empty charge descriptions
    valid_charges = []
    valid_amounts_from_charges = []
    for c, a in zip(charges, amounts_from_charges):
        if c:
            valid_charges.append(c)
            valid_amounts_from_charges.append(a)

    if not valid_charges:
        valid_charges = ["No Charge Specified"]
        valid_amounts_from_charges = [None]

    # 3. Match charges and amounts
    normalized = []
    for i, chg in enumerate(valid_charges):
        amt_val = valid_amounts_from_charges[i] if i < len(valid_amounts_from_charges) else None
        if amt_val is None:
            if len(amounts) == 1 and len(valid_charges) > 1:
                amt_val = round(amounts[0] / len(valid_charges), 2)
            elif i < len(amounts):
                amt_val = amounts[i]
            elif len(amounts) == 1 and i == 0:
                amt_val = amounts[0]
            else:
                amt_val = 0.0
        normalized.append({
            "charge": chg,
            "amount": amt_val,
        })
    return normalized


def _split_list_field(val: Any) -> List[str]:
    """Split a multi-value field (list or delimited string) into a list of strings."""
    if val is None:
        return []
    if isinstance(val, list):
        out = []
        for item in val:
            if isinstance(item, dict):
                # Prefer full POA display, then number
                s = (
                    item.get("poa_full")
                    or item.get("poa_number")
                    or item.get("case_number")
                    or item.get("value")
                    or ""
                )
                out.append(str(s).strip())
            else:
                out.append(str(item).strip())
        return [x for x in out if x]
    s = str(val).strip()
    if not s:
        return []
    for sep in ("|", ";", "\n"):
        if sep in s:
            return [p.strip() for p in s.split(sep) if p.strip()]
    # Comma: only if multiple tokens look like distinct ids (not "Last, First")
    if "," in s and not re.search(r"[A-Za-z]{3,},\s*[A-Za-z]", s):
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) > 1:
            return parts
    return [s]


def normalize_charge_rows(bond_data: dict) -> List[Dict[str, Any]]:
    """
    Build the canonical per-charge rows for appearance bond generation.

    Preference order for charge source:
      1. charge_details (structured from Lead Explorer / update-charge-bonds)
      2. charge_list (dashboard write-bond modal)
      3. charges / charge + bond_amount (legacy)

    Each row:
      {
        "charge": str,
        "amount": float,
        "case_number": str,   # case this charge belongs to
        "poa_number": str,    # exactly one POA for this charge (may be empty)
        "bond_type": str,
        "index": int,
      }

    Rules:
      - One row → one appearance bond PDF
      - case_number may repeat across rows (same case, multiple counts) or differ
        (multiple cases per defendant)
      - poa_number is 1:1 with charge index from poa_numbers / parallel lists;
        a single shared POA is **not** copied onto every charge
    """
    bond_data = bond_data or {}
    rows: List[Dict[str, Any]] = []

    # ── Structured charge_details ──────────────────────────────────────────
    details = bond_data.get("charge_details") or bond_data.get("charge_list")
    if isinstance(details, list) and details:
        for i, item in enumerate(details):
            if isinstance(item, dict):
                desc = (
                    item.get("charge")
                    or item.get("description")
                    or item.get("charge_desc")
                    or ""
                ).strip()
                if not desc:
                    continue
                amt = item.get("bond_amount", item.get("amount", item.get("bond")))
                rows.append({
                    "charge": desc,
                    "amount": _safe_float(amt) if amt is not None else 0.0,
                    "case_number": str(
                        item.get("case_number")
                        or item.get("Case_Number")
                        or item.get("appearance_bond_number")
                        or ""
                    ).strip(),
                    "poa_number": str(
                        item.get("poa_number")
                        or item.get("poa_full")
                        or item.get("POA_Number")
                        or ""
                    ).strip(),
                    "bond_type": str(item.get("bond_type") or item.get("bondType") or "Surety").strip(),
                    "court_date": str(
                        item.get("court_date") or item.get("CourtDate") or ""
                    ).strip(),
                    "court_time": str(
                        item.get("court_time") or item.get("CourtTime") or ""
                    ).strip(),
                    "county": str(item.get("county") or item.get("County") or "").strip(),
                    "index": i,
                })
            else:
                desc = str(item).strip()
                if desc:
                    rows.append({
                        "charge": desc,
                        "amount": 0.0,
                        "case_number": "",
                        "poa_number": "",
                        "bond_type": "Surety",
                        "court_date": "",
                        "court_time": "",
                        "county": "",
                        "index": i,
                    })

    # ── Legacy charges + amounts ───────────────────────────────────────────
    if not rows:
        charges_input = bond_data.get("charges") or bond_data.get("charge")
        bond_amount_input = bond_data.get("bond_amount")
        for i, item in enumerate(_normalize_charges_and_amounts(charges_input, bond_amount_input)):
            rows.append({
                "charge": item["charge"],
                "amount": item["amount"],
                "case_number": "",
                "poa_number": "",
                "bond_type": "Surety",
                "court_date": "",
                "court_time": "",
                "county": "",
                "index": i,
            })

    if not rows:
        rows = [{
            "charge": "No Charge Specified",
            "amount": _safe_float(bond_data.get("bond_amount")),
            "case_number": str(bond_data.get("case_number") or "").strip(),
            "poa_number": "",
            "bond_type": "Surety",
            "court_date": "",
            "court_time": "",
            "county": "",
            "index": 0,
        }]

    # ── Parallel POA list (one POA per charge — never broadcast) ────────────
    poa_list = _split_list_field(
        bond_data.get("poa_numbers")
        if bond_data.get("poa_numbers") is not None
        else bond_data.get("poa_number")
    )
    # ── Parallel / fallback case numbers ───────────────────────────────────
    case_list = _split_list_field(
        bond_data.get("case_numbers")
        if bond_data.get("case_numbers") is not None
        else bond_data.get("case_number")
    )
    default_case = str(bond_data.get("case_number") or bond_data.get("Case_Number") or "").strip()

    booking_number = str(
        bond_data.get("booking_number")
        or bond_data.get("booking")
        or bond_data.get("Booking_Number")
        or ""
    ).strip()
    # Never treat booking/arrest number as a court case number
    if _is_booking_as_case(default_case, booking_number):
        default_case = ""
    case_list = [c for c in case_list if not _is_booking_as_case(c, booking_number)]

    default_court_date = str(
        bond_data.get("court_date") or bond_data.get("Court_Date") or ""
    ).strip()
    default_court_time = str(
        bond_data.get("court_time") or bond_data.get("Court_Time") or ""
    ).strip()
    if default_court_date:
        d_cd, d_ct = _split_court_datetime(default_court_date, default_court_time)
        default_court_date, default_court_time = d_cd, d_ct

    for i, row in enumerate(rows):
        if not row.get("poa_number"):
            if i < len(poa_list):
                row["poa_number"] = poa_list[i]
            # Do NOT fall back to poa_list[0] for later charges — one POA per charge only
        # Reject booking-as-case pollution from modal defaults
        if _is_booking_as_case(row.get("case_number"), booking_number):
            row["case_number"] = ""
        if not row.get("case_number"):
            if i < len(case_list):
                row["case_number"] = case_list[i]
            elif default_case and len(case_list) <= 1:
                # Single case number on the defendant/case may apply to every count
                # on that case; multiple case_numbers list takes precedence above.
                row["case_number"] = default_case
        # Court date/time defaults (never leave empty→TBN when parent has a real date)
        cd = str(row.get("court_date") or "").strip()
        ct = str(row.get("court_time") or "").strip()
        if not cd or cd.upper() in ("TBN", "TBD"):
            cd, ct = default_court_date or cd, ct or default_court_time
        if cd:
            cd, ct = _split_court_datetime(cd, ct)
        row["court_date"] = cd
        row["court_time"] = ct
        row["index"] = i

    return rows


def _split_charge(charge_text: str, max_line1: int = 80) -> tuple:
    """Split a charge description across two lines if needed."""
    charge_text = (charge_text or "").strip()
    if len(charge_text) <= max_line1:
        return charge_text, ""
    # Try to split at a natural boundary
    split_idx = charge_text.rfind(" ", 0, max_line1)
    if split_idx == -1:
        split_idx = max_line1
    return charge_text[:split_idx].strip(), charge_text[split_idx:].strip()


_PLACEHOLDER_CHARGES = frozenset({
    "",
    "unspecified charge",
    "no charge specified",
    "unknown",
    "n/a",
    "none",
})


def _is_placeholder_charge(text: Any) -> bool:
    s = str(text or "").strip().lower()
    return s in _PLACEHOLDER_CHARGES


def _digits_only(val: Any) -> str:
    return re.sub(r"\D", "", str(val or ""))


def _is_booking_as_case(case_number: Any, booking_number: Any) -> bool:
    """True when case_number is empty or is just the booking/arrest number (wrong field)."""
    case = str(case_number or "").strip()
    booking = str(booking_number or "").strip()
    if not case:
        return True
    if not booking:
        return False
    if case == booking:
        return True
    # Compare digit cores (26CF016741 vs 1029767 never match; 1029767 vs 1029767 does)
    cd, bd = _digits_only(case), _digits_only(booking)
    return bool(cd and bd and cd == bd and not re.search(r"[A-Za-z]", case))


def _parse_defendant_name(
    full_name: str = "",
    first_name: str = "",
    last_name: str = "",
) -> tuple[str, str]:
    """
    Return (first_name, last_name).

    Supports jail formats:
      - LAST, FIRST MIDDLE  (Lee / most FL rosters)
      - FIRST MIDDLE LAST
    """
    first_name = str(first_name or "").strip()
    last_name = str(last_name or "").strip()
    if first_name or last_name:
        return first_name, last_name

    full = str(full_name or "").strip()
    if not full:
        return "", ""

    if "," in full:
        last_part, rest = full.split(",", 1)
        return rest.strip(), last_part.strip()

    parts = full.split()
    if len(parts) >= 2:
        return " ".join(parts[:-1]), parts[-1]
    return "", parts[0]


def _split_court_datetime(court_date: Any, court_time: Any = "") -> tuple[str, str]:
    """
    Normalize court date + time.

    Accepts:
      - separate court_date / court_time
      - combined "9/8/2026, 8:30:00 AM" or "9/8/2026 8:30 AM"
      - ISO datetime strings
    Returns (date_display, time_display). Empty time when unknown (not TBN).
    """
    time_out = str(court_time or "").strip()
    raw = str(court_date or "").strip()
    if not raw or raw.upper() in ("TBN", "TBD", "N/A", "NONE"):
        return ("TBN" if not raw else raw.upper() if raw.upper() in ("TBN", "TBD") else raw), time_out

    # Combined date + time in one field
    combined = raw
    # "9/8/2026, 8:30:00 AM" or "9/8/2026 8:30:00 AM"
    m = re.match(
        r"^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})[,\s]+(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)\s*$",
        combined,
    )
    if m:
        return m.group(1).strip(), (time_out or m.group(2).strip())

    # ISO datetime
    try:
        iso = combined.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        date_fmt = dt.strftime("%-m/%-d/%Y") if hasattr(dt, "strftime") else dt.strftime("%m/%d/%Y")
        # Portable month/day without leading zeros
        date_fmt = f"{dt.month}/{dt.day}/{dt.year}"
        time_fmt = dt.strftime("%I:%M:%S %p").lstrip("0")
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and not time_out:
            return date_fmt, time_out
        return date_fmt, time_out or time_fmt
    except ValueError:
        pass

    # Date only formats already in raw
    return raw, time_out


def _parse_date_parts(date_input) -> dict:
    """Parse a date string, date, or datetime object into day, month name, and year components."""
    if not date_input:
        now = datetime.now()
        return {
            "day": str(now.day),
            "month": now.strftime("%B"),
            "year": str(now.year),
            "year_yy": now.strftime("%y"),
            "formatted": now.strftime("%m/%d/%Y"),
        }
        
    # Check if native datetime/date
    if isinstance(date_input, (datetime, date)):
        return {
            "day": str(date_input.day),
            "month": date_input.strftime("%B"),
            "year": str(date_input.year),
            "year_yy": date_input.strftime("%y"),
            "formatted": date_input.strftime("%m/%d/%Y"),
        }
        
    # Coerce to string
    date_str = str(date_input).strip()
    if not date_str:
        now = datetime.now()
        return {
            "day": str(now.day),
            "month": now.strftime("%B"),
            "year": str(now.year),
            "year_yy": now.strftime("%y"),
            "formatted": now.strftime("%m/%d/%Y"),
        }
        
    # Try common formats
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"):
        clean_str = date_str
        if fmt.endswith(".%fZ") and date_str.endswith("Z"):
            clean_str = date_str[:-1]
        try:
            dt = datetime.strptime(clean_str, fmt)
            return {
                "day": str(dt.day),
                "month": dt.strftime("%B"),
                "year": str(dt.year),
                "year_yy": dt.strftime("%y"),
                "formatted": dt.strftime("%m/%d/%Y"),
            }
        except ValueError:
            continue
            
    # Try ISO generic
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return {
            "day": str(dt.day),
            "month": dt.strftime("%B"),
            "year": str(dt.year),
            "year_yy": dt.strftime("%y"),
            "formatted": dt.strftime("%m/%d/%Y"),
        }
    except ValueError:
        pass

    # Fallback
    return {
        "day": "", 
        "month": "", 
        "year": date_str,
        "year_yy": date_str[-2:] if len(date_str) >= 2 else "",
        "formatted": date_str,
    }


def _amount_to_words(amount: float) -> str:
    """Convert a numeric bond amount to Title Case words with a ' Dollars' suffix."""
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return ""
    if amount <= 0:
        return "Zero and 00/100 Dollars"
    
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven",
            "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
            "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty",
            "Sixty", "Seventy", "Eighty", "Ninety"]
    
    def _chunk(n):
        if n == 0:
            return ""
        if n < 20:
            return ones[int(n)]
        if n < 100:
            return tens[int(n) // 10] + (" " + ones[int(n) % 10] if n % 10 else "")
        return ones[int(n) // 100] + " Hundred" + (" " + _chunk(n % 100) if n % 100 else "")
    
    whole = int(amount)
    cents = round((amount - whole) * 100)
    
    parts = []
    if whole >= 1000000:
        parts.append(_chunk(whole // 1000000) + " Million")
        whole %= 1000000
    if whole >= 1000:
        parts.append(_chunk(whole // 1000) + " Thousand")
        whole %= 1000
    if whole > 0:
        parts.append(_chunk(whole))
    
    result = " ".join(parts) if parts else "Zero"
    
    if cents:
        result += f" and {cents:02d}/100 Dollars"
    else:
        result += " and 00/100 Dollars"
    
    return result


def _widget_base_name(field_name: Optional[str]) -> str:
    """Strip Acrobat clone suffixes like 'Arrest/case No [193]' → 'Arrest/case No'."""
    name = str(field_name or "").strip()
    if not name:
        return ""
    m = re.match(r"^(.*?)(?:\s*\[\d+\])\s*$", name)
    return (m.group(1).strip() if m else name)


def _set_widget_value_with_scaling(widget, val, default_font_size=10):
    """
    Writes a value to a PDF form widget, automatically scaling the font size
    to prevent visual text clipping or boundary overflow.
    """
    val_str = str(val if val is not None else "").strip()
    try:
        if not val_str:
            widget.field_value = ""
            widget.update()
            return

        rect = getattr(widget, "rect", None)
        if not rect:
            widget.text_fontsize = default_font_size
            widget.field_value = val_str
            widget.update()
            return

        width = max(1.0, float(rect.x1 - rect.x0))
        height = max(1.0, float(rect.y1 - rect.y0))

        # Normalize newline characters
        val_str = val_str.replace("\r\n", "\n").replace("\r", "\n")
        lines = val_str.split("\n")
        max_line_len = max((len(line) for line in lines), default=0)
        num_lines = max(1, len(lines))

        # Estimate width per char as font_size * char_width_multiplier
        char_width_multiplier = 0.45

        # 1. Size constraint by width
        if max_line_len > 0:
            size_by_width = width / (max_line_len * char_width_multiplier)
        else:
            size_by_width = default_font_size

        # 2. Size constraint by height
        if num_lines > 1:
            size_by_height = height / (num_lines * 1.25)
        else:
            size_by_height = height * 0.8

        font_size = min(default_font_size, size_by_width, size_by_height)

        # Cap lower bound to keep it legible (5.5 is readable on high-DPI screens/print)
        font_size = max(5.5, font_size)

        widget.text_fontsize = font_size
        widget.field_value = val_str
        widget.update()
    except Exception as exc:
        # Never abort a full bond package on a single widget failure
        logger.warning(
            "[appearance-bond] widget write failed field=%s: %s",
            getattr(widget, "field_name", "?"),
            exc,
        )
        try:
            widget.field_value = val_str
            widget.update()
        except Exception:
            pass


def _apply_field_values(page, field_values: dict, font_sizes: Optional[dict] = None) -> None:
    """Write values to form widgets; match base field names (handles [n] suffixes)."""
    font_sizes = font_sizes or {}
    for widget in page.widgets() or []:
        raw_name = widget.field_name or ""
        base = _widget_base_name(raw_name)
        if raw_name in field_values:
            key = raw_name
        elif base in field_values:
            key = base
        else:
            continue
        val = field_values[key]
        default_fs = font_sizes.get(key) or font_sizes.get(base) or 10
        _set_widget_value_with_scaling(widget, val, default_font_size=default_fs)


def fill_osi_bond(data: dict) -> bytes:
    """
    Fill the OSI Appearance Bond template with arrest data.
    
    Expected data keys:
        name/defendant_name, first_name, last_name, booking_number, county, bond_amount,
        charge, court_date, court_time, case_number, address, dob,
        bond_date, poa_number, court_type, indemnitor_name
    
    Returns: PDF bytes
    """
    if not OSI_TEMPLATE.exists():
        raise FileNotFoundError(f"OSI template not found: {OSI_TEMPLATE}")
    
    doc = fitz.open(str(OSI_TEMPLATE))
    page = doc[0]
    
    bond_amount = _safe_float(data.get("bond_amount", 0))
    premium = max(100.0, bond_amount * 0.10)
    date_parts = _parse_date_parts(data.get("bond_date", ""))
    charge_raw = data.get("charge") or data.get("charges") or ""
    if _is_placeholder_charge(charge_raw):
        charge_raw = ""
    charge_line1, charge_line2 = _split_charge(charge_raw)
    
    # Full name parsing (LAST, FIRST MIDDLE for FL jail rosters)
    full_name = data.get("name") or data.get("defendant_name") or ""
    first_name, last_name = _parse_defendant_name(
        full_name,
        first_name=data.get("first_name") or "",
        last_name=data.get("last_name") or "",
    )
            
    booking_number = str(
        data.get("booking_number") or data.get("defendant_booking_number") or ""
    ).strip()
    county = data.get("county") or data.get("defendant_county") or ""
    address = data.get("address") or data.get("defendant_address") or ""

    case_number = str(
        data.get("case_number") or data.get("defendant_case_number") or ""
    ).strip()
    # Arrest # stays in Arrest/case No; CaseNum is court case only (never booking)
    if _is_booking_as_case(case_number, booking_number):
        case_number = ""

    court_date_raw = data.get("court_date") or data.get("defendant_court_date") or ""
    court_time_raw = data.get("court_time") or data.get("defendant_court_time") or ""
    court_date, court_time = _split_court_datetime(court_date_raw, court_time_raw)
    if not court_date:
        court_date = "TBN"
    
    # Indemnitor + Defendant display name
    indemnitor = data.get("indemnitor_name", "")
    ind_def_display = f"{indemnitor} / {full_name}" if indemnitor else full_name
    
    # ── Field Mapping ──
    field_values = {
        "DefLastName": last_name,
        "DefFirstName": first_name,
        "DefCounty": county,
        "DefCourtType": data.get("court_type") or data.get("defendant_court_type") or "",
        "BondAmountCharge1": f"${bond_amount:,.2f}",
        "DefCharge1": charge_line1 or "No Charge Specified",
        "DefCharge1Line2": charge_line2,
        "CourtDate": court_date,
        "CourtTime": court_time if str(court_date).upper() != "TBN" else "",
        "CaseNum": case_number,
        "Arrest/case No": booking_number,
        "DefAddress": address,
        "DayDD": date_parts["day"],
        "Month": date_parts["month"],
        "YearYY": date_parts["year_yy"],
        "PowerNum": data.get("poa_number", "OSI"),
        "WrittenPremiumAmount": _amount_to_words(premium),
        "NumericPremiumAmount": f"${premium:,.2f}",
        "BondAgentName": AGENT_NAME,
        "BondAgentLicenseNum": AGENT_LICENSE,
        "AgencyDetails": AGENCY_DETAILS,
        "IndNameandDefName": ind_def_display,
        "Other": "",
        "Transfer agency": "",
        "Transfer address": "",
        "Transfer number": "",
    }
    
    # Default font sizes per field for premium layout aesthetics
    font_sizes = {
        "DefLastName": 11,
        "DefFirstName": 11,
        "DefAddress": 8.5,
        "WrittenPremiumAmount": 8.5,
        "AgencyDetails": 8,
        "IndNameandDefName": 9,
    }

    _apply_field_values(page, field_values, font_sizes)

    # Output
    buf = io.BytesIO()
    try:
        doc.save(buf)
    finally:
        doc.close()
    buf.seek(0)
    return buf.read()


def fill_palmetto_bond(data: dict) -> bytes:
    """
    Fill the Palmetto Appearance Bond template with arrest data.
    
    Expected data keys:
        name/defendant_name, booking_number, county, bond_amount, charge, court_date,
        case_number, address, bond_date, poa_number
    
    Returns: PDF bytes
    """
    if not PALMETTO_TEMPLATE.exists():
        raise FileNotFoundError(f"Palmetto template not found: {PALMETTO_TEMPLATE}")
    
    doc = fitz.open(str(PALMETTO_TEMPLATE))
    page = doc[0]
    
    bond_amount = _safe_float(data.get("bond_amount", 0))
    premium = max(100.0, bond_amount * 0.10)
    date_parts = _parse_date_parts(data.get("bond_date", ""))

    charge_raw = data.get("charge") or data.get("charges") or ""
    if _is_placeholder_charge(charge_raw):
        charge_raw = ""
    charge_line1, charge_line2 = _split_charge(charge_raw)

    full_name = data.get("name") or data.get("defendant_name") or ""
    booking_number = str(
        data.get("booking_number") or data.get("defendant_booking_number") or ""
    ).strip()
    county = data.get("county") or data.get("defendant_county") or ""
    address = data.get("address") or data.get("defendant_address") or ""

    case_number = str(
        data.get("case_number") or data.get("defendant_case_number") or ""
    ).strip()
    if _is_booking_as_case(case_number, booking_number):
        case_number = ""

    court_date, court_time = _split_court_datetime(
        data.get("court_date") or data.get("defendant_court_date") or "",
        data.get("court_time") or data.get("defendant_court_time") or "",
    )
    if not court_date:
        court_date = "TBN"
    if court_time and str(court_date).upper() != "TBN":
        court_datetime = f"{court_date} {court_time}".strip()
    else:
        court_datetime = court_date
    
    # ── Field Mapping ──
    field_values = {
        "defendantNameField": full_name,
        "countyField": county,
        "numericBondAmount": f"${bond_amount:,.2f}",
        "chargesField1": charge_line1 or "No Charge Specified",
        "chargesField2": charge_line2,
        "CourtDateAndTimeField": court_datetime,
        "ArrestNumberField": booking_number,
        # Some Palmetto revisions use a separate case field; safe no-op if absent
        "CaseNumberField": case_number,
        "DefendantAddress": address,
        "powerNumField": data.get("poa_number", ""),
        "dayField": date_parts["day"],
        "monthWrittenField": date_parts["month"],
        "yearYYYYField": date_parts["year"],
        "cirCoField": data.get("court_type") or data.get("defendant_court_type") or "",
        "agentBailLicNumField": AGENT_LICENSE,
        "AgentField#0": AGENT_NAME,
        "AgentField#1": AGENT_NAME,
        "writtenPremiumAmount": _amount_to_words(premium),
        "calculatedPremiumField": f"${premium:,.2f}",
        "CollateralField": data.get("collateral") or "Indemnity Agreement, Promissory Note",
        "collateralDescriptionField": "",
        "AgencyField": AGENCY_NAME,
        "whoSignedField": "defendant and family/friends",
        "Transfer agent": "",
    }
    
    # Default font sizes per field for premium layout aesthetics
    font_sizes = {
        "defendantNameField": 11,
        "DefendantAddress": 8.5,
        "writtenPremiumAmount": 8.0,
        "whoSignedField": 8.5,
        "chargesField1": 9.0,
    }

    _apply_field_values(page, field_values, font_sizes)

    buf = io.BytesIO()
    try:
        doc.save(buf)
    finally:
        doc.close()
    buf.seek(0)
    return buf.read()


def generate_appearance_bonds(bond_data: dict, template: str = "osi") -> list[bytes]:
    """
    Generate filled appearance bond PDFs — **one PDF per charge**.

    Identity:
      - Each charge → one appearance bond
      - Each charge → one POA number (exclusive; not shared across charges)
      - Each charge → a case_number (defendant may have multiple case numbers)

    Args:
        bond_data: Defendant, indemnitor, booking, charge_details / charges, POAs.
        template: "osi" or "palmetto".

    Returns:
        List of PDF byte buffers in charge order (same length as normalize_charge_rows).
    """
    surety = (template or bond_data.get("surety") or "osi").lower().strip()
    if surety not in ("osi", "palmetto"):
        surety = "osi"

    rows = normalize_charge_rows(bond_data)
    pdfs: List[bytes] = []
    missing_poa = []
    missing_case = []

    default_court = (
        bond_data.get("court_date")
        or bond_data.get("defendant_court_date")
        or "TBN"
    )
    if not str(default_court).strip():
        default_court = "TBN"
    default_time = bond_data.get("court_time") or bond_data.get("defendant_court_time") or ""
    default_county = bond_data.get("county") or bond_data.get("defendant_county") or ""

    for row in rows:
        charge_data = dict(bond_data)
        charge_data["charge"] = row["charge"]
        charge_data["bond_amount"] = row["amount"]
        charge_data["case_number"] = row.get("case_number") or ""
        charge_data["poa_number"] = row.get("poa_number") or ""
        charge_data["bond_type"] = row.get("bond_type") or "Surety"
        charge_data["charge_index"] = row.get("index", 0)
        charge_data["surety"] = surety
        # Per-charge court/county with TBN default for unknown dates
        cd = (row.get("court_date") or "").strip() or str(default_court).strip() or "TBN"
        charge_data["court_date"] = cd
        charge_data["defendant_court_date"] = cd
        if cd.upper() == "TBN":
            charge_data["court_time"] = ""
            charge_data["defendant_court_time"] = ""
        else:
            ct = (row.get("court_time") or "").strip() or str(default_time).strip()
            charge_data["court_time"] = ct
            charge_data["defendant_court_time"] = ct
        if row.get("county"):
            charge_data["county"] = row["county"]
            charge_data["defendant_county"] = row["county"]
        elif default_county:
            charge_data["county"] = default_county

        if not charge_data["poa_number"]:
            missing_poa.append(row["index"])
        if not charge_data["case_number"]:
            missing_case.append(row["index"])

        if surety == "palmetto":
            pdf_bytes = fill_palmetto_bond(charge_data)
        else:
            pdf_bytes = fill_osi_bond(charge_data)
        pdfs.append(pdf_bytes)

    if missing_poa:
        logger.warning(
            "[appearance-bond] charges missing POA (one POA required per charge): indices=%s defendant=%s",
            missing_poa,
            bond_data.get("defendant_name") or bond_data.get("name") or "?",
        )
    if missing_case:
        logger.warning(
            "[appearance-bond] charges missing case_number: indices=%s defendant=%s",
            missing_case,
            bond_data.get("defendant_name") or bond_data.get("name") or "?",
        )

    logger.info(
        "[appearance-bond] generated %s bond(s) surety=%s defendant=%s",
        len(pdfs),
        surety,
        bond_data.get("defendant_name") or bond_data.get("name") or "?",
    )
    return pdfs


def generate_appearance_bond(data: dict) -> bytes:
    """
    Generate appearance bond PDF(s) for the given surety.

    - Single charge → one PDF
    - Multiple charges → uncollated merge (2 copies per charge by default) so
      print jobs stay one-file-per-defendant while still one bond form per charge

    Args:
        data: Dict with keys: surety ('osi'|'palmetto'), name, booking_number,
              county, bond_amount, charge(s), charge_details, case_number(s),
              poa_number(s), etc.

    Returns: PDF bytes
    """
    surety = (data.get("surety", "osi") or "osi").lower().strip()
    pdfs = generate_appearance_bonds(data, template=surety)
    if not pdfs:
        return b""
    if len(pdfs) == 1:
        return pdfs[0]
    # Multi-charge: merge into one print-ready stream (2 copies per charge)
    copies = int(data.get("copies_per_charge") or 2)
    return merge_uncollated_bonds(pdfs, copies_per_charge=copies)


# Procedural constants — appearance bonds are never e-signed
APPEARANCE_BOND_SIGNATURE_MODE = "wet_ink_live"
APPEARANCE_BOND_PROCEDURE = (
    "Generate unsigned PDF → store file → print → live (wet-ink) signature "
    "on paper → take signed original to the jail"
)
APPEARANCE_BOND_ESIGN = False  # never SignNow / Adobe Sign


def appearance_bond_procedure_meta() -> Dict[str, Any]:
    """Shared metadata for UI, packet docs, and storage."""
    return {
        "print_only": True,
        "e_sign": APPEARANCE_BOND_ESIGN,
        "signature_mode": APPEARANCE_BOND_SIGNATURE_MODE,
        "signature_required": "live_wet_ink",
        "storage_state": "unsigned_file",
        "procedure": APPEARANCE_BOND_PROCEDURE,
        "delivery": "print_and_jail",
        "not_for": ["signnow", "adobe_sign", "adobe_acrobat_sign", "email_sign"],
    }


def describe_appearance_bonds(bond_data: dict) -> List[Dict[str, Any]]:
    """
    Diagnostic / UI: list planned appearance bonds without generating PDFs.
    Useful for Bond Desk to show “Charge → Case # → POA #” before print.
    """
    rows = normalize_charge_rows(bond_data)
    proc = appearance_bond_procedure_meta()
    return [
        {
            "charge_index": r["index"],
            "charge": r["charge"],
            "bond_amount": r["amount"],
            "case_number": r.get("case_number") or "",
            "poa_number": r.get("poa_number") or "",
            "bond_type": r.get("bond_type") or "Surety",
            "ready": bool(r.get("poa_number") and r.get("case_number")),
            **proc,
        }
        for r in rows
    ]


def store_appearance_bond_pdfs(
    pdfs: List[bytes],
    *,
    bond_data: dict,
    surety: str = "osi",
    packet_id: Optional[str] = None,
    booking_number: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Persist **unsigned** appearance bond PDFs to disk for print workflow.

    Files are never sent for e-signature. Staff prints, wet-signs, takes to jail.
    Returns one metadata dict per charge PDF (paths are relative to repo root
    when possible, absolute otherwise).
    """
    surety = (surety or "osi").lower().strip()
    rows = normalize_charge_rows(bond_data)
    booking = (
        booking_number
        or bond_data.get("booking_number")
        or bond_data.get("defendant_booking_number")
        or "unknown"
    )
    booking_safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(booking))[:40]
    pkt = packet_id or f"BOND-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    pkt_safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(pkt))[:40]

    root = Path(__file__).resolve().parent / "uploads" / "appearance_bonds" / pkt_safe
    root.mkdir(parents=True, exist_ok=True)

    stored: List[Dict[str, Any]] = []
    proc = appearance_bond_procedure_meta()
    for i, blob in enumerate(pdfs):
        row = rows[i] if i < len(rows) else {}
        charge_slug = re.sub(
            r"[^A-Za-z0-9_-]+", "_", (row.get("charge") or f"charge_{i + 1}")[:30]
        )
        fname = f"{surety}_{booking_safe}_ch{i + 1:02d}_{charge_slug}_UNSIGNED.pdf"
        path = root / fname
        path.write_bytes(blob)
        try:
            rel = str(path.relative_to(Path(__file__).resolve().parent.parent))
        except ValueError:
            rel = str(path)
        stored.append({
            "charge_index": i,
            "charge": row.get("charge") or "",
            "case_number": row.get("case_number") or "",
            "poa_number": row.get("poa_number") or "",
            "bond_amount": row.get("amount"),
            "filename": fname,
            "file_path": rel,
            "absolute_path": str(path),
            "size_bytes": len(blob),
            "status": "unsigned_stored",
            "signed": False,
            **proc,
        })
        logger.info(
            "[appearance-bond] stored UNSIGNED print file %s (%s bytes)",
            path.name,
            len(blob),
        )
    return stored


def generate_safe_filename(data: dict) -> str:
    """Generate a filesystem-safe filename for the bond PDF."""
    name = re.sub(r'[^A-Za-z0-9_-]', '_', (data.get("name") or data.get("defendant_name") or "defendant"))
    charge = data.get("charge", "charge") or "charge"
    charge_short = re.sub(r'[^A-Za-z0-9_-]', '_', charge[:25])
    surety = (data.get("surety", "osi") or "osi").upper()
    date_str = datetime.now().strftime("%m-%d-%Y")
    return f"AppearanceBond_{surety}_{name}_{charge_short}_{date_str}.pdf"


def merge_uncollated_bonds(pdfs: list[bytes], copies_per_charge: int = 2) -> bytes:
    """
    Merge a list of charge bond PDFs into a single print-ready PDF stream where each
    charge bond is duplicated N times consecutively (uncollated output: 2x per charge).

    Example for 3 charges with copies_per_charge=2:
        - Page 1: Charge 1 (Copy 1 - Court)
        - Page 2: Charge 1 (Copy 2 - Agency File)
        - Page 3: Charge 2 (Copy 1 - Court)
        - Page 4: Charge 2 (Copy 2 - Agency File)
        - Page 5: Charge 3 (Copy 1 - Court)
        - Page 6: Charge 3 (Copy 2 - Agency File)
    """
    if copies_per_charge < 1:
        copies_per_charge = 1

    out_doc = fitz.open()
    pages_added = 0
    for pdf_bytes in pdfs:
        if not pdf_bytes:
            continue
        for _ in range(copies_per_charge):
            src_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if src_doc.page_count > 0:
                out_doc.insert_pdf(src_doc)
                pages_added += src_doc.page_count
            src_doc.close()

    if pages_added == 0:
        out_doc.close()
        raise ValueError("No PDF pages to merge — generate at least one appearance bond first")

    buf = io.BytesIO()
    out_doc.save(buf)
    out_doc.close()
    buf.seek(0)
    return buf.read()

