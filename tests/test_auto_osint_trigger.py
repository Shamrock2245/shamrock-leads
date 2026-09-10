import inspect
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from dashboard.services.auto_osint_trigger import trigger_auto_osint_profiling, _execute_auto_osint
from dashboard.models.osint import EngineType
from dashboard.routers import intake as intake_mod


@pytest.mark.asyncio
async def test_trigger_auto_osint_profiling_skip_when_missing():
    bond_doc = {"booking_number": "TEST-123", "county": "Lee"}
    res = await trigger_auto_osint_profiling(bond_doc)
    assert res is None


@pytest.mark.asyncio
async def test_trigger_auto_osint_skips_indemnitor_email_and_generic_email():
    bond_doc = {
        "booking_number": "TEST-123",
        "email": "cosigner@example.com",
        "indemnitor_email": "cosigner@example.com",
        "phone": "2395550000",
        "indemnitor_phone": "2395550000",
    }
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
            await asyncio_sleep_zero()
            mock_exec.assert_called_once()
            assert mock_exec.call_args.kwargs["email"] == "target@example.com"
            assert mock_exec.call_args.kwargs["booking_number"] == "TEST-123"


async def asyncio_sleep_zero():
    import asyncio
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_execute_auto_osint_attaches_to_active_bonds_not_defendants():
    mock_svc = MagicMock()
    mock_svc.run_scan = AsyncMock(return_value="scan-1")
    mock_svc.get_scan = AsyncMock(return_value={
        "status": "completed",
        "total_accounts": 2,
        "platforms_found": ["paypal"],
        "engines_requested": ["holehe"],
        "osint_risk_score": 10,
    })
    mock_svc.extract_importable_fields = MagicMock(return_value={
        "social_profiles": {"paypal": "https://paypal.me/x"},
        "usernames": ["x"],
    })
    mock_result = MagicMock()
    mock_result.matched_count = 1
    mock_bonds = MagicMock()
    mock_bonds.update_one = AsyncMock(return_value=mock_result)

    with patch("dashboard.services.osint_service.get_osint_service", return_value=mock_svc), \
         patch("dashboard.services.auto_osint_trigger.get_collection", return_value=mock_bonds), \
         patch("dashboard.services.auto_osint_trigger.asyncio.sleep", new_callable=AsyncMock):
        await _execute_auto_osint(
            booking_number="TEST-123",
            full_name="John Doe",
            email="target@example.com",
            phone="",
            plate="",
            engines=[EngineType.holehe],
            actor="test",
        )

    assert "attach_to_subject" not in [c[0] for c in mock_svc.method_calls]
    mock_bonds.update_one.assert_awaited_once()
    filt, update = mock_bonds.update_one.call_args[0]
    assert filt == {"booking_number": "TEST-123"}
    assert "osint_intel" in update["$set"]
    assert "osint_last_scanned_at" in update["$set"]
    assert update["$set"]["osint_scan_id"] == "scan-1"


@pytest.mark.asyncio
async def test_execute_auto_osint_treats_unmatched_bond_as_failure():
    mock_svc = MagicMock()
    mock_svc.run_scan = AsyncMock(return_value="scan-1")
    mock_svc.get_scan = AsyncMock(return_value={"status": "completed", "total_accounts": 0})
    mock_svc.extract_importable_fields = MagicMock(return_value={})
    mock_result = MagicMock()
    mock_result.matched_count = 0
    mock_bonds = MagicMock()
    mock_bonds.update_one = AsyncMock(return_value=mock_result)

    with patch("dashboard.services.osint_service.get_osint_service", return_value=mock_svc), \
         patch("dashboard.services.auto_osint_trigger.get_collection", return_value=mock_bonds), \
         patch("dashboard.services.auto_osint_trigger.asyncio.sleep", new_callable=AsyncMock):
        await _execute_auto_osint(
            booking_number="MISSING",
            full_name="X",
            email="a@b.com",
            phone="",
            plate="",
            engines=[],
            actor="test",
        )

    mock_bonds.update_one.assert_awaited_once()


def test_intake_promotion_awaits_auto_osint():
    source = inspect.getsource(intake_mod)
    assert "await trigger_auto_osint_profiling(bond_doc, actor=\"intake_promotion\")" in source

