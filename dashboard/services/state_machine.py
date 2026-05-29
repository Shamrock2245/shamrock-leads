from datetime import datetime, timezone
from dashboard.extensions import get_db
from dashboard.services.audit_service import AuditService

class BondStateMachine:
    """Enforces valid state transitions for active bonds."""
    
    VALID_TRANSITIONS = {
        "active": ["monitoring", "alert", "exonerated", "forfeited", "surrendered"],
        "monitoring": ["active", "alert", "exonerated", "forfeited", "surrendered"],
        "alert": ["active", "monitoring", "exonerated", "forfeited", "surrendered"],
        "forfeited": ["reinstated"],
        "surrendered": ["reinstated"],
        "exonerated": [],  # Terminal state
        "reinstated": ["active", "monitoring", "alert", "exonerated", "forfeited", "surrendered"],
    }

    @staticmethod
    async def transition_bond(booking_number: str, new_status: str, actor: str, reason: str = "") -> dict:
        db = get_db()
        
        # 1. Fetch current bond
        current_bond = await db.active_bonds.find_one({"booking_number": booking_number})
        if not current_bond:
            raise ValueError(f"Bond not found for booking: {booking_number}")
            
        current_status = current_bond.get("status", "active")
        
        if current_status == new_status:
            return {
                "success": True, 
                "status": new_status, 
                "from_status": current_status, 
                "poa_released": False, 
                "poa_number": None, 
                "note": "No change"
            }
            
        # 2. Validate transition
        allowed = BondStateMachine.VALID_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            raise ValueError(f"Invalid transition from '{current_status}' to '{new_status}'")
            
        # 3. Update status and push to timeline
        now = datetime.now(timezone.utc)
        timeline_event = {
            "timestamp": now.isoformat(),
            "event": "status_changed",
            "detail": f"Status changed to {new_status}: {reason}",
            "agent": actor,
            "source": "state_machine"
        }
        
        history_entry = {"from": current_status, "to": new_status, "timestamp": now, "actor": actor, "reason": reason}
        await db.active_bonds.update_one(
            {"booking_number": booking_number},
            {
                "$set": {"status": new_status, "updated_at": now},
                "$push": {"status_history": history_entry, "timeline": timeline_event}
            }
        )
        
        # 4. Immutable Audit Log
        await AuditService.log_event(
            entity_type="bond",
            entity_id=booking_number,
            action="status_change",
            details={"from": current_status, "to": new_status, "reason": reason},
            actor=actor
        )
        
        poa_released = False
        poa_number_returned = None
        # 5. Fire Side Effects
        if new_status in ["exonerated", "forfeited", "surrendered"]:
            poa_number = current_bond.get("poa_number")
            if poa_number:
                # Need to implement auto-release logic
                from dashboard.services.poa_service import auto_release_poa
                await auto_release_poa(poa_number, reason=new_status, actor=actor)
                poa_released = True
                poa_number_returned = poa_number
            
            # Cancel pending compliance tasks
            from dashboard.services.task_engine import TaskEngine
            await TaskEngine.cancel_pending_tasks(booking_number, reason=f"Bond {new_status}")
            
        elif new_status == "active" and current_status != "active":
            # Schedule compliance tasks if transitioning to active
            from dashboard.services.task_engine import TaskEngine
            await TaskEngine.schedule_compliance_tasks(booking_number)
                
        return {
            "success": True,
            "status": new_status,
            "from_status": current_status,
            "poa_released": poa_released,
            "poa_number": poa_number_returned,
            "history_entry": history_entry
        }
