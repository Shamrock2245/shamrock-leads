"""
Unit tests for Twenty CRM style Paperwork Operations Hub & Drag-and-Drop Rules endpoints.
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


@patch("dashboard.routers.paperwork.get_collection")
def test_get_doc_rules_config_endpoint(mock_get_col, test_app):
    mock_rules_col = AsyncMock()
    mock_get_col.return_value = mock_rules_col
    mock_rules_col.find_one.return_value = {
        "categories": {
            "universal": ["master_bail_application", "indemnity_agreement"],
            "payment_plan": ["payment_plan_agreement"],
        },
        "updated_at": "2026-07-24T12:30:00Z",
    }

    client = TestClient(test_app)
    response = client.get("/api/paperwork/config/rules")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "categories" in data
    assert "universal" in data["categories"]
    assert len(data["categories"]["universal"]) == 2


@patch("dashboard.routers.paperwork.get_collection")
def test_save_doc_rules_config_endpoint(mock_get_col, test_app):
    mock_rules_col = AsyncMock()
    mock_get_col.return_value = mock_rules_col
    mock_rules_col.update_one = AsyncMock()

    payload = {
        "categories": {
            "universal": ["master_bail_application", "indemnity_agreement", "promissory_note"],
            "payment_plan": ["payment_plan_agreement", "credit_card_authorization"],
            "osi_surety": ["osi_appearance_bond"],
            "palmetto_surety": ["palmetto_power_certificate"],
            "conditional": ["cosigner_addendum"],
        }
    }

    client = TestClient(test_app)
    response = client.post("/api/paperwork/config/rules", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "categories" in data
    assert len(data["categories"]["universal"]) == 3
    mock_rules_col.update_one.assert_called_once()


def test_swipesimple_link_endpoint(test_app):
    client = TestClient(test_app)
    payload = {"packet_id": "pkt_3003", "amount": 750.00, "phone": "239-555-0199", "deliver": False}
    response = client.post("/api/paperwork/payment/swipesimple-link", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "swipesimple.com" in data["payment_link"]
    assert data["amount"] == 750.00


@patch("dashboard.routers.paperwork.get_collection")
def test_cash_payment_log_endpoint(mock_get_col, test_app):
    mock_tx_col = AsyncMock()
    mock_get_col.return_value = mock_tx_col
    mock_tx_col.insert_one = AsyncMock()

    client = TestClient(test_app)
    payload = {"packet_id": "pkt_3003", "amount": 500.00, "received_from": "Jane Doe", "notes": "Cash at office"}
    response = client.post("/api/paperwork/payment/cash-log", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["receipt_id"].startswith("CASH-")
    mock_tx_col.insert_one.assert_called_once()


@patch("dashboard.routers.paperwork.get_collection")
def test_post_release_remedy_doc_endpoint(mock_get_col, test_app):
    mock_remedy_col = AsyncMock()
    mock_get_col.return_value = mock_remedy_col
    mock_remedy_col.insert_one = AsyncMock()

    client = TestClient(test_app)
    payload = {
        "doc_type": "motion_vacate_forfeiture",
        "packet_id": "pkt_3003",
        "case_number": "26-CF-009999",
        "defendant_name": "Test Defendant",
        "county": "Lee",
    }
    response = client.post("/api/paperwork/post-release/remedy-doc", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["doc_id"].startswith("REMEDY-")
    assert "Motion to Vacate" in data["message"]
    mock_remedy_col.insert_one.assert_called_once()
