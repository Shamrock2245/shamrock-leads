"""Collier: bond only from this person's published bond span; "" when absent; published $0 kept."""
from scrapers.counties.collier import CollierCountyScraper
from tests.test_collier_glades_published_bond import _collier, _collier_person


def test_charge_text_dollars_never_bond_even_with_bonded_summary():
    recs = _collier(_collier_person("ctl02", "202600000601", "GRAND THEFT $750-$5K $20,000", "10/07/2026  BONDED"))
    assert recs["202600000601"].Bond_Amount == ""


def test_published_zero_bond_kept():
    recs = _collier(_collier_person("ctl02", "202600000602", "DUI", "No information available.", bond_amount="$0.00"))
    assert recs["202600000602"].Bond_Amount == "0"


def test_amount_format_and_unknown():
    f = CollierCountyScraper._published_bond_amount
    assert f("$1,500.00") == "1500"
    assert f("$250.50") == "250.5"
    assert f("No information available.") == ""
    assert f("") == ""


def test_status_never_hardcoded():
    recs = _collier(_collier_person("ctl02", "202600000603", "DUI", "No information available."))
    assert recs["202600000603"].Status == ""
