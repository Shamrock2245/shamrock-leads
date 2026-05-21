from datetime import datetime, timedelta, timezone
from dashboard.extensions import get_db

class TaskEngine:
    @staticmethod
    async def create_task(booking_number: str, title: str, description: str, due_date: datetime, task_type: str = "general"):
        db = get_db()
        due_date_str = due_date.isoformat() if isinstance(due_date, datetime) else due_date
        
        # Idempotent task creation: avoid duplicate pending tasks of the same type for the same booking
        existing = await db.tasks.find_one({
            "booking_number": booking_number,
            "task_type": task_type,
            "status": "pending"
        })
        
        if existing:
            # Update due date and description if it already exists
            await db.tasks.update_one(
                {"_id": existing["_id"]},
                {"$set": {"due_date": due_date_str, "description": description, "title": title}}
            )
            return str(existing["_id"])
            
        task = {
            "booking_number": booking_number,
            "title": title,
            "description": description,
            "due_date": due_date_str,
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
        db = get_db()
        # Schedule an initial check-in 7 days from now
        due_date = datetime.now(timezone.utc) + timedelta(days=7)
        await TaskEngine.create_task(
            booking_number=booking_number,
            title="Initial Check-in",
            description="Verify defendant's current address and employment status.",
            due_date=due_date,
            task_type="check_in"
        )
        
        # Check if there's a court date and schedule a reminder task
        bond = await db.active_bonds.find_one({"booking_number": booking_number})
        if bond and bond.get("court_date"):
            try:
                court_date_str = bond["court_date"]
                if isinstance(court_date_str, str):
                    court_date = datetime.fromisoformat(court_date_str.replace("Z", "+00:00"))
                else:
                    court_date = court_date_str
                    
                if court_date.tzinfo is None:
                    court_date = court_date.replace(tzinfo=timezone.utc)
                    
                # Only schedule if court date is in the future
                if court_date > datetime.now(timezone.utc):
                    # Schedule task 3 days before court
                    reminder_date = court_date - timedelta(days=3)
                    if reminder_date < datetime.now(timezone.utc):
                        reminder_date = datetime.now(timezone.utc) + timedelta(hours=1)
                        
                    await TaskEngine.create_task(
                        booking_number=booking_number,
                        title="Court Reminder Call",
                        description=f"Call defendant to remind them of court on {court_date.strftime('%m/%d/%Y')}.",
                        due_date=reminder_date,
                        task_type="court_reminder"
                    )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Error scheduling court reminder task: {e}")
