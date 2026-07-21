"""Regression tests — dashboard county list must not show bare + labeled dupes.

Mongo stores bare names (``Lee``); registry uses multi-state labels
(``Lee (FL)``). The UI merge helper must absorb bare names into labels.
"""
from dashboard.extensions import (
    KEY_FL_COUNTIES,
    KEY_FL_COUNTY_LABELS,
    REGISTERED_COUNTIES,
    county_label,
    merge_county_list_for_ui,
    normalize_county_ui_name,
    parse_registered_county,
)


def test_key_fl_counties_include_lee_and_sarasota():
    assert "Lee" in KEY_FL_COUNTIES
    assert "Sarasota" in KEY_FL_COUNTIES
    assert "Lee (FL)" in KEY_FL_COUNTY_LABELS
    assert "Sarasota (FL)" in KEY_FL_COUNTY_LABELS
    assert "Lee (FL)" in REGISTERED_COUNTIES
    assert "Sarasota (FL)" in REGISTERED_COUNTIES


def test_merge_absorbs_bare_into_labeled():
    merged = merge_county_list_for_ui(["Lee", "Sarasota", "Collier", "Unknownville"])
    assert "Lee (FL)" in merged
    assert "Sarasota (FL)" in merged
    assert "Lee" not in merged  # bare absorbed
    assert "Sarasota" not in merged
    assert "Unknownville (FL)" in merged  # unregistered → labeled FL


def test_merge_keeps_multi_state_lee_distinct():
    merged = merge_county_list_for_ui(["Lee", "Lee (GA)"])
    assert "Lee (FL)" in merged
    assert "Lee (GA)" in merged
    assert "Lee (SC)" in merged  # still registered
    # Never a bare "Lee" alongside labeled forms
    assert "Lee" not in merged


def test_merge_is_idempotent_on_registry_only():
    a = merge_county_list_for_ui([])
    b = merge_county_list_for_ui(list(REGISTERED_COUNTIES))
    assert a == b == sorted(REGISTERED_COUNTIES)
    assert len(a) == len(set(a))


def test_normalize_county_ui_name():
    assert normalize_county_ui_name("Lee") == "Lee (FL)"
    assert normalize_county_ui_name("Lee (FL)") == "Lee (FL)"
    assert normalize_county_ui_name("Lee", "GA") == "Lee (GA)"
    assert county_label(*parse_registered_county("Sarasota (FL)")) == "Sarasota (FL)"
