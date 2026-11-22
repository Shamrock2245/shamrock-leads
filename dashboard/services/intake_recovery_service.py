"""
ShamrockLeads — Abandoned Intake Recovery Service
==================================================
Detects intakes that were started but never completed, and sends
a gentle 3-stage follow-up to get the indemnitor to finish.

Recovery cadence (deduped per intake_id + touch):
  touch_1 (T+30m) : Quick-resume iMessage with direct role-aware portal link
  touch_2 (T+4h)  : Helpful follow-up reminder
  touch_3 (T+24h) : Final 24/7 support check-in

Safety:
  - Defaults to mode="review" (queues staff notification; no auto-send)
  - Skips opted-out intakes (do_not_contact / opt_out / sms_opt_out)
  - Skips intakes that already converted (signed paperwork or active/posted bond)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

PORTAL_LANDING_URL = "https://www.shamrockbailbonds.biz/portal-landing"


def _parse_dt(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def build_resume_url(intake: Dict[str, Any]) -> str:
    """Build a direct role-aware portal-landing URL without creating a DocuSeal packet."""
    raw_role = str(intake.get("role") or "").strip().lower()
    role = "defendant" if raw_role == "defendant" else "indemnitor"
    url = f"{PORTAL_LANDING_URL}?role={role}&source=recovery"
    county = str(intake.get("county") or intake.get("defendant_county") or "").strip()
    if county:
        url += f"&county={quote(county)}"
    return url


def _is_opted_out(intake: Dict[str, Any]) -> bool:
    for key in ("do_not_contact", "opt_out", "sms_opt_out", "doNotContact", "optOut"):
        val = intake.get(key)
        if val is True or str(val).strip().lower() in ("true", "1", "yes"):
            return True
    return False


class IntakeRecoveryService:
    """
    Scans intake_queue for abandoned submissions and sends
    staged recovery messages to get indemnitors to complete their intake.
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

    async def _already_converted(self, intake: Dict[str, Any], intake_id: str) -> bool:
        """Skip recovery if this intake already signed paperwork or bound a bond."""
        bond_case_id = intake.get("bond_case_id") or ""
        try:
            pkt_or = [{"intake_id": intake_id}]
            if bond_case_id:
                pkt_or.append({"bond_case_id": bond_case_id})
            signed_pkt = await self.db["paperwork_packets"].find_one(
                {"$or": pkt_or, "status": {"$in": ["signed", "completed"]}}
            )
            if signed_pkt:
                return True
        except Exception:
            pass

        try:
            bc_or = [{"intake_id": intake_id}]
            if bond_case_id:
                bc_or.append({"bond_case_id": bond_case_id})
            active_bc = await self.db["bond_cases"].find_one(
                {
                    "$or": bc_or,
                    "status": {"$in": ["active", "posted", "discharged", "exonerated", "completed"]},
                }
            )
            if active_bc:
                return True
        except Exception:
            pass
        return False

    async def _next_touch(
        self,
        *,
        intake_id: str,
        age: timedelta,
        stale_minutes: int,
        nudge_2_hours: float,
        nudge_3_hours: float,
        cooldown_hours: float,
        now: datetime,
    ) -> Optional[str]:
        """Return 'touch_1', 'touch_2', 'touch_3', or None if on cooldown/done."""
        sent_touches = set()
        latest_dt: Optional[datetime] = None

        cursor = self.recovery_log.find({"intake_id": intake_id})
        async for row in cursor:
            touch = row.get("touch") or "touch_1"
            sent_touches.add(touch)
            row_dt = _parse_dt(row.get("created_at"))
            if row_dt and (latest_dt is None or row_dt > latest_dt):
                latest_dt = row_dt

        if latest_dt is not None:
            gap_hours = min(float(cooldown_hours), 3.0) if "touch_2" not in sent_touches else float(cooldown_hours)
            if (now - latest_dt) < timedelta(hours=gap_hours):
                return None

        if "touch_1" not in sent_touches and age >= timedelta(minutes=stale_minutes):
            return "touch_1"
        if "touch_1" in sent_touches and "touch_2" not in sent_touches and age >= timedelta(hours=nudge_2_hours):
            return "touch_2"
        if "touch_2" in sent_touches and "touch_3" not in sent_touches and age >= timedelta(hours=nudge_3_hours):
            return "touch_3"
        return None

    async def scan_and_recover(self, config: dict | None = None, *, now: datetime | None = None) -> dict:
        """
        Main entry point — called by the background cron.

        Scans for intakes that stalled and sends staged recovery nudges.
        """
        if config is None:
            from dashboard.services.automation_config import get_automation_config
            full_cfg = await get_automation_config(self.db)
            config = full_cfg.get("intake_recovery", {})

        mode = (config.get("mode") or "review").lower()  # review | full_auto
        stale_minutes = int(config.get("stale_minutes", 30))
        nudge_2_hours = float(config.get("nudge_2_hours", 4))
        nudge_3_hours = float(config.get("nudge_3_hours", 24))
        max_per_cycle = int(config.get("max_per_cycle", 10))
        cooldown_hours = float(config.get("cooldown_hours", 24))
        send_client = mode == "full_auto"

        now = now or datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(minutes=stale_minutes)

        results = {
            "scanned": 0,
            "recovered_sent": 0,
            "review_queued": 0,
            "skipped_cooldown": 0,
            "skipped_no_phone": 0,
            "skipped_opt_out": 0,
            "skipped_converted": 0,
            "errors": 0,
            "mode": mode,
        }

        cursor = self.intake_queue.find({
            "status": {"$in": ["pending", "incomplete", "started"]},
            "$or": [
                {"updated_at": {"$lt": stale_cutoff}},
                {"created_at": {"$lt": stale_cutoff}, "updated_at": {"$exists": False}},
            ],
        }).sort("created_at", -1).limit(max_per_cycle * 3)

        acted_count = 0
        async for intake in cursor:
            results["scanned"] += 1
            if acted_count >= max_per_cycle:
                break

            intake_id = intake.get("intake_id") or str(intake.get("_id", ""))
            phone = (intake.get("phone") or intake.get("indemnitor_phone") or "").strip()

            if _is_opted_out(intake):
                results["skipped_opt_out"] += 1
                continue

            if not phone:
                results["skipped_no_phone"] += 1
                continue

            if await self._already_converted(intake, intake_id):
                results["skipped_converted"] += 1
                continue

            ref_dt = _parse_dt(intake.get("updated_at")) or _parse_dt(intake.get("created_at"))
            age = (now - ref_dt) if ref_dt else timedelta(minutes=stale_minutes)

            touch = await self._next_touch(
                intake_id=intake_id,
                age=age,
                stale_minutes=stale_minutes,
                nudge_2_hours=nudge_2_hours,
                nudge_3_hours=nudge_3_hours,
                cooldown_hours=cooldown_hours,
                now=now,
            )
            if not touch:
                results["skipped_cooldown"] += 1
                continue

            resume_url = build_resume_url(intake)
            try:
                if send_client:
                    sent_ok = await self._send_recovery(intake, touch=touch, resume_url=resume_url)
                    if sent_ok:
                        await self._log_recovery(intake, channel="imessage", touch=touch, resume_url=resume_url, now=now)
                        results["recovered_sent"] += 1
                        acted_count += 1
                    else:
                        results["errors"] += 1
                else:
                    await self._queue_review(intake, touch=touch, resume_url=resume_url)
                    await self._log_recovery(intake, channel="review", touch=touch, resume_url=resume_url, now=now)
                    results["review_queued"] += 1
                    acted_count += 1
            except Exception as exc:
                logger.error("[intake-recovery] Error for intake %s: %s", intake_id, exc)
                results["errors"] += 1

        acted = results["recovered_sent"] + results["review_queued"]
        if acted > 0:
            logger.info(
                "☘️  Intake recovery mode=%s: scanned=%s sent=%s review=%s cooldown=%s opt_out=%s converted=%s errors=%s",
                mode, results["scanned"], results["recovered_sent"], results["review_queued"],
                results["skipped_cooldown"], results["skipped_opt_out"],
                results["skipped_converted"], results["errors"],
            )

        return results

    def build_recovery_message(self, intake: dict, *, touch: str = "touch_1", resume_url: str = "") -> str:
        first_name = intake.get("indemnitor_first_name") or intake.get("contact_name", "")
        defendant = intake.get("defendant_name") or intake.get("defendant_first_name", "")
        display_name = first_name.split()[0] if first_name else "there"
        portal = resume_url or build_resume_url(intake)
        subject_clause = f" for {defendant}" if defendant else ""

        if touch == "touch_2":
            return (
                f"Hi {display_name}, checking back from Shamrock Bail Bonds — your bail intake"
                f"{subject_clause} is still saved and ready whenever you are:\n\n"
                f"{portal}\n\n"
                f"Call (239) 332-2245 or text (239) 955-0178 anytime. 🍀"
            )
        if touch == "touch_3":
            return (
                f"Hi {display_name}, Shamrock Bail Bonds here. If you still need help"
                f"{subject_clause}, our team is standing by 24/7:\n\n"
                f"{portal}\n\n"
                f"Call (239) 332-2245 or text (239) 955-0178 — we're here to help. 🍀"
            )
        return (
            f"Hi {display_name}! It looks like you started your bail bond intake"
            f"{subject_clause} but didn't get a chance to finish. "
            f"Pick up right where you left off:\n\n"
            f"{portal}\n\n"
            f"Available 24/7 — call (239) 332-2245 or text (239) 955-0178. 🍀"
        )

    async def _queue_review(self, intake: dict, *, touch: str = "touch_1", resume_url: str = ""):
        """Staff notification only — no client message (review mode)."""
        try:
            from dashboard.routers.notifications import create_notification
            defendant = intake.get("defendant_name") or intake.get("defendant_first_name") or "Unknown"
            intake_id = intake.get("intake_id") or str(intake.get("_id", "?"))
            phone = (intake.get("phone") or intake.get("indemnitor_phone") or "").strip()
            await create_notification(
                notification_type="intake_recovery_review",
                title=f"🔄 Abandoned intake ({touch}): {defendant}",
                message=(
                    f"Intake {intake_id} stalled ({touch}). Phone ...{phone[-4:] if len(phone) >= 4 else '????'}. "
                    f"Resume URL: {resume_url}"
                ),
                entity_id=intake_id,
                entity_type="intake",
                metadata={"mode": "review", "touch": touch, "resume_url": resume_url},
            )
        except Exception as exc:
            logger.debug("[intake-recovery] review notification skipped: %s", exc)

    async def _send_recovery(self, intake: dict, *, touch: str = "touch_1", resume_url: str = "") -> bool:
        """Send a recovery iMessage/SMS to the indemnitor."""
        phone = (intake.get("phone") or intake.get("indemnitor_phone") or "").strip()
        if not phone:
            return False

        message = self.build_recovery_message(intake, touch=touch, resume_url=resume_url)
        from dashboard.services.bb_client import send_message_universal
        result = await send_message_universal(phone, message)
        if result.get("success"):
            logger.info("[intake-recovery] %s sent for intake %s to ...%s",
                        touch, intake.get("intake_id", "?"), phone[-4:])
            return True
        logger.warning("[intake-recovery] %s send failed for %s: %s",
                       touch, intake.get("intake_id", "?"), result.get("error"))
        return False

    async def _log_recovery(
        self,
        intake: dict,
        *,
        channel: str = "imessage",
        touch: str = "touch_1",
        resume_url: str = "",
        now: Optional[datetime] = None,
    ):
        """Record the recovery attempt for dedup/audit."""
        await self.recovery_log.insert_one({
            "intake_id": intake.get("intake_id") or str(intake.get("_id", "")),
            "phone": intake.get("phone") or intake.get("indemnitor_phone") or "",
            "defendant_name": intake.get("defendant_name", ""),
            "channel": channel,
            "touch": touch,
            "resume_url": resume_url,
            "created_at": now or datetime.now(timezone.utc),
        })
