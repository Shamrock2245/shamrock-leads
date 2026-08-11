"""
tests/test_automation_hub_enhancements.py
Unit tests for Automation Hub parameter tuning, master sweep trigger, and service status endpoints.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.automation_control import automation_control_bp

api_app = FastAPI()
api_app.include_router(automation_control_bp)

client = TestClient(api_app)


def test_automation_endpoints_require_auth():
    """Control plane must reject unauthenticated callers."""
    assert client.get("/api/automation/status").status_code == 401
    assert client.post("/api/automation/parameters", json={"key": "x", "params": {}}).status_code == 401
    assert client.post("/api/automation/trigger-all").status_code == 401
    assert client.post("/api/automation/config", json={}).status_code == 401


@patch("dashboard.routers.automation_control.get_automation_config")
@patch("dashboard.routers.automation_control.get_db")
def test_automation_status_endpoint(mock_get_db, mock_get_cfg):
    """Verify /api/automation/status returns all registered service keys with machine auth."""
    mock_get_cfg.return_value = {"type": "automation_master"}
    mock_db = MagicMock()
    log_col = MagicMock()
    log_col.find_one = AsyncMock(return_value=None)
    mock_db.__getitem__.return_value = log_col
    mock_get_db.return_value = mock_db

    with patch.dict("os.environ", {"GAS_API_KEY": "test-gas-key"}, clear=False):
        response = client.get(
            "/api/automation/status",
            headers={"X-API-Key": "test-gas-key"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "status" in data
    assert "service_count" in data
    assert data["service_count"] > 20


@patch("dashboard.routers.automation_control.get_db")
def test_automation_parameters_endpoint(mock_get_db):
    """Verify POST /api/automation/parameters updates config section when staff-session mocked."""
    mock_db = MagicMock()
    config_col = MagicMock()
    config_col.find_one = AsyncMock(return_value={"type": "automation_master"})
    config_col.update_one = AsyncMock(return_value=None)

    mock_db.__getitem__.return_value = config_col
    mock_get_db.return_value = mock_db

    payload = {
        "key": "speed_to_contact",
        "params": {"interval_seconds": 1200, "min_lead_score": 75}
    }
    with patch(
        "dashboard.routers.automation_control._staff_session_ok",
        return_value=True,
    ), patch(
        "dashboard.routers.automation_control._actor_label",
        return_value="test@shamrockbailbonds.biz",
    ):
        response = client.post("/api/automation/parameters", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["key"] == "speed_to_contact"
    assert data["updated_params"]["interval_seconds"] == 1200


def test_automation_trigger_all_endpoint():
    """Verify POST /api/automation/trigger-all sets trigger events with machine key."""
    with patch.dict("os.environ", {"GAS_API_KEY": "test-gas-key"}, clear=False):
        response = client.post(
            "/api/automation/trigger-all",
            headers={"X-API-Key": "test-gas-key"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "triggered_count" in data
    assert "triggered" in data


def test_automation_parameters_rejects_bad_mode():
    with patch(
        "dashboard.routers.automation_control._staff_session_ok",
        return_value=True,
    ):
        response = client.post(
            "/api/automation/parameters",
            json={"key": "speed_to_contact", "params": {"mode": "nuclear"}},
        )
    assert response.status_code == 400
