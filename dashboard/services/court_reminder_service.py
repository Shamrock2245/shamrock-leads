"""
Court & Payment Reminder Service — BlueBubbles iMessage/SMS
============================================================
Schedules and sends 4-touch court date reminders AND payment plan
delinquency alerts via BlueBubbles (iMessage + SMS/RCS fallback).

Replaces Twilio dependency with the BlueBubbles bridge running on
the office iMac (shamrockbailoffice@gmail.com / 239-955-0178).

Reminder Types:
  - Court Reminders: 7d, 3d, 1d, morning-of for defendants
  - Payment Reminders: 7d, 3d, 1d past-due for indemnitors

Both defendant AND indemnitor phone numbers are resolved from
the bond record and messaged independently.
"""

import os
import secrets
import logging
from datetime import datetime, timedelta, timezone

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

_PUBLIC_URL = os.getenv("DASHBOARD_PUBLIC_URL", "https://shamrockbailbonds.biz")


class CourtReminderService:
    """Schedules and sends reminders via BlueBubbles iMessage/SMS."""

    def __init__(self, db=None):
        self.db = db

    async def schedule_reminders(
        self,
        booking_number: str,
        defendant_name: str,
        phone: str,
        court_date_str: str,
        court_location: str,
        case_number: str,
        indemnitor_phones: list = None,
    ):
        """Schedule 4-touch court date reminder sequence: 7d, 3d, 1d, morning-of.

        Sends to both defendant AND all indemnitor phone numbers on file.
        """
        try:
            court_reminders = get_collection("court_reminders")
            court_date = datetime.fromisoformat(
                court_date_str.replace("Z", "+00:00")
            )
            if court_date.tzinfo is None:
                court_date = court_date.replace(tzinfo=timezone.utc)

            now_iso = datetime.now(timezone.utc).isoformat()
            date_str = court_date.strftime("%m/%d/%Y")

            # Build all recipient phone numbers
            all_phones = []
            if phone:
                all_phones.append({"phone": phone, "role": "defendant"})
            for ip in (indemnitor_phones or []):
                if ip and ip != phone:
                    all_phones.append({"phone": ip, "role": "indemnitor"})

            # If no indemnitor phones provided, try to look up from bond record
            if not indemnitor_phones:
                ind_phones = await self._lookup_indemnitor_phones(booking_number)
                for ip in ind_phones:
                    if ip and ip != phone:
                        all_phones.append({"phone": ip, "role": "indemnitor"})

            reminders = []
            touches = [
                ("7d", 7, "in 7 days"),
                ("3d", 3, "in 3 days"),
                ("1d", 1, "TOMORROW"),
                ("morning", 0, "TODAY"),
            ]

            for touch_id, days_before, urgency_text in touches:
                if days_before > 0:
                    send_at = (court_date - timedelta(days=days_before)).isoformat()
                else:
                    send_at = court_date.replace(
                        hour=6, minute=0, second=0, microsecond=0
                    ).isoformat()

                for recipient in all_phones:
                    # Generate a unique geolocator link per reminder (mandatory project requirement)
                    geo_token = secrets.token_urlsafe(12)
                    geo_url = f"{_PUBLIC_URL.rstrip('/')}/g/{geo_token}"
                    try:
                        geo_col = get_collection("geo_pings")
                        await geo_col.insert_one({
                            "token": geo_token,
                            "booking_number": booking_number,
                            "phone": recipient["phone"],
                            "recipient": f"court_reminder_{touch_id}_{recipient['role']}",
                            "status": "pending",
                            "ping_count": 0,
                            "pings": [],
                            "created_at": now_iso,
                        })
                    except Exception:
                        geo_url = ""  # non-fatal — message still sends without link

                    msg = self._build_court_message(
                        defendant_name,
                        date_str,
                        court_location,
                        case_number,
                        urgency_text,
                        recipient["role"],
                        geo_url=geo_url,
                    )
                    reminders.append({
                        "booking_number": booking_number,
                        "defendant_name": defendant_name,
                        "phone": recipient["phone"],
                        "recipient_role": recipient["role"],
                        "court_date": court_date_str,
                        "touch": touch_id,
                        "send_at": send_at,
                        "message": msg,
                        "geo_token": geo_token,
                        "status": "pending",
                        "reminder_type": "court",
                        "created_at": now_iso,
                    })

            if reminders:
                await court_reminders.insert_many(reminders)

            return {
                "success": True,
                "scheduled": len(reminders),
                "scheduled_count": len(reminders),
                "recipients": len(all_phones),
            }
        except Exception as e:
            logger.error("[court_reminder] schedule error: %s", e)
            return {"error": str(e)}

    async def schedule_payment_reminders(
        self,
        booking_number: str,
        defendant_name: str,
        amount_due: float,
        due_date_str: str,
        indemnitor_phones: list = None,
        defendant_phone: str = None,
    ):
        """Schedule 3-touch payment delinquency reminders: 1d, 3d, 7d past due.

        Sends to both indemnitor(s) and defendant phone.
        """
        try:
            court_reminders = get_collection("court_reminders")
            due_date = datetime.fromisoformat(
                due_date_str.replace("Z", "+00:00")
            )
            if due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=timezone.utc)

            now_iso = datetime.now(timezone.utc).isoformat()
            date_str = due_date.strftime("%m/%d/%Y")
            amount_str = f"${amount_due:,.2f}"

            # Build all recipient phone numbers
            all_phones = []
            if defendant_phone:
                all_phones.append({"phone": defendant_phone, "role": "defendant"})
            for ip in (indemnitor_phones or []):
                if ip and ip != defendant_phone:
                    all_phones.append({"phone": ip, "role": "indemnitor"})

            # If no phones provided, look up from bond record
            if not all_phones:
                phones = await self._lookup_all_phones(booking_number)
                all_phones = phones

            reminders = []
            touches = [
                ("1d_past", 1, "1 day past due"),
                ("3d_past", 3, "3 days past due"),
                ("7d_past", 7, "7 days past due"),
            ]

            for touch_id, days_after, urgency_text in touches:
                send_at = (due_date + timedelta(days=days_after)).replace(
                    hour=10, minute=0, second=0, microsecond=0
                ).isoformat()

                for recipient in all_phones:
                    msg = self._build_payment_message(
                        defendant_name,
                        amount_str,
                        date_str,
                        urgency_text,
                        recipient["role"],
                    )
                    reminders.append({
                        "booking_number": booking_number,
                        "defendant_name": defendant_name,
                        "phone": recipient["phone"],
                        "recipient_role": recipient["role"],
                        "amount_due": amount_due,
                        "due_date": due_date_str,
                        "touch": touch_id,
                        "send_at": send_at,
                        "message": msg,
                        "status": "pending",
                        "reminder_type": "payment",
                        "created_at": now_iso,
                    })

            if reminders:
                await court_reminders.insert_many(reminders)

            return {
                "success": True,
                "scheduled": len(reminders),
                "scheduled_count": len(reminders),
                "recipients": len(all_phones),
            }
        except Exception as e:
            logger.error("[payment_reminder] schedule error: %s", e)
            return {"error": str(e)}

    async def process_due_reminders(self):
        """Find all reminders where send_at <= now and status == 'pending'.
        Send via BlueBubbles iMessage/SMS."""
        try:
            from dashboard.services.bb_client import send_message_universal

            court_reminders = get_collection("court_reminders")
            now = datetime.now(timezone.utc).isoformat()

            cursor = court_reminders.find(
                {"status": "pending", "send_at": {"$lte": now}}
            )
            due_reminders = await cursor.to_list(length=100)
            sent_count = 0
            failed_count = 0

            for reminder in due_reminders:
                try:
                    result = await send_message_universal(
                        phone=reminder["phone"],
                        message=reminder["message"],
                    )
                    success = result.get("success", False)

                    if success:
                        await court_reminders.update_one(
                            {"_id": reminder["_id"]},
                            {"$set": {
                                "status": "sent",
                                "sent_at": datetime.now(timezone.utc).isoformat(),
                                "bb_response": str(result.get("data", {}))[:500],
                            }},
                        )
                        sent_count += 1
                        logger.info(
                            "[reminder] Sent %s %s to %s (%s)",
                            reminder.get("reminder_type", "court"),
                            reminder["touch"],
                            reminder["phone"],
                            reminder.get("recipient_role", "unknown"),
                        )
                    else:
                        raise Exception(result.get("error", "BB send failed"))

                except Exception as e:
                    await court_reminders.update_one(
                        {"_id": reminder["_id"]},
                        {"$set": {
                            "status": "failed",
                            "error": str(e)[:500],
                            "failed_at": datetime.now(timezone.utc).isoformat(),
                        }},
                    )
                    failed_count += 1
                    logger.error(
                        "[reminder] Failed %s to %s: %s",
                        reminder["touch"],
                        reminder["phone"],
                        e,
                    )

            return {"success": True, "sent": sent_count, "failed": failed_count}
        except Exception as e:
            logger.error("[reminder] process_due error: %s", e)
            return {"error": str(e)}

    # ── Private helpers ──

    async def _lookup_indemnitor_phones(self, booking_number: str) -> list:
        """Look up indemnitor phone numbers from the bond record."""
        phones = []
        try:
            for coll_name in ["prospective_bonds", "active_bonds"]:
                coll = get_collection(coll_name)
                doc = await coll.find_one({"booking_number": booking_number})
                if doc:
                    # Check indemnitors array
                    for ind in doc.get("indemnitors", []):
                        p = ind.get("phone", "")
                        if p and p not in phones:
                            phones.append(p)
                    # Check legacy single indemnitor
                    ind = doc.get("indemnitor", {})
                    if isinstance(ind, dict):
                        p = ind.get("phone", "")
                        if p and p not in phones:
                            phones.append(p)
                    break
        except Exception as e:
            logger.error("[reminder] indemnitor phone lookup error: %s", e)
        return phones

    async def _lookup_all_phones(self, booking_number: str) -> list:
        """Look up all phone numbers (defendant + indemnitors) from bond record."""
        result = []
        try:
            for coll_name in ["prospective_bonds", "active_bonds"]:
                coll = get_collection(coll_name)
                doc = await coll.find_one({"booking_number": booking_number})
                if doc:
                    # Defendant phone
                    dp = doc.get("phone", "")
                    if dp:
                        result.append({"phone": dp, "role": "defendant"})
                    # Indemnitor phones
                    for ind in doc.get("indemnitors", []):
                        p = ind.get("phone", "")
                        if p and p != dp:
                            result.append({"phone": p, "role": "indemnitor"})
                    break
        except Exception as e:
            logger.error("[reminder] all phones lookup error: %s", e)
        return result

    async def cancel_reminders(self, booking_number: str) -> int:
        """
        Cancel all pending court reminders for a booking number.
        Used when a bond is renewed, exonerated, or surrendered.

        Returns:
            Number of reminders cancelled.
        """
        try:
            col = get_collection("court_reminders")
            result = await col.update_many(
                {"booking_number": booking_number, "status": "pending"},
                {"$set": {
                    "status": "cancelled",
                    "cancelled_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
            count = result.modified_count
            if count:
                logger.info("[court_reminder] Cancelled %d pending reminders for %s",
                            count, booking_number)
            return count
        except Exception as exc:
            logger.error("[court_reminder] cancel_reminders error for %s: %s",
                         booking_number, exc)
            return 0

    async def auto_scan_and_schedule(self) -> dict:
        """
        Hourly cron: scan active_bonds for court dates within 8 days,
        auto-schedule 4-touch reminders (7d/3d/1d/morning-of) for each bond
        that doesn't already have pending/sent reminders.

        Edge cases handled:
          - court_date as ISO string, date-only string, datetime, empty/null
          - skips bonds already having pending or sent reminders
          - skips bonds with court_date in the past
        """
        active_bonds = get_collection("active_bonds")
        court_reminders = get_collection("court_reminders")
        now = datetime.now(timezone.utc)
        scan_window_end = now + timedelta(days=8)

        scheduled_count = 0
        skipped_count = 0
        error_count = 0
        scanned_count = 0

        try:
            cursor = active_bonds.find(
                {"status": "active"},
                {"booking_number": 1, "defendant_name": 1, "phone": 1,
                 "court_date": 1, "court_location": 1, "case_number": 1,
                 "county": 1, "indemnitors": 1, "indemnitor_phone": 1}
            )
            async for bond in cursor:
                scanned_count += 1
                booking_number = bond.get("booking_number", "")
                if not booking_number:
                    continue

                # Normalize court_date to timezone-aware datetime
                court_date = None
                raw_cd = bond.get("court_date")
                try:
                    if isinstance(raw_cd, datetime):
                        court_date = raw_cd
                    elif isinstance(raw_cd, str) and raw_cd.strip():
                        cd_str = raw_cd.strip().replace("Z", "+00:00")
                        try:
                            court_date = datetime.fromisoformat(cd_str)
                        except ValueError:
                            court_date = datetime.strptime(raw_cd.strip()[:10], "%Y-%m-%d")
                    if court_date and court_date.tzinfo is None:
                        court_date = court_date.replace(tzinfo=timezone.utc)
                except Exception:
                    continue  # skip records with unparseable dates

                if not court_date:
                    continue
                if court_date < now:
                    continue  # past court date — skip
                if court_date > scan_window_end:
                    continue  # too far out — skip

                # Check for existing pending/sent reminders (dedup)
                existing = await court_reminders.find_one({
                    "booking_number": booking_number,
                    "reminder_type": "court",
                    "status": {"$in": ["scheduled", "pending", "sent"]},
                })
                if existing:
                    skipped_count += 1
                    continue

                # Build indemnitor phone list
                indemnitor_phones = []
                for ind in bond.get("indemnitors", []):
                    p = ind.get("phone", "")
                    if p:
                        indemnitor_phones.append(p)
                legacy = bond.get("indemnitor_phone", "")
                if legacy and legacy not in indemnitor_phones:
                    indemnitor_phones.append(legacy)

                # Schedule 4-touch reminder sequence
                try:
                    result = await self.schedule_reminders(
                        booking_number=booking_number,
                        defendant_name=bond.get("defendant_name", "Defendant"),
                        phone=bond.get("phone", ""),
                        court_date_str=court_date.isoformat(),
                        court_location=bond.get("court_location", bond.get("county", "Lee") + " County"),
                        case_number=bond.get("case_number", ""),
                        indemnitor_phones=indemnitor_phones,
                    )
                    if result.get("success"):
                        scheduled_count += 1
                    else:
                        error_count += 1
                        logger.warning("[auto_scan] Failed for %s: %s", booking_number, result)
                except Exception as inner_e:
                    error_count += 1
                    logger.error("[auto_scan] Error scheduling %s: %s", booking_number, inner_e)

        except Exception as e:
            logger.error("[auto_scan] Scan error: %s", e)
            return {"success": False, "error": str(e)}

        logger.info(
            "[auto_scan] Complete — scanned=%d scheduled=%d skipped=%d errors=%d",
            scanned_count, scheduled_count, skipped_count, error_count
        )
        return {
            "success": True,
            "bonds_scanned": scanned_count,
            "scheduled": scheduled_count,
            "skipped": skipped_count,
            "errors": error_count,
            "scan_window_days": 8,
            "scanned_at": now.isoformat(),
        }

    @staticmethod
    def _build_court_message(
        name: str, date: str, location: str, case: str,
        urgency: str, role: str,
        geo_url: str = "",
    ) -> str:
        """Build court reminder message text. geo_url is mandatory per project rules."""
        geo_line = f"\nConfirm your location: {geo_url}" if geo_url else ""
        if role == "indemnitor":
            return (
                f"SHAMROCK BAIL BONDS — Court Reminder\n\n"
                f"This is a reminder that {name} has court {urgency} "
                f"({date}) at {location} County (Case: {case}).\n\n"
                f"As a co-signer, please ensure they attend. "
                f"Reply for assistance.{geo_line}\n"
                f"☘️ Shamrock Bail Bonds (239) 332-2245"
            )
        return (
            f"SHAMROCK BAIL BONDS — Court Reminder\n\n"
            f"{name}, you have court {urgency} ({date}) at "
            f"{location} County (Case: {case}).\n\n"
            f"Please arrive early and dress appropriately. "
            f"Reply for assistance.{geo_line}\n"
            f"☘️ Shamrock Bail Bonds (239) 332-2245"
        )

    @staticmethod
    def _build_payment_message(
        name: str, amount: str, date: str,
        urgency: str, role: str,
    ) -> str:
        """Build payment reminder message text."""
        if role == "indemnitor":
            return (
                f"SHAMROCK BAIL BONDS — Payment Reminder\n\n"
                f"A payment of {amount} for {name}'s bond was due "
                f"on {date} and is now {urgency}.\n\n"
                f"As a co-signer, you are responsible for this payment. "
                f"Please call 239-230-5962 or reply to arrange payment."
            )
        return (
            f"SHAMROCK BAIL BONDS — Payment Reminder\n\n"
            f"{name}, your bond payment of {amount} was due on "
            f"{date} and is now {urgency}.\n\n"
            f"Please call 239-230-5962 or reply to arrange payment."
        )
