"""Defendant extractors must not inherit indemnitor generic keys."""
from dashboard.routers.intake import _extract_defendant, _extract_indemnitor


def test_defendant_does_not_inherit_indemnitor_phone_email_employer():
    payload = {
        "indemnitorName": "Jane Cosigner",
        "phone": "2395550001",
        "email": "jane@example.com",
        "employer": "Cosigner LLC",
        "employerPhone": "2395550002",
        "defendantName": "John Defendant",
        "DefBookingNumber": "LEE-1",
    }
    defendant = _extract_defendant(payload)
    indemnitor = _extract_indemnitor(payload)
    assert indemnitor["phone"] == "2395550001"
    assert indemnitor["email"] == "jane@example.com"
    assert indemnitor["employer"] == "Cosigner LLC"
    assert defendant["phone"] == ""
    assert defendant["email"] == ""
    assert defendant["employer"] == ""
    assert defendant["employerPhone"] == ""


def test_defendant_prefixed_contact_and_vehicle_keys_are_kept():
    payload = {
        "email": "jane@example.com",
        "phone": "2395550001",
        "defendantEmail": "john@example.com",
        "defendantPhone": "2395559999",
        "defendantEmployer": "Dockside",
        "vehiclePlate": "ABC123",
        "vehicleMake": "Ford",
        "make": "should-not-win",
        "plate": "WRONG",
    }
    defendant = _extract_defendant(payload)
    assert defendant["email"] == "john@example.com"
    assert defendant["phone"] == "2395559999"
    assert defendant["employer"] == "Dockside"
    assert defendant["vehiclePlate"] == "ABC123"
    assert defendant["vehicleMake"] == "Ford"


def test_defendant_ssn_and_dl_do_not_trade_places_with_indemnitor():
    payload = {
        "DefSSN": "123-45-6789",
        "DefDL": "D123",
        "DefDLState": "FL",
        "defendantStreetAddress": "1 Palm St",
        "IndSSN": "987654321",
        "IndDL": "R555",
        "ssn": "987654321",
        "dl": "R555",
        "address": "9 Bay Rd",
    }
    defendant = _extract_defendant(payload, apply_defaults=False)
    indemnitor = _extract_indemnitor(payload, apply_defaults=False)
    assert defendant["ssn"] == "123-45-6789"
    assert defendant["dl"] == "D123" and defendant["dlState"] == "FL"
    assert defendant["address"] == "1 Palm St" and defendant["street"] == "1 Palm St"
    assert indemnitor["ssn"] == "987654321"
    assert indemnitor["dl"] == "R555"
    assert indemnitor["address"] == "9 Bay Rd"
    assert defendant["ssn"] != indemnitor["ssn"]
