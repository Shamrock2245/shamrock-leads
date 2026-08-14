"""Statutory premium + Lee URL ingest field mapping."""
from __future__ import annotations

from dashboard.services.premium import statutory_premium
from dashboard.services.url_ingest_service import (
    _compose_address,
    _first_str,
    _normalize_dob,
)


def test_five_hundred_dollar_bond_is_one_hundred():
    assert statutory_premium(500) == 100.0
    assert statutory_premium(500, charge_count=1) == 100.0


def test_above_one_thousand_is_ten_percent():
    assert statutory_premium(1500) == 150.0
    assert statutory_premium(5000) == 500.0


def test_one_thousand_is_one_hundred():
    assert statutory_premium(1000) == 100.0


def test_per_charge_minimum():
    assert statutory_premium(500, charge_count=2) == 200.0
    assert statutory_premium(0, charge_amounts=[500, 500]) == 200.0
    assert statutory_premium(0, charge_amounts=[500, 2000]) == 300.0


def test_zero_bond():
    assert statutory_premium(0) == 0.0
    assert statutory_premium(None) == 0.0  # type: ignore[arg-type]


def test_normalize_dob_iso_and_slash():
    iso, display = _normalize_dob("1998-03-15T00:00:00Z")
    assert iso == "1998-03-15"
    assert display == "03/15/1998"
    iso2, display2 = _normalize_dob("03/15/1998")
    assert iso2 == "1998-03-15"
    assert display2 == "03/15/1998"


def test_compose_address_from_split_fields():
    addr = _compose_address({
        "address1": "123 Palm Ave",
        "city": "Fort Myers",
        "state": "FL",
        "zip": "33901",
    })
    assert addr == "123 Palm Ave, Fort Myers, FL 33901"


def test_compose_address_nested_and_aliases():
    addr = _compose_address({
        "streetAddress": "9 Oak St",
        "addressCity": "Lehigh Acres",
        "postalCode": "33936",
    })
    assert "9 Oak St" in addr
    assert "Lehigh Acres" in addr
    assert "33936" in addr


def test_first_str_skips_empty():
    assert _first_str("", None, "n/a", "Real St") == "Real St"
