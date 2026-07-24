"""
Unit tests for Twenty CRM style Paperwork Operations Hub endpoints.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.paperwork import paperwork_bp


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(paperwork_bp)
    return app


@patch("dashboard.routers.paperwork.get_collection")
def test_list_all_packets_endpoint(mock_get_col, test_app):
    mock_packets_col = AsyncMock()
    mock_get_col.return_value = mock_packets_col

    mock_cursor = AsyncMock()
    mock_cursor.to_list = AsyncMock(return_value=[
        {
            "packet_id": "pkt_1001",
            "defendant_name": "John Doe",
            "indemnitor_name": "Mary Doe",
            "surety_id": "osi",
            "status": "sent",
            "created_at": "2026-07-24T12:00:00Z",
        }
    ])
    mock_find = MagicMock()
    mock_find.sort.return_value = mock_cursor
    mock_packets_col.find = MagicMock(return_value=mock_find)
    mock_packets_col.count_documents = AsyncMock(return_value=5)

    client = TestClient(test_app)
    response = client.get("/api/paperwork/all")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert len(data["packets"]) == 1
    assert data["packets"][0]["packet_id"] == "pkt_1001"
    assert "summary" in data
    assert data["summary"]["total_packets"] == 5


@patch("dashboard.routers.paperwork.get_collection")
def test_packet_hydration_audit_endpoint(mock_get_col, test_app):
    mock_packets_col = AsyncMock()
    mock_get_col.return_value = mock_packets_col

    mock_packets_col.find_one.return_value = {
        "packet_id": "pkt_2002",
        "booking_number": "LEE-2026-999",
        "surety_id": "osi",
        "status": "sent",
        "defendant_name": "Robert Paulson",
        "defendant_dob": "1985-04-12",
        "defendant_address": "123 Main St, Fort Myers, FL",
        "indemnitor_name": "Jane Paulson",
        "indemnitor_phone": "239-555-0199",
        "indemnitor_address": "123 Main St, Fort Myers, FL",
        "case_number": "26-CF-000123",
        "bond_amount": 5000.0,
        "poa_number": "OSI3-889900",
    }

    client = TestClient(test_app)
    response = client.get("/api/paperwork/pkt_2002/hydration-audit")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["packet_id"] == "pkt_2002"
    assert data["hydration_score"] > 80.0
    assert data["hydrated_count"] >= 10
    assert len(data["fields"]) == 11
