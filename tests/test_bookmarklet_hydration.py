"""
Unit tests for bookmarklet booking extraction payload normalization and intake/appearance-bond hydration.
"""
import pytest
from dashboard.routers.intake import _extract_defendant
from dashboard.bond_pdf_service import normalize_charge_rows, _parse_defendant_name


def test_bookmarklet_defendant_extraction():
    """Verify that raw bookmarklet JSON is normalized cleanly by the backend."""
    bookmarklet_data = {
        "county": "Lee",
        "defendantFullName": "PERKINS, MICHAEL JAMES",
        "defendantArrestNumber": "1029767",
        "defendantDOB": "1985-04-12",
        "defendantRace": "W",
        "defendantSex": "M",
        "defendantHeight": "5'10\"",
        "defendantWeight": "180",
        "defendantStreetAddress": "2424 JACKSON ST",
        "defendantCity": "FORT MYERS",
        "defendantState": "FL",
        "defendantZip": "33901",
        "charges": [
            {
                "description": "DRUGS-POSSESS - POSSESS CONTROLLED SUBSTANCE W/O PRESCRIPTION",
                "bondAmount": "5000",
                "bondType": "CASH / SURETY",
                "caseNumber": "26CF016741",
                "hearing": "9/8/2026, 8:30:00 AM",
                "courtLocation": "LEE COUNTY JUSTICE CENTER"
            }
        ]
    }

    defendant = _extract_defendant(bookmarklet_data)
    assert defendant["name"] == "PERKINS, MICHAEL JAMES"
    assert defendant["bookingNumber"] == "1029767"
    assert defendant["dob"] == "1985-04-12"
    assert defendant["street"] == "2424 JACKSON ST"
    assert defendant["city"] == "FORT MYERS"
    assert defendant["state"] == "FL"
    assert defendant["zip"] == "33901"
    assert "DRUGS" in defendant["charges"]
    assert len(defendant["charge_details"]) == 1
    assert defendant["charge_details"][0]["caseNumber"] == "26CF016741"


def test_bookmarklet_to_appearance_bond_rows():
    """Verify that bookmarklet data structures map seamlessly to appearance bond charge rows."""
    lead_data = {
        "full_name": "PERKINS, MICHAEL JAMES",
        "booking_number": "1029767",
        "county": "Lee",
        "bond_amount": 5000,
        "case_number": "26CF016741",
        "court_date": "9/8/2026",
        "court_time": "8:30:00 AM",
        "charge_details": [
            {
                "charge": "DRUGS-POSSESS - POSSESS CONTROLLED SUBSTANCE W/O PRESCRIPTION",
                "bond_amount": 5000,
                "case_number": "26CF016741",
                "court_date": "9/8/2026",
                "court_time": "8:30:00 AM",
                "poa_number": "OSI6 20132136",
            }
        ]
    }

    rows = normalize_charge_rows(lead_data)
    assert len(rows) == 1
    assert rows[0]["case_number"] == "26CF016741"
    assert rows[0]["court_date"] == "9/8/2026"
    assert rows[0]["court_time"] == "8:30:00 AM"
    assert rows[0]["amount"] == 5000
    assert rows[0]["poa_number"] == "OSI6 20132136"

    first, last = _parse_defendant_name(lead_data["full_name"])
    assert last == "PERKINS"
    assert first == "MICHAEL JAMES"
