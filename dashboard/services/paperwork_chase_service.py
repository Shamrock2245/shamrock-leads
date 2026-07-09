"""
ShamrockLeads — Unsigned Paperwork Auto-Chase Service
=====================================================
Automatically follows up on SignNow packets that haven't been signed.

Chase sequence:
  T+2h   : iMessage nudge → "Your bail paperwork is still waiting..."
  T+6h   : iMessage + SMS fallback → "Don't miss the window..."
  T+24h  : Slack alert to staff → "⚠️ Unsigned packet for {defendant}"
  T+48h  : Final attempt (optional Shannon voice call)

Respects:
  - Max nudges per packet (configurable, default 3)
  - Won't re-chase packets that have been signed, voided, or expired
  - Logs all chase events to audit trail
"""
import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class PaperworkChaseService:
    """
    Scans the paperwork_packets collection for unsigned packets
    and sends escalating follow-up reminders.
    """

    def __init__(self, db):
        self.db = db

    @property
    def packets(self):
        return self.db["paperwork_packets"]

    @property
    def chase_log(self):
        return self.db["paperwork_chase_log"]

    @property
    def notifications(self):
        return self.db["notifications"]

    async def scan_and_chase(self, config: dict | None = None) -> dict:
        """
        Main entry point — called by the background cron.

        Scans for unsigned packets and sends appropriate follow-ups
        based on how long the packet has been pending.

        Args:
            config: paperwork_chase config section from automation_config

        Returns:
            Summary dict with counts
        """
        if config is None:
            from dashboard.services.automation_config import get_automation_config
            full_cfg = await get_automation_config(self.db)
            config = full_cfg.get("paperwork_chase", {})

        mode = (config.get("mode") or "review").lower()  # review | staff_only | full_auto
        nudge_1_hours = config.get("nudge_1_hours", 2)
        nudge_2_hours = config.get("nudge_2_hours", 6)
        staff_alert_hours = config.get("staff_alert_hours", 24)
        max_nudges = config.get("max_nudges", 3)
        send_client = mode == "full_auto"
        send_staff = mode in ("full_auto", "staff_only", "review")

        now = datetime.now(timezone.utc)
        results = {
            "scanned": 0,
            "nudge_1_sent": 0,
            "nudge_2_sent": 0,
            "staff_alerts": 0,
            "review_queued": 0,
            "skipped_max_nudges": 0,
            "errors": 0,
            "mode": mode,
        }

        # Find packets in pending_signature or delivered status
        cursor = self.packets.find({
            "status": {"$in": ["pending_signature", "delivered"]},
            "signnow_status": {"$in": ["sent", None]},
        })

        async for packet in cursor:
            results["scanned"] += 1
            packet_id = packet.get("packet_id", "")

            # Determine packet age
            created_at = packet.get("signnow_sent_at") or packet.get("delivered_at") or packet.get("created_at")
            if not created_at:
                continue

            # Handle both datetime objects and ISO strings
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
            elif not hasattr(created_at, "timestamp"):
                continue

            # Ensure timezone-aware
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            age_hours = (now - created_at).total_seconds() / 3600

            # Check how many nudges already sent
            nudge_count = await self.chase_log.count_documents({"packet_id": packet_id})
            if nudge_count >= max_nudges:
                results["skipped_max_nudges"] += 1
                continue

            # Determine which nudge tier to send
            try:
                if age_hours >= staff_alert_hours and not await self._already_chased(packet_id, "staff_alert"):
                    if send_staff:
                        await self._send_staff_alert(packet)
                        await self._log_chase(packet, "staff_alert", age_hours)
                        results["staff_alerts"] += 1

                elif age_hours >= nudge_2_hours and not await self._already_chased_any(
                    packet_id, ("nudge_2", "nudge_2_review")
                ):
                    if send_client:
                        await self._send_nudge(packet, nudge_level=2)
                        await self._log_chase(packet, "nudge_2", age_hours)
                        results["nudge_2_sent"] += 1
                    elif mode == "review":
                        await self._queue_review(packet, "nudge_2", age_hours)
                        await self._log_chase(packet, "nudge_2_review", age_hours)
                        results["review_queued"] += 1
                    # staff_only: wait for 24h staff alert tier

                elif age_hours >= nudge_1_hours and not await self._already_chased_any(
                    packet_id, ("nudge_1", "nudge_1_review")
                ):
                    if send_client:
                        await self._send_nudge(packet, nudge_level=1)
                        await self._log_chase(packet, "nudge_1", age_hours)
                        results["nudge_1_sent"] += 1
                    elif mode == "review":
                        await self._queue_review(packet, "nudge_1", age_hours)
                        await self._log_chase(packet, "nudge_1_review", age_hours)
                        results["review_queued"] += 1

            except Exception as exc:
                logger.error("[paperwork-chase] Error processing packet %s: %s", packet_id, exc)
                results["errors"] += 1

        total_actions = (
            results["nudge_1_sent"]
            + results["nudge_2_sent"]
            + results["staff_alerts"]
            + results["review_queued"]
        )
        if total_actions > 0:
            logger.info(
                "☘️  Paperwork chase mode=%s: scanned=%s review=%s nudge1=%s nudge2=%s staff=%s errors=%s",
                mode, results["scanned"], results["review_queued"], results["nudge_1_sent"],
                results["nudge_2_sent"], results["staff_alerts"], results["errors"],
            )

        return results

    async def _queue_review(self, packet: dict, chase_type: str, age_hours: float):
        """Staff-facing review item — no client contact (review mode)."""
        try:
            from dashboard.routers.notifications import create_notification
            defendant = packet.get("defendant_name", "Unknown")
            packet_id = packet.get("packet_id", "?")
            await create_notification(
                notification_type="paperwork_chase_review",
                title=f"📋 Chase ready: {defendant}",
                message=(
                    f"Packet {packet_id} unsigned ~{age_hours:.0f}h "
                    f"({chase_type}). Approve chase or send manually."
                ),
                entity_id=packet_id,
                entity_type="paperwork_packet",
                metadata={
                    "chase_type": chase_type,
                    "age_hours": round(age_hours, 1),
                    "mode": "review",
                },
            )
        except Exception as exc:
            logger.debug("[paperwork-chase] review notification skipped: %s", exc)

    async def _already_chased(self, packet_id: str, chase_type: str) -> bool:
        """Check if this specific chase level has already been sent."""
        existing = await self.chase_log.find_one({
            "packet_id": packet_id,
            "chase_type": chase_type,
        })
        return existing is not None

    async def _already_chased_any(self, packet_id: str, chase_types: tuple) -> bool:
        existing = await self.chase_log.find_one({
            "packet_id": packet_id,
            "chase_type": {"$in": list(chase_types)},
        })
        return existing is not None

    async def _send_nudge(self, packet: dict, nudge_level: int):
        """Send an iMessage/SMS nudge to the indemnitor.

        Args:
            packet: The paperwork_packets document
            nudge_level: 1 (gentle) or 2 (urgent)
        """
        phone = packet.get("indemnitor_phone") or packet.get("delivered_to", "")
        if not phone:
            logger.debug("[paperwork-chase] No phone for packet %s — skipping nudge", packet.get("packet_id"))
            return

        defendant_name = packet.get("defendant_name", "your loved one")
        magic_link = packet.get("magic_link", "https://shamrockbailbonds.biz")

        if nudge_level == 1:
            message = (
                f"Hi! Your Shamrock Bail Bonds paperwork for {defendant_name} is "
                f"still waiting for your signature. It only takes a few minutes:\n\n"
                f"{magic_link}\n\n"
                f"Questions? Call us: 239-332-2245 🍀"
            )
        else:
            message = (
                f"⏰ Time-sensitive: Your bail bond paperwork for {defendant_name} "
                f"hasn't been signed yet. The faster we get signatures, the faster "
                f"we can get them released.\n\n"
                f"Sign now: {magic_link}\n\n"
                f"Need help? Call 239-332-2245 — we can walk you through it."
            )

        try:
            from dashboard.services.bb_client import send_message_universal
            result = await send_message_universal(phone, message)
            if result.get("success"):
                logger.info("[paperwork-chase] Nudge %d sent for packet %s to ...%s",
                            nudge_level, packet.get("packet_id"), phone[-4:])
            else:
                logger.warning("[paperwork-chase] Nudge %d failed for packet %s: %s",
                               nudge_level, packet.get("packet_id"), result.get("error"))
        except Exception as exc:
            logger.error("[paperwork-chase] Send error for packet %s: %s", packet.get("packet_id"), exc)

    async def _send_staff_alert(self, packet: dict):
        """Post a Slack alert and create a dashboard notification for staff."""
        packet_id = packet.get("packet_id", "?")
        defendant = packet.get("defendant_name", "Unknown")
        county = packet.get("defendant_county", "")
        phone = packet.get("indemnitor_phone", "")

        # Dashboard notification
        try:
            from dashboard.routers.notifications import create_notification
            await create_notification(
                notification_type="unsigned_paperwork",
                title=f"⚠️ Unsigned: {defendant}",
                message=f"Paperwork packet {packet_id} unsigned for 24+ hours. Manual follow-up needed.",
                entity_id=packet_id,
                entity_type="paperwork_packet",
                metadata={
                    "county": county,
                    "phone_last4": phone[-4:] if phone else "",
                },
            )
        except Exception as exc:
            logger.warning("[paperwork-chase] Notification creation failed: %s", exc)

        # Slack alert
        webhook_url = os.getenv("SLACK_WEBHOOK_LEADS", "")
        if webhook_url:
            try:
                import httpx
                text = (
                    f"⚠️ *Unsigned Paperwork Alert*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"*Packet:* {packet_id}\n"
                    f"*Defendant:* {defendant}\n"
                    f"*County:* {county}\n"
                    f"*Phone:* ...{phone[-4:] if phone else '???'}\n"
                    f"*Status:* Unsigned for 24+ hours\n"
                    f"*Action:* Manual follow-up required\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )
                async with httpx.AsyncClient() as client:
                    await client.post(webhook_url, json={"text": text}, timeout=5)
            except Exception as exc:
                logger.warning("[paperwork-chase] Slack alert failed: %s", exc)

    async def _log_chase(self, packet: dict, chase_type: str, age_hours: float):
        """Record the chase event for dedup and audit."""
        await self.chase_log.insert_one({
            "packet_id": packet.get("packet_id"),
            "intake_id": packet.get("intake_id"),
            "defendant_name": packet.get("defendant_name"),
            "chase_type": chase_type,
            "age_hours": round(age_hours, 1),
            "phone": packet.get("indemnitor_phone", ""),
            "created_at": datetime.now(timezone.utc),
        })
