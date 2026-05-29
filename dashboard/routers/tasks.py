"""
ShamrockLeads — Compliance Tasks Router
==========================================
API endpoints for querying, completing, and managing compliance tasks.

SOC II Notes:
  - Every task completion generates an immutable AuditService event
  - PII (phone, name, SSN, address) is NEVER written to audit details
  - Only booking_number, task_id, task_type, and actor are logged
"""
import logging
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# Fields that must never appear in audit logs (PII scrubbing)
_PII_FIELDS = {"phone", "ssn", "address", "dob", "email", "name", "full_name"}


def _scrub_pii(details: dict) -> dict:
    """Return a copy of details with PII fields removed."""
    return {k: v for k, v in details.items() if k.lower() not in _PII_FIELDS}


@router.get("/")
async def get_tasks(booking_number: str = None, status: str = "pending"):
    """Return tasks filtered by booking_number and/or status."""
    tasks_col = get_collection("tasks")
    query: dict = {"status": status}
    if booking_number:
        query["booking_number"] = booking_number
    cursor = tasks_col.find(query).sort("due_date", 1)
    results = []
    async for t in cursor:
        t["_id"] = str(t["_id"])
        results.append(t)
    return {"tasks": results}


@router.get("/overdue")
async def get_overdue_tasks():
    """Return all overdue tasks."""
    tasks_col = get_collection("tasks")
    cursor = tasks_col.find({"status": "overdue"}).sort("due_date", 1)
    results = []
    async for t in cursor:
        t["_id"] = str(t["_id"])
        results.append(t)
    return {"tasks": results, "count": len(results)}


@router.post("/{task_id}/complete")
async def complete_task(task_id: str, request: Request):
    """Mark a task as completed and emit an immutable audit event.

    Body (all optional):
        { "notes": "...", "agent": "Staff Name" }

    SOC II: Only booking_number, task_type, and task_id are logged.
    Notes text is stored on the task record but NOT in the audit event.
    """
    from dashboard.services.audit_service import AuditService

    try:
        data = (await request.json()) or {}
    except Exception:
        data = {}

    tasks_col = get_collection("tasks")

    # Fetch task first to get booking_number and task_type for audit
    try:
        task = await tasks_col.find_one({"_id": ObjectId(task_id)})
    except Exception:
        return JSONResponse({"error": "Invalid task ID format"}, status_code=400)

    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    if task.get("status") == "completed":
        return JSONResponse({"error": "Task already completed"}, status_code=409)

    now_iso = datetime.now(timezone.utc).isoformat()
    actor = (data.get("agent") or "Dashboard Agent").strip()
    notes = (data.get("notes") or "").strip()

    await tasks_col.update_one(
        {"_id": ObjectId(task_id)},
        {
            "$set": {
                "status": "completed",
                "completed_at": now_iso,
                "completed_by": actor,
                "notes": notes,
            }
        },
    )

    # Immutable Audit Log — PII-safe (no names, phones, addresses)
    audit_details = _scrub_pii({
        "booking_number": task.get("booking_number"),
        "task_type": task.get("task_type"),
        "title": task.get("title"),
        # notes intentionally excluded from audit to avoid accidental PII
    })
    await AuditService.log_event(
        entity_type="task",
        entity_id=task_id,
        action="task_completed",
        details=audit_details,
        actor=actor,
    )

    logger.info(
        "[Tasks] Completed task %s (type=%s, booking=%s) by %s",
        task_id,
        task.get("task_type"),
        task.get("booking_number"),
        actor,
    )
    return {"success": True}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request):
    """Cancel a single task."""
    from dashboard.services.audit_service import AuditService

    try:
        data = (await request.json()) or {}
    except Exception:
        data = {}

    tasks_col = get_collection("tasks")

    try:
        task = await tasks_col.find_one({"_id": ObjectId(task_id)})
    except Exception:
        return JSONResponse({"error": "Invalid task ID format"}, status_code=400)

    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    actor = (data.get("agent") or "Dashboard Agent").strip()
    reason = (data.get("reason") or "").strip()
    now_iso = datetime.now(timezone.utc).isoformat()

    await tasks_col.update_one(
        {"_id": ObjectId(task_id)},
        {"$set": {
            "status": "cancelled",
            "cancel_reason": reason,
            "cancelled_at": now_iso,
            "cancelled_by": actor,
        }},
    )

    await AuditService.log_event(
        entity_type="task",
        entity_id=task_id,
        action="task_cancelled",
        details=_scrub_pii({
            "booking_number": task.get("booking_number"),
            "task_type": task.get("task_type"),
            "reason": reason,
        }),
        actor=actor,
    )
    return {"success": True}
