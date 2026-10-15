"""Duval / Palm Beach: unknown bond is "", a published 0 is kept (never invented)."""
import inspect

import pytest

from scrapers.pbso_parse import published_card_bond, row_to_ingest_dict
from scrapers.counties.duval import _published_bond_str
import scrapers.counties.duval as duval
import scrapers.counties.palm_beach as palm_beach


@pytest.mark.parametrize("text,expected", [
    ("Current Bond: $1,500.00\n", "1500.00"),
    ("Current Bond: $100.00\nCurrent Bond: $200.00\n", "300.00"),
    ("Current Bond: $0.00\n", "0"),
    ("Current Bond: $0.00\nOriginal Bond: $0.00\n", "0"),
    ("Current Bond: $0.00\nOriginal Bond: $2,000.00\n", "2000.00"),
    ("Current Bond:\n$750.00\n", "750.00"),
    ("Original Bond: $500.00\n", "500.00"),
    ("Current Bond: NO BOND\n", ""),
    ("Current Bond: $100.00\nCurrent Bond: NO BOND\n", ""),
    ("Name: X\nBooking Number: 1\n", ""),
    ("", ""),
])
def test_pbso_published_card_bond(text, expected):
    assert published_card_bond(text) == expected


def test_pbso_ingest_dict_keeps_unknown_and_zero():
    assert row_to_ingest_dict({"bond_amount": ""}, "https://example.invalid/x")["bond_amount"] == ""
    assert row_to_ingest_dict({}, "https://example.invalid/x")["bond_amount"] == ""
    assert row_to_ingest_dict({"bond_amount": "0"}, "https://example.invalid/x")["bond_amount"] == "0"


@pytest.mark.parametrize("value,expected", [
    (1500, "1500"), ("$1,500.00", "1500"), (250.5, "250.50"),
    (0, "0"), ("0.00", "0"),
    (None, ""), ("", ""), ("NO BOND", ""), ("None", ""), (True, ""),
])
def test_duval_published_bond_str(value, expected):
    assert _published_bond_str(value) == expected


def test_no_zero_bond_defaults_left_in_source():
    for mod in (duval, palm_beach):
        src = inspect.getsource(mod)
        assert 'else "0"' not in src
        assert '"bond_amount", "0"' not in src
    import scrapers.pbso_parse as p
    src = inspect.getsource(p)
    assert '"bond_amount": "0"' not in src
    assert 'or "0"' not in src
