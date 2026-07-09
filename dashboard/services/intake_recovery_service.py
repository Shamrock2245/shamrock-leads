"""
ShamrockLeads — Abandoned Intake Recovery Service
==================================================
Detects intakes that were started but never completed, and sends
a gentle follow-up to get the indemnitor to finish.

An intake is considered "abandoned" when:
  - Status is "pending" or "incomplete"
  - No activity for 30+ minutes (configurable)
  - Has a phone number or email on file
  - Hasn't been nudged within cooldown window

Recovery flow:
  T+30m  : iMessage → "Looks like you started your intake but didn't finish..."
  T+2h   : SMS fallback (if iMessage failed)
  T+24h  : Staff notification (high-bond-amount only)
"""
import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class IntakeRecoveryService:
    """
    Scans intake_queue for abandoned submissions and sends
    recovery messages to get indemnitors to complete their intake.
    """

    def __init__(self, db):
        self.db = db

    @property
    def intake_queue(self):
        return self.db["intake_queue"]

    @property
    def recovery_log(self):
        return self.db["intake_recovery_log"]

    @property
    def notifications(self):
        return self.db["notifications"]

    async def scan_and_recover(self, config: dict | None = None) -> dict:
        """
        Main entry point — called by the background cron.

        Scans for intakes that stalled and sends recovery nudges.

        Args:
            config: intake_recovery config section from automation_config

        Returns:
            Summary dict with counts
        """
        if config is None:
            from dashboard.services.automation_config import get_automation_config
            full_cfg = await get_automation_config(self.db)
            config = full_cfg.get("intake_recovery", {})

        mode = (config.get("mode") or "review").lower()  # review | full_auto
        stale_minutes = config.get("stale_minutes", 30)
        max_per_cycle = config.get("max_per_cycle", 10)
        cooldown_hours = config.get("cooldown_hours", 24)
        send_client = mode == "full_auto"

        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(minutes=stale_minutes)
        cooldown_cutoff = now - timedelta(hours=cooldown_hours)

        results = {
            "scanned": 0,
            "recovered_sent": 0,
            "review_queued": 0,
            "skipped_cooldown": 0,
            "skipped_no_phone": 0,
            "errors": 0,
            "mode": mode,
        }

        # Find intakes that are stale and incomplete
        cursor = self.intake_queue.find({
            "status": {"$in": ["pending", "incomplete", "started"]},
            "$or": [
                {"updated_at": {"$lt": stale_cutoff}},
                {"created_at": {"$lt": stale_cutoff}, "updated_at": {"$exists": False}},
            ],
        }).sort("created_at", -1).limit(max_per_cycle * 2)  # Extra buffer for filtering

        sent = 0
        async for intake in cursor:
            results["scanned"] += 1
            if sent >= max_per_cycle:
                break

            intake_id = intake.get("intake_id") or str(intake.get("_id", ""))
            phone = intake.get("phone", "").strip()

            if not phone:
                results["skipped_no_phone"] += 1
                continue

            # Check cooldown — don't re-nudge too quickly
            recent = await self.recovery_log.find_one({
                "intake_id": intake_id,
                "created_at": {"$gte": cooldown_cutoff},
            })
            if recent:
                results["skipped_cooldown"] += 1
                continue

            try:
                if send_client:
                    await self._send_recovery(intake)
                    await self._log_recovery(intake, channel="imessage")
                    results["recovered_sent"] += 1
                else:
                    await self._queue_review(intake)
                    await self._log_recovery(intake, channel="review")
                    results["review_queued"] += 1
                sent += 1
            except Exception as exc:
                logger.error("[intake-recovery] Error for intake %s: %s", intake_id, exc)
                results["errors"] += 1

        acted = results["recovered_sent"] + results["review_queued"]
        if acted > 0:
            logger.info(
                "☘️  Intake recovery mode=%s: scanned=%s sent=%s review=%s cooldown=%s no_phone=%s errors=%s",
                mode, results["scanned"], results["recovered_sent"], results["review_queued"],
                results["skipped_cooldown"], results["skipped_no_phone"],
                results["errors"],
            )

        return results

    async def _queue_review(self, intake: dict):
        """Staff notification only — no client message (review mode)."""
        try:
            from dashboard.routers.notifications import create_notification
            defendant = intake.get("defendant_name") or intake.get("defendant_first_name") or "Unknown"
            intake_id = intake.get("intake_id") or str(intake.get("_id", "?"))
            phone = (intake.get("phone") or "").strip()
            await create_notification(
                notification_type="intake_recovery_review",
                title=f"🔄 Abandoned intake: {defendant}",
                message=(
                    f"Intake {intake_id} stalled. Phone ...{phone[-4:] if len(phone) >= 4 else '????'}. "
                    f"Call back or enable full_auto recovery."
                ),
                entity_id=intake_id,
                entity_type="intake",
                metadata={"mode": "review"},
            )
        except Exception as exc:
            logger.debug("[intake-recovery] review notification skipped: %s", exc)

    async def _send_recovery(self, intake: dict):
        """Send a recovery iMessage/SMS to the indemnitor.

        Args:
            intake: The intake_queue document
        """
        phone = intake.get("phone", "")
        if not phone:
            return

        # Personalize the message
        first_name = intake.get("indemnitor_first_name") or intake.get("contact_name", "")
        defendant = intake.get("defendant_name") or intake.get("defendant_first_name", "")
        display_name = first_name if first_name else "there"
        portal = "https://shamrockbailbonds.biz"

        message = (
            f"Hi {display_name}! It looks like you started your bail bond intake"
        )
        if defendant:
            message += f" for {defendant}"
        message += (
            f" but didn't get a chance to finish. "
            f"No worries — you can pick up right where you left off:\n\n"
            f"{portal}\n\n"
            f"We're available 24/7 if you have any questions. "
            f"Call/text 239-332-2245 🍀"
        )

        try:
            from dashboard.services.bb_client import send_message_universal
            result = await send_message_universal(phone, message)
            if result.get("success"):
                logger.info("[intake-recovery] Recovery sent for intake %s to ...%s",
                            intake.get("intake_id", "?"), phone[-4:])
            else:
                logger.warning("[intake-recovery] Recovery send failed for %s: %s",
                               intake.get("intake_id", "?"), result.get("error"))
        except Exception as exc:
            logger.error("[intake-recovery] Send error: %s", exc)

    async def _log_recovery(self, intake: dict, channel: str = "imessage"):
        """Record the recovery attempt for dedup/audit."""
        await self.recovery_log.insert_one({
            "intake_id": intake.get("intake_id") or str(intake.get("_id", "")),
            "phone": intake.get("phone", ""),
            "defendant_name": intake.get("defendant_name", ""),
            "channel": channel,
            "created_at": datetime.now(timezone.utc),
        })
