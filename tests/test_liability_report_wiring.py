"""
Unit tests for liability report wiring, financial split calculations, and active bond sync.
"""

from unittest.mock import AsyncMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.poa import poa_bp
from dashboard.routers.reports import _calc_surety_split, reports_bp


def test_calc_surety_split_osi():
    # Bond $5000 -> Gross Premium 10% = $500
    # OSI: Surety $7.50 / $100 gross = $37.50
    # BUF: $5.00 / $100 gross = $25.00
    # Agent retains: $500 - $37.50 - $25.00 = $437.50
    split = _calc_surety_split(5000.0, "OSI")
    assert split["premium"] == 500.0
    assert split["surety_owed"] == 37.50
    assert split["buf_owed"] == 25.00
    assert split["agent_retains"] == 437.50


def test_calc_surety_split_palmetto():
    # Bond $10000 -> Gross Premium 10% = $1000
    # Palmetto: Surety $10.00 / $100 gross = $100.00
    # BUF: $5.00 / $100 gross = $50.00
    # Agent retains: $1000 - $100.00 - $50.00 = $850.00
    split = _calc_surety_split(10000.0, "PALMETTO")
    assert split["premium"] == 1000.0
    assert split["surety_owed"] == 100.0
    assert split["buf_owed"] == 50.0
    assert split["agent_retains"] == 850.0


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(poa_bp)
    app.include_router(reports_bp)
    return app


@patch("dashboard.routers.poa.get_collection")
def test_poa_execute_calculates_splits_and_syncs_active_bonds(mock_get_col, test_app):
    mock_poa_col = AsyncMock()
    mock_active_col = AsyncMock()

    def side_effect(name):
        if name == "poa_inventory":
            return mock_poa_col
        if name == "active_bonds":
            return mock_active_col
        return AsyncMock()

    mock_get_col.side_effect = side_effect
    mock_poa_col.find_one.return_value = None

    client = TestClient(test_app)
    payload = {
        "poa_number": "554433",
        "poa_prefix": "OSI3",
        "date_executed": "2026-07-24",
        "amount": 5000.0,
        "defendant_first_name": "Mark",
        "defendant_last_name": "Stevens",
        "charge": "Grand Theft",
    }
    response = client.post("/api/poa/execute", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["gross_premium"] == 500.0
    assert data["surety_owed"] == 37.50
    assert data["buf_owed"] == 25.00
    assert data["agent_retains"] == 437.50

    # Verify active_bonds collection received upsert
    mock_active_col.update_one.assert_called_once()
    args, kwargs = mock_active_col.update_one.call_args
    set_doc = kwargs["$set"] if "$set" in kwargs else args[1]["$set"]
    assert set_doc["poa_number"] == "554433"
    assert set_doc["surety"] == "OSI"
    assert set_doc["bond_amount"] == 5000.0
    assert set_doc["gross_premium"] == 500.0
    assert set_doc["surety_owed"] == 37.50
    assert set_doc["buf_owed"] == 25.0
