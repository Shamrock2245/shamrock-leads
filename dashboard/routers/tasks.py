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
    data = await request.json()
    tasks_col = get_collection("tasks")
    await tasks_col.update_one(
        {"_id": ObjectId(task_id)},
        {
            "$set": {
                "status": "completed", 
                "completed_at": datetime.now(timezone.utc).isoformat(), 
                "notes": data.get("notes", "")
            }
        }
    )
    return {"success": True}
