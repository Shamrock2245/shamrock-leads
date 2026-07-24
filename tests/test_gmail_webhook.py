"""
Unit tests for Gmail Pub/Sub push notification webhook & court email real-time scheduler.
"""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.webhooks import webhooks_bp
from dashboard.services.court_email_scheduler import CourtEmailScheduler


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(webhooks_bp)
    return app


def test_court_email_scheduler_process_single_message_dedup():
    mock_db = MagicMock()
    mock_log_col = MagicMock()
    mock_db.__getitem__.return_value = mock_log_col

    # Return existing document to simulate duplicate
    mock_log_col.find_one.return_value = {"_id": "123", "message_id": "msg_duplicate_123"}

    scheduler = CourtEmailScheduler(db=mock_db)
    result = scheduler.process_single_message("msg_duplicate_123")

    assert result["duplicate"] is True
    assert result["processed"] is False


from dashboard.services.court_email_processor import CourtEmailProcessor

@patch.object(CourtEmailProcessor, "process_email")
@patch("dashboard.services.gmail_reader.GmailReaderService")
@patch("dashboard.services.google_calendar_service.GoogleCalendarService")
@patch("dashboard.extensions.get_collection")
def test_court_email_scheduler_process_single_message_success(
    mock_get_col, mock_cal_class, mock_gmail_class, mock_process_email
):
    mock_process_email.return_value = {
        "event_type": "courtDate",
        "case_number": "2026-CF-00456",
        "defendant_name": "John Doe",
        "datetime_info": "2026-08-15 09:00 AM",
    }
    mock_db = MagicMock()
    mock_log_col = MagicMock()
    mock_db.__getitem__.return_value = mock_log_col
    mock_log_col.find_one.return_value = None  # Not duplicate

    # Mock Gmail reader
    mock_gmail_inst = MagicMock()
    mock_gmail_class.return_value = mock_gmail_inst
    mock_gmail_inst.is_configured = True
    mock_gmail_inst.get_message_details.return_value = {
        "message_id": "msg_999",
        "subject": "COURT NOTICE: Case 2026-CF-00456 - Hearing Date",
        "body": "Defendant: John Doe\nCase No: 2026-CF-00456\nCourt Date: 08/15/2026 09:00 AM\nLocation: Lee County Courthouse",
        "sender": "clerk@leecounty.gov",
        "received_at": "2026-07-24T12:00:00Z",
    }

    # Mock Calendar
    mock_cal_inst = MagicMock()
    mock_cal_class.return_value = mock_cal_inst
    mock_cal_inst.create_event.return_value = {"id": "event_123"}

    # Mock MongoDB collections
    mock_def_col = AsyncMock()
    mock_def_col.find_one.return_value = {
        "phone": "239-555-0100",
        "email": "john.doe@example.com",
    }
    mock_reminders_col = AsyncMock()

    def col_side_effect(name):
        if name == "defendants":
            return mock_def_col
        if name == "court_reminders":
            return mock_reminders_col
        col = AsyncMock()
        col.find_one.return_value = None
        return col

    mock_get_col.side_effect = col_side_effect

    scheduler = CourtEmailScheduler(db=mock_db)
    scheduler._log_collection = mock_log_col
    result = scheduler.process_single_message("msg_999")

    assert result["processed"] is True
    assert result["duplicate"] is False
    assert result["event_type"] == "courtDate"
    assert result["calendar_event"] is True
    assert mock_cal_inst.create_event.called


@patch("dashboard.routers.webhooks.get_collection")
@patch("dashboard.services.court_email_scheduler.CourtEmailScheduler.process_single_message")
def test_gmail_pubsub_webhook_endpoint(mock_process_single, mock_get_col, test_app):
    mock_audit_col = AsyncMock()
    mock_log_col = AsyncMock()

    def side_effect(name):
        if name == "audit_events":
            return mock_audit_col
        if name == "court_email_log":
            return mock_log_col
        return AsyncMock()

    mock_get_col.side_effect = side_effect
    mock_process_single.return_value = {
        "message_id": "msg_abc_123",
        "processed": True,
        "event_type": "courtDate",
    }

    client = TestClient(test_app)

    # Encode Pub/Sub message data
    pubsub_data = json.dumps({
        "emailAddress": "admin@shamrockbailbonds.biz",
        "historyId": "987654",
        "messageId": "msg_abc_123",
    })
    b64_data = base64.b64encode(pubsub_data.encode("utf-8")).decode("utf-8")

    payload = {
        "message": {
            "data": b64_data,
            "messageId": "msg_abc_123",
            "publishTime": "2026-07-24T12:00:00Z",
        },
        "subscription": "projects/shamrock-leads/subscriptions/gmail-push",
    }

    response = client.post("/api/webhooks/gmail", json=payload)
    assert response.status_code == 200
    res = response.json()
    assert res["success"] is True
    assert res["result"]["processed"] is True
    mock_process_single.assert_called_once_with("msg_abc_123")
