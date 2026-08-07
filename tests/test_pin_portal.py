"""
Tests for Mobile PIN Portal Router (paperwork.shamrockbailbonds.biz)
"""
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from dashboard.main import app
from dashboard.routers.pin_portal import pin_portal_router, _extract_signing_link_from_packet
from dashboard.services.bb_client import normalize_bb_send_result, bb_send_accepted

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
    mock_packets = MagicMock()
    mock_packets.find_one = AsyncMock(return_value={
        "signing_link": "https://sign.shamrockbailbonds.biz/s/abc123",
        "created_at": "2026-08-07",
    })
    with patch("dashboard.routers.pin_portal.get_collection", return_value=mock_packets):
        payload = {"phone": "2395550199", "pin": "224545"}
        response = client.post("/api/portal/verify-pin", json=payload, follow_redirects=True)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["verified"] is True
    assert "PORTAL-ADMIN" in data["session_token"]
    assert data["signing_link"] == "https://sign.shamrockbailbonds.biz/s/abc123"


def test_send_pin_via_bluebubbles_mock():
    mock_pins_col = MagicMock()
    mock_pins_col.update_one = AsyncMock()

    with patch("dashboard.routers.pin_portal.get_collection", return_value=mock_pins_col), \
         patch("dashboard.services.bb_client.send_message_universal", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"success": True, "sent": True, "queued": False, "channel": "imessage"}

        payload = {"phone": "2395550199"}
        response = client.post("/api/portal/send-pin", json=payload, follow_redirects=True)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["channel"] == "imessage"
        assert mock_send.called


def test_send_pin_accepts_queued_bb_shape():
    """Regression: live BB returned status=queued without sent/queued booleans → false 503."""
    mock_pins_col = MagicMock()
    mock_pins_col.update_one = AsyncMock()

    with patch("dashboard.routers.pin_portal.get_collection", return_value=mock_pins_col), \
         patch("dashboard.services.bb_client.send_message_universal", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {
            "success": True,
            "status": "queued",
            "channel": "queued",
            "queued_id": "abc",
            "error": None,
        }
        response = client.post(
            "/api/portal/send-pin",
            json={"phone": "2395550100"},
            follow_redirects=True,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["success"] is True
        assert data["queued"] is True
        assert data["channel"] == "queued"


def test_normalize_bb_send_result_shapes():
    n = normalize_bb_send_result({
        "success": True, "status": "queued", "channel": "queued", "error": None,
    })
    assert n["queued"] is True
    assert n["sent"] is False
    assert bb_send_accepted(n) is True

    n2 = normalize_bb_send_result({"success": True, "channel": "imessage"})
    assert n2["sent"] is True
    assert bb_send_accepted(n2) is True

    n3 = normalize_bb_send_result({"success": False, "error": "boom"})
    assert bb_send_accepted(n3) is False


def test_extract_signing_link_from_submitters():
    link = _extract_signing_link_from_packet({
        "docuseal_submitters": [{"embed_src": "https://sign.example/s/xyz"}],
    })
    assert link == "https://sign.example/s/xyz"


def test_no_twilio_imports_in_pin_portal():
    with open("dashboard/routers/pin_portal.py", "r") as f:
        content = f.read()
    assert "twilio_service" not in content
    assert "TwilioService" not in content
    assert "OutreachSequencer" not in content
    assert "send_text" not in content
