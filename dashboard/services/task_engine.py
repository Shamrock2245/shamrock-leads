from datetime import datetime, timedelta, timezone
from dashboard.extensions import get_db

class TaskEngine:
    @staticmethod
    async def create_task(booking_number: str, title: str, description: str, due_date: datetime, task_type: str = "general"):
        db = get_db()
        task = {
            "booking_number": booking_number,
            "title": title,
            "description": description,
            "due_date": due_date.isoformat() if isinstance(due_date, datetime) else due_date,
            "task_type": task_type,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        result = await db.tasks.insert_one(task)
        return str(result.inserted_id)
        
    @staticmethod
    async def cancel_pending_tasks(booking_number: str, reason: str = ""):
        db = get_db()
        await db.tasks.update_many(
            {"booking_number": booking_number, "status": "pending"},
            {"$set": {"status": "cancelled", "cancel_reason": reason}}
        )
        
    @staticmethod
    async def schedule_compliance_tasks(booking_number: str):
        # Schedule an initial check-in 7 days from now
        due_date = datetime.now(timezone.utc) + timedelta(days=7)
        await TaskEngine.create_task(
            booking_number=booking_number,
            title="Initial Check-in",
            description="Verify defendant's current address and employment status.",
            due_date=due_date,
            task_type="check_in"
        )
