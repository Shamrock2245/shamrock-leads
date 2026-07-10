"""Unit tests for transparent check-in enrollment (Tracks A + C)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashboard.services.checkin_enrollment_service import (
    CONSENT_VERSION,
    CONDITION_SUMMARY,
    get_condition_language,
)


def test_condition_language_has_transparent_terms():
    lang = get_condition_language()
    assert lang["consent_version"] == CONSENT_VERSION
    full = lang["full_clause"].lower()
    assert "check-in" in full or "check in" in full
    assert "voluntary" in full
    assert "permission" in full
    assert "not sold" in full
    assert "traccar" in full
    assert lang["gps_engine"] == "traccar"
    assert CONDITION_SUMMARY
    assert "{url}" in lang["message_template"]


def test_booking_to_unique_id():
    from dashboard.services.traccar_client import booking_to_unique_id
    assert booking_to_unique_id("lee-12345") == "shamrock-LEE-12345"
    assert booking_to_unique_id("a/b#c") == "shamrock-A-B-C"


@pytest.mark.asyncio
async def test_submit_portal_checkin_requires_consent():
    from dashboard.services.client_portal_service import submit_portal_checkin

    result = await submit_portal_checkin(
        booking_number="TEST-BOOK-1",
        lat=26.1,
        lng=-81.8,
        accuracy=10.0,
        consent=False,
    )
    assert result["success"] is False
    assert "consent" in result["error"].lower()


@pytest.mark.asyncio
async def test_enable_checkin_sets_flags():
    from dashboard.services import checkin_enrollment_service as svc

    mock_bonds = MagicMock()
    mock_bonds.find_one = AsyncMock(return_value={
        "booking_number": "BK-99",
        "defendant_name": "Test Defendant",
        "status": "active",
    })
    mock_bonds.update_one = AsyncMock()

    mock_tokens = MagicMock()
    mock_tokens.insert_one = AsyncMock()

    mock_audit = MagicMock()
    mock_audit.insert_one = AsyncMock()

    def get_col(name):
        if name == "active_bonds":
            return mock_bonds
        if name == "portal_tokens":
            return mock_tokens
        if name == "audit_events":
            return mock_audit
        return MagicMock()

    with patch.object(svc, "get_collection", side_effect=get_col), \
         patch(
             "dashboard.services.client_portal_service.generate_portal_token",
             new_callable=AsyncMock,
             return_value={
                 "success": True,
                 "url": "https://leads.shamrockbailbonds.biz/c/abc",
                 "token": "abc",
             },
         ), \
         patch(
             "dashboard.services.task_engine.TaskEngine.create_task",
             new_callable=AsyncMock,
             return_value="task123",
         ):
        result = await svc.enable_checkin_monitoring(
            "BK-99",
            source="test",
            actor="pytest",
            create_staff_task=True,
        )

    assert result["success"] is True
    assert result["check_in_required"] is True
    assert result["portal_url"]
    assert result["task_id"] == "task123"
    assert mock_bonds.update_one.await_count >= 1
