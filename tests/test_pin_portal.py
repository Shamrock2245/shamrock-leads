"""
Tests for Mobile PIN Portal Router (paperwork.shamrockbailbonds.biz)
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from dashboard.main import app
from dashboard.routers.pin_portal import pin_portal_router

app.include_router(pin_portal_router)
client = TestClient(app)


def test_portal_ui_route():
    response = client.get("/api/portal/portal-ui", follow_redirects=True)
    assert response.status_code == 200
    assert "Shamrock Bail Bonds" in response.text
    assert "Mobile E-Sign Portal" in response.text


def test_done_landing_page():
    response = client.get("/api/portal/done", follow_redirects=True)
    assert response.status_code == 200
    assert "Paperwork Successfully Signed!" in response.text
    assert "Call Office" in response.text


def test_verify_admin_pin_bypass():
    payload = {"phone": "2395550199", "pin": "224545"}
    response = client.post("/api/portal/verify-pin", json=payload, follow_redirects=True)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["verified"] is True
    assert "PORTAL-ADMIN" in data["session_token"]


def test_send_pin_via_bluebubbles_mock():
    mock_pins_col = MagicMock()
    mock_pins_col.update_one = AsyncMock()

    with patch("dashboard.routers.pin_portal.get_collection", return_value=mock_pins_col), \
         patch("dashboard.services.bb_client.send_message_universal", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"success": True, "sent": True, "channel": "imessage"}
        
        payload = {"phone": "2395550199"}
        response = client.post("/api/portal/send-pin", json=payload, follow_redirects=True)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["channel"] == "imessage"
        assert mock_send.called


def test_no_twilio_imports_in_pin_portal():
    with open("dashboard/routers/pin_portal.py", "r") as f:
        content = f.read()
    assert "twilio_service" not in content
    assert "TwilioService" not in content
    assert "OutreachSequencer" not in content
