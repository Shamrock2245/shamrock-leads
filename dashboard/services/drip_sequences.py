"""
ShamrockLeads — The Closer: Drip Sequence Definitions
======================================================
Defines the automated follow-up sequences used by "The Closer" to recover
abandoned intakes and convert uncontacted hot leads.

CRITICAL: All outreach is human-gated.
  Every sequence step creates an outreach_queue entry with
  status="pending_approval". No message is sent until a staff member
  explicitly approves it in the dashboard.

Trigger conditions are evaluated by the cron job in cron.py
(_run_drip_scanner, every 30 minutes) which calls
DripSequenceRunner.scan_and_queue().

PII rules:
  - Templates use {first_name}, {defendant_name}, {county} — never phone/SSN.
  - Logging uses last-4 of phone only.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Sequence Definitions
# ─────────────────────────────────────────────────────────────────────────────

SEQUENCES: dict[str, dict] = {
    "abandoned_intake": {
        "trigger": "intake_started but no paperwork_sent within 2 hours",
        "description": "Re-engage leads who started the intake form but never completed paperwork.",
        "steps": [
            {
                "step": 1,
                "delay_hours": 2,
                "template_key": "abandoned_intake_step1",
                "stop_on_reply": True,
            },
            {
                "step": 2,
                "delay_hours": 24,
                "template_key": "abandoned_intake_step2",
                "stop_on_reply": True,
            },
            {
                "step": 3,
                "delay_hours": 72,
                "template_key": "abandoned_intake_step3",
                "stop_on_reply": True,
            },
        ],
        "max_attempts": 3,
        "channel": "imessage",
        "requires_approval": True,
    },
    "hot_lead_first_touch": {
        "trigger": "lead_score >= 80 AND no_prior_contact",
        "description": "Immediate first-touch for high-value leads with no prior outreach.",
        "steps": [
            {
                "step": 1,
                "delay_hours": 0,
                "template_key": "hot_lead_first_touch_step1",
                "stop_on_reply": True,
            },
            {
                "step": 2,
                "delay_hours": 4,
                "template_key": "hot_lead_followup_4h",
                "stop_on_reply": True,
            },
        ],
        "max_attempts": 2,
        "channel": "imessage",
        "requires_approval": True,
    },
    "court_date_reminder": {
        "trigger": "court_date within 72 hours AND bond_status == active",
        "description": "Remind bonded defendants of upcoming court dates.",
        "steps": [
            {
                "step": 1,
                "delay_hours": 0,
                "template_key": "court_reminder_72h",
                "stop_on_reply": False,
            },
            {
                "step": 2,
                "delay_hours": 48,
                "template_key": "court_reminder_24h",
                "stop_on_reply": False,
            },
        ],
        "max_attempts": 2,
        "channel": "imessage",
        "requires_approval": True,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
#  Message Templates
# ─────────────────────────────────────────────────────────────────────────────

DRIP_TEMPLATES: dict[str, str] = {
    "abandoned_intake_step1": (
        "Hi {first_name}! 👋 We noticed you started paperwork for {defendant_name}'s bail bond "
        "but didn't finish. We saved your progress — it only takes a few more minutes.\n\n"
        "Pick up where you left off: {portal_link}\n\n"
        "Questions? Call/text us 24/7: 239-332-2245\n"
        "— Shamrock Bail Bonds 🍀"
    ),
    "abandoned_intake_step2": (
        "Hi {first_name}, just checking in. Your bail bond application for {defendant_name} "
        "is still waiting. We can have them out within hours once paperwork is complete.\n\n"
        "{portal_link}\n239-332-2245"
    ),
    "abandoned_intake_step3": (
        "Hi {first_name}, this is our last follow-up regarding {defendant_name}'s bail bond. "
        "We're here whenever you're ready — no pressure.\n\n"
        "239-332-2245 | shamrockbailbonds.biz"
    ),
    "hot_lead_first_touch_step1": (
        "Hi {first_name}! Shamrock Bail Bonds here 🍀 We can help get {defendant_name} "
        "out of {county} County Jail fast. Most bonds are posted within 2 hours.\n\n"
        "Get started: {portal_link}\n\n"
        "Call/text 24/7: 239-332-2245"
    ),
    "hot_lead_followup_4h": (
        "Hi {first_name}, following up on {defendant_name}'s bail. "
        "We're available right now — reply or call 239-332-2245.\n\n"
        "{portal_link}"
    ),
    "court_reminder_72h": (
        "Hi {first_name}, this is Shamrock Bail Bonds. "
        "Reminder: {defendant_name} has a court date in 3 days. "
        "Missing court can result in bond forfeiture.\n\n"
        "Court date: {court_date}\nCourt: {court_name}\n\n"
        "Questions? 239-332-2245"
    ),
    "court_reminder_24h": (
        "⚠️ Hi {first_name}, TOMORROW is {defendant_name}'s court date.\n\n"
        "Court: {court_name}\nTime: {court_time}\n\n"
        "Please ensure they appear. Missing court will forfeit the bond.\n"
        "239-332-2245 — Shamrock Bail Bonds"
    ),
}


def render_template(template_key: str, context: dict) -> str:
    """Render a drip template with the given context variables.

    Missing variables are replaced with empty strings (fail-safe).
    PII note: context should never include phone/SSN/address.
    """
    template = DRIP_TEMPLATES.get(template_key, "")
    if not template:
        logger.warning("[DripSequences] Unknown template key: %s", template_key)
        return ""
    try:
        return template.format_map({k: (v or "") for k, v in context.items()})
    except KeyError as exc:
        logger.warning("[DripSequences] Template %s missing key: %s", template_key, exc)
        return template


# ─────────────────────────────────────────────────────────────────────────────
#  DripSequenceRunner
# ─────────────────────────────────────────────────────────────────────────────

class DripSequenceRunner:
    """Scans for leads matching drip trigger conditions and queues messages
    for human approval.

    Usage (from cron):
        runner = DripSequenceRunner(db)
        result = await runner.scan_and_queue()
    """

    def __init__(self, db):
        self.db = db

    # ── Collections ───────────────────────────────────────────────────────────

    @property
    def intake_queue(self):
        return self.db["intake_queue"]

    @property
    def outreach_sequences(self):
        return self.db["outreach_sequences"]

    @property
    def outreach_queue(self):
        return self.db["outreach_queue"]

    @property
    def arrests(self):
        return self.db["arrests"]

    @property
    def active_bonds(self):
        return self.db["active_bonds"]

    # ── Main entry point ──────────────────────────────────────────────────────

    async def scan_and_queue(self) -> dict:
        """Scan all trigger conditions and queue pending-approval messages.

        Returns a summary dict with counts per sequence type.
        """
        results: dict[str, int] = {}

        # 1. Abandoned intakes
        count = await self._scan_abandoned_intakes()
        results["abandoned_intake"] = count

        # 2. Hot leads with no prior contact
        count = await self._scan_hot_leads()
        results["hot_lead_first_touch"] = count

        # 3. Court date reminders (72h window)
        count = await self._scan_court_reminders()
        results["court_date_reminder"] = count

        total = sum(results.values())
        if total > 0:
            logger.info(
                "[DripScanner] Queued %d messages for approval: %s",
                total, results,
            )
        return {"queued": total, "by_sequence": results}

    # ── Trigger: Abandoned Intakes ────────────────────────────────────────────

    async def _scan_abandoned_intakes(self) -> int:
        """Find intakes started >2h ago with no paperwork sent."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        queued = 0

        cursor = self.intake_queue.find({
            "status": {"$in": ["new", "pending", "intake_started"]},
            "paperwork_sent": {"$ne": True},
            "created_at": {"$lt": cutoff.isoformat()},
            "opted_out": {"$ne": True},
        })

        async for intake in cursor:
            booking_number = intake.get("booking_number", "")
            county = intake.get("county", "")
            if not booking_number:
                continue

            # Check if already in an active abandoned_intake sequence
            existing = await self.outreach_sequences.find_one({
                "booking_number": booking_number,
                "sequence_type": "abandoned_intake",
                "status": {"$in": ["active", "pending_approval"]},
            })
            if existing:
                continue

            # Get contact phone
            phone = intake.get("indemnitor", {}).get("phone", "") or intake.get("phone", "")
            if not phone:
                continue

            # Check opted-out
            if intake.get("opted_out"):
                continue

            seq_id = await self._create_sequence(
                sequence_type="abandoned_intake",
                booking_number=booking_number,
                county=county,
                phone=phone,
                context={
                    "first_name": intake.get("indemnitor", {}).get("name", "").split()[0] or "there",
                    "defendant_name": intake.get("defendant_name", ""),
                    "county": county,
                    "portal_link": f"https://shamrockbailbonds.biz/intake?bn={booking_number}",
                },
            )
            if seq_id:
                queued += 1

        return queued

    # ── Trigger: Hot Leads ────────────────────────────────────────────────────

    async def _scan_hot_leads(self) -> int:
        """Find arrests with lead_score >= 80 and no prior outreach."""
        queued = 0

        cursor = self.arrests.find({
            "lead_score": {"$gte": 80},
            "shamrock_status": {"$nin": [
                "contacted", "negotiating", "paperwork",
                "ready", "bonded", "closed", "do_not_contact",
            ]},
            "opted_out": {"$ne": True},
        })

        async for arrest in cursor:
            booking_number = arrest.get("booking_number", "")
            county = arrest.get("county", "")
            if not booking_number:
                continue

            # Check for any prior outreach
            prior = await self.outreach_sequences.find_one({
                "booking_number": booking_number,
                "status": {"$in": ["active", "completed", "pending_approval"]},
            })
            if prior:
                continue

            phone = arrest.get("phone", "") or arrest.get("contact_phone", "")
            if not phone:
                continue

            defendant_name = arrest.get("defendant_name", "") or arrest.get("full_name", "")
            seq_id = await self._create_sequence(
                sequence_type="hot_lead_first_touch",
                booking_number=booking_number,
                county=county,
                phone=phone,
                context={
                    "first_name": "there",
                    "defendant_name": defendant_name,
                    "county": county,
                    "portal_link": f"https://shamrockbailbonds.biz/intake?bn={booking_number}",
                },
            )
            if seq_id:
                queued += 1

        return queued

    # ── Trigger: Court Date Reminders ─────────────────────────────────────────

    async def _scan_court_reminders(self) -> int:
        """Find active bonds with court dates within 72 hours."""
        now = datetime.now(timezone.utc)
        window_end = now + timedelta(hours=72)
        queued = 0

        cursor = self.active_bonds.find({
            "status": "active",
            "court_date": {
                "$gte": now.isoformat(),
                "$lte": window_end.isoformat(),
            },
        })

        async for bond in cursor:
            booking_number = bond.get("booking_number", "")
            county = bond.get("county", "")
            if not booking_number:
                continue

            # Check if reminder already queued for this court date
            court_date_str = bond.get("court_date", "")
            existing = await self.outreach_sequences.find_one({
                "booking_number": booking_number,
                "sequence_type": "court_date_reminder",
                "court_date": court_date_str,
                "status": {"$in": ["active", "pending_approval", "completed"]},
            })
            if existing:
                continue

            phone = (bond.get("indemnitor") or {}).get("phone", "") or bond.get("defendant_phone", "")
            if not phone:
                continue

            indemnitor_name = (bond.get("indemnitor") or {}).get("name", "")
            first_name = indemnitor_name.split()[0] if indemnitor_name else "there"

            # Parse court date for display
            try:
                court_dt = datetime.fromisoformat(court_date_str.replace("Z", "+00:00"))
                court_date_display = court_dt.strftime("%A, %B %d at %I:%M %p")
                court_time_display = court_dt.strftime("%I:%M %p")
            except Exception:
                court_date_display = court_date_str
                court_time_display = ""

            seq_id = await self._create_sequence(
                sequence_type="court_date_reminder",
                booking_number=booking_number,
                county=county,
                phone=phone,
                context={
                    "first_name": first_name,
                    "defendant_name": bond.get("defendant_name", ""),
                    "county": county,
                    "court_date": court_date_display,
                    "court_name": bond.get("court_name", "the courthouse"),
                    "court_time": court_time_display,
                    "portal_link": f"https://shamrockbailbonds.biz/intake?bn={booking_number}",
                },
                extra_fields={"court_date": court_date_str},
            )
            if seq_id:
                queued += 1

        return queued

    # ── Internal: Create Sequence + Queue First Step ──────────────────────────

    async def _create_sequence(
        self,
        sequence_type: str,
        booking_number: str,
        county: str,
        phone: str,
        context: dict,
        extra_fields: Optional[dict] = None,
    ) -> Optional[str]:
        """Create a new drip sequence and queue the first step for approval.

        Returns the sequence_id if created, None if skipped.
        """
        seq_def = SEQUENCES.get(sequence_type)
        if not seq_def:
            logger.error("[DripSequences] Unknown sequence type: %s", sequence_type)
            return None

        sequence_id = f"DRIP-{uuid.uuid4().hex[:10].upper()}"
        now = datetime.now(timezone.utc)

        # Build step metadata
        steps_meta = []
        for step_def in seq_def["steps"]:
            send_at = now + timedelta(hours=step_def["delay_hours"])
            message_text = render_template(step_def["template_key"], context)
            steps_meta.append({
                "step": step_def["step"],
                "template_key": step_def["template_key"],
                "delay_hours": step_def["delay_hours"],
                "scheduled_for": send_at.isoformat(),
                "message_text": message_text,
                "status": "pending_approval" if step_def["step"] == 1 else "waiting",
                "stop_on_reply": step_def.get("stop_on_reply", True),
                "queue_id": None,
            })

        seq_doc = {
            "sequence_id": sequence_id,
            "sequence_type": sequence_type,
            "booking_number": booking_number,
            "county": county,
            "phone": phone,
            "status": "pending_approval",
            "created_at": now.isoformat(),
            "steps": steps_meta,
            "current_step": 1,
            "context": context,
            **(extra_fields or {}),
        }

        await self.outreach_sequences.insert_one(seq_doc)

        # Queue step 1 for human approval
        first_step = steps_meta[0]
        queue_id = f"OQ-{uuid.uuid4().hex[:10].upper()}"
        queue_doc = {
            "queue_id": queue_id,
            "sequence_id": sequence_id,
            "sequence_type": sequence_type,
            "booking_number": booking_number,
            "county": county,
            "phone": phone,
            "message_text": first_step["message_text"],
            "template_key": first_step["template_key"],
            "step": 1,
            "status": "pending_approval",
            "created_at": now.isoformat(),
            "scheduled_for": first_step["scheduled_for"],
            "channel": seq_def.get("channel", "imessage"),
            "requires_approval": True,
        }
        await self.outreach_queue.insert_one(queue_doc)

        # Update sequence with queue_id for step 1
        await self.outreach_sequences.update_one(
            {"sequence_id": sequence_id},
            {"$set": {"steps.0.queue_id": queue_id}},
        )

        logger.info(
            "[DripSequences] Created %s sequence %s for booking %s (step 1 queued for approval)",
            sequence_type, sequence_id, booking_number,
        )
        return sequence_id

    # ── Advance Sequence After Approval ──────────────────────────────────────

    async def advance_sequence(self, sequence_id: str, approved_step: int) -> dict:
        """Called after a step is approved and sent.

        Queues the next step for approval (if any).
        """
        seq = await self.outreach_sequences.find_one({"sequence_id": sequence_id})
        if not seq:
            return {"success": False, "error": "Sequence not found"}

        seq_def = SEQUENCES.get(seq["sequence_type"], {})
        steps = seq.get("steps", [])
        next_step_num = approved_step + 1
        next_step = next((s for s in steps if s["step"] == next_step_num), None)

        if not next_step:
            # Sequence complete
            await self.outreach_sequences.update_one(
                {"sequence_id": sequence_id},
                {"$set": {"status": "completed", "completed_at": datetime.now(timezone.utc).isoformat()}},
            )
            return {"success": True, "status": "completed"}

        # Queue next step for approval
        now = datetime.now(timezone.utc)
        queue_id = f"OQ-{uuid.uuid4().hex[:10].upper()}"
        queue_doc = {
            "queue_id": queue_id,
            "sequence_id": sequence_id,
            "sequence_type": seq["sequence_type"],
            "booking_number": seq["booking_number"],
            "county": seq["county"],
            "phone": seq["phone"],
            "message_text": next_step["message_text"],
            "template_key": next_step["template_key"],
            "step": next_step_num,
            "status": "pending_approval",
            "created_at": now.isoformat(),
            "scheduled_for": next_step["scheduled_for"],
            "channel": seq_def.get("channel", "imessage"),
            "requires_approval": True,
        }
        await self.outreach_queue.insert_one(queue_doc)

        # Update sequence
        step_index = next_step_num - 1
        await self.outreach_sequences.update_one(
            {"sequence_id": sequence_id},
            {
                "$set": {
                    "current_step": next_step_num,
                    f"steps.{step_index}.status": "pending_approval",
                    f"steps.{step_index}.queue_id": queue_id,
                }
            },
        )

        logger.info(
            "[DripSequences] Sequence %s advanced to step %d (queued for approval)",
            sequence_id, next_step_num,
        )
        return {"success": True, "status": "pending_approval", "next_step": next_step_num}

    # ── Stop Sequence ─────────────────────────────────────────────────────────

    async def stop_sequence(self, sequence_id: str, reason: str = "manual") -> dict:
        """Stop a drip sequence and cancel any pending queue entries."""
        now = datetime.now(timezone.utc).isoformat()

        # Cancel pending queue entries
        await self.outreach_queue.update_many(
            {"sequence_id": sequence_id, "status": "pending_approval"},
            {"$set": {"status": "cancelled", "cancelled_at": now, "cancel_reason": reason}},
        )

        # Mark sequence as stopped
        await self.outreach_sequences.update_one(
            {"sequence_id": sequence_id},
            {"$set": {"status": "stopped", "stopped_at": now, "stop_reason": reason}},
        )

        logger.info("[DripSequences] Sequence %s stopped. reason=%s", sequence_id, reason)
        return {"success": True, "sequence_id": sequence_id, "reason": reason}
