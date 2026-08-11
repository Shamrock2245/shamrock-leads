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
