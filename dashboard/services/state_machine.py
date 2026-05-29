"""
ShamrockLeads — Bond State Machine
=====================================
Enforces valid state transitions for active bonds.

Race-condition mitigation:
  - Uses a conditional $set with the expected current status as the filter
    (optimistic locking pattern).  If another process already changed the
    status, the update matches 0 documents and we re-fetch + retry once.
  - All state changes are written atomically with $push to status_history
    and timeline in the same update_one call.
"""
import logging
from datetime import datetime, timezone

from dashboard.extensions import get_db
from dashboard.services.audit_service import AuditService

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2  # optimistic-lock retry budget


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
    async def transition_bond(
        booking_number: str,
        new_status: str,
        actor: str,
        reason: str = "",
    ) -> dict:
        """Atomically transition a bond to a new status.

        Uses optimistic locking: the $set filter includes the expected
        current_status so that a concurrent write from another process
        causes our update to match 0 documents, at which point we retry
        up to _MAX_RETRIES times before raising.
        """
        db = get_db()

        for attempt in range(1, _MAX_RETRIES + 2):
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
                    "note": "No change",
                }

            # 2. Validate transition
            allowed = BondStateMachine.VALID_TRANSITIONS.get(current_status, [])
            if new_status not in allowed:
                raise ValueError(
                    f"Invalid transition from '{current_status}' to '{new_status}'"
                )

            # 3. Atomic update with optimistic lock (filter on current status)
            now = datetime.now(timezone.utc)
            timeline_event = {
                "timestamp": now.isoformat(),
                "event": "status_changed",
                "detail": f"Status changed to {new_status}: {reason}",
                "agent": actor,
                "source": "state_machine",
            }
            history_entry = {
                "from": current_status,
                "to": new_status,
                "timestamp": now,
                "actor": actor,
                "reason": reason,
            }

            result = await db.active_bonds.update_one(
                # Optimistic lock: only match if status hasn't changed since we read it
                {"booking_number": booking_number, "status": current_status},
                {
                    "$set": {"status": new_status, "updated_at": now},
                    "$push": {
                        "status_history": history_entry,
                        "timeline": timeline_event,
                    },
                },
            )

            if result.matched_count == 0:
                # Another process changed the status concurrently
                if attempt <= _MAX_RETRIES:
                    logger.warning(
                        "BondStateMachine: optimistic lock miss for %s (attempt %d/%d) — retrying",
                        booking_number,
                        attempt,
                        _MAX_RETRIES,
                    )
                    continue
                raise RuntimeError(
                    f"Concurrent modification detected for bond {booking_number}. "
                    "Please retry the operation."
                )

            # 4. Immutable Audit Log (SOC II)
            await AuditService.log_event(
                entity_type="bond",
                entity_id=booking_number,
                action="status_change",
                details={"from": current_status, "to": new_status, "reason": reason},
                actor=actor,
            )

            poa_released = False
            poa_number_returned = None

            # 5. Side effects
            if new_status in ("exonerated", "forfeited", "surrendered"):
                poa_number = current_bond.get("poa_number")
                if poa_number:
                    try:
                        from dashboard.services.poa_service import auto_release_poa
                        await auto_release_poa(poa_number, reason=new_status, actor=actor)
                        poa_released = True
                        poa_number_returned = poa_number
                    except Exception as exc:
                        logger.error(
                            "auto_release_poa failed for %s: %s", poa_number, exc
                        )
                # Cancel pending compliance tasks
                from dashboard.services.task_engine import TaskEngine
                await TaskEngine.cancel_pending_tasks(
                    booking_number, reason=f"Bond {new_status}"
                )

            elif new_status == "active" and current_status != "active":
                # Schedule compliance tasks when transitioning to active
                from dashboard.services.task_engine import TaskEngine
                await TaskEngine.schedule_compliance_tasks(booking_number)

            return {
                "success": True,
                "status": new_status,
                "from_status": current_status,
                "poa_released": poa_released,
                "poa_number": poa_number_returned,
                "history_entry": history_entry,
            }

        # Should never reach here
        raise RuntimeError(f"transition_bond exhausted retries for {booking_number}")
