import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from dashboard.services.auto_osint_trigger import trigger_auto_osint_profiling

@pytest.mark.asyncio
async def test_trigger_auto_osint_profiling_skip_when_missing():
    # Should safely return None when no email, phone, or plate
    bond_doc = {"booking_number": "TEST-123", "county": "Lee"}
    res = await trigger_auto_osint_profiling(bond_doc)
    assert res is None

@pytest.mark.asyncio
async def test_trigger_auto_osint_profiling_dispatches():
    bond_doc = {
        "booking_number": "TEST-123",
        "county": "Lee",
        "defendant_email": "target@example.com",
        "defendant_name": "John Doe",
    }
    with patch("dashboard.services.auto_osint_trigger.get_collection") as mock_get_col:
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(return_value=None)
        mock_col.insert_one = AsyncMock(return_value=None)
        mock_get_col.return_value = mock_col
        
        with patch("dashboard.services.auto_osint_trigger._execute_auto_osint", new_callable=AsyncMock) as mock_exec:
            res = await trigger_auto_osint_profiling(bond_doc)
            assert res == "dispatched_TEST-123"

