"""
ShamrockLeads — Bond PDF Generation Service
Fills official OSI and Palmetto Appearance Bond PDF templates
with arrest record data using PyMuPDF (fitz).

One appearance bond per criminal charge.
"""

import io
import os
import re
from datetime import datetime
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
AGENT_NAME = "Brendan O'Neal"
AGENT_LICENSE = "P139768"
AGENCY_DETAILS = "Shamrock Bail Bonds\r1528 Broadway\rFort Myers, FL 33901\r239-332-2245\rshamrockbailbonds.biz"
AGENCY_NAME = "Shamrock Bail Bonds"


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


def _parse_date_parts(date_str: str) -> dict:
    """Parse a date string into day, month name, and year components."""
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
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return {
                "day": str(dt.day),
                "month": dt.strftime("%B"),
                "year": str(dt.year),
                "year_yy": dt.strftime("%y"),
                "formatted": dt.strftime("%m/%d/%Y"),
            }
        except ValueError:
            continue
    return {
        "day": "", "month": "", "year": date_str,
        "year_yy": date_str[-2:] if len(date_str) >= 2 else "",
        "formatted": date_str,
    }


def _amount_to_words(amount: float) -> str:
    """Convert a numeric bond amount to words for the written premium field."""
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return ""
    if amount <= 0:
        return "Zero"
    
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
        result += f" and {cents}/100"
    else:
        result += " and 00/100"
    
    return result


def fill_osi_bond(data: dict) -> bytes:
    """
    Fill the OSI Appearance Bond template with arrest data.
    
    Expected data keys:
        name, first_name, last_name, booking_number, county, bond_amount,
        charge, court_date, court_time, case_number, address, dob,
        bond_date, poa_number, court_type, indemnitor_name
    
    Returns: PDF bytes
    """
    if not OSI_TEMPLATE.exists():
        raise FileNotFoundError(f"OSI template not found: {OSI_TEMPLATE}")
    
    doc = fitz.open(str(OSI_TEMPLATE))
    page = doc[0]
    
    bond_amount = float(data.get("bond_amount", 0) or 0)
    premium = max(100, bond_amount * 0.10)
    date_parts = _parse_date_parts(data.get("bond_date", ""))
    charge_line1, charge_line2 = _split_charge(data.get("charge", ""))
    
    # Full name parsing
    full_name = data.get("name", "")
    first_name = data.get("first_name", "")
    last_name = data.get("last_name", "")
    if not first_name and not last_name and full_name:
        parts = full_name.strip().split()
        if len(parts) >= 2:
            last_name = parts[-1]
            first_name = " ".join(parts[:-1])
        elif parts:
            last_name = parts[0]
    
    # Indemnitor + Defendant display name
    indemnitor = data.get("indemnitor_name", "")
    ind_def_display = f"{indemnitor} / {full_name}" if indemnitor else full_name
    
    # ── Field Mapping ──
    field_values = {
        "DefLastName": last_name,
        "DefFirstName": first_name,
        "DefCounty": data.get("county", ""),
        "DefCourtType": data.get("court_type", ""),
        "BondAmountCharge1": f"${bond_amount:,.2f}",
        "DefCharge1": charge_line1,
        "DefCharge1Line2": charge_line2,
        "CourtDate": data.get("court_date", ""),
        "CourtTime": data.get("court_time", ""),
        "CaseNum": data.get("case_number", ""),
        "Arrest/case No": data.get("booking_number", ""),
        "DefAddress": data.get("address", ""),
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
    
    # Write values to form fields
    for widget in page.widgets():
        field_name = widget.field_name
        if field_name in field_values:
            val = field_values[field_name]
            if val:  # Only write non-empty values
                widget.field_value = str(val)
                widget.update()
    
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
        name, booking_number, county, bond_amount, charge, court_date,
        case_number, address, bond_date, poa_number
    
    Returns: PDF bytes
    """
    if not PALMETTO_TEMPLATE.exists():
        raise FileNotFoundError(f"Palmetto template not found: {PALMETTO_TEMPLATE}")
    
    doc = fitz.open(str(PALMETTO_TEMPLATE))
    page = doc[0]
    
    bond_amount = float(data.get("bond_amount", 0) or 0)
    premium = max(100, bond_amount * 0.10)
    date_parts = _parse_date_parts(data.get("bond_date", ""))
    charge_line1, charge_line2 = _split_charge(data.get("charge", ""))
    
    full_name = data.get("name", "")
    court_datetime = data.get("court_date", "")
    if data.get("court_time"):
        court_datetime = f"{court_datetime} {data['court_time']}"
    
    # ── Field Mapping ──
    field_values = {
        "defendantNameField": full_name,
        "countyField": data.get("county", ""),
        "numericBondAmount": f"${bond_amount:,.2f}",
        "chargesField1": charge_line1,
        "chargesField2": charge_line2,
        "CourtDateAndTimeField": court_datetime,
        "ArrestNumberField": data.get("booking_number", ""),
        "DefendantAddress": data.get("address", ""),
        "powerNumField": data.get("poa_number", ""),
        "dayField": date_parts["day"],
        "monthWrittenField": date_parts["month"],
        "yearYYYYField": date_parts["year"],
        "cirCoField": data.get("court_type", ""),
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
    
    for widget in page.widgets():
        field_name = widget.field_name
        if field_name in field_values:
            val = field_values[field_name]
            if val:
                widget.field_value = str(val)
                widget.update()
    
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    buf.seek(0)
    return buf.read()


def generate_appearance_bond(data: dict) -> bytes:
    """
    Generate a filled appearance bond PDF for the given surety.
    
    Args:
        data: Dict with keys: surety ('osi'|'palmetto'), name, booking_number,
              county, bond_amount, charge, court_date, address, etc.
    
    Returns: PDF bytes of the filled template
    """
    surety = (data.get("surety", "osi") or "osi").lower().strip()
    
    if surety == "palmetto":
        return fill_palmetto_bond(data)
    else:
        return fill_osi_bond(data)


def generate_safe_filename(data: dict) -> str:
    """Generate a filesystem-safe filename for the bond PDF."""
    name = re.sub(r'[^A-Za-z0-9_-]', '_', (data.get("name", "defendant") or "defendant"))
    charge = data.get("charge", "charge") or "charge"
    charge_short = re.sub(r'[^A-Za-z0-9_-]', '_', charge[:25])
    surety = (data.get("surety", "osi") or "osi").upper()
    date_str = datetime.now().strftime("%m-%d-%Y")
    return f"AppearanceBond_{surety}_{name}_{charge_short}_{date_str}.pdf"
