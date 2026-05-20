import pytest
import io
from datetime import datetime, date
import fitz
from dashboard.bond_pdf_service import (
    _safe_float,
    _amount_to_words,
    _parse_date_parts,
    _normalize_charges_and_amounts,
    generate_appearance_bonds,
    generate_appearance_bond,
    fill_osi_bond,
    fill_palmetto_bond,
)


def test_safe_float():
    assert _safe_float(125.5) == 125.5
    assert _safe_float(100) == 100.0
    assert _safe_float("$1,250.00") == 1250.0
    assert _safe_float("-$500.50") == -500.5
    assert _safe_float(None) == 0.0
    assert _safe_float("") == 0.0
    assert _safe_float("N/A") == 0.0


def test_amount_to_words():
    # Standard Title Case and Dollars suffix verification
    assert _amount_to_words(150.0) == "One Hundred Fifty and 00/100 Dollars"
    assert _amount_to_words(150.25) == "One Hundred Fifty and 25/100 Dollars"
    assert _amount_to_words(2500) == "Two Thousand Five Hundred and 00/100 Dollars"
    assert _amount_to_words(0) == "Zero and 00/100 Dollars"
    assert _amount_to_words(-10) == "Zero and 00/100 Dollars"
    assert _amount_to_words(1000000) == "One Million and 00/100 Dollars"


def test_parse_date_parts():
    # 1. Test None/Empty
    res = _parse_date_parts(None)
    assert res["day"] != ""
    assert res["month"] != ""
    assert len(res["year"]) == 4

    # 2. Test native Date/Datetime
    dt = datetime(2026, 5, 20, 14, 30, 0)
    res = _parse_date_parts(dt)
    assert res["day"] == "20"
    assert res["month"] == "May"
    assert res["year"] == "2026"
    assert res["year_yy"] == "26"
    assert res["formatted"] == "05/20/2026"

    d = date(2026, 5, 20)
    res = _parse_date_parts(d)
    assert res["day"] == "20"
    assert res["month"] == "May"
    assert res["year"] == "2026"
    assert res["year_yy"] == "26"
    assert res["formatted"] == "05/20/2026"

    # 3. Test Strings
    res = _parse_date_parts("05/20/2026")
    assert res["day"] == "20"
    assert res["month"] == "May"
    assert res["year"] == "2026"

    res = _parse_date_parts("2026-05-20")
    assert res["day"] == "20"
    assert res["month"] == "May"
    assert res["year"] == "2026"

    # ISO Format
    res = _parse_date_parts("2026-05-20T17:12:41Z")
    assert res["day"] == "20"
    assert res["month"] == "May"
    assert res["year"] == "2026"


def test_normalize_charges_and_amounts():
    # Case A: List of dicts (Wix format)
    charges_a = [
        {"charge": "DUI", "amount": 1000},
        {"description": "BATTERY", "bond_amount": 500}
    ]
    res = _normalize_charges_and_amounts(charges_a, None)
    assert len(res) == 2
    assert res[0] == {"charge": "DUI", "amount": 1000.0}
    assert res[1] == {"charge": "BATTERY", "amount": 500.0}

    # Case B: List of strings and list of amounts
    charges_b = ["DUI", "BATTERY"]
    amounts_b = [1000, 500]
    res = _normalize_charges_and_amounts(charges_b, amounts_b)
    assert len(res) == 2
    assert res[0] == {"charge": "DUI", "amount": 1000.0}
    assert res[1] == {"charge": "BATTERY", "amount": 500.0}

    # Case C: List of strings and single amount (falls back to putting all on first charge)
    charges_c = ["DUI", "BATTERY"]
    amount_c = 1500
    res = _normalize_charges_and_amounts(charges_c, amount_c)
    assert len(res) == 2
    assert res[0] == {"charge": "DUI", "amount": 1500.0}
    assert res[1] == {"charge": "BATTERY", "amount": 0.0}

    # Case D: Pipe delimited strings
    charges_d = "DUI | BATTERY | THEFT"
    amount_d = "1000 | 500 | 250"
    res = _normalize_charges_and_amounts(charges_d, amount_d)
    assert len(res) == 3
    assert res[0] == {"charge": "DUI", "amount": 1000.0}
    assert res[1] == {"charge": "BATTERY", "amount": 500.0}
    assert res[2] == {"charge": "THEFT", "amount": 250.0}

    # Case E: Newline delimited strings
    charges_e = "DUI\nBATTERY"
    amount_e = "1000\n500"
    res = _normalize_charges_and_amounts(charges_e, amount_e)
    assert len(res) == 2
    assert res[0] == {"charge": "DUI", "amount": 1000.0}
    assert res[1] == {"charge": "BATTERY", "amount": 500.0}

    # Case F: Semicolon delimited strings
    charges_f = "DUI; BATTERY"
    amount_f = "1000; 500"
    res = _normalize_charges_and_amounts(charges_f, amount_f)
    assert len(res) == 2
    assert res[0] == {"charge": "DUI", "amount": 1000.0}
    assert res[1] == {"charge": "BATTERY", "amount": 500.0}

    # Case G: Comma split (only if length matches amounts)
    charges_g = "DUI, BATTERY"
    amount_g = "1000, 500"
    res = _normalize_charges_and_amounts(charges_g, amount_g)
    assert len(res) == 2
    assert res[0] == {"charge": "DUI", "amount": 1000.0}
    assert res[1] == {"charge": "BATTERY", "amount": 500.0}

    # Case H: Comma within description (no split)
    charges_h = "DUI, 1ST OFFENSE"
    amount_h = "1000"
    res = _normalize_charges_and_amounts(charges_h, amount_h)
    assert len(res) == 1
    assert res[0] == {"charge": "DUI, 1ST OFFENSE", "amount": 1000.0}


def test_fill_osi_bond():
    test_data = {
        "defendant_name": "John Doe",
        "booking_number": "BK-12345",
        "county": "Lee",
        "bond_amount": 1500,
        "charge": "DUI | LEAVING THE SCENE",
        "court_date": "06/15/2026",
        "court_time": "09:00 AM",
        "case_number": "2026-CF-0001",
        "address": "123 Main St, Fort Myers, FL 33901",
        "bond_date": "05/20/2026",
        "poa_number": "OSI-998877",
        "court_type": "Circuit",
        "indemnitor_name": "Jane Doe",
    }
    
    # fill_osi_bond should return bytes and PyMuPDF should load it successfully
    pdf_bytes = fill_osi_bond(test_data)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count == 1
    
    # Verify a few field values were set
    page = doc[0]
    widgets = {w.field_name: w.field_value for w in page.widgets()}
    assert widgets.get("DefLastName") == "Doe"
    assert widgets.get("DefFirstName") == "John"
    assert widgets.get("DefCounty") == "Lee"
    assert "$1,500.00" in widgets.get("BondAmountCharge1", "")
    assert widgets.get("PowerNum") == "OSI-998877"
    assert widgets.get("IndNameandDefName") == "Jane Doe / John Doe"
    doc.close()


def test_fill_palmetto_bond():
    test_data = {
        "defendant_name": "John Doe",
        "booking_number": "BK-12345",
        "county": "Collier",
        "bond_amount": 5000,
        "charge": "GRAND THEFT",
        "court_date": "06/20/2026",
        "court_time": "10:30 AM",
        "case_number": "2026-CF-0002",
        "address": "456 Naples Rd, Naples, FL 34102",
        "bond_date": "05/20/2026",
        "poa_number": "PAL-112233",
        "court_type": "Circuit",
        "indemnitor_name": "Jane Doe",
    }
    
    pdf_bytes = fill_palmetto_bond(test_data)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count == 1
    
    page = doc[0]
    widgets = {w.field_name: w.field_value for w in page.widgets()}
    assert widgets.get("defendantNameField") == "John Doe"
    assert widgets.get("countyField") == "Collier"
    assert "$5,000.00" in widgets.get("numericBondAmount", "")
    assert widgets.get("powerNumField") == "PAL-112233"
    doc.close()


def test_generate_appearance_bonds_plural():
    test_data = {
        "defendant_name": "John Doe",
        "booking_number": "BK-12345",
        "county": "Lee",
        "bond_amount": "5000 | 2500",
        "charges": "GRAND THEFT | BURGLARY",
        "bond_date": "05/20/2026",
        "poa_number": "OSI-111 | OSI-222",
    }
    
    # Should generate exactly 2 separate PDF pages/buffers (one per charge)
    pdfs = generate_appearance_bonds(test_data, template="osi")
    assert len(pdfs) == 2
    
    # Validate PDF 1
    doc1 = fitz.open(stream=pdfs[0], filetype="pdf")
    widgets1 = {w.field_name: w.field_value for w in doc1[0].widgets()}
    assert widgets1.get("DefCharge1") == "GRAND THEFT"
    assert "$5,000.00" in widgets1.get("BondAmountCharge1", "")
    assert widgets1.get("PowerNum") == "OSI-111"
    doc1.close()
    
    # Validate PDF 2
    doc2 = fitz.open(stream=pdfs[1], filetype="pdf")
    widgets2 = {w.field_name: w.field_value for w in doc2[0].widgets()}
    assert widgets2.get("DefCharge1") == "BURGLARY"
    assert "$2,500.00" in widgets2.get("BondAmountCharge1", "")
    assert widgets2.get("PowerNum") == "OSI-222"
    doc2.close()


def test_generate_appearance_bond_singular():
    test_data = {
        "defendant_name": "John Doe",
        "booking_number": "BK-12345",
        "county": "Lee",
        "bond_amount": 5000,
        "charges": "GRAND THEFT",
        "bond_date": "05/20/2026",
        "poa_number": "OSI-111",
    }
    
    # Should work seamlessly for backward compatibility, returning a single byte buffer
    pdf_bytes = generate_appearance_bond(test_data)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    widgets = {w.field_name: w.field_value for w in doc[0].widgets()}
    assert widgets.get("DefCharge1") == "GRAND THEFT"
    assert "$5,000.00" in widgets.get("BondAmountCharge1", "")
    doc.close()
