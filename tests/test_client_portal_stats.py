"""Regression tests for staff-side Client Portal dashboard metrics."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from dashboard.routers.client_portal import portal_stats


def test_portal_stats_returns_completed_checkins_from_the_last_seven_days():
    tokens = MagicMock()
    tokens.count_documents = AsyncMock(side_effect=[4, 11])
    checkins = MagicMock()
    checkins.count_documents = AsyncMock(return_value=7)

    def collection(name):
        return tokens if name == "portal_tokens" else checkins

    with patch("dashboard.routers.client_portal.get_collection", side_effect=collection):
        stats = asyncio.run(portal_stats())

    assert stats == {
        "active_tokens": 4,
        "checkins_7d": 7,
        "total_all_time": 11,
    }

    checkin_query = checkins.count_documents.await_args.args[0]
    assert checkin_query["status"] == "completed"
    assert "$gte" in checkin_query["checkin_at"]
