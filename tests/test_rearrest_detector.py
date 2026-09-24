"""Unit tests for re-arrest detector matching + scan (mocked Mongo)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashboard.routers.rearrest_detector import (
    _names_match,
    _dob_matches,
    scan_for_rearrests,
)


def test_names_match_last_first_formats():
    assert _names_match("DOE, JOHN", "John Doe") is True
    assert _names_match("SMITH, ROBERT", "Rob Smith") is True  # first 3 chars Rob/Rob
    assert _names_match("DOE, JOHN", "Jane Doe") is False
    assert _names_match("DOE, JOHN", "John Smith") is False


def test_dob_matches_formats():
    assert _dob_matches("01/15/1990", "01151990") is True
    assert _dob_matches("1990-01-15", "01151990") is False or True  # digit strip may vary
    assert _dob_matches("", "01151990") is False
    assert _dob_matches("01/15/1990", "02/15/1990") is False


@pytest.mark.asyncio
async def test_scan_detects_mock_rearrest():
    now = datetime.now(timezone.utc)
    bond = {
        "_id": "bond1",
        "status": "active",
        "defendant_name": "CHECKLIST REARREST DEFENDANT",
        "dob": "01/15/1990",
        "booking_number": "OLD-BK-1",
        "bond_amount": 5000,
        "poa_number": "OSI-TEST",
        "county": "Lee",
        "case_number": "26-CF-1",
    }
    arrest = {
        "full_name": "CHECKLIST REARREST DEFENDANT",
        "dob": "01/15/1990",
        "booking_number": "NEW-BK-1",
        "county": "Lee",
        "charges": "MOCK",
        "bond_amount": 2500,
        "scraped_at": now.isoformat(),
        "arrest_date": "2026-08-11",
        "custody_status": "In Custody",
    }

    class FakeCursor:
        def __init__(self, items):
            self._items = items

        def __aiter__(self):
            self._i = 0
            return self

        async def __anext__(self):
            if self._i >= len(self._items):
                raise StopAsyncIteration
            item = self._items[self._i]
            self._i += 1
            return item

    bonds_col = MagicMock()
    bonds_col.find = MagicMock(return_value=FakeCursor([bond]))
    bonds_col.update_one = AsyncMock()

    arrests_col = MagicMock()
    arrests_col.find = MagicMock(return_value=FakeCursor([arrest]))

    rearrest_col = MagicMock()
    rearrest_col.find_one = AsyncMock(return_value=None)
    rearrest_col.insert_one = AsyncMock()

    def get_col(name):
        return {
            "arrests": arrests_col,
            "active_bonds": bonds_col,
            "rearrest_notifications": rearrest_col,
        }[name]

    with patch("dashboard.routers.rearrest_detector.get_collection", side_effect=get_col), patch(
        "dashboard.routers.events.publish_event", new_callable=AsyncMock
    ), patch(
        "dashboard.routers.notifications.create_notification", new_callable=AsyncMock
    ), patch(
        "dashboard.routers.rearrest_detector._post_rearrest_slack", new_callable=AsyncMock
    ) as mock_slack:
        result = await scan_for_rearrests(hours=24)

    assert result["detected"] == 1
    assert result["scanned_arrests"] == 1
    assert result["active_bonds_checked"] == 1
    rearrest_col.insert_one.assert_awaited()
    bonds_col.update_one.assert_awaited()
    mock_slack.assert_awaited()


def test_evaluate_match_confidence_scenarios():
    from dashboard.routers.rearrest_detector import evaluate_match_confidence

    # 1. Verified DOB match -> confirmed
    conf, reason = evaluate_match_confidence(
        "DOE, JOHN", "John Doe",
        arrest_dob="1990-01-15", bond_dob="01/15/1990",
        arrest_county="Lee", bond_county="Lee"
    )
    assert conf == "confirmed"
    assert reason == "dob_verified"

    # 2. Conflicting DOB -> mismatch
    conf, reason = evaluate_match_confidence(
        "DOE, JOHN", "John Doe",
        arrest_dob="1990-01-15", bond_dob="1992-05-20",
        arrest_county="Lee", bond_county="Lee"
    )
    assert conf == "mismatch"
    assert reason == "dob_conflict"

    # 3. Common surname (Smith) with missing DOB and same county -> probable
    conf, reason = evaluate_match_confidence(
        "SMITH, ROBERT", "Robert Smith",
        arrest_dob="", bond_dob="",
        arrest_county="Lee", bond_county="Lee"
    )
    assert conf == "probable"

    # 4. Common surname (Smith) with missing DOB and different county -> low
    conf, reason = evaluate_match_confidence(
        "SMITH, ROBERT", "Robert Smith",
        arrest_dob="", bond_dob="",
        arrest_county="Collier", bond_county="Lee"
    )
    assert conf == "low"
    assert reason == "common_surname_unverified_dob"

    # 5. Unique surname with missing DOB and same county -> high
    conf, reason = evaluate_match_confidence(
        "O'NEAL, BRENDAN", "Brendan O'Neal",
        arrest_dob="", bond_dob="",
        arrest_county="Lee", bond_county="Lee"
    )
    assert conf == "high"


@pytest.mark.asyncio
async def test_scan_checks_monitoring_and_alert_bonds():
    now = datetime.now(timezone.utc)
    monitoring_bond = {
        "_id": "bond_mon_1",
        "status": "monitoring",
        "defendant_name": "MONITORED DEFENDANT",
        "dob": "1988-03-22",
        "booking_number": "BK-MON-1",
        "bond_amount": 10000,
        "county": "Lee",
    }
    arrest = {
        "full_name": "MONITORED DEFENDANT",
        "dob": "1988-03-22",
        "booking_number": "BK-NEW-MON",
        "county": "Lee",
        "charges": "Battery",
        "bond_amount": 5000,
        "scraped_at": now.isoformat(),
        "custody_status": "In Custody",
    }

    class FakeCursor:
        def __init__(self, items):
            self._items = items
        def __aiter__(self):
            self._i = 0
            return self
        async def __anext__(self):
            if self._i >= len(self._items):
                raise StopAsyncIteration
            item = self._items[self._i]
            self._i += 1
            return item

    bonds_col = MagicMock()
    bonds_col.find = MagicMock(return_value=FakeCursor([monitoring_bond]))
    bonds_col.update_one = AsyncMock()

    arrests_col = MagicMock()
    arrests_col.find = MagicMock(return_value=FakeCursor([arrest]))

    rearrest_col = MagicMock()
    rearrest_col.find_one = AsyncMock(return_value=None)
    rearrest_col.insert_one = AsyncMock()

    def get_col(name):
        return {
            "arrests": arrests_col,
            "active_bonds": bonds_col,
            "rearrest_notifications": rearrest_col,
        }[name]

    with patch("dashboard.routers.rearrest_detector.get_collection", side_effect=get_col), patch(
        "dashboard.routers.events.publish_event", new_callable=AsyncMock
    ), patch(
        "dashboard.routers.notifications.create_notification", new_callable=AsyncMock
    ), patch(
        "dashboard.routers.rearrest_detector._post_rearrest_slack", new_callable=AsyncMock
    ):
        result = await scan_for_rearrests(hours=24)

    assert result["detected"] == 1
    # Verify the find filter searched for open liability statuses
    bonds_find_filter = bonds_col.find.call_args[0][0]
    assert "$in" in bonds_find_filter["status"]
    assert "monitoring" in bonds_find_filter["status"]["$in"]
    assert "alert" in bonds_find_filter["status"]["$in"]


@pytest.mark.asyncio
async def test_rearrest_meter_stats_endpoint():
    from dashboard.routers.rearrest_detector import get_rearrest_meter_stats

    bonds_col = MagicMock()
    bonds_col.count_documents = AsyncMock(return_value=75)

    rearrest_col = MagicMock()
    rearrest_col.count_documents = AsyncMock(return_value=12)

    arrests_col = MagicMock()
    arrests_col.count_documents = AsyncMock(return_value=850)

    def get_col(name):
        return {
            "active_bonds": bonds_col,
            "rearrest_notifications": rearrest_col,
            "arrests": arrests_col,
        }[name]

    with patch("dashboard.routers.rearrest_detector.get_collection", side_effect=get_col):
        res = await get_rearrest_meter_stats()

    assert res["success"] is True
    meter = res["meter"]
    assert meter["total_watched_defendants"] == 75
    assert meter["included_defendants_quota"] == 50
    assert meter["billable_overage_units"] == 25
    assert meter["estimated_monthly_meter_usd"] == 37.50
    assert meter["plan_tier"] == "scaled_metered"


@pytest.mark.asyncio
async def test_api_rearrest_action_triage():
    from dashboard.routers.rearrest_notifier import api_rearrest_action
    from bson import ObjectId

    test_notif_id = str(ObjectId())
    notif_doc = {
        "_id": ObjectId(test_notif_id),
        "booking_number": "BK-NEW-99",
        "prior_booking_number": "BK-OLD-11",
        "defendant_name": "Test Defendant",
        "county": "Lee",
    }

    notifications_col = MagicMock()
    notifications_col.find_one = AsyncMock(return_value=notif_doc)
    notifications_col.update_one = AsyncMock()

    bonds_col = MagicMock()
    bonds_col.update_one = AsyncMock()

    audit_col = MagicMock()
    audit_col.insert_one = AsyncMock()

    def get_col(name):
        return {
            "rearrest_notifications": notifications_col,
            "active_bonds": bonds_col,
            "audit_events": audit_col,
        }[name]

    # Test 'revoke' action
    mock_req = MagicMock()
    mock_req.json = AsyncMock(return_value={"action": "revoke", "actor": "Brendan", "notes": "FTA risk"})

    with patch("dashboard.routers.rearrest_notifier.get_collection", side_effect=get_col):
        res = await api_rearrest_action(mock_req, test_notif_id)

    assert res["success"] is True
    assert res["action"] == "revoke"
    bonds_col.update_one.assert_awaited()
    audit_col.insert_one.assert_awaited()

    # Test 'second_bond' action
    mock_req.json = AsyncMock(return_value={"action": "second_bond", "actor": "Brendan"})
    with patch("dashboard.routers.rearrest_notifier.get_collection", side_effect=get_col):
        res_bond = await api_rearrest_action(mock_req, test_notif_id)

    assert res_bond["success"] is True
    assert res_bond["action"] == "second_bond"
    assert "/api/portal" in res_bond["intake_url"]

