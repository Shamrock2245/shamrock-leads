import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from dashboard.services.state_machine import BondStateMachine

# ── Helpers ───────────────────────────────────────────────────────────────────
def _make_db(bond_doc=None):
    """Build a mock Motor db with configurable collection responses."""
    db = MagicMock()
    active_bonds = MagicMock()
    
    active_bonds.find_one = AsyncMock(return_value=bond_doc)
    active_bonds.update_one = AsyncMock(return_value=MagicMock(matched_count=1, modified_count=1))
    
    db.active_bonds = active_bonds
    return db

# ═══════════════════════════════════════════════════════════════════════════════
#  Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════
try:
    import pytest_asyncio  # noqa: F401
    HAS_ASYNCIO = True
except ImportError:
    HAS_ASYNCIO = False

@pytest.mark.skipif(not HAS_ASYNCIO, reason="pytest-asyncio not installed")
@pytest.mark.asyncio
class TestBondStateMachine:

    @patch("dashboard.services.state_machine.get_db")
    @patch("dashboard.services.state_machine.AuditService.log_event", new_callable=AsyncMock)
    @patch("dashboard.services.task_engine.TaskEngine.cancel_pending_tasks", new_callable=AsyncMock)
    @patch("dashboard.services.task_engine.TaskEngine.schedule_compliance_tasks", new_callable=AsyncMock)
    # mock poa auto release
    @patch("dashboard.services.poa_service.auto_release_poa", new_callable=AsyncMock)
    async def test_valid_transition_active_to_exonerated(self, mock_auto_release, mock_schedule, mock_cancel, mock_audit, mock_get_db):
        mock_db = _make_db({"booking_number": "123", "status": "active", "poa_number": "POA-1"})
        mock_get_db.return_value = mock_db
        
        result = await BondStateMachine.transition_bond("123", "exonerated", "TestAgent", "Case Dismissed")
        
        assert result["success"] is True
        assert result["status"] == "exonerated"
        assert result["poa_released"] is True
        
        # Verify db update called
        mock_db.active_bonds.update_one.assert_called_once()
        
        # Verify side effects
        mock_auto_release.assert_called_once_with("POA-1", reason="exonerated", actor="TestAgent")
        mock_cancel.assert_called_once_with("123", reason="Bond exonerated")
        mock_schedule.assert_not_called()
        mock_audit.assert_called_once()
        
    @patch("dashboard.services.state_machine.get_db")
    async def test_invalid_transition(self, mock_get_db):
        mock_db = _make_db({"booking_number": "123", "status": "exonerated"})
        mock_get_db.return_value = mock_db
        
        # Transitioning FROM exonerated to active should raise ValueError
        with pytest.raises(ValueError, match="Invalid transition from 'exonerated' to 'active'"):
            await BondStateMachine.transition_bond("123", "active", "TestAgent")
            
    @patch("dashboard.services.state_machine.get_db")
    @patch("dashboard.services.state_machine.AuditService.log_event", new_callable=AsyncMock)
    @patch("dashboard.services.task_engine.TaskEngine.cancel_pending_tasks", new_callable=AsyncMock)
    @patch("dashboard.services.task_engine.TaskEngine.schedule_compliance_tasks", new_callable=AsyncMock)
    async def test_valid_transition_monitoring_to_active(self, mock_schedule, mock_cancel, mock_audit, mock_get_db):
        mock_db = _make_db({"booking_number": "123", "status": "monitoring"})
        mock_get_db.return_value = mock_db
        
        result = await BondStateMachine.transition_bond("123", "active", "TestAgent", "Back to normal")
        
        assert result["success"] is True
        assert result["status"] == "active"
        
        # Verify db update called
        mock_db.active_bonds.update_one.assert_called_once()
        
        # Verify side effects
        mock_schedule.assert_called_once_with("123")
        mock_cancel.assert_not_called()
        mock_audit.assert_called_once()

    @patch("dashboard.services.state_machine.get_db")
    async def test_no_op_transition(self, mock_get_db):
        mock_db = _make_db({"booking_number": "123", "status": "active"})
        mock_get_db.return_value = mock_db
        
        result = await BondStateMachine.transition_bond("123", "active", "TestAgent")
        
        assert result["success"] is True
        assert result["status"] == "active"
        assert result["note"] == "No change"
        
        mock_db.active_bonds.update_one.assert_not_called()
