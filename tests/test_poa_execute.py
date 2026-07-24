"""
Unit tests for POA execution and prefix-to-surety determination in poa.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.poa import determine_surety_from_prefix, poa_bp


def test_determine_surety_from_prefix():
    # OSI prefixes
    assert determine_surety_from_prefix("OSI3") == "osi"
    assert determine_surety_from_prefix("osi6") == "osi"
    assert determine_surety_from_prefix("OSI100") == "osi"

    # Palmetto prefixes
    assert determine_surety_from_prefix("PSC2") == "palmetto"
    assert determine_surety_from_prefix("psc5") == "palmetto"
    assert determine_surety_from_prefix("PAL10") == "palmetto"

    # Explicit fallback for custom prefix
    assert determine_surety_from_prefix("CUSTOM1", "palmetto") == "palmetto"
    assert determine_surety_from_prefix("CUSTOM1", "osi") == "osi"
    assert determine_surety_from_prefix("", None) == "osi"


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(poa_bp)
    return app


@patch("dashboard.routers.poa.get_collection")
def test_api_poa_execute_missing_number(mock_get_col, test_app):
    client = TestClient(test_app)
    response = client.post("/api/poa/execute", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "poa_number is required"



@patch("dashboard.routers.poa.get_collection")
def test_api_poa_execute_success(mock_get_col, test_app):
    mock_col = AsyncMock()
    mock_get_col.return_value = mock_col
    mock_col.find_one.return_value = None

    client = TestClient(test_app)
    payload = {
        "poa_number": "998877",
        "poa_prefix": "OSI3",
        "date_executed": "2026-07-24",
        "amount": 5000.0,
        "defendant_first_name": "John",
        "defendant_last_name": "Doe",
        "charge": "Battery",
    }
    response = client.post("/api/poa/execute", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["success"] is True
    assert res_data["poa_number"] == "998877"
    assert res_data["poa_prefix"] == "OSI3"
    assert res_data["surety_id"] == "osi"
    assert res_data["defendant_name"] == "John Doe"
    assert res_data["charge"] == "Battery"
    assert res_data["amount"] == 5000.0

    mock_col.insert_one.assert_called_once()


@patch("dashboard.routers.poa.get_collection")
def test_api_poa_execute_palmetto_optional_charge(mock_get_col, test_app):
    mock_col = AsyncMock()
    mock_get_col.return_value = mock_col
    existing_doc = {"_id": "123", "poa_number": "776655", "status": "available"}
    mock_col.find_one.return_value = existing_doc

    client = TestClient(test_app)
    payload = {
        "poa_number": "776655",
        "poa_prefix": "PSC5",
        "date_executed": "2026-07-24",
        "amount": 10000.0,
        "defendant_first_name": "Jane",
        "defendant_last_name": "Smith",
        # charge omitted — optional!
    }
    response = client.post("/api/poa/execute", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["success"] is True
    assert res_data["surety_id"] == "palmetto"
    assert res_data["defendant_name"] == "Jane Smith"
    assert res_data["charge"] is None

    mock_col.update_one.assert_called_once()
