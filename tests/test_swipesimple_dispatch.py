"""
Tests for SwipeSimple payment link text (BlueBubbles) & email (Gmail) dispatch.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.paperwork import paperwork_bp


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(paperwork_bp)
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


def test_swipesimple_dispatch_basic(client):
    mock_pkts = MagicMock()
    mock_pkts.find_one = AsyncMock(return_value=None)
    mock_pkts.update_one = AsyncMock()

    mock_disp = MagicMock()
    mock_disp.insert_one = AsyncMock()

    def get_col_mock(name):
        if name == "paperwork_packets":
            return mock_pkts
        if name == "payment_dispatches":
            return mock_disp
        mock_generic = MagicMock()
        mock_generic.find_one = AsyncMock(return_value=None)
        mock_generic.insert_one = AsyncMock()
        return mock_generic

    with patch("dashboard.routers.paperwork.get_collection", side_effect=get_col_mock), \
         patch("dashboard.routers.paperwork.send_message_universal", new_callable=AsyncMock) as mock_bb_send, \
         patch("dashboard.services.gmail_reader.GmailReaderService.send_email") as mock_send_email, \
         patch("dashboard.services.gmail_reader.GmailReaderService.is_configured", True):

        mock_bb_send.return_value = {
            "success": True,
            "sent": True,
            "queued": False,
            "channel": "imessage",
        }
        mock_send_email.return_value = {"success": True, "id": "msg_123", "error": None}

        payload = {
            "packet_id": "PKT-TEST-100",
            "booking_number": "LEE-2026-00999",
            "phone": "2395550199",
            "email": "test@example.com",
            "amount": 1500.0,
            "defendant_name": "Jane Doe",
            "deliver_text": True,
            "deliver_email": True,
        }

        res = client.post("/api/paperwork/payment/swipesimple-link", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["amount"] == 1500.0
        assert data["text_delivered"] is True
        assert data["email_delivered"] is True
        assert "swipesimple.com" in data["payment_link"]

        # Verify BlueBubbles message content (send_message_universal path)
        mock_bb_send.assert_called_once()
        bb_args = mock_bb_send.call_args
        assert "2395550199" in str(bb_args[0][0])
        assert "$1,500.00" in bb_args[0][1]
        assert "Jane Doe" in bb_args[0][1]

        # Verify Gmail email content
        mock_send_email.assert_called_once()
        email_kwargs = mock_send_email.call_args.kwargs
        assert email_kwargs["to"] == "test@example.com"
        assert "$1,500.00" in email_kwargs["subject"]
        assert "$1,500.00" in email_kwargs["body_html"]
        assert "Jane Doe" in email_kwargs["body_html"]


def test_swipesimple_dispatch_context_fallback(client):
    mock_packet = {
        "packet_id": "PKT-CTX-200",
        "booking_number": "COL-2026-00444",
        "indemnitor_phone": "2395550200",
        "indemnitor_email": "indemnitor@example.com",
        "defendant_name": "Bob Smith",
        "premium_amount": 750.0,
    }

    mock_pkts = MagicMock()
    mock_pkts.find_one = AsyncMock(return_value=mock_packet)
    mock_pkts.update_one = AsyncMock()

    mock_disp = MagicMock()
    mock_disp.insert_one = AsyncMock()

    def get_col_mock(name):
        if name == "paperwork_packets":
            return mock_pkts
        if name == "payment_dispatches":
            return mock_disp
        mock_generic = MagicMock()
        mock_generic.find_one = AsyncMock(return_value=None)
        mock_generic.insert_one = AsyncMock()
        return mock_generic

    with patch("dashboard.routers.paperwork.get_collection", side_effect=get_col_mock), \
         patch("dashboard.routers.paperwork.send_message_universal", new_callable=AsyncMock) as mock_bb_send, \
         patch("dashboard.services.gmail_reader.GmailReaderService.send_email") as mock_send_email, \
         patch("dashboard.services.gmail_reader.GmailReaderService.is_configured", True):

        mock_bb_send.return_value = {
            "success": True,
            "sent": False,
            "queued": True,
            "channel": "queued",
        }
        mock_send_email.return_value = {"success": True, "id": "msg_456"}

        # Omitting phone, email, amount, defendant_name — relying on DB lookup
        payload = {"packet_id": "PKT-CTX-200"}

        res = client.post("/api/paperwork/payment/swipesimple-link", json=payload)
        assert res.status_code == 200
        data = res.json()

        assert data["success"] is True
        assert data["amount"] == 750.0
        assert data["recipient_phone"] == "2395550200"
        assert data["recipient_email"] == "indemnitor@example.com"
        assert data["defendant_name"] == "Bob Smith"
        # Queued BB is still accepted (text_delivered True via bb_send_accepted)
        assert data["text_delivered"] is True
        assert data["text_queued"] is True
        assert data["email_delivered"] is True


def test_swipesimple_link_no_deliver(client):
    """deliver=False should return link without sending."""
    mock_pkts = MagicMock()
    mock_pkts.find_one = AsyncMock(return_value=None)
    mock_pkts.update_one = AsyncMock()
    mock_disp = MagicMock()
    mock_disp.insert_one = AsyncMock()

    def get_col_mock(name):
        if name == "paperwork_packets":
            return mock_pkts
        if name == "payment_dispatches":
            return mock_disp
        mock_generic = MagicMock()
        mock_generic.find_one = AsyncMock(return_value=None)
        mock_generic.insert_one = AsyncMock()
        return mock_generic

    with patch("dashboard.routers.paperwork.get_collection", side_effect=get_col_mock), \
         patch("dashboard.routers.paperwork.send_message_universal", new_callable=AsyncMock) as mock_bb_send, \
         patch("dashboard.services.gmail_reader.GmailReaderService.send_email") as mock_send_email:

        payload = {
            "packet_id": "pkt_3003",
            "amount": 750.00,
            "phone": "239-555-0199",
            "deliver": False,
        }
        res = client.post("/api/paperwork/payment/swipesimple-link", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["amount"] == 750.00
        assert data["text_delivered"] is False
        assert data["email_delivered"] is False
        mock_bb_send.assert_not_called()
        mock_send_email.assert_not_called()
