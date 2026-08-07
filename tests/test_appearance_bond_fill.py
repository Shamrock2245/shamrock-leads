"""
Regression: appearance bond PDFs must use real charge, court case #, and hearing date.
Booking/arrest # must never be duplicated into CaseNum.
"""
from dashboard.bond_pdf_service import (
    _parse_defendant_name,
    _is_booking_as_case,
    _split_court_datetime,
    _is_placeholder_charge,
    normalize_charge_rows,
    fill_osi_bond,
)


def test_parse_name_last_comma_first():
    first, last = _parse_defendant_name("PERKINS, MICHAEL JAMES")
    assert last == "PERKINS"
    assert first == "MICHAEL JAMES"


def test_parse_name_first_last():
    first, last = _parse_defendant_name("Michael James Perkins")
    assert last == "Perkins"
    assert "Michael" in first


def test_booking_not_case_number():
    assert _is_booking_as_case("1029767", "1029767") is True
    assert _is_booking_as_case("26CF016741", "1029767") is False
    assert _is_booking_as_case("", "1029767") is True


def test_split_court_datetime_combined():
    d, t = _split_court_datetime("9/8/2026, 8:30:00 AM", "")
    assert d == "9/8/2026"
    assert "8:30" in t


def test_normalize_rows_rejects_booking_as_case_and_fills_court():
    rows = normalize_charge_rows({
        "booking_number": "1029767",
        "case_number": "1029767",  # wrong modal default
        "court_date": "9/8/2026, 8:30:00 AM",
        "charge_details": [{
            "charge": "DRUGS-POSSESS - POSSESS CONTROLLED SUBSTANCE W/O PRESCRIPTION",
            "bond_amount": 5000,
            "case_number": "1029767",
            "court_date": "TBN",
        }],
    })
    # Parent case was booking — cleared; row case also cleared
    assert rows[0]["case_number"] == ""
    assert rows[0]["court_date"] == "9/8/2026"
    assert "8:30" in rows[0]["court_time"]
    assert "DRUGS" in rows[0]["charge"]


def test_normalize_rows_keeps_real_case():
    rows = normalize_charge_rows({
        "booking_number": "1029767",
        "charge_details": [{
            "charge": "DRUGS-POSSESS - POSSESS CONTROLLED SUBSTANCE W/O PRESCRIPTION",
            "bond_amount": 5000,
            "case_number": "26CF016741",
            "court_date": "9/8/2026",
            "court_time": "8:30:00 AM",
        }],
    })
    assert rows[0]["case_number"] == "26CF016741"
    assert rows[0]["court_date"] == "9/8/2026"
    assert "8:30" in rows[0]["court_time"]


def test_fill_osi_perkins_style():
    """End-to-end field mapping for the Perkins / Lee County failure case."""
    pdf = fill_osi_bond({
        "name": "PERKINS, MICHAEL JAMES",
        "booking_number": "1029767",
        "county": "Lee",
        "bond_amount": 5000,
        "charge": "DRUGS-POSSESS - POSSESS CONTROLLED SUBSTANCE W/O PRESCRIPTION",
        "case_number": "26CF016741",
        "court_date": "9/8/2026",
        "court_time": "8:30:00 AM",
        "court_type": "Circuit Court",
        "address": "2424 JACKSON ST FORT MYERS FL 33901",
        "poa_number": "OSI6 20132136",
        "bond_date": "08/07/2026",
    })
    assert pdf[:4] == b"%PDF"

    import fitz
    doc = fitz.open(stream=pdf, filetype="pdf")
    fields = {w.field_name: (w.field_value or "") for w in doc[0].widgets() or []}
    doc.close()

    assert fields.get("Arrest/case No") == "1029767"
    assert fields.get("CaseNum") == "26CF016741"
    assert fields.get("Arrest/case No") != fields.get("CaseNum")
    assert "DRUGS" in (fields.get("DefCharge1") or "")
    assert "Unspecified" not in (fields.get("DefCharge1") or "")
    assert fields.get("CourtDate") == "9/8/2026"
    assert "8:30" in (fields.get("CourtTime") or "")
    assert fields.get("DefLastName") == "PERKINS"
    assert "MICHAEL" in (fields.get("DefFirstName") or "")


def test_fill_osi_does_not_put_booking_in_casenum():
    pdf = fill_osi_bond({
        "name": "DOE, JOHN",
        "booking_number": "1029767",
        "case_number": "1029767",  # polluted
        "bond_amount": 1000,
        "charge": "TEST CHARGE",
        "court_date": "TBN",
        "county": "Lee",
        "poa_number": "OSI3 1",
    })
    import fitz
    doc = fitz.open(stream=pdf, filetype="pdf")
    fields = {w.field_name: (w.field_value or "") for w in doc[0].widgets() or []}
    doc.close()
    assert fields.get("Arrest/case No") == "1029767"
    assert fields.get("CaseNum") in ("", None)


def test_placeholder_charge():
    assert _is_placeholder_charge("Unspecified Charge")
    assert not _is_placeholder_charge("DRUGS-POSSESS")
