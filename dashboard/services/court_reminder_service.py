from datetime import datetime, timezone, timedelta
from dashboard.extensions import get_collection
from dashboard.services.twilio_service import TwilioService
import os

class CourtReminderService:
    def __init__(self):
        self.twilio = TwilioService(
            account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
            auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
            from_number=os.getenv("TWILIO_FROM_NUMBER")
        )
        
    async def schedule_reminders(self, booking_number: str, defendant_name: str, phone: str, court_date_str: str, court_location: str, case_number: str):
        """Schedule 4-touch reminder sequence: 7d, 3d, 1d, morning-of."""
        try:
            court_reminders = get_collection("court_reminders")
            
            # Parse court date
            court_date = datetime.fromisoformat(court_date_str.replace('Z', '+00:00'))
            if court_date.tzinfo is None:
                court_date = court_date.replace(tzinfo=timezone.utc)
                
            reminders = []
            
            # 7 days before
            reminders.append({
                "booking_number": booking_number,
                "defendant_name": defendant_name,
                "phone": phone,
                "court_date": court_date_str,
                "send_at": (court_date - timedelta(days=7)).isoformat(),
                "message": f"SHAMROCK BAIL BONDS ALERT: {defendant_name}, you have court in 7 days ({court_date.strftime('%m/%d/%Y')}) for {court_location} County (Case: {case_number}). Please arrive early and dress appropriately. Reply to this message if you need assistance.",
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat()
            })
            
            # 3 days before
            reminders.append({
                "booking_number": booking_number,
                "defendant_name": defendant_name,
                "phone": phone,
                "court_date": court_date_str,
                "send_at": (court_date - timedelta(days=3)).isoformat(),
                "message": f"SHAMROCK BAIL BONDS ALERT: {defendant_name}, you have court in 3 days ({court_date.strftime('%m/%d/%Y')}) for {court_location} County (Case: {case_number}). Please arrive early and dress appropriately. Reply to this message if you need assistance.",
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat()
            })
            
            # 1 day before
            reminders.append({
                "booking_number": booking_number,
                "defendant_name": defendant_name,
                "phone": phone,
                "court_date": court_date_str,
                "send_at": (court_date - timedelta(days=1)).isoformat(),
                "message": f"SHAMROCK BAIL BONDS ALERT: {defendant_name}, you have court TOMORROW ({court_date.strftime('%m/%d/%Y')}) for {court_location} County (Case: {case_number}). Please arrive early and dress appropriately. Reply to this message if you need assistance.",
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat()
            })
            
            # Morning of (6 AM)
            morning_of = court_date.replace(hour=6, minute=0, second=0, microsecond=0)
            reminders.append({
                "booking_number": booking_number,
                "defendant_name": defendant_name,
                "phone": phone,
                "court_date": court_date_str,
                "send_at": morning_of.isoformat(),
                "message": f"SHAMROCK BAIL BONDS ALERT: {defendant_name}, you have court TODAY ({court_date.strftime('%m/%d/%Y')}) for {court_location} County (Case: {case_number}). Please arrive early and dress appropriately. Reply to this message if you need assistance.",
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat()
            })
            
            # Insert into MongoDB
            await court_reminders.insert_many(reminders)
            
            return {"success": True, "scheduled_count": len(reminders)}
        except Exception as e:
            return {"error": str(e)}
            
    async def process_due_reminders(self):
        """Find all reminders where send_at <= now and status == 'pending', send via Twilio."""
        try:
            court_reminders = get_collection("court_reminders")
            now = datetime.now(timezone.utc).isoformat()
            
            # Find due reminders
            cursor = court_reminders.find({
                "status": "pending",
                "send_at": {"$lte": now}
            })
            
            due_reminders = await cursor.to_list(length=100)
            sent_count = 0
            failed_count = 0
            
            for reminder in due_reminders:
                try:
                    # Send SMS
                    response = await self.twilio.send_sms(
                        to=reminder["phone"],
                        body=reminder["message"]
                    )
                    
                    # Update status
                    await court_reminders.update_one(
                        {"_id": reminder["_id"]},
                        {"$set": {
                            "status": "sent",
                            "sent_at": datetime.now(timezone.utc).isoformat(),
                            "twilio_sid": response.get("sid")
                        }}
                    )
                    sent_count += 1
                except Exception as e:
                    # Update status to failed
                    await court_reminders.update_one(
                        {"_id": reminder["_id"]},
                        {"$set": {
                            "status": "failed",
                            "error": str(e),
                            "failed_at": datetime.now(timezone.utc).isoformat()
                        }}
                    )
                    failed_count += 1
                    
            return {"success": True, "sent": sent_count, "failed": failed_count}
        except Exception as e:
            return {"error": str(e)}
