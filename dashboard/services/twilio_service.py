import httpx
from datetime import datetime, timedelta

class TwilioService:
    def __init__(self, account_sid: str, auth_token: str, from_number: str):
        self.sid = account_sid
        self.token = auth_token
        self.from_number = from_number
        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}"

    async def send_sms(self, to: str, body: str) -> dict:
        """Send SMS via Twilio REST API."""
        url = f"{self.base_url}/Messages.json"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                auth=(self.sid, self.token),
                data={
                    "From": self.from_number,
                    "To": to,
                    "Body": body,
                }
            )
            resp.raise_for_status()
            return resp.json()

    async def schedule_court_reminders(self, defendant_name: str, phone: str, 
                                        court_date: str, court_location: str, case_number: str):
        """Schedule 4-touch reminder sequence: 7d, 3d, 1d, morning-of."""
        # Store in MongoDB 'court_reminders' collection
        # Celery beat picks them up and sends at the right time
        
        # Example of how the reminders would be structured:
        reminders = []
        try:
            c_date = datetime.fromisoformat(court_date)
            
            # 7 days before
            reminders.append({
                "send_at": (c_date - timedelta(days=7)).isoformat(),
                "message": f"SHAMROCK BAIL BONDS ALERT: {defendant_name}, you have court in 7 days ({c_date.strftime('%m/%d/%Y')}) for {court_location} County (Case: {case_number}). Please arrive early and dress appropriately. Reply to this message if you need assistance."
            })
            
            # 3 days before
            reminders.append({
                "send_at": (c_date - timedelta(days=3)).isoformat(),
                "message": f"SHAMROCK BAIL BONDS ALERT: {defendant_name}, you have court in 3 days ({c_date.strftime('%m/%d/%Y')}) for {court_location} County (Case: {case_number}). Please arrive early and dress appropriately. Reply to this message if you need assistance."
            })
            
            # 1 day before
            reminders.append({
                "send_at": (c_date - timedelta(days=1)).isoformat(),
                "message": f"SHAMROCK BAIL BONDS ALERT: {defendant_name}, you have court TOMORROW ({c_date.strftime('%m/%d/%Y')}) for {court_location} County (Case: {case_number}). Please arrive early and dress appropriately. Reply to this message if you need assistance."
            })
            
            # Morning of
            reminders.append({
                "send_at": c_date.replace(hour=6, minute=0).isoformat(),
                "message": f"SHAMROCK BAIL BONDS ALERT: {defendant_name}, you have court TODAY ({c_date.strftime('%m/%d/%Y')}) for {court_location} County (Case: {case_number}). Please arrive early and dress appropriately. Reply to this message if you need assistance."
            })
            
            return reminders
        except ValueError:
            # Handle invalid date format
            return []
