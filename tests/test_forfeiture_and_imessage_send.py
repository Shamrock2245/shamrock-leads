"""
Unit tests for forfeiture alert testing and iMessage sending error handling.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.legacy import legacy_bp
from dashboard.routers.discharge_monitor import discharge_monitor_bp

create_test_app = FastAPI()
create_test_app.include_router(legacy_bp)
create_test_app.include_router(discharge_monitor_bp)

client = TestClient(create_test_app)


@pytest.mark.asyncio
async def test_send_forfeiture_alerts_failure():
    """Verify send_forfeiture_alerts returns success=False and explicit error when BB fails."""
    from dashboard.services.forfeiture_alert_service import send_forfeiture_alerts

    with patch("dashboard.services.forfeiture_alert_service.get_forfeiture_alert_phones", AsyncMock(return_value=["+12397849365"])):
        with patch("dashboard.services.bb_client.send_imessage", AsyncMock(return_value={"success": False, "error": "BlueBubbles host unreachable (404)"})):
            res = await send_forfeiture_alerts(
                defendant_name="TEST DEFENDANT",
                county="Lee",
                case_number="TEST-123",
                bond_amount=5000,
            )
            assert res["success"] is False
            assert res["sent"] == 0
            assert res["total_phones"] == 1
            assert "error" in res
            assert "BlueBubbles host unreachable" in res["error"]


def test_imessage_send_handles_non_json_response():
    """Verify /api/imessage/send handles non-JSON / HTML error pages from BlueBubbles cleanly without crashing with 500 JSONDecodeError."""
    fake_srv = {"url": "https://invalid-tunnel.dev", "password": "pass", "label": "Office", "email": "test@test.com"}
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(return_value=None)
    mock_col.insert_one = AsyncMock(return_value=MagicMock())

    with patch("dashboard.routers.legacy.get_collection", return_value=mock_col):
        with patch("dashboard.routers.legacy.BB_SERVERS", {"2399550178": fake_srv}):
            with patch("dashboard.routers.legacy.get_bb_server", return_value=fake_srv):
                with patch("dashboard.routers.bb_private_api.BlueBubblesClient.send_text", AsyncMock(return_value={
                    "success": False,
                    "status_code": 404,
                    "error": "non_json_response",
                    "message": "Non-JSON response (404) — check BlueBubbles server URL/tunnel status",
                })):
                    response = client.post(
                        "/api/imessage/send",
                        json={"phone": "2397849365", "message": "Test message"},
                    )
                    assert response.status_code == 502
                    data = response.json()
                    assert data["success"] is False
                    assert "Non-JSON response" in data["error"] or "unreachable" in data["error"]


def test_discharge_monitor_test_forfeiture_returns_502_on_failure():
    """Verify /api/discharge-monitor/test-forfeiture returns non-200 status when alerts fail to deliver."""
    with patch("dashboard.auth.pin_middleware.session_is_god_admin", return_value=True):
        with patch("dashboard.services.forfeiture_alert_service.send_forfeiture_alerts", AsyncMock(return_value={
            "success": False,
            "sent": 0,
            "total_phones": 3,
            "errors": [{"phone": "9365", "error": "no_bb_server"}],
            "error": "Forfeiture alert could not be delivered (0/3 sent). Cause: no_bb_server",
        })):
            response = client.post("/api/discharge-monitor/test-forfeiture", json={})
            assert response.status_code == 502
            data = response.json()
            assert data["success"] is False
            assert "Forfeiture alert could not be delivered" in data["error"]
