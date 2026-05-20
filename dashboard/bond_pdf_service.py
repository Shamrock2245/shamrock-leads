"""
ShamrockLeads — Bond PDF Generation Service
Fills official OSI and Palmetto Appearance Bond PDF templates
with arrest record data using PyMuPDF (fitz).

One appearance bond per criminal charge.
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime, date
from pathlib import Path

import fitz  # PyMuPDF


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
            if i < len(amounts):
                amt_val = amounts[i]
            elif len(amounts) == 1 and i == 0:
                amt_val = amounts[0]
            else:
                # If only one overall amount was specified but we have multiple charges,
                # put the entire amount on the first charge and $0 on the rest.
                amt_val = amounts[0] if (len(amounts) == 1 and i == 0) else 0.0
        normalized.append({
            "charge": chg,
            "amount": amt_val
        })
    return normalized


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


def _set_widget_value_with_scaling(widget, val, default_font_size=10):
    """
    Writes a value to a PDF form widget, automatically scaling the font size
    to prevent visual text clipping or boundary overflow.
    """
    val_str = str(val if val is not None else "").strip()
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

    width = rect.x1 - rect.x0
    height = rect.y1 - rect.y0
    
    # Normalize newline characters
    val_str = val_str.replace("\r\n", "\n").replace("\r", "\n")
    lines = val_str.split("\n")
    max_line_len = max(len(line) for line in lines) if lines else 0
    num_lines = len(lines)
    
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
    charge_line1, charge_line2 = _split_charge(data.get("charge", ""))
    
    # Full name parsing
    full_name = data.get("name") or data.get("defendant_name") or ""
    first_name = data.get("first_name") or ""
    last_name = data.get("last_name") or ""
    if not first_name and not last_name and full_name:
        parts = full_name.strip().split()
        if len(parts) >= 2:
            last_name = parts[-1]
            first_name = " ".join(parts[:-1])
        elif parts:
            last_name = parts[0]
            
    booking_number = data.get("booking_number") or data.get("defendant_booking_number") or ""
    county = data.get("county") or data.get("defendant_county") or ""
    address = data.get("address") or data.get("defendant_address") or ""
    
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
        "DefCharge1": charge_line1,
        "DefCharge1Line2": charge_line2,
        "CourtDate": data.get("court_date") or data.get("defendant_court_date") or "",
        "CourtTime": data.get("court_time") or data.get("defendant_court_time") or "",
        "CaseNum": data.get("case_number") or data.get("defendant_case_number") or "",
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
    
    # Write values to form fields
    for widget in page.widgets():
        field_name = widget.field_name
        if field_name in field_values:
            val = field_values[field_name]
            default_fs = font_sizes.get(field_name, 10)
            _set_widget_value_with_scaling(widget, val, default_font_size=default_fs)
            
    # Output
    buf = io.BytesIO()
    doc.save(buf)
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
    charge_line1, charge_line2 = _split_charge(data.get("charge", ""))
    
    full_name = data.get("name") or data.get("defendant_name") or ""
    booking_number = data.get("booking_number") or data.get("defendant_booking_number") or ""
    county = data.get("county") or data.get("defendant_county") or ""
    address = data.get("address") or data.get("defendant_address") or ""
    
    court_datetime = data.get("court_date") or data.get("defendant_court_date") or ""
    court_time = data.get("court_time") or data.get("defendant_court_time") or ""
    if court_time:
        court_datetime = f"{court_datetime} {court_time}".strip()
    
    # ── Field Mapping ──
    field_values = {
        "defendantNameField": full_name,
        "countyField": county,
        "numericBondAmount": f"${bond_amount:,.2f}",
        "chargesField1": charge_line1,
        "chargesField2": charge_line2,
        "CourtDateAndTimeField": court_datetime,
        "ArrestNumberField": booking_number,
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
        "CollateralField": "Indemnity Agreement, Promissory Note",
        "collateralDescriptionField": "",
        "AgencyField": AGENCY_NAME,
        "whoSignedField": "defendant and family/friends",
        "Transfer agent": "",
    }
    
    # Default font sizes per field for premium layout aesthetics
    font_sizes = {
        "defendantNameField": 11,
        "DefendantAddress": 8.5,
        "writtenPremiumAmount": 8.5,
    }
    
    for widget in page.widgets():
        field_name = widget.field_name
        if field_name in field_values:
            val = field_values[field_name]
            default_fs = font_sizes.get(field_name, 10)
            _set_widget_value_with_scaling(widget, val, default_font_size=default_fs)
            
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    buf.seek(0)
    return buf.read()


def generate_appearance_bonds(bond_data: dict, template: str = "osi") -> list[bytes]:
    """
    Generate filled appearance bond PDFs (one per criminal charge).
    
    Args:
        bond_data: Dict containing defendant, indemnitor, and booking facts.
        template: "osi" or "palmetto" template set to use.
        
    Returns: List of PDF byte buffers (one per normalized charge).
    """
    charges_input = bond_data.get("charges")
    bond_amount_input = bond_data.get("bond_amount")
    
    normalized_list = _normalize_charges_and_amounts(charges_input, bond_amount_input)
    
    pdfs = []
    poas = bond_data.get("poa_number") or ""
    poa_list = []
    if isinstance(poas, list):
        poa_list = [str(p).strip() for p in poas]
    elif isinstance(poas, str):
        if "|" in poas:
            poa_list = [p.strip() for p in poas.split("|")]
        elif ";" in poas:
            poa_list = [p.strip() for p in poas.split(";")]
        elif "," in poas:
            poa_list = [p.strip() for p in poas.split(",")]
        else:
            poa_list = [poas.strip()]
            
    for idx, item in enumerate(normalized_list):
        charge_data = dict(bond_data)
        charge_data["charge"] = item["charge"]
        charge_data["bond_amount"] = item["amount"]
        
        # Match POA to the charge
        if idx < len(poa_list):
            charge_data["poa_number"] = poa_list[idx]
        elif len(poa_list) == 1:
            charge_data["poa_number"] = poa_list[0] if idx == 0 else ""
        else:
            charge_data["poa_number"] = ""
            
        # Select appropriate filling routine
        surety = template.lower().strip()
        if surety == "palmetto":
            pdf_bytes = fill_palmetto_bond(charge_data)
        else:
            pdf_bytes = fill_osi_bond(charge_data)
            
        pdfs.append(pdf_bytes)
        
    return pdfs


def generate_appearance_bond(data: dict) -> bytes:
    """
    Generate a single filled appearance bond PDF for the given surety.
    Backward compatibility wrapper.
    
    Args:
        data: Dict with keys: surety ('osi'|'palmetto'), name, booking_number,
              county, bond_amount, charge, court_date, address, etc.
              
    Returns: PDF bytes of the first filled template page
    """
    surety = (data.get("surety", "osi") or "osi").lower().strip()
    pdfs = generate_appearance_bonds(data, template=surety)
    return pdfs[0] if pdfs else b""


def generate_safe_filename(data: dict) -> str:
    """Generate a filesystem-safe filename for the bond PDF."""
    name = re.sub(r'[^A-Za-z0-9_-]', '_', (data.get("name") or data.get("defendant_name") or "defendant"))
    charge = data.get("charge", "charge") or "charge"
    charge_short = re.sub(r'[^A-Za-z0-9_-]', '_', charge[:25])
    surety = (data.get("surety", "osi") or "osi").upper()
    date_str = datetime.now().strftime("%m-%d-%Y")
    return f"AppearanceBond_{surety}_{name}_{charge_short}_{date_str}.pdf"
