import pytest
from unittest.mock import AsyncMock, MagicMock
from config.write_counties import (
    WRITE_ELIGIBLE_COUNTIES,
    WATCH_ALSO,
    fa_watch_counties,
    is_write_eligible,
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
    doc = {"booking_number": "12345", "county": "Palm Beach", "bond_amount": 5000}
    serialized = serialize_doc(doc)
    assert serialized["write_eligible"] is True

    doc2 = {"booking_number": "67890", "county": "Orange", "bond_amount": 5000}
    serialized2 = serialize_doc(doc2)
    assert serialized2["write_eligible"] is False
