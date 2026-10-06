"""The count on a state graphic and the defendant list behind it share one definition."""

from datetime import datetime, timezone

from dashboard.extensions import ACTIVE_STATE_CODES
from dashboard.services.intel_population import (
    PRESETS,
    canonical_state,
    parse_money,
    population_stages,
    resolve_preset,
    state_clause,
)


def test_every_active_state_folds_and_florida_owns_blank_rows():
    assert canonical_state(None) == "FL"
    assert canonical_state("") == "FL"
    assert canonical_state("Florida") == "FL"
    assert canonical_state("Georgia") == "GA"
    assert canonical_state("OH") == "OH"
    assert set(ACTIVE_STATE_CODES) >= {"FL", "GA", "SC", "NC", "TN", "TX", "LA", "AL", "CT", "MS", "OH"}
    for code in ACTIVE_STATE_CODES:
        assert canonical_state(code) == code
    # Only Florida's clause claims legacy rows that never stored a state.
    assert "$exists" in str(state_clause("FL"))
    assert "$exists" not in str(state_clause("TX"))


def test_money_strings_parse_to_the_same_number_as_ints():
    assert parse_money("$1,250.00") == 1250
    assert parse_money(1250) == 1250
    assert parse_money("nope") == 0


def test_graphic_presets_build_the_same_filters_the_list_uses():
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    day = population_stages(resolve_preset("24h"), state="FL", now=now)
    assert day[0]["$match"]["$and"][0]["$or"][0]["state"]["$in"]
    assert any("scraped_at" in clause or "$or" in clause for clause in day[0]["$match"]["$and"])
    assert "_safe_bond" in day[1]["$addFields"]

    ready = resolve_preset("bond_ready")
    ready_stages = population_stages(ready, now=now)
    assert ready["min_bond"] == 1000
    assert ready["min_score"] == 40
    assert ready["custody"] is True
    assert ready_stages[-1]["$match"]["_safe_bond"]["$gte"] == 1000
    assert "custody" in ready_stages[-1]["$match"]["_custody_str"]["$regex"]

    writable = population_stages(resolve_preset("writable", days=7), state="TN", now=now)
    assert writable[-1]["$match"]["_safe_bond"]["$gte"] == 0.01
    hot = resolve_preset("hot", days=30)
    assert hot["min_score"] == 70
    assert hot["days"] == 30
    assert "writable" in PRESETS
