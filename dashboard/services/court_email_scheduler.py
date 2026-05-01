"""
ShamrockLeads — Court Email Automation Scheduler
==================================================
Cron job that polls Gmail for court emails every 15 minutes:
  1. Fetch unread court emails via GmailReaderService
  2. Parse with CourtEmailProcessor
  3. Create Calendar events (with dedup)
  4. Send iMessage notifications via BlueBubbles
  5. Alert Slack on forfeitures/discharges
  6. Log everything to MongoDB court_email_log

This replaces manual email monitoring with a fully automated pipeline.

Usage:
    from dashboard.services.court_email_scheduler import CourtEmailScheduler
    scheduler = CourtEmailScheduler(db)
    scheduler.start()  # Starts the 15-min cron
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class CourtEmailScheduler:
    """
    Orchestrates the Gmail → Parse → Calendar → BlueBubbles pipeline.
    Designed to be called on a cron schedule (every 15 minutes).
    """

    # Slack webhooks for different event types
    SLACK_ROUTES = {
        "courtDate": "SLACK_WEBHOOK_ARRESTS",     # Court dates → #new-arrests
        "forfeiture": "SLACK_WEBHOOK_ERRORS",      # Forfeitures → #scraper-errors (high priority)
        "discharge": "SLACK_WEBHOOK_LEADS",        # Discharges → #leads
    }

    def __init__(self, db=None):
        """
        Args:
            db: PyMongo database instance for logging processed emails.
        """
        self._db = db
        self._log_collection = db["court_email_log"] if db is not None else None
        self._scheduler = None

    def process_all(self) -> Dict[str, Any]:
        """
        Run one cycle of the email processing pipeline.
        Called by the cron scheduler or manually via API.

        Returns:
            Summary dict with counts of processed/skipped/errored emails.
        """
        import os

        stats = {
            "processed": 0,
            "skipped_duplicate": 0,
            "errors": 0,
            "calendar_events_created": 0,
            "messages_sent": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # ── Step 1: Fetch emails ──
        try:
            from dashboard.services.gmail_reader import GmailReaderService
            reader = GmailReaderService()

            if not reader.is_configured:
                logger.warning("[CourtEmailScheduler] Gmail not configured — skipping")
                stats["error"] = "gmail_not_configured"
                return stats

            emails = reader.fetch_unread_court_emails(since_hours=1)
            if not emails:
                logger.info("[CourtEmailScheduler] No new court emails")
                return stats

            logger.info("[CourtEmailScheduler] Processing %d emails", len(emails))

        except Exception as e:
            logger.error("[CourtEmailScheduler] Gmail fetch failed: %s", e)
            stats["errors"] += 1
            stats["error"] = str(e)
            return stats

        # ── Step 2: Process each email ──
        from dashboard.services.court_email_processor import CourtEmailProcessor

        for email_data in emails:
            try:
                msg_id = email_data["message_id"]

                # Dedup check — skip if already processed
                if self._is_duplicate(msg_id):
                    stats["skipped_duplicate"] += 1
                    continue

                # Parse the email
                parsed = CourtEmailProcessor.process_email(
                    subject=email_data["subject"],
                    body=email_data["body"],
                    sender=email_data["sender"],
                )

                event_type = parsed.get("event_type", "unknown")
                case_number = parsed.get("case_number")

                # ── Step 3: Calendar event ──
                if case_number and parsed.get("datetime_info"):
                    try:
                        from dashboard.services.google_calendar_service import GoogleCalendarService
                        cal_svc = GoogleCalendarService()
                        event = cal_svc.create_event(parsed)
                        if event:
                            stats["calendar_events_created"] += 1
                    except Exception as cal_err:
                        logger.warning("[CourtEmailScheduler] Calendar event failed: %s", cal_err)

                # ── Step 4: Slack notification ──
                self._notify_slack(event_type, parsed)

                # ── Step 5: BlueBubbles notification (defendant + indemnitor) ──
                if case_number:
                    try:
                        sms_text = CourtEmailProcessor.generate_sms_summary(parsed)
                        if sms_text:
                            # Look up ALL relevant phone numbers
                            phones = self._find_notification_phones(
                                parsed.get("defendant_name"),
                                case_number,
                            )
                            for phone in phones:
                                self._send_bb_notification(phone, sms_text)
                                stats["messages_sent"] += 1
                    except Exception as bb_err:
                        logger.warning("[CourtEmailScheduler] BB notification failed: %s", bb_err)

                # ── Step 6: Log to MongoDB ──
                self._log_processed_email(msg_id, email_data, parsed)

                # ── Step 7: Mark as read ──
                try:
                    reader.mark_as_read(msg_id)
                except Exception:
                    pass

                stats["processed"] += 1

            except Exception as e:
                logger.error("[CourtEmailScheduler] Failed to process email: %s", e)
                stats["errors"] += 1

        logger.info(
            "[CourtEmailScheduler] ✅ Cycle complete — %d processed, %d dupes, %d errors",
            stats["processed"], stats["skipped_duplicate"], stats["errors"],
        )
        return stats

    def _is_duplicate(self, message_id: str) -> bool:
        """Check if a Gmail message ID has already been processed."""
        if not self._log_collection:
            return False
        return self._log_collection.find_one({"message_id": message_id}) is not None

    def _log_processed_email(
        self, message_id: str, raw_email: Dict, parsed: Dict
    ):
        """Write processed email to court_email_log collection."""
        if not self._log_collection:
            return

        try:
            self._log_collection.insert_one({
                "message_id": message_id,
                "subject": raw_email.get("subject", ""),
                "sender": raw_email.get("sender", ""),
                "received_at": raw_email.get("received_at"),
                "event_type": parsed.get("event_type"),
                "case_number": parsed.get("case_number"),
                "defendant_name": parsed.get("defendant_name"),
                "datetime_info": parsed.get("datetime_info"),
                "processed_at": datetime.now(timezone.utc),
            })
        except Exception as e:
            logger.error("[CourtEmailScheduler] Log write failed: %s", e)

    def _find_notification_phones(self, name: Optional[str], case_number: str) -> List[str]:
        """
        Look up phone numbers for BOTH the defendant and indemnitor.
        Returns deduplicated list of phone numbers to notify.
        """
        if not self._db:
            return []

        phones = set()

        try:
            # Defendant phone — try case_number match first, then name
            if case_number:
                record = self._db["arrests"].find_one(
                    {"case_number": case_number, "phone": {"$exists": True, "$ne": ""}},
                    {"phone": 1},
                )
                if record and record.get("phone"):
                    phones.add(record["phone"])

            if name:
                record = self._db["defendants"].find_one(
                    {"full_name": {"$regex": name, "$options": "i"}, "phone": {"$exists": True, "$ne": ""}},
                    {"phone": 1},
                )
                if record and record.get("phone"):
                    phones.add(record["phone"])

            # Indemnitor phone — look up via case/bond linkage
            if case_number:
                # Try indemnitors collection (linked by case_number)
                indem = self._db["indemnitors"].find_one(
                    {"case_number": case_number, "phone": {"$exists": True, "$ne": ""}},
                    {"phone": 1},
                )
                if indem and indem.get("phone"):
                    phones.add(indem["phone"])

                # Also check bonded_cases for indemnitor_phone
                bond_case = self._db["bonded_cases"].find_one(
                    {"case_number": case_number, "indemnitor_phone": {"$exists": True, "$ne": ""}},
                    {"indemnitor_phone": 1},
                )
                if bond_case and bond_case.get("indemnitor_phone"):
                    phones.add(bond_case["indemnitor_phone"])

        except Exception as e:
            logger.debug("[CourtEmailScheduler] Phone lookup failed: %s", e)

        return list(phones)

    def _send_bb_notification(self, phone: str, message: str):
        """Send an iMessage/SMS via BlueBubbles. Works in both sync and async contexts."""
        try:
            import asyncio
            from dashboard.services.bb_client import send_message_universal

            try:
                loop = asyncio.get_running_loop()
                # Inside a running loop (Quart) — schedule as a task
                asyncio.ensure_future(self._async_send_bb(phone, message))
            except RuntimeError:
                # No running loop (cron/APScheduler) — create one
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(send_message_universal(phone, message))
                    if result.get("success"):
                        logger.info("[CourtEmailScheduler] ✅ BB sent to ...%s", phone[-4:])
                    else:
                        logger.warning("[CourtEmailScheduler] BB failed: %s", result.get("error"))
                finally:
                    loop.close()
        except Exception as e:
            logger.error("[CourtEmailScheduler] BB send exception: %s", e)

    async def _async_send_bb(self, phone: str, message: str):
        """Async helper for BB sends when already inside an event loop."""
        try:
            from dashboard.services.bb_client import send_message_universal
            result = await send_message_universal(phone, message)
            if result.get("success"):
                logger.info("[CourtEmailScheduler] ✅ BB sent to ...%s", phone[-4:])
            else:
                logger.warning("[CourtEmailScheduler] BB failed: %s", result.get("error"))
        except Exception as e:
            logger.error("[CourtEmailScheduler] Async BB failed: %s", e)

    def _notify_slack(self, event_type: str, parsed: Dict):
        """Send a Slack alert based on event type."""
        import os
        import requests as http_requests

        webhook_var = self.SLACK_ROUTES.get(event_type)
        if not webhook_var:
            return

        webhook_url = os.getenv(webhook_var, "")
        if not webhook_url:
            return

        emoji_map = {
            "courtDate": "⚖️",
            "forfeiture": "🔴",
            "discharge": "🟢",
        }
        emoji = emoji_map.get(event_type, "📧")

        case_number = parsed.get("case_number", "N/A")
        defendant = parsed.get("defendant_name", "Unknown")
        date_info = parsed.get("datetime_info", {})
        date_str = date_info.get("date_str", "TBD") if date_info else "TBD"

        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{emoji} {event_type.upper()} — {case_number}",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Defendant:* {defendant}"},
                        {"type": "mrkdwn", "text": f"*Case:* {case_number}"},
                        {"type": "mrkdwn", "text": f"*Date:* {date_str}"},
                        {"type": "mrkdwn", "text": f"*Source:* {parsed.get('sender', 'email')}"},
                    ],
                },
                {
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": f"_CourtEmailScheduler • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
                    }],
                },
            ],
        }

        try:
            http_requests.post(webhook_url, json=payload, timeout=5)
        except Exception:
            pass
