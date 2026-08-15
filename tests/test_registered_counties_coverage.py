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


def test_fl_is_full_67_and_total_358():
    fl = [c for c in REGISTERED_COUNTIES if c.endswith("(FL)")]
    assert len(fl) == 67, f"Expected 67 FL counties, got {len(fl)}"
    assert len(REGISTERED_COUNTIES) == 358, f"Expected 358 total, got {len(REGISTERED_COUNTIES)}"


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


# Wave-4 Expansion Across 7 States (32 new scrapers)
EXPECTED_MULTI_STATE_WAVE4 = {
    "Blount (TN)", "Bradley (TN)", "Sevier (TN)", "Washington (TN)", "Wilson (TN)",
    "Bell (TX)", "Brazos (TX)", "Hays (TX)", "Jefferson (TX)", "Lubbock (TX)", "McLennan (TX)", "Nueces (TX)", "Webb (TX)",
    "Bridgeport (CT)", "Hartford (CT)", "New Haven (CT)", "Stamford (CT)",
    "Caddo (LA)", "Calcasieu (LA)", "Ouachita (LA)", "St. Tammany (LA)",
    "Baldwin (AL)", "Montgomery (AL)", "Shelby (AL)", "Tuscaloosa (AL)",
    "DeSoto (MS)", "Harrison (MS)", "Rankin (MS)",
    "Robeson (NC)", "Rowan (NC)", "Wayne (NC)", "Wilkes (NC)",
}


# Wave-5 expansion coverage remains in the current 358-scraper registry.
EXPECTED_MULTI_STATE_WAVE5 = {
    "Gordon (GA)", "Walker (GA)", "Whitfield (GA)", "Tift (GA)", "Ware (GA)", "Coffee (GA)", "Appling (GA)", "Bleckley (GA)", "Crisp (GA)", "Laurens (GA)", "Effingham (GA)",
    "Ellis (TX)", "Johnson (TX)", "Ector (TX)", "Midland (TX)", "Potter (TX)", "Bastrop (TX)", "Guadalupe (TX)", "Comal (TX)", "Victoria (TX)", "Walker (TX)",
    "Maury (TN)", "Robertson (TN)", "Hamblen (TN)", "Bedford (TN)", "Coffee (TN)", "Lincoln (TN)", "Giles (TN)",
    "Nash (NC)", "Vance (NC)", "Rockingham (NC)", "Granville (NC)", "Person (NC)", "Warren (NC)", "Caswell (NC)", "Chowan (NC)", "Perquimans (NC)",
    "Houston (AL)", "Morgan (AL)", "Etowah (AL)", "Cullman (AL)", "DeKalb (AL)", "Jackson (AL)",
    "Lauderdale (MS)", "Forrest (MS)", "Jones (MS)", "Madison (MS)",
    "Ascension (LA)", "Livingston (LA)",
}


def test_wave3_nc_tn_tx_counties_are_registered():
    reg = set(REGISTERED_COUNTIES)
    missing = EXPECTED_WAVE3 - reg
    assert not missing, f"Wave-3 counties missing from REGISTERED_COUNTIES: {sorted(missing)}"


def test_wave4_multi_state_counties_are_registered():
    reg = set(REGISTERED_COUNTIES)
    missing = EXPECTED_MULTI_STATE_WAVE4 - reg
    assert not missing, f"Wave-4 multi-state counties missing from REGISTERED_COUNTIES: {sorted(missing)}"


def test_wave5_multi_state_counties_are_registered():
    reg = set(REGISTERED_COUNTIES)
    missing = EXPECTED_MULTI_STATE_WAVE5 - reg
    assert not missing, f"Wave-5 multi-state counties missing from REGISTERED_COUNTIES: {sorted(missing)}"


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
