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

@patch("dashboard.routers.automation_control.get_automation_config")
@patch("dashboard.routers.automation_control.get_db")
def test_automation_status_endpoint(mock_get_db, mock_get_cfg):
    """Verify /api/automation/status returns all registered service keys."""
    mock_get_cfg.return_value = {"type": "automation_master"}
    mock_db = MagicMock()
    log_col = MagicMock()
    log_col.find_one = AsyncMock(return_value=None)
    mock_db.__getitem__.return_value = log_col
    mock_get_db.return_value = mock_db

    response = client.get("/api/automation/status")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "status" in data
    assert "service_count" in data
    assert data["service_count"] > 20

@patch("dashboard.routers.automation_control.get_db")
def test_automation_parameters_endpoint(mock_get_db):
    """Verify POST /api/automation/parameters updates config section."""
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
    response = client.post("/api/automation/parameters", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["key"] == "speed_to_contact"
    assert data["updated_params"]["interval_seconds"] == 1200

def test_automation_trigger_all_endpoint():
    """Verify POST /api/automation/trigger-all sets trigger events."""
    response = client.post("/api/automation/trigger-all")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "triggered_count" in data
    assert "triggered" in data
