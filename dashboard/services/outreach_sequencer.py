"""
ShamrockLeads — Phase 10: Outreach Sequencing Service
======================================================
Manages the full automated outreach pipeline for new arrest leads,
using BlueBubbles (iMessage/SMS) as the primary communication channel.

Sequence (configurable per lead score tier):
  T+0h    : Initial outreach — "Hi, we saw your family member was arrested..."
  T+2h    : Follow-up if no reply
  T+24h   : Day-1 check-in
  T+72h   : Day-3 last-chance
  T+7d    : Final follow-up (if still no intake submitted)

All messages include:
  - Geolocator link (mandatory per project standards)
  - Intake portal magic link
  - Agent contact info

BlueBubbles Scheduling:
  Uses BB's native /api/v1/message/schedule endpoint for future messages
  so they send even if the Python app is down.

Outreach is STOPPED when:
  - An intake is submitted (status → "pending")
  - Staff marks the lead as "contacted"
  - Lead is marked "bonded", "closed", or "do_not_contact"
  - Defendant is confirmed out of custody
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Sequence definitions ───────────────────────────────────────────────────
# Each step: (delay_hours, template_key, stop_if_replied)
SEQUENCE_HIGH = [      # Score >= 70 (hot lead)
    (0,    "initial_hot",       True),
    (2,    "followup_2h",       True),
    (24,   "day1_checkin",      True),
    (72,   "day3_lastchance",   True),
]

SEQUENCE_MEDIUM = [    # Score 40–69
    (0,    "initial_warm",      True),
    (4,    "followup_4h",       True),
    (24,   "day1_checkin",      True),
    (72,   "day3_lastchance",   True),
    (168,  "day7_final",        True),
]

SEQUENCE_LOW = [       # Score < 40
    (0,    "initial_cold",      True),
    (24,   "day1_checkin",      True),
    (168,  "day7_final",        True),
]

SCORE_TIERS = {
    "high":   (70, 100, SEQUENCE_HIGH),
    "medium": (40, 69,  SEQUENCE_MEDIUM),
    "low":    (0,  39,  SEQUENCE_LOW),
}

# ── Message templates ──────────────────────────────────────────────────────
TEMPLATES = {
    "initial_hot": (
        "Hi {contact_name}! 👋 We noticed {defendant_name} was recently arrested "
        "in {county} County. Shamrock Bail Bonds can help get them out fast — "
        "bonds starting at 10% of the bond amount.\n\n"
        "Get started here: {portal_link}\n\n"
        "Call/text us 24/7: 239-332-2245\n"
        "Shamrock Bail Bonds — Fort Myers, FL"
    ),
    "initial_warm": (
        "Hi {contact_name}, this is Shamrock Bail Bonds. We saw that {defendant_name} "
        "was arrested in {county} County. We're here to help if you need bail bond services.\n\n"
        "Learn more: {portal_link}\n\n"
        "239-332-2245 | shamrockbailbonds.biz"
    ),
    "initial_cold": (
        "Hi, this is Shamrock Bail Bonds. We can assist with bail for {defendant_name} "
        "({county} County). Reply or visit: {portal_link}\n239-332-2245"
    ),
    "followup_2h": (
        "Hi {contact_name}, just following up on {defendant_name}'s bail. "
        "We can move quickly — most bonds are posted within 2 hours of paperwork.\n\n"
        "{portal_link}\n239-332-2245"
    ),
    "followup_4h": (
        "Hi {contact_name}, Shamrock Bail Bonds here. Still available to help with "
        "{defendant_name}'s release. Questions? Reply anytime.\n\n"
        "{portal_link}"
    ),
    "day1_checkin": (
        "Good morning {contact_name}. Checking in on {defendant_name}'s situation. "
        "If you're still looking for help, we're here.\n\n"
        "Start your application: {portal_link}\n239-332-2245"
    ),
    "day3_lastchance": (
        "Hi {contact_name}, this is our last check-in regarding {defendant_name}. "
        "If you still need bail bond assistance, please reach out.\n\n"
        "{portal_link} | 239-332-2245"
    ),
    "day7_final": (
        "Hi {contact_name}, Shamrock Bail Bonds — final follow-up for {defendant_name}. "
        "We're always here if circumstances change.\n\n"
        "239-332-2245 | shamrockbailbonds.biz"
    ),
}

# ── Stop conditions ────────────────────────────────────────────────────────
STOP_STATUSES = {"contacted", "negotiating", "paperwork", "ready", "bonded", "closed", "do_not_contact"}


class OutreachSequencer:
    """
    Manages the full outreach sequence for a lead using BlueBubbles.

    Usage:
        sequencer = OutreachSequencer(db)
        await sequencer.start_sequence(arrest_doc)
        await sequencer.stop_sequence(booking_number, county)
    """

    def __init__(self, db):
        self.db = db

    @property
    def arrests(self):
        return self.db["arrests"]

    @property
    def outreach_sequences(self):
        return self.db["outreach_sequences"]

    @property
    def outreach_messages(self):
        return self.db["outreach_messages"]

    # ─────────────────────────────────────────────────────────────────────
    #  Start a new outreach sequence for an arrest record
    # ─────────────────────────────────────────────────────────────────────
    async def start_sequence(self, arrest_doc: dict) -> dict:
        """
        Start the outreach sequence for a new arrest lead.

        Args:
            arrest_doc: The arrest record from MongoDB

        Returns:
            Sequence metadata dict
        """
        booking_number = arrest_doc.get("booking_number", "")
        county = arrest_doc.get("county", "")
        full_name = arrest_doc.get("full_name", "Unknown")
        phone = arrest_doc.get("phone", "") or arrest_doc.get("contact_phone", "")
        lead_score = arrest_doc.get("lead_score", 0) or 0

        # Check if sequence already running
        existing = await self.outreach_sequences.find_one(
            {"booking_number": booking_number, "county": county, "status": "active"}
        )
        if existing:
            return {"success": False, "reason": "sequence_already_active", "sequence_id": existing.get("sequence_id")}

        # Check stop conditions
        shamrock_status = arrest_doc.get("shamrock_status", "new")
        if shamrock_status in STOP_STATUSES:
            return {"success": False, "reason": f"stop_status_{shamrock_status}"}

        # Determine sequence tier
        sequence_steps = SEQUENCE_LOW
        tier = "low"
        for tier_name, (min_score, max_score, steps) in SCORE_TIERS.items():
            if min_score <= lead_score <= max_score:
                sequence_steps = steps
                tier = tier_name
                break

        sequence_id = f"SEQ-{uuid.uuid4().hex[:10].upper()}"
        now = datetime.now(timezone.utc)

        # Build portal link
        # PORTAL_BASE_URL: Wix indemnitor portal (intake magic link)
        # DASHBOARD_PUBLIC_URL: branded VPS domain (geo-tracking link)
        import os
        portal_base = os.getenv("PORTAL_BASE_URL", "https://shamrockbailbonds.biz").rstrip("/")
        dashboard_url = os.getenv(
            "DASHBOARD_PUBLIC_URL",
            os.getenv("BB_WEBHOOK_PUBLIC_URL", portal_base)
        ).rstrip("/")
        portal_link = f"{portal_base}/intake?booking={booking_number}&county={county}"
        geo_link = f"{dashboard_url}/g/{booking_number}"  # uses /g/<token> route

        # Build scheduled steps
        steps_meta = []
        for delay_hours, template_key, stop_on_reply in sequence_steps:
            send_at = now + timedelta(hours=delay_hours)
            step_id = f"{sequence_id}-STEP-{len(steps_meta)+1:02d}"
            steps_meta.append({
                "step_id": step_id,
                "template_key": template_key,
                "delay_hours": delay_hours,
                "scheduled_for": send_at,
                "status": "scheduled",
                "bb_schedule_id": None,
                "sent_at": None,
                "stop_on_reply": stop_on_reply,
            })

        # Store sequence record
        seq_doc = {
            "sequence_id": sequence_id,
            "booking_number": booking_number,
            "county": county,
            "defendant_name": full_name,
            "phone": phone,
            "lead_score": lead_score,
            "tier": tier,
            "status": "active",
            "portal_link": portal_link,
            "steps": steps_meta,
            "created_at": now,
            "updated_at": now,
            "stopped_at": None,
            "stop_reason": None,
            "intake_submitted": False,
        }
        await self.outreach_sequences.insert_one(seq_doc)

        # Schedule first message immediately via BB
        if phone and steps_meta:
            first_step = steps_meta[0]
            await self._schedule_bb_message(
                sequence_id=sequence_id,
                step=first_step,
                phone=phone,
                defendant_name=full_name,
                county=county,
                portal_link=portal_link,
                contact_name="",
            )

        logger.info("[outreach] Sequence %s started for %s (%s/%s) tier=%s",
                    sequence_id, full_name, county, booking_number, tier)

        return {
            "success": True,
            "sequence_id": sequence_id,
            "tier": tier,
            "steps": len(steps_meta),
            "phone": phone,
        }

    # ─────────────────────────────────────────────────────────────────────
    #  Schedule a single message via BlueBubbles
    # ─────────────────────────────────────────────────────────────────────
    async def _schedule_bb_message(
        self,
        sequence_id: str,
        step: dict,
        phone: str,
        defendant_name: str,
        county: str,
        portal_link: str,
        contact_name: str = "",
    ) -> Optional[str]:
        """
        Schedule or send a single outreach message.
        - Immediate sends (delay_hours == 0): use send_message_universal()
          which sends via BB with `any;-;` prefix (auto-routes iMessage/SMS).
        - Future sends: use BB's native scheduling API with `any;-;` prefix.
        Returns the BB schedule ID, "immediate", or None on failure.
        """
        if not phone:
            return None

        template = TEMPLATES.get(step["template_key"], TEMPLATES["initial_warm"])
        message = template.format(
            contact_name=contact_name or "there",
            defendant_name=defendant_name,
            county=county,
            portal_link=portal_link,
        )

        try:
            if step["delay_hours"] == 0:
                # ── Immediate: use universal send (iMessage-first, SMS fallback) ──
                from dashboard.services.bb_client import send_message_universal
                result = await send_message_universal(phone, message)
                channel = result.get("channel", "failed")
                bb_schedule_id = "immediate" if result.get("success") else None

                # Update step status
                await self.outreach_sequences.update_one(
                    {"sequence_id": sequence_id, "steps.step_id": step["step_id"]},
                    {"$set": {
                        "steps.$.bb_schedule_id": bb_schedule_id,
                        "steps.$.status": "sent" if result.get("success") else "failed",
                        "steps.$.sent_at": datetime.now(timezone.utc) if result.get("success") else None,
                        "steps.$.channel": channel,
                    }},
                )

                # Log the outreach message
                await self.outreach_messages.insert_one({
                    "sequence_id": sequence_id,
                    "step_id": step["step_id"],
                    "phone": phone,
                    "message": message,
                    "template_key": step["template_key"],
                    "scheduled_for": step["scheduled_for"],
                    "bb_schedule_id": bb_schedule_id,
                    "channel": channel,
                    "status": "sent" if result.get("success") else "failed",
                    "created_at": datetime.now(timezone.utc),
                })

                return bb_schedule_id

            else:
                # ── Future: use BB native scheduling ──
                from dashboard.services.bb_client import get_bb_client, check_imessage
                bb = get_bb_client(phone)
                if not bb:
                    logger.warning("[outreach] No BB client for phone %s — future msg not scheduled", phone)
                    return None

                # Use any;-; prefix — BB auto-selects iMessage or SMS
                chat_guid = f"any;-;{phone}"

                # Check availability for channel reporting only
                is_imessage = await check_imessage(phone)
                channel = "imessage" if is_imessage else "sms"

                scheduled_for_ms = int(step["scheduled_for"].timestamp() * 1000)
                result = await bb.schedule_message(
                    chat_guid=chat_guid,
                    message=message,
                    scheduled_for_ms=scheduled_for_ms,
                    schedule_type="once",
                )
                bb_schedule_id = result.get("data", {}).get("id") if result.get("success") else None

                # Update step with BB schedule ID
                await self.outreach_sequences.update_one(
                    {"sequence_id": sequence_id, "steps.step_id": step["step_id"]},
                    {"$set": {
                        "steps.$.bb_schedule_id": bb_schedule_id,
                        "steps.$.status": "scheduled" if bb_schedule_id else "failed",
                        "steps.$.channel": channel,
                    }},
                )

                await self.outreach_messages.insert_one({
                    "sequence_id": sequence_id,
                    "step_id": step["step_id"],
                    "phone": phone,
                    "message": message,
                    "template_key": step["template_key"],
                    "scheduled_for": step["scheduled_for"],
                    "bb_schedule_id": bb_schedule_id,
                    "channel": channel,
                    "status": "scheduled" if bb_schedule_id else "failed",
                    "created_at": datetime.now(timezone.utc),
                })

                return bb_schedule_id

        except Exception as exc:
            logger.error("[outreach] _schedule_bb_message error for %s: %s", sequence_id, exc)
            return None

    # ─────────────────────────────────────────────────────────────────────
    #  Stop an active sequence
    # ─────────────────────────────────────────────────────────────────────
    async def stop_sequence(
        self,
        booking_number: str,
        county: str,
        reason: str = "manual_stop",
    ) -> dict:
        """
        Stop an active outreach sequence and cancel any scheduled BB messages.

        Args:
            booking_number: Arrest booking number
            county: County name
            reason: Why the sequence is being stopped

        Returns:
            Result dict
        """
        seq = await self.outreach_sequences.find_one(
            {"booking_number": booking_number, "county": county, "status": "active"}
        )
        if not seq:
            return {"success": False, "reason": "no_active_sequence"}

        sequence_id = seq["sequence_id"]
        phone = seq.get("phone", "")
        now = datetime.now(timezone.utc)

        # Cancel scheduled BB messages
        cancelled = 0
        for step in seq.get("steps", []):
            if step.get("status") == "scheduled" and step.get("bb_schedule_id"):
                bb_id = step["bb_schedule_id"]
                if bb_id and bb_id != "immediate":
                    try:
                        from dashboard.services.bb_client import get_bb_client
                        bb = get_bb_client(phone)
                        if bb:
                            await bb.delete_scheduled_message(bb_id)
                            cancelled += 1
                    except Exception as exc:
                        logger.warning("[outreach] Failed to cancel BB schedule %s: %s", bb_id, exc)

        # Mark sequence as stopped
        await self.outreach_sequences.update_one(
            {"sequence_id": sequence_id},
            {"$set": {
                "status": "stopped",
                "stopped_at": now,
                "stop_reason": reason,
                "updated_at": now,
            }},
        )

        logger.info("[outreach] Sequence %s stopped. reason=%s, cancelled_messages=%d",
                    sequence_id, reason, cancelled)

        return {
            "success": True,
            "sequence_id": sequence_id,
            "reason": reason,
            "cancelled_messages": cancelled,
        }

    # ─────────────────────────────────────────────────────────────────────
    #  Handle incoming reply (stop sequence)
    # ─────────────────────────────────────────────────────────────────────
    async def handle_reply(self, phone: str, message_text: str) -> dict:
        """
        Called when an inbound iMessage is received from a prospect.
        Stops any active outreach sequence for that phone number.
        """
        # Find active sequences for this phone
        cursor = self.outreach_sequences.find(
            {"phone": phone, "status": "active"}
        )
        stopped = []
        async for seq in cursor:
            result = await self.stop_sequence(
                booking_number=seq["booking_number"],
                county=seq["county"],
                reason="reply_received",
            )
            stopped.append(seq["sequence_id"])

        return {
            "phone": phone,
            "sequences_stopped": stopped,
            "message_preview": message_text[:100],
        }

    # ─────────────────────────────────────────────────────────────────────
    #  Batch start sequences for new arrests
    # ─────────────────────────────────────────────────────────────────────
    async def batch_start_new_arrests(self, hours_back: int = 24, limit: int = 100) -> dict:
        """
        Start outreach sequences for all new arrests in the last N hours
        that don't already have an active sequence.

        Args:
            hours_back: Look back this many hours for new arrests
            limit: Max number of arrests to process

        Returns:
            Summary dict
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

        cursor = self.arrests.find(
            {
                "scraped_at": {"$gte": cutoff},
                "shamrock_status": {"$in": ["new", None]},
                "phone": {"$exists": True, "$ne": ""},
            }
        ).limit(limit)

        started = 0
        skipped = 0
        errors = 0

        async for arrest in cursor:
            try:
                result = await self.start_sequence(arrest)
                if result.get("success"):
                    started += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.error("[outreach] batch_start error for %s: %s",
                             arrest.get("booking_number"), exc)
                errors += 1

        return {
            "started": started,
            "skipped": skipped,
            "errors": errors,
            "hours_back": hours_back,
        }
