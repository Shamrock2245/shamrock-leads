import pytest
from unittest.mock import AsyncMock, MagicMock
from config.write_counties import (
    WRITE_ELIGIBLE_COUNTIES,
    WATCH_ALSO,
    fa_query_county_values,
    fa_watch_counties,
    is_write_eligible,
    resolve_fa_watch_counties,
)
from dashboard.services.automation_config import (
    DEFAULT_CONFIG,
    get_automation_config,
)
from dashboard.routers.helpers import serialize_doc


def test_write_counties_contract():
    expected_write = ["Lee", "Charlotte", "Collier", "Sarasota", "Manatee", "Palm Beach"]
    assert WRITE_ELIGIBLE_COUNTIES == expected_write
    assert "Hendry" in WATCH_ALSO
    assert "DeSoto" in WATCH_ALSO

    fa_list = fa_watch_counties()
    assert "Palm Beach" in fa_list
    assert len(fa_list) == 8
    for c in expected_write:
        assert c in fa_list
    assert "Hendry" in fa_list
    assert "DeSoto" in fa_list


def test_is_write_eligible():
    assert is_write_eligible("Palm Beach") is True
    assert is_write_eligible("palm beach") is True
    assert is_write_eligible("Palm Beach (FL)") is True
    assert is_write_eligible("Lee") is True
    assert is_write_eligible("Lee (FL)") is True
    assert is_write_eligible("Charlotte County") is True
    assert is_write_eligible("Collier") is True
    assert is_write_eligible("Sarasota") is True
    assert is_write_eligible("Manatee") is True

    # Scrape-only or watch-only counties
    assert is_write_eligible("Hendry") is False
    assert is_write_eligible("DeSoto") is False
    assert is_write_eligible("Orange") is False
    assert is_write_eligible("Broward") is False
    assert is_write_eligible("") is False
    assert is_write_eligible(None) is False
    assert is_write_eligible("Lee", state="GA") is False


def test_is_write_eligible_fail_closed_identity():
    assert is_write_eligible("Lee (GA)") is False
    assert is_write_eligible("Lee (AL)") is False
    assert is_write_eligible("Lee (SC)") is False
    assert is_write_eligible("Lee", state=None) is False
    assert is_write_eligible("Lee", state="") is False
    assert is_write_eligible("Lee (FL)", state="GA") is False
    assert is_write_eligible("Lee (FL)", state="FL") is True
    assert is_write_eligible("Lee", state="FL") is True


def test_resolve_fa_watch_counties_default_is_write_book():
    result = resolve_fa_watch_counties(env_watch_counties="", stored_targets=None)
    assert result == fa_watch_counties()
    assert "Palm Beach" in result
    assert len(result) == 8


def test_resolve_fa_watch_counties_unions_stored():
    old = ["Lee", "Collier", "Charlotte", "Sarasota", "Manatee", "Hendry", "DeSoto"]
    result = resolve_fa_watch_counties(env_watch_counties="", stored_targets=old)
    assert "Palm Beach" in result
    assert len(result) == 8
    for c in old:
        assert c in result


def test_resolve_fa_watch_counties_env_override():
    assert resolve_fa_watch_counties(env_watch_counties="Lee,Orange") == ["Lee", "Orange"]
    assert resolve_fa_watch_counties(env_watch_counties="*") is None


def test_fa_query_county_values_includes_label_variants():
    values = fa_query_county_values(["Palm Beach", "Lee"])
    assert "Palm Beach" in values
    assert "Palm Beach (FL)" in values
    assert "Lee" in values
    assert "Lee (FL)" in values


def test_default_config_fa_target_counties():
    target = DEFAULT_CONFIG["first_appearance_watcher"]["target_counties"]
    assert "Palm Beach" in target
    assert "Lee" in target
    assert len(target) == 8


@pytest.mark.asyncio
async def test_get_automation_config_union_logic():
    # Simulate an existing Mongo document with the old 7-county list (no Palm Beach)
    old_counties = ["Lee", "Collier", "Charlotte", "Sarasota", "Manatee", "Hendry", "DeSoto"]
    stored_doc = {
        "type": "automation_master",
        "_revenue_automations_v1": "2026-01-01T00:00:00Z",
        "_lifecycle_automations_v1": "2026-01-01T00:00:00Z",
        "first_appearance_watcher": {
            "enabled": True,
            "interval_seconds": 1800,
            "slack_digest": True,
            "target_counties": old_counties,
        },
    }

    mock_coll = MagicMock()
    mock_coll.find_one = AsyncMock(return_value=dict(stored_doc))
    mock_coll.update_one = AsyncMock()

    mock_db = {"automation_config": mock_coll}

    cfg = await get_automation_config(mock_db)

    # Verify Palm Beach was unioned in
    updated_targets = cfg["first_appearance_watcher"]["target_counties"]
    assert "Palm Beach" in updated_targets
    assert len(updated_targets) == 8

    # Verify update_one was called to persist the unioned list back to Mongo
    assert mock_coll.update_one.called
    update_call = mock_coll.update_one.call_args
    assert update_call[0][0] == {"type": "automation_master"}
    assert "first_appearance_watcher.target_counties" in update_call[0][1]["$set"]
    assert "Palm Beach" in update_call[0][1]["$set"]["first_appearance_watcher.target_counties"]


def test_serialize_doc_write_eligible():
    doc = {"booking_number": "12345", "county": "Palm Beach", "state": "FL", "bond_amount": 5000}
    serialized = serialize_doc(doc)
    assert serialized["write_eligible"] is True

    doc2 = {"booking_number": "67890", "county": "Orange", "state": "FL", "bond_amount": 5000}
    serialized2 = serialize_doc(doc2)
    assert serialized2["write_eligible"] is False

    ga_labeled = serialize_doc({"county": "Lee (GA)", "bond_amount": 5000})
    assert ga_labeled["write_eligible"] is False

    ga_state = serialize_doc({"county": "Lee", "state": "GA", "bond_amount": 5000})
    assert ga_state["write_eligible"] is False

    missing_state = serialize_doc({"county": "Palm Beach", "bond_amount": 5000})
    assert missing_state["write_eligible"] is False


def test_watcher_query_uses_fa_watch_counties(monkeypatch):
    monkeypatch.delenv("WATCH_COUNTIES", raising=False)
    from core.first_appearance_watcher import FirstAppearanceWatcher

    captured = {}

    class _Cursor:
        def sort(self, *args, **kwargs):
            return self

        def limit(self, n):
            return []

    class _Coll:
        def find(self, query):
            captured["query"] = query
            return _Cursor()

    watcher = FirstAppearanceWatcher.__new__(FirstAppearanceWatcher)
    watcher._arrests = _Coll()
    watcher._db = None

    watcher._query_candidates()

    query = captured["query"]
    assert "county" in query
    values = query["county"]["$in"]
    assert "Palm Beach" in values
    assert "Palm Beach (FL)" in values
    assert "Lee" in values
    assert "Hendry" in values
    assert "DeSoto" in values
    assert len(resolve_fa_watch_counties(env_watch_counties="", stored_targets=None)) == 8


def test_watcher_skips_pbso_blotter_index():
    from core.first_appearance_watcher import FirstAppearanceWatcher

    watcher = FirstAppearanceWatcher.__new__(FirstAppearanceWatcher)
    watcher._scrapers = {}
    result = watcher._refetch_record({
        "county": "Palm Beach",
        "detail_url": "https://www3.pbso.org/blotter/index.cfm",
        "booking_number": "123",
    })
    assert result is None
