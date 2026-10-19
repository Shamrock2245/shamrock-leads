"""Pinellas: status only from the published booking-cell custody line; blank is "" (never assumed)."""
import inspect

import pytest

from scrapers.counties import pinellas
from scrapers.counties.pinellas import PinellasCountyScraper


def _raw(custody):
    return {"name": "DOE, JANE", "booking_num": "2600009999", "inmate_num": "123",
            "booking_dt": "10/7/2026 6:29:22 AM", "custody": custody, "race": "WHITE", "sex": "FEMALE",
            "dob": "01/04/1999", "charge": "PETIT THEFT", "bond_amount": "5000", "case_number": "26-1-CF"}


def _rec(custody):
    return PinellasCountyScraper()._row_to_record(_raw(custody))


def test_blank_custody_is_unknown_not_in_custody():
    r = _rec("")
    assert r.Status == "" and "status_source" not in (r.extra_data or {})


def test_whitespace_custody_is_unknown():
    assert _rec("   ").Status == ""


@pytest.mark.parametrize("cell", ["In Custody", "IN CUSTODY", "In Jail"])
def test_published_in_custody(cell):
    r = _rec(cell)
    assert r.Status == "In Custody" and r.extra_data["status_source"] == "published_booking_cell_custody"


@pytest.mark.parametrize("cell", ["Released", "RELEASED 10/8/2026"])
def test_published_released(cell):
    r = _rec(cell)
    assert r.Status == "Released" and r.extra_data["status_source"] == "published_booking_cell_custody"


def test_other_published_text_kept_verbatim():
    r = _rec("Transferred")
    assert r.Status == "Transferred" and r.extra_data["status_source"] == "published_booking_cell_custody"


def test_no_in_custody_fallback_in_code():
    src = inspect.getsource(PinellasCountyScraper._row_to_record)
    assert 'status = "In Custody"' not in src


def test_bond_charges_and_key_unchanged():
    r = _rec("")
    assert (r.Booking_Number, r.Bond_Amount, r.Charges, r.Case_Number) == ("2600009999", "5000", "PETIT THEFT", "26-1-CF")


def test_relay_only_setup_untouched():
    src = inspect.getsource(pinellas)
    assert "PINELLAS_EGRESS_MODE" in src
