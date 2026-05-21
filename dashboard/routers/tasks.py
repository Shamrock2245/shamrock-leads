from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dashboard.extensions import get_collection
from bson import ObjectId
from datetime import datetime, timezone

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

@router.get("/")
async def get_tasks(booking_number: str = None, status: str = "pending"):
    tasks_col = get_collection("tasks")
    query = {"status": status}
    if booking_number:
        query["booking_number"] = booking_number
        
    cursor = tasks_col.find(query).sort("due_date", 1)
    results = []
    async for t in cursor:
        t["_id"] = str(t["_id"])
        results.append(t)
        
    return {"tasks": results}

@router.post("/{task_id}/complete")
async def complete_task(task_id: str, request: Request):
    from dashboard.services.audit_service import AuditService
    data = await request.json()
    tasks_col = get_collection("tasks")
    
    # Fetch task first to get booking_number for audit
    task = await tasks_col.find_one({"_id": ObjectId(task_id)})
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)
        
    now_iso = datetime.now(timezone.utc).isoformat()
    await tasks_col.update_one(
        {"_id": ObjectId(task_id)},
        {
            "$set": {
                "status": "completed", 
                "completed_at": now_iso, 
                "notes": data.get("notes", "")
            }
        }
    )
    
    # Immutable Audit Log for SOC II
    actor = data.get("agent", "Dashboard Agent")
    await AuditService.log_event(
        entity_type="task",
        entity_id=task_id,
        action="task_completed",
        details={
            "booking_number": task.get("booking_number"),
            "title": task.get("title"),
            "notes": data.get("notes", "")
        },
        actor=actor
    )
    
    return {"success": True}
