"""Ensure finished FL scrapers stay on REGISTERED_COUNTIES (frontend fleet).

Wave-2 FL counties were scheduled in main.py but missing from the registry,
so the Scraper Health / Multi-State Ops UIs never showed them.
"""
from __future__ import annotations

from dashboard.extensions import (
    REGISTERED_COUNTIES,
    county_label,
    registered_county_to_trigger_key,
)

# All 67 FL counties with scrapers registered in main.py Wave 2
EXPECTED_FL_WAVE2 = {
    "Baker (FL)",
    "Bradford (FL)",
    "Calhoun (FL)",
    "Franklin (FL)",
    "Gilchrist (FL)",
    "Gulf (FL)",
    "Hamilton (FL)",
    "Holmes (FL)",
    "Jefferson (FL)",
    "Lafayette (FL)",
    "Levy (FL)",
    "Liberty (FL)",
    "Madison (FL)",
    "Union (FL)",
    "Wakulla (FL)",
    "Washington (FL)",
}


def test_wave2_fl_counties_are_registered():
    reg = set(REGISTERED_COUNTIES)
    missing = EXPECTED_FL_WAVE2 - reg
    assert not missing, f"Wave-2 FL counties missing from REGISTERED_COUNTIES: {sorted(missing)}"


def test_fl_is_full_67_and_total_269():
    fl = [c for c in REGISTERED_COUNTIES if c.endswith("(FL)")]
    assert len(fl) == 67, f"Expected 67 FL counties, got {len(fl)}"
    assert len(REGISTERED_COUNTIES) == 269, f"Expected 269 total, got {len(REGISTERED_COUNTIES)}"


# Wave-3 NC/TN/TX metros (2026-07-26)
EXPECTED_WAVE3 = {
    "Buncombe (NC)",
    "Johnston (NC)",
    "Onslow (NC)",
    "Williamson (TN)",
    "Montgomery (TN)",
    "Sumner (TN)",
    "Cameron (TX)",
    "Brazoria (TX)",
    "Galveston (TX)",
}


def test_wave3_nc_tn_tx_counties_are_registered():
    reg = set(REGISTERED_COUNTIES)
    missing = EXPECTED_WAVE3 - reg
    assert not missing, f"Wave-3 counties missing from REGISTERED_COUNTIES: {sorted(missing)}"


def test_st_johns_st_lucie_trigger_keys_have_no_period_slug():
    """Periods in 'St. Johns' must not produce st._johns job keys."""
    assert registered_county_to_trigger_key("St. Johns (FL)") == "st_johns"
    assert registered_county_to_trigger_key("St. Lucie (FL)") == "st_lucie"


def test_st_johns_scraper_id_matches_trigger_key():
    from scrapers.counties.st_johns import StJohnsCountyScraper
    from scrapers.counties.st_lucie import StLucieCountyScraper

    assert StJohnsCountyScraper().scraper_id == "scraper_st_johns"
    assert StLucieCountyScraper().scraper_id == "scraper_st_lucie"


def test_baker_scraper_on_registry():
    from scrapers.counties.baker import BakerCountyScraper

    s = BakerCountyScraper()
    label = county_label(s.county, s.state)
    assert label == "Baker (FL)"
    assert label in REGISTERED_COUNTIES
    assert registered_county_to_trigger_key(label) == "baker"
