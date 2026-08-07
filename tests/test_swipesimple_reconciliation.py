"""
Tests for SwipeSimple Payment Reconciliation Service
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from dashboard.services.swipesimple_reconciliation_service import SwipeSimpleReconciliationService


def test_parse_swipesimple_receipt():
    sample_text = """
    SwipeSimple Payment Received
    Amount: $500.00
    Transaction ID: TX-99887766
    Cardholder Name: Jane Doe
    Note: For John Doe Lee County
    """
    data = SwipeSimpleReconciliationService.parse_swipesimple_receipt(sample_text)
    assert data["amount"] == 500.0
    assert data["transaction_id"] == "TX-99887766"
    assert data["cardholder_name"] == "Jane Doe"
    assert data["defendant_name"] == "John Doe Lee County"


@pytest.mark.asyncio
async def test_reconcile_payment_mock_db():
    db = {
        "payments": MagicMock(),
        "active_bonds": MagicMock(),
        "intake_queue": MagicMock(),
    }
    db["payments"].update_one = AsyncMock()
    db["active_bonds"].find_one = AsyncMock(return_value={"_id": "bond_123", "poa_number": "OSI-100"})
    db["active_bonds"].update_one = AsyncMock()

    service = SwipeSimpleReconciliationService(db)
    res = await service.reconcile_payment({
        "transaction_id": "TX-12345",
        "amount": 500.0,
        "defendant_name": "John Doe",
        "cardholder_name": "Jane Doe",
    })

    assert res["reconciled"] is True
    assert res["transaction_id"] == "TX-12345"
    assert res["matched_bond_id"] == "bond_123"
