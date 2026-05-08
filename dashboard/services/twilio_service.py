"""
ShamrockLeads — Twilio SMS Service
====================================
Sends SMS messages via Twilio REST API and schedules 4-touch court reminder
sequences by persisting them to the MongoDB ``court_reminders`` collection.

The reminder processor (CourtReminderService) picks up pending reminders
and delivers them via BlueBubbles iMessage (primary) or Twilio SMS (fallback).

Environment variables required:
  TWILIO_ACCOUNT_SID             -- Twilio Account SID (AC...)
  TWILIO_AUTH_TOKEN              -- Twilio Auth Token
  TWILIO_FROM_NUMBER             -- Twilio phone number (+12395550178)
  TWILIO_MESSAGING_SERVICE_SID   -- Optional: Messaging Service SID (MG...)
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class TwilioService:
    """Async Twilio SMS client with MongoDB-backed reminder scheduling."""

    def __init__(
        self,
        account_sid: Optional[str] = None,
        auth_token: Optional[str] = None,
        from_number: Optional[str] = None,
        messaging_service_sid: Optional[str] = None,
    ):
        self.sid = account_sid or os.getenv("TWILIO_ACCOUNT_SID", "")
        self.token = auth_token or os.getenv("TWILIO_AUTH_TOKEN", "")
        self.from_number = from_number or os.getenv("TWILIO_FROM_NUMBER", "")
        self.messaging_service_sid = (
            messaging_service_sid or os.getenv("TWILIO_MESSAGING_SERVICE_SID", "")
        )
        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}"

    def _is_configured(self) -> bool:
        """Return True if Twilio credentials are present."""
        return bool(self.sid and self.token and (self.from_number or self.messaging_service_sid))

    async def send_sms(self, to: str, body: str) -> dict:
        """
        Send an SMS via Twilio REST API.

        Args:
            to:   Destination phone number in E.164 format (+12395550100).
            body: Message text (max 1600 chars; long messages are split by Twilio).

        Returns:
            Twilio message resource dict on success.

        Raises:
            RuntimeError: If Twilio credentials are not configured.
            httpx.HTTPStatusError: On 4xx/5xx from Twilio.
        """
        if not self._is_configured():
            raise RuntimeError(
                "Twilio credentials not configured. "
                "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER."
            )

        url = f"{self.base_url}/Messages.json"
        payload: dict = {"To": to, "Body": body}

        # Prefer Messaging Service SID (enables carrier lookup + smart encoding)
        if self.messaging_service_sid:
            payload["MessagingServiceSid"] = self.messaging_service_sid
        else:
            payload["From"] = self.from_number

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                auth=(self.sid, self.token),
                data=payload,
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(
                "[twilio] SMS sent to ...%s | SID=%s | status=%s",
                to[-4:],
                result.get("sid"),
                result.get("status"),
            )
            return result

    async def schedule_court_reminders(
        self,
        defendant_name: str,
        phone: str,
        court_date: str,
        court_location: str,
        case_number: str,
        booking_number: str = "",
        indemnitor_phones: Optional[list] = None,
        db=None,
    ) -> list:
        """
        Schedule a 4-touch court reminder sequence and persist to MongoDB.

        Touch points:
          * 7 days before  -- "you have court in 7 days"
          * 3 days before  -- "you have court in 3 days"
          * 1 day before   -- "you have court TOMORROW"
          * Morning of     -- "you have court TODAY" (sent at 6:00 AM)

        All reminders are written to the ``court_reminders`` collection with
        ``status="pending"``. The CourtReminderService cron picks them up and
        delivers via BlueBubbles (iMessage) or Twilio SMS fallback.

        Args:
            defendant_name:    Full name of the defendant.
            phone:             Defendant phone in E.164 or local format.
            court_date:        ISO 8601 court date string.
            court_location:    County or courthouse name.
            case_number:       Court case number.
            booking_number:    Jail booking number (for dedup / cross-reference).
            indemnitor_phones: Additional phones to receive the same reminders.
            db:                Optional Motor AsyncIOMotorDatabase instance.
                               If None, uses get_collection() from extensions.

        Returns:
            List of inserted reminder dicts (serialized, without _id).
        """
        try:
            c_date = datetime.fromisoformat(court_date.replace("Z", "+00:00"))
        except (ValueError, TypeError) as exc:
            logger.error("[twilio] Invalid court_date %r: %s", court_date, exc)
            return []

        if c_date.tzinfo is None:
            c_date = c_date.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        all_phones = [phone] + (indemnitor_phones or [])
        seen: set = set()
        unique_phones = []
        for p in all_phones:
            p = p.strip()
            if p and p not in seen:
                seen.add(p)
                unique_phones.append(p)

        date_str = c_date.strftime("%m/%d/%Y")
        first_name = (
            defendant_name.split(",")[0].strip()
            if "," in defendant_name
            else defendant_name.split()[0]
        )

        touch_points = [
            {
                "offset_label": "7d",
                "send_at": c_date - timedelta(days=7),
                "when": "in 7 days",
            },
            {
                "offset_label": "3d",
                "send_at": c_date - timedelta(days=3),
                "when": "in 3 days",
            },
            {
                "offset_label": "1d",
                "send_at": c_date - timedelta(days=1),
                "when": "TOMORROW",
            },
            {
                "offset_label": "morning",
                "send_at": c_date.replace(hour=6, minute=0, second=0, microsecond=0),
                "when": "TODAY",
            },
        ]

        reminders = []
        for tp in touch_points:
            send_at = tp["send_at"]
            if send_at <= now:
                logger.debug(
                    "[twilio] Skipping %s reminder for %s -- in the past",
                    tp["offset_label"],
                    defendant_name,
                )
                continue

            message = (
                f"SHAMROCK BAIL BONDS ALERT: {first_name}, you have court {tp['when']} "
                f"({date_str}) at {court_location} County (Case: {case_number}). "
                "Please arrive early and dress appropriately. "
                "Questions? Call/text 239-332-2245"
            )

            for recipient_phone in unique_phones:
                reminders.append({
                    "type": "court_reminder",
                    "offset_label": tp["offset_label"],
                    "booking_number": booking_number,
                    "defendant_name": defendant_name,
                    "phone": recipient_phone,
                    "court_date": c_date.isoformat(),
                    "court_location": court_location,
                    "case_number": case_number,
                    "message": message,
                    "send_at": send_at,
                    "status": "pending",
                    "channel": "imessage",
                    "created_at": now,
                    "sent_at": None,
                    "error": None,
                })

        if not reminders:
            logger.info(
                "[twilio] No future reminders to schedule for %s (court %s)",
                defendant_name,
                date_str,
            )
            return []

        try:
            if db is not None:
                collection = db["court_reminders"]
            else:
                from dashboard.extensions import get_collection
                collection = get_collection("court_reminders")

            await collection.insert_many(reminders)
            logger.info(
                "[twilio] Scheduled %d court reminders for %s (booking: %s)",
                len(reminders),
                defendant_name,
                booking_number or "N/A",
            )
        except Exception as exc:
            logger.error(
                "[twilio] Failed to persist court reminders for %s: %s",
                defendant_name,
                exc,
            )
            return reminders

        return [
            {k: v.isoformat() if isinstance(v, datetime) else v for k, v in r.items()}
            for r in reminders
        ]

    async def get_reminder_status(self, booking_number: str, db=None) -> list:
        """Retrieve all scheduled/sent reminders for a booking number."""
        try:
            if db is not None:
                collection = db["court_reminders"]
            else:
                from dashboard.extensions import get_collection
                collection = get_collection("court_reminders")

            cursor = (
                collection.find({"booking_number": booking_number}, {"_id": 0})
                .sort("send_at", 1)
            )
            results = []
            async for doc in cursor:
                for k, v in doc.items():
                    if isinstance(v, datetime):
                        doc[k] = v.isoformat()
                results.append(doc)
            return results
        except Exception as exc:
            logger.error(
                "[twilio] Failed to fetch reminder status for %s: %s",
                booking_number,
                exc,
            )
            return []

    async def cancel_reminders(self, booking_number: str, db=None) -> int:
        """
        Cancel all pending reminders for a booking number (e.g., on exoneration).

        Returns:
            Number of reminders cancelled.
        """
        try:
            if db is not None:
                collection = db["court_reminders"]
            else:
                from dashboard.extensions import get_collection
                collection = get_collection("court_reminders")

            result = await collection.update_many(
                {"booking_number": booking_number, "status": "pending"},
                {"$set": {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc)}},
            )
            count = result.modified_count
            if count > 0:
                logger.info(
                    "[twilio] Cancelled %d pending reminders for booking %s",
                    count,
                    booking_number,
                )
            return count
        except Exception as exc:
            logger.error(
                "[twilio] Failed to cancel reminders for %s: %s",
                booking_number,
                exc,
            )
            return 0
