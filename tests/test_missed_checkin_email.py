"""Missed check-in email fallback: defendant-only, phone-missing, send-once."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashboard.routers.bonds import _maybe_send_overdue_checkin_email


NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def _bond(**overrides):
    base = {
        "booking_number": "LEE-99",
        "defendant_name": "John Defendant",
        "defendant_email": "john@example.com",
        "defendant_phone": "",
        "indemnitor_email": "jane@example.com",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_skips_indemnitor_email_when_defendant_email_missing():
    sent = await _maybe_send_overdue_checkin_email(
        _bond(defendant_email=""), NOW,
    )
    assert sent is False


@pytest.mark.asyncio
async def test_skips_when_defendant_phone_present():
    with patch("dashboard.services.client_portal_service.generate_portal_token", new_callable=AsyncMock) as tok:
        sent = await _maybe_send_overdue_checkin_email(
            _bond(defendant_phone="2395551234"), NOW,
        )
    assert sent is False
    tok.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_already_sent():
    sent = await _maybe_send_overdue_checkin_email(
        _bond(checkin_email_fallback_sent_at=NOW), NOW,
    )
    assert sent is False


@pytest.mark.asyncio
async def test_skips_when_token_generation_fails():
    with patch(
        "dashboard.services.client_portal_service.generate_portal_token",
        new_callable=AsyncMock,
        return_value={"success": False, "error": "Bond not found"},
    ), patch("dashboard.services.gmail_reader.GmailReaderService") as gmail_cls:
        sent = await _maybe_send_overdue_checkin_email(_bond(), NOW)
    assert sent is False
    gmail_cls.assert_not_called()


@pytest.mark.asyncio
async def test_sends_to_defendant_email_and_persists_sent_at():
    mock_gmail = MagicMock()
    mock_gmail.send_email.return_value = {"success": True}
    mock_bonds = MagicMock()
    mock_bonds.update_one = AsyncMock()

    with patch(
        "dashboard.services.client_portal_service.generate_portal_token",
        new_callable=AsyncMock,
        return_value={"success": True, "url": "https://leads.shamrockbailbonds.biz/c/abc"},
    ), patch("dashboard.services.gmail_reader.GmailReaderService", return_value=mock_gmail), \
         patch("dashboard.routers.bonds.get_collection", return_value=mock_bonds):
        sent = await _maybe_send_overdue_checkin_email(_bond(), NOW)

    assert sent is True
    mock_gmail.send_email.assert_called_once()
    assert mock_gmail.send_email.call_args.kwargs["to"] == "john@example.com"
    mock_bonds.update_one.assert_awaited_once()
    filt, update = mock_bonds.update_one.call_args[0]
    assert filt == {"booking_number": "LEE-99"}
    assert update["$set"]["checkin_email_fallback_sent_at"] == NOW
