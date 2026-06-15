"""
ShamrockLeads — Task Engine
==============================
Creates, cancels, and schedules compliance tasks for active bonds.

PII Policy:
  - Booking numbers are safe to log (not PII)
  - Never log phone numbers, names, or SSNs here
"""
import logging
from datetime import datetime, timedelta, timezone

from dashboard.extensions import get_db

logger = logging.getLogger(__name__)


class TaskEngine:

    @staticmethod
    async def create_task(
        booking_number: str,
        title: str,
        description: str,
        due_date: datetime,
        task_type: str = "general",
        surety_id: str = "",
    ) -> str:
        """Create a compliance task, idempotent per (booking_number, task_type, status=pending).

        If a pending task of the same type already exists, its due_date and
        description are updated rather than creating a duplicate.

        Args:
            surety_id: Optional insurance company identifier ('osi' | 'palmetto').
                       Stored on the task document so the UI can show the correct
                       surety badge without an extra bond lookup.
        """
        db = get_db()
        due_date_str = due_date.isoformat() if isinstance(due_date, datetime) else due_date

        # Resolve surety_id from the bond if not explicitly provided
        _surety = surety_id
        if not _surety:
            try:
                bond = await db.active_bonds.find_one(
                    {"booking_number": booking_number},
                    {"insurance_company": 1, "surety": 1},
                )
                if bond:
                    raw = (bond.get("insurance_company") or bond.get("surety") or "").lower()
                    _surety = "palmetto" if ("palm" in raw or "psc" in raw) else "osi"
            except Exception:
                pass

        existing = await db.tasks.find_one({
            "booking_number": booking_number,
            "task_type": task_type,
            "status": "pending",
        })
        if existing:
            await db.tasks.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "due_date": due_date_str,
                    "description": description,
                    "title": title,
                    "surety_id": _surety,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
            logger.debug(
                "[TaskEngine] Updated existing %s task for booking %s",
                task_type, booking_number,
            )
            return str(existing["_id"])

        task = {
            "booking_number": booking_number,
            "title": title,
            "description": description,
            "due_date": due_date_str,
            "task_type": task_type,
            "status": "pending",
            "surety_id": _surety,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = await db.tasks.insert_one(task)
        logger.info(
            "[TaskEngine] Created %s task for booking %s (due %s) [surety=%s]",
            task_type, booking_number, due_date_str[:10], _surety,
        )
        return str(result.inserted_id)

    @staticmethod
    async def cancel_pending_tasks(booking_number: str, reason: str = "") -> int:
        """Cancel all pending tasks for a booking. Returns count cancelled."""
        db = get_db()
        result = await db.tasks.update_many(
            {"booking_number": booking_number, "status": "pending"},
            {"$set": {
                "status": "cancelled",
                "cancel_reason": reason,
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        if result.modified_count:
            logger.info(
                "[TaskEngine] Cancelled %d pending tasks for booking %s (%s)",
                result.modified_count, booking_number, reason,
            )
        return result.modified_count

    @staticmethod
    async def schedule_compliance_tasks(booking_number: str) -> None:
        """Schedule the standard compliance task suite for a newly active bond.

        Tasks created:
          1. Initial Check-in (7 days out)
          2. Court Reminder Call (3 days before court date, if known)
          3. 30-Day Check-in (30 days out)
        """
        db = get_db()
        now = datetime.now(timezone.utc)

        # 1. Initial check-in
        await TaskEngine.create_task(
            booking_number=booking_number,
            title="Initial Check-in",
            description="Verify defendant's current address and employment status.",
            due_date=now + timedelta(days=7),
            task_type="check_in",
        )

        # 2. 30-day check-in
        await TaskEngine.create_task(
            booking_number=booking_number,
            title="30-Day Check-in",
            description="Monthly compliance check — confirm address, employment, and court compliance.",
            due_date=now + timedelta(days=30),
            task_type="check_in_30d",
        )

        # 3. Court reminder (if court date is set)
        await TaskEngine.schedule_court_reminder(booking_number)

    @staticmethod
    async def schedule_court_reminder(booking_number: str) -> None:
        """Schedule (or reschedule) a Court Reminder task based on the bond's court_date.

        Safe to call multiple times — idempotent via create_task's upsert logic.
        Called automatically when a court date is updated on the bond record.
        """
        db = get_db()
        bond = await db.active_bonds.find_one({"booking_number": booking_number})
        if not bond or not bond.get("court_date"):
            return

        try:
            court_date_raw = bond["court_date"]
            if isinstance(court_date_raw, str):
                court_date = datetime.fromisoformat(court_date_raw.replace("Z", "+00:00"))
            elif isinstance(court_date_raw, datetime):
                court_date = court_date_raw
            else:
                logger.warning(
                    "[TaskEngine] Unrecognised court_date type for booking %s: %s",
                    booking_number, type(court_date_raw),
                )
                return

            if court_date.tzinfo is None:
                court_date = court_date.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            if court_date <= now:
                logger.debug(
                    "[TaskEngine] Court date is in the past for booking %s — skipping reminder",
                    booking_number,
                )
                return

            # Remind 3 days before; if that's already past, remind in 1 hour
            reminder_date = court_date - timedelta(days=3)
            if reminder_date <= now:
                reminder_date = now + timedelta(hours=1)

            await TaskEngine.create_task(
                booking_number=booking_number,
                title="Court Reminder Call",
                description=(
                    f"Call defendant to remind them of court on "
                    f"{court_date.strftime('%m/%d/%Y')}."
                ),
                due_date=reminder_date,
                task_type="court_reminder",
            )

        except Exception as exc:
            logger.error(
                "[TaskEngine] Error scheduling court reminder for booking %s: %s",
                booking_number, exc,
            )
