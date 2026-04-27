"""
ShamrockLeads — Twilio Service (Phase E — Manus)
Async Twilio SMS client + 4-touch court reminder scheduler.
"""

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
                url, auth=(self.sid, self.token),
                data={"From": self.from_number, "To": to, "Body": body},
            )
            resp.raise_for_status()
            return resp.json()

    async def schedule_court_reminders(self, defendant_name: str, phone: str,
                                       court_date: str, court_location: str,
                                       case_number: str):
        """Schedule 4-touch reminder sequence: 7d, 3d, 1d, morning-of."""
        reminders = []
        try:
            c_date = datetime.fromisoformat(court_date)

            touches = [
                (7, "in 7 days"),
                (3, "in 3 days"),
                (1, "TOMORROW"),
            ]
            for days, label in touches:
                reminders.append({
                    "send_at": (c_date - timedelta(days=days)).isoformat(),
                    "message": (
                        f"SHAMROCK BAIL BONDS ALERT: {defendant_name}, you have court "
                        f"{label} ({c_date.strftime('%m/%d/%Y')}) for {court_location} "
                        f"County (Case: {case_number}). Please arrive early and dress "
                        f"appropriately. Reply to this message if you need assistance."
                    ),
                })

            # Morning of
            reminders.append({
                "send_at": c_date.replace(hour=6, minute=0).isoformat(),
                "message": (
                    f"SHAMROCK BAIL BONDS ALERT: {defendant_name}, you have court "
                    f"TODAY ({c_date.strftime('%m/%d/%Y')}) for {court_location} "
                    f"County (Case: {case_number}). Please arrive early and dress "
                    f"appropriately. Reply to this message if you need assistance."
                ),
            })
            return reminders
        except ValueError:
            return []
