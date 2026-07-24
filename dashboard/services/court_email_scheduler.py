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

                # ── Step 4.5: Auto-exonerate bond on discharge ──────────────
                # When a discharge email arrives from the court, we automatically:
                #   1. Mark the bond as exonerated in active_bonds
                #   2. Cancel pending geo_pings tokens (stop location tracking)
                #   3. Cancel pending court reminders / check-in messages
                #   4. Write an audit event
                # This is the critical link between court email → tracking stop.
                if event_type == "discharge" and case_number:
                    try:
                        exon_count = self._auto_exonerate_bond(
                            case_number=case_number,
                            defendant_name=parsed.get("defendant_name"),
                            note=(
                                f"Discharge email received: "
                                f"{email_data.get('subject', '')} "
                                f"from {email_data.get('sender', 'court')}"
                            ),
                        )
                        stats["bonds_exonerated"] = stats.get("bonds_exonerated", 0) + exon_count
                        if exon_count:
                            logger.info(
                                "[CourtEmailScheduler] ✅ Auto-exonerated %d bond(s) for case %s",
                                exon_count, case_number,
                            )
                    except Exception as exon_err:
                        logger.warning(
                            "[CourtEmailScheduler] Auto-exonerate failed for %s: %s",
                            case_number, exon_err,
                        )

                # ── Step 5: Notify defendant + indemnitor (email + BlueBubbles) ──
                if case_number or parsed.get("defendant_name"):
                    try:
                        sms_text = CourtEmailProcessor.generate_sms_summary(parsed)
                        contacts = self._find_notification_contacts(
                            parsed.get("defendant_name"),
                            case_number,
                        )
                        # Email court date / forfeiture / discharge notices
                        email_stats = self._send_court_emails(parsed, contacts, event_type)
                        stats["emails_sent"] = stats.get("emails_sent", 0) + email_stats

                        # BlueBubbles iMessage / SMS when phones on file
                        if sms_text:
                            for phone in contacts.get("phones") or []:
                                self._send_bb_notification(phone, sms_text)
                                stats["messages_sent"] += 1

                        # Schedule multi-touch court reminders on calendar court dates
                        if event_type == "courtDate" and parsed.get("datetime_info"):
                            self._schedule_court_reminders(parsed, contacts)
                    except Exception as notify_err:
                        logger.warning(
                            "[CourtEmailScheduler] Client notification failed: %s", notify_err
                        )

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

    def process_single_message(self, message_id: str) -> Dict[str, Any]:
        """
        Process a single court email immediately upon Webhook receipt.

        Args:
            message_id: Gmail message ID from Pub/Sub webhook notification.

        Returns:
            Dict summarizing outcome.
        """
        result = {
            "message_id": message_id,
            "processed": False,
            "duplicate": False,
            "event_type": None,
            "calendar_event": False,
            "messages_sent": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if self._is_duplicate(message_id):
            result["duplicate"] = True
            logger.info("[CourtEmailScheduler] Single msg %s skipped — duplicate", message_id)
            return result

        try:
            from dashboard.services.gmail_reader import GmailReaderService
            reader = GmailReaderService()

            if not reader.is_configured:
                result["error"] = "gmail_not_configured"
                return result

            email_data = reader.get_message_details(message_id)
            if not email_data:
                result["error"] = "message_not_found"
                return result

            from dashboard.services.court_email_processor import CourtEmailProcessor
            parsed = CourtEmailProcessor.process_email(
                subject=email_data["subject"],
                body=email_data["body"],
                sender=email_data["sender"],
            )

            event_type = parsed.get("event_type", "unknown")
            case_number = parsed.get("case_number")
            result["event_type"] = event_type

            # Calendar event
            if case_number and parsed.get("datetime_info"):
                try:
                    from dashboard.services.google_calendar_service import GoogleCalendarService
                    cal_svc = GoogleCalendarService()
                    event = cal_svc.create_event(parsed)
                    if event:
                        result["calendar_event"] = True
                except Exception as cal_err:
                    logger.warning("[CourtEmailScheduler] Calendar event failed: %s", cal_err)

            # Slack notification
            self._notify_slack(event_type, parsed)

            # Auto-exonerate if discharge
            if event_type == "discharge" and case_number:
                try:
                    exon_count = self._auto_exonerate_bond(
                        case_number=case_number,
                        defendant_name=parsed.get("defendant_name"),
                        note=f"Real-time webhook discharge email: {email_data.get('subject', '')}",
                    )
                    result["bonds_exonerated"] = exon_count
                except Exception as exon_err:
                    logger.warning("[CourtEmailScheduler] Auto-exonerate failed: %s", exon_err)

            # Notify contacts & schedule reminders
            if case_number or parsed.get("defendant_name"):
                try:
                    sms_text = CourtEmailProcessor.generate_sms_summary(parsed)
                    contacts = self._find_notification_contacts(
                        parsed.get("defendant_name"),
                        case_number,
                    )
                    email_stats = self._send_court_emails(parsed, contacts, event_type)
                    result["emails_sent"] = email_stats

                    if sms_text:
                        for phone in contacts.get("phones") or []:
                            self._send_bb_notification(phone, sms_text)
                            result["messages_sent"] += 1

                    if event_type == "courtDate" and parsed.get("datetime_info"):
                        self._schedule_court_reminders(parsed, contacts)
                except Exception as notify_err:
                    logger.warning("[CourtEmailScheduler] Client notification failed: %s", notify_err)

            # Log to MongoDB
            self._log_processed_email(message_id, email_data, parsed)

            # Mark as read
            try:
                reader.mark_as_read(message_id)
            except Exception:
                pass

            result["processed"] = True
            logger.info("[CourtEmailScheduler] ✅ Single msg %s processed successfully (%s)", message_id, event_type)

        except Exception as e:
            logger.error("[CourtEmailScheduler] Failed processing msg %s: %s", message_id, e)
            result["error"] = str(e)

        return result


    def _is_duplicate(self, message_id: str) -> bool:
        """Check if a Gmail message ID has already been processed."""
        if self._log_collection is None:
            return False
        return self._log_collection.find_one({"message_id": message_id}) is not None

    def _log_processed_email(
        self, message_id: str, raw_email: Dict, parsed: Dict
    ):
        """Write processed email to court_email_log collection."""
        if self._log_collection is None:
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
        """Backward-compatible phone-only helper."""
        return self._find_notification_contacts(name, case_number).get("phones") or []

    def _find_notification_contacts(
        self, name: Optional[str], case_number: str
    ) -> Dict[str, List[str]]:
        """
        Look up phones + emails for defendant and indemnitor.
        Returns {"phones": [...], "emails": [...], "defendant_phones": [], "indemnitor_phones": []}.
        """
        empty = {
            "phones": [],
            "emails": [],
            "defendant_phones": [],
            "indemnitor_phones": [],
            "defendant_emails": [],
            "indemnitor_emails": [],
        }
        if self._db is None:
            return empty

        phones: set = set()
        emails: set = set()
        def_phones: set = set()
        ind_phones: set = set()
        def_emails: set = set()
        ind_emails: set = set()

        def _add_phone(bucket: set, val: Optional[str]):
            if val and str(val).strip():
                bucket.add(str(val).strip())
                phones.add(str(val).strip())

        def _add_email(bucket: set, val: Optional[str]):
            v = (val or "").strip().lower()
            if v and "@" in v and "shamrock" not in v:  # never spam our own inbox as client
                bucket.add(v)
                emails.add(v)

        try:
            if case_number:
                for coll_name in ("active_bonds", "bonds", "bonded_cases", "arrests"):
                    try:
                        rec = self._db[coll_name].find_one(
                            {"$or": [
                                {"case_number": case_number},
                                {"case_no": case_number},
                            ]},
                        )
                    except Exception:
                        rec = None
                    if not rec:
                        continue
                    _add_phone(def_phones, rec.get("phone") or rec.get("defendant_phone"))
                    _add_phone(ind_phones, rec.get("indemnitor_phone") or rec.get("cosigner_phone"))
                    _add_email(def_emails, rec.get("email") or rec.get("defendant_email"))
                    _add_email(ind_emails, rec.get("indemnitor_email") or rec.get("cosigner_email"))

                indem = self._db["indemnitors"].find_one(
                    {"case_number": case_number},
                )
                if indem:
                    _add_phone(ind_phones, indem.get("phone"))
                    _add_email(ind_emails, indem.get("email"))

            if name:
                record = self._db["defendants"].find_one(
                    {"full_name": {"$regex": name, "$options": "i"}},
                )
                if record:
                    _add_phone(def_phones, record.get("phone"))
                    _add_email(def_emails, record.get("email"))

        except Exception as e:
            logger.debug("[CourtEmailScheduler] Contact lookup failed: %s", e)

        return {
            "phones": list(phones),
            "emails": list(emails),
            "defendant_phones": list(def_phones),
            "indemnitor_phones": list(ind_phones),
            "defendant_emails": list(def_emails),
            "indemnitor_emails": list(ind_emails),
        }

    def _send_court_emails(
        self, parsed: Dict, contacts: Dict[str, List[str]], event_type: str
    ) -> int:
        """Email defendant + indemnitor about court events. Returns # sent."""
        try:
            from dashboard.services.gmail_reader import GmailReaderService
            reader = GmailReaderService()
            if not reader.is_configured:
                return 0
        except Exception:
            return 0

        defendant = parsed.get("defendant_name") or "the defendant"
        case_number = parsed.get("case_number") or "N/A"
        datetime_val = parsed.get("datetime_info")
        if isinstance(datetime_val, dict):
            date_str = datetime_val.get("date_str") or "TBD"
            time_str = datetime_val.get("time_str") or ""
        elif isinstance(datetime_val, str):
            date_str = datetime_val
            time_str = ""
        else:
            date_str = "TBD"
            time_str = ""
        when = f"{date_str}" + (f" at {time_str}" if time_str else "")
        location = parsed.get("location") or "See court notice"
        county = parsed.get("county") or ""

        if event_type == "courtDate":
            subject = f"Court Date Notice — {defendant} ({case_number})"
            body = (
                f"Shamrock Bail Bonds — Court Date Notice\n\n"
                f"Defendant: {defendant}\n"
                f"Case: {case_number}\n"
                f"County: {county or 'Florida'}\n"
                f"When: {when}\n"
                f"Where: {location}\n\n"
                f"Please appear on time. Failure to appear may result in bond forfeiture "
                f"and a warrant for arrest.\n\n"
                f"Questions? Call (239) 332-2245 or reply to this email.\n"
                f"— Shamrock Bail Bonds\n"
                f"1528 Broadway, Ft. Myers, FL 33901\n"
            )
            html = (
                f"<div style='font-family:Calibri,Arial,sans-serif;color:#0f172a'>"
                f"<h2 style='color:#0B3D2E'>☘️ Court Date Notice</h2>"
                f"<p><b>Defendant:</b> {defendant}<br>"
                f"<b>Case:</b> {case_number}<br>"
                f"<b>When:</b> {when}<br>"
                f"<b>Where:</b> {location}</p>"
                f"<p style='color:#b91c1c'><b>Important:</b> Failure to appear may result in "
                f"bond forfeiture and a warrant.</p>"
                f"<p>Questions? Call <b>(239) 332-2245</b>.</p>"
                f"<p style='color:#64748b;font-size:12px'>Shamrock Bail Bonds · 1528 Broadway, Ft. Myers, FL</p>"
                f"</div>"
            )
        elif event_type == "forfeiture":
            subject = f"URGENT — Bond Forfeiture Notice — {defendant} ({case_number})"
            body = (
                f"URGENT — Shamrock Bail Bonds\n\n"
                f"A bond forfeiture notice was received for {defendant}, case {case_number}.\n"
                f"Please contact us immediately at (239) 332-2245.\n"
            )
            html = None
        elif event_type == "discharge":
            subject = f"Bond Discharge — {defendant} ({case_number})"
            body = (
                f"Shamrock Bail Bonds\n\n"
                f"Good news: a discharge/exoneration notice was received for {defendant}, "
                f"case {case_number}. Your bond obligation may be released pending final confirmation.\n"
                f"Call (239) 332-2245 with questions.\n"
            )
            html = None
        else:
            return 0

        for addr in contacts.get("emails") or []:
            res = reader.send_email(addr, subject, body, body_html=html, reply_to="admin@shamrockbailbonds.biz")
            if (isinstance(res, dict) and res.get("success")) or (isinstance(res, bool) and res) or (isinstance(res, str) and ("sent" in res.lower() or "success" in res.lower())):
                sent += 1
        return sent

    @staticmethod
    def _parse_court_datetime(date_str: str, time_str: str = "09:00 AM") -> Optional[datetime]:
        """
        Parse court-email date/time into a timezone-aware datetime.

        CourtEmailProcessor yields e.g. date_str='05/15/2026', time_str='09:00 AM'.
        Delegates to parse_court_date_string (shared with CourtReminderService).
        """
        if not date_str:
            return None
        from dashboard.services.court_reminder_service import parse_court_date_string

        combined = f"{date_str.strip()} {(time_str or '09:00 AM').strip()}".strip()
        return parse_court_date_string(combined)

    def _schedule_court_reminders(self, parsed: Dict, contacts: Dict[str, List[str]]):
        """Best-effort schedule of BlueBubbles multi-touch court reminders."""
        try:
            import asyncio
            from dashboard.services.court_reminder_service import CourtReminderService

            datetime_val = parsed.get("datetime_info")
            if isinstance(datetime_val, dict):
                date_str = datetime_val.get("date_str")
                time_str = datetime_val.get("time_str") or "09:00 AM"
            elif isinstance(datetime_val, str):
                parts = datetime_val.split(" ", 1)
                date_str = parts[0]
                time_str = parts[1] if len(parts) > 1 else "09:00 AM"
            else:
                date_str = None
                time_str = "09:00 AM"
            if not date_str:
                return

            court_dt = self._parse_court_datetime(date_str, time_str)
            if court_dt is None:
                logger.warning(
                    "[CourtEmailScheduler] Unparseable court date %r %r — skip reminders",
                    date_str, time_str,
                )
                return
            # Always pass ISO so schedule_reminders parsing is deterministic
            court_date_str = court_dt.isoformat()

            phones = contacts.get("defendant_phones") or contacts.get("phones") or []
            if not phones:
                return
            svc = CourtReminderService(db=self._db)

            async def _run():
                await svc.schedule_reminders(
                    booking_number=parsed.get("case_number") or "unknown",
                    defendant_name=parsed.get("defendant_name") or "Defendant",
                    phone=phones[0],
                    court_date_str=court_date_str,
                    court_location=parsed.get("location") or "",
                    case_number=parsed.get("case_number") or "",
                    indemnitor_phones=contacts.get("indemnitor_phones") or [],
                )

            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(_run())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                finally:
                    loop.close()
        except Exception as e:
            logger.debug("[CourtEmailScheduler] schedule reminders skipped: %s", e)

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
                    is_ok = (isinstance(result, dict) and result.get("success")) or (isinstance(result, bool) and result) or (isinstance(result, str) and ("sent" in result.lower() or "success" in result.lower()))
                    if is_ok:
                        logger.info("[CourtEmailScheduler] ✅ BB sent to ...%s", str(phone)[-4:])
                    else:
                        err_msg = result.get("error") if isinstance(result, dict) else str(result)
                        logger.warning("[CourtEmailScheduler] BB failed: %s", err_msg)
                finally:
                    loop.close()
        except Exception as e:
            logger.error("[CourtEmailScheduler] BB send exception: %s", e)

    async def _async_send_bb(self, phone: str, message: str):
        """Async helper for BB sends when already inside an event loop."""
        try:
            from dashboard.services.bb_client import send_message_universal
            result = await send_message_universal(phone, message)
            is_ok = (isinstance(result, dict) and result.get("success")) or (isinstance(result, bool) and result) or (isinstance(result, str) and ("sent" in result.lower() or "success" in result.lower()))
            if is_ok:
                logger.info("[CourtEmailScheduler] ✅ BB sent to ...%s", str(phone)[-4:])
            else:
                err_msg = result.get("error") if isinstance(result, dict) else str(result)
                logger.warning("[CourtEmailScheduler] BB failed: %s", err_msg)
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
        date_info = parsed.get("datetime_info")
        if isinstance(date_info, dict):
            date_str = date_info.get("date_str", "TBD")
        elif isinstance(date_info, str):
            date_str = date_info
        else:
            date_str = "TBD"

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

    def _auto_exonerate_bond(
        self,
        case_number: str,
        defendant_name: Optional[str] = None,
        note: str = "",
    ) -> int:
        """
        Automatically exonerate all active bonds matching a case number.
        Called when a discharge email is received from the court.

        Steps:
          1. Find matching bonds in active_bonds by case_number
          2. Mark each as exonerated, clear tracking flags
          3. Cancel pending geo_pings tokens
          4. Cancel pending court reminders
          5. Write audit log entry

        Returns:
            Number of bonds exonerated (0 if none found).
        """
        if self._db is None:
            logger.warning("[_auto_exonerate_bond] No DB — cannot exonerate")
            return 0

        now = datetime.now(timezone.utc)
        exon_count = 0

        try:
            # Find all active bonds matching this case number
            bonds = list(self._db["active_bonds"].find(
                {
                    "case_number": case_number,
                    "status": {"$in": ["active", "monitoring", "alert"]},
                },
                {"booking_number": 1, "defendant_name": 1, "indemnitor_phone": 1,
                 "indemnitor_name": 1, "county": 1, "_id": 0},
            ))

            if not bonds:
                # Also try booking_number-based lookup if defendant name is known
                if defendant_name:
                    import re
                    pattern = re.compile(re.escape(defendant_name), re.IGNORECASE)
                    bonds = list(self._db["active_bonds"].find(
                        {
                            "defendant_name": {"$regex": pattern},
                            "status": {"$in": ["active", "monitoring", "alert"]},
                        },
                        {"booking_number": 1, "defendant_name": 1, "indemnitor_phone": 1,
                         "indemnitor_name": 1, "county": 1, "_id": 0},
                    ))

            for bond in bonds:
                booking_number = bond.get("booking_number", "")
                if not booking_number:
                    continue

                # 1. Mark bond as exonerated
                self._db["active_bonds"].update_one(
                    {"booking_number": booking_number},
                    {"$set": {
                        "status": "exonerated",
                        "tracking_active": False,
                        "check_in_required": False,
                        "exonerated_at": now.isoformat(),
                        "exoneration_source": "court_email",
                        "exoneration_case_number": case_number,
                        "exoneration_note": note,
                        "updated_at": now,
                    }}
                )

                # 2. Cancel pending geo_pings tokens
                self._db["geo_pings"].update_many(
                    {"booking_number": booking_number,
                     "status": {"$in": ["pending", "captured"]}},
                    {"$set": {"status": "cancelled_exonerated",
                              "cancelled_at": now.isoformat()}},
                )

                # 3. Cancel pending court reminders / check-in messages
                self._db["court_reminders"].update_many(
                    {"booking_number": booking_number,
                     "status": {"$in": ["scheduled", "pending"]}},
                    {"$set": {"status": "cancelled_exonerated",
                              "cancelled_at": now.isoformat()}},
                )

                # 4. Audit log
                self._db["audit_events"].insert_one({
                    "event_type": "bond_exonerated",
                    "entity_id": booking_number,
                    "entity_type": "bond_case",
                    "defendant_name": bond.get("defendant_name", defendant_name or ""),
                    "case_number": case_number,
                    "source": "court_email",
                    "note": note,
                    "exonerated_at": now,
                    "timestamp": now,
                })

                exon_count += 1
                logger.info(
                    "[_auto_exonerate_bond] Exonerated booking %s (%s) — case %s",
                    booking_number,
                    bond.get("defendant_name", "?"),
                    case_number,
                )

        except Exception as e:
            logger.error("[_auto_exonerate_bond] DB error: %s", e)

        return exon_count
