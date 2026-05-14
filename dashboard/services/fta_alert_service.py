"""
FTA (Failure to Appear) Alert Service — ShamrockLeads

Detects defendants who have missed their court date and triggers
a multi-channel escalation pipeline:

  Level 1 (0–24h after FTA):  Notify agent via in-app notification + Telegram
  Level 2 (24–48h after FTA): Send iMessage via BlueBubbles to defendant + indemnitors
  Level 3 (48h+ after FTA):   Flag bond for surrender review, Telegram + in-app

Primary channel: BlueBubbles iMessage (auto-routes to SMS/RCS for non-iPhones)
Fallback: Twilio SMS (only when BB server is unreachable)

Runs via cron every 4 hours.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict

log = logging.getLogger("shamrock.fta_alert")


class FTAAlertService:
    """Detects missed court dates and escalates per defendant."""

    def __init__(self, db):
        self.db = db

    async def scan_and_alert(self) -> Dict:
        """
        Scan all active bonds for missed court dates and fire alerts.

        Returns:
            dict with scanned, fta_detected, alerts_sent, escalated counts
        """
        now = datetime.now(timezone.utc)
        scanned = 0
        fta_detected = 0
        alerts_sent = 0
        escalated = 0

        # Only check bonds that are active/monitoring/alert status
        cursor = self.db["active_bonds"].find(
            {
                "status": {"$in": ["active", "monitoring", "alert"]},
                "court_date": {"$exists": True, "$ne": None, "$ne": ""},
            }
        )

        async for bond in cursor:
            scanned += 1
            court_date_raw = bond.get("court_date")
            if not court_date_raw:
                continue

            try:
                if isinstance(court_date_raw, str):
                    court_dt = datetime.fromisoformat(
                        court_date_raw.replace("Z", "+00:00")
                    )
                    if court_dt.tzinfo is None:
                        court_dt = court_dt.replace(tzinfo=timezone.utc)
                else:
                    court_dt = court_date_raw
                    if hasattr(court_dt, "tzinfo") and court_dt.tzinfo is None:
                        court_dt = court_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            # Court date must be in the past to be an FTA
            if court_dt >= now:
                continue

            hours_past = (now - court_dt).total_seconds() / 3600
            booking_number = bond.get("booking_number", "")

            # Check if already processed this FTA
            existing_fta = await self.db["fta_alerts"].find_one(
                {"booking_number": booking_number, "court_date": court_date_raw}
            )

            if existing_fta:
                # Check if escalation is needed
                escalation_level = existing_fta.get("escalation_level", 1)
                last_escalated_at = existing_fta.get("last_escalated_at")
                if last_escalated_at:
                    try:
                        last_dt = datetime.fromisoformat(
                            last_escalated_at.replace("Z", "+00:00")
                        )
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                        hours_since_last = (now - last_dt).total_seconds() / 3600
                    except (ValueError, TypeError):
                        hours_since_last = 999
                else:
                    hours_since_last = 999

                # Escalate to level 2 after 24h
                if escalation_level == 1 and hours_past >= 24 and hours_since_last >= 20:
                    await self._escalate_level2(bond, existing_fta, hours_past)
                    escalated += 1
                # Escalate to level 3 after 48h
                elif escalation_level == 2 and hours_past >= 48 and hours_since_last >= 20:
                    await self._escalate_level3(bond, existing_fta, hours_past)
                    escalated += 1
                continue

            # New FTA detected
            fta_detected += 1
            fta_record = {
                "booking_number": booking_number,
                "defendant_name": bond.get("defendant_name", ""),
                "court_date": court_date_raw,
                "bond_amount": bond.get("bond_amount", 0),
                "county": bond.get("county", ""),
                "charges": bond.get("charges", ""),
                "hours_past_court": round(hours_past, 1),
                "escalation_level": 1,
                "detected_at": now.isoformat(),
                "last_escalated_at": now.isoformat(),
                "status": "open",
                "resolved": False,
            }

            await self.db["fta_alerts"].insert_one(fta_record)

            # Level 1: Notify agent
            sent = await self._escalate_level1(bond, fta_record, hours_past)
            if sent:
                alerts_sent += 1

            # Update bond status to alert
            await self.db["active_bonds"].update_one(
                {"booking_number": booking_number},
                {"$set": {
                    "status": "alert",
                    "fta_detected_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }}
            )

        log.info(
            "[FTAAlert] scanned=%d fta_detected=%d alerts_sent=%d escalated=%d",
            scanned, fta_detected, alerts_sent, escalated,
        )
        return {
            "scanned": scanned,
            "fta_detected": fta_detected,
            "alerts_sent": alerts_sent,
            "escalated": escalated,
        }

    async def _escalate_level1(self, bond: dict, fta_record: dict, hours_past: float) -> bool:
        """Level 1: In-app notification + Telegram staff alert."""
        booking_number = bond.get("booking_number", "")
        defendant_name = bond.get("defendant_name", "Unknown")
        bond_amount = bond.get("bond_amount", 0)

        try:
            from dashboard.api.notifications import create_notification
            await create_notification(
                notification_type="fta_alert",
                title=f"⚠️ FTA ALERT: {defendant_name}",
                message=(
                    f"Missed court {round(hours_past, 0):.0f}h ago. "
                    f"Bond: ${bond_amount:,.0f} | County: {bond.get('county', '')} | "
                    f"Charges: {(bond.get('charges') or '')[:60]}"
                ),
                entity_id=booking_number,
                entity_type="active_bond",
                metadata={
                    "booking_number": booking_number,
                    "hours_past_court": hours_past,
                    "bond_amount": bond_amount,
                    "fta_level": 1,
                },
            )
        except Exception as e:
            log.warning("[FTAAlert] In-app notification failed for %s: %s", booking_number, e)

        try:
            from dashboard.services.telegram_service import get_telegram_service
            tg = get_telegram_service()
            await tg.send_staff_alert(
                f"🚨 *FTA ALERT — Level 1*\n"
                f"Defendant: *{defendant_name}*\n"
                f"Booking: `{booking_number}`\n"
                f"Bond: ${bond_amount:,.0f}\n"
                f"County: {bond.get('county', '')}\n"
                f"Missed court {round(hours_past, 0):.0f}h ago\n"
                f"Charges: {(bond.get('charges') or '')[:80]}\n"
                f"Action: Attempt contact immediately"
            )
        except Exception as e:
            log.debug("[FTAAlert] Telegram alert failed for %s: %s", booking_number, e)

        return True

    async def _escalate_level2(self, bond: dict, fta_record: dict, hours_past: float) -> bool:
        """Level 2 (24h+): iMessage via BlueBubbles to defendant + indemnitors."""
        booking_number = bond.get("booking_number", "")
        defendant_name = bond.get("defendant_name", "Unknown")

        phones = await self._collect_phones(bond)
        first_name = self._first_name(defendant_name)

        message = (
            f"URGENT — SHAMROCK BAIL BONDS: {first_name} missed their court date. "
            f"This is a serious matter. Please call 239-332-2245 IMMEDIATELY. "
            f"Failure to resolve this may result in bond forfeiture and a warrant for arrest."
        )

        sent_count = await self._send_bb_to_phones(phones, message, booking_number, "fta_level2")

        now_iso = datetime.now(timezone.utc).isoformat()
        await self.db["fta_alerts"].update_one(
            {"booking_number": booking_number, "court_date": fta_record["court_date"]},
            {"$set": {
                "escalation_level": 2,
                "last_escalated_at": now_iso,
                "level2_bb_sent": sent_count,
                "level2_escalated_at": now_iso,
            }}
        )

        try:
            from dashboard.api.notifications import create_notification
            await create_notification(
                notification_type="fta_escalation",
                title=f"🚨 FTA Level 2: {defendant_name}",
                message=(
                    f"iMessage sent to {sent_count} contact(s). "
                    f"Booking: {booking_number}"
                ),
                entity_id=booking_number,
                entity_type="active_bond",
                metadata={"fta_level": 2, "bb_sent": sent_count},
            )
        except Exception:
            pass

        log.info("[FTAAlert] Level 2 escalated for %s — %d BB messages sent",
                 booking_number, sent_count)
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def _collect_phones(self, bond: dict) -> list:
        """Collect all unique phone numbers for a bond (defendant + indemnitors)."""
        phones = []
        seen = set()

        def _add(p):
            if p and p not in seen:
                seen.add(p)
                phones.append(p)

        _add(bond.get("indemnitor_phone"))
        _add(bond.get("phone"))

        booking_number = bond.get("booking_number", "")
        try:
            async for ind in self.db["indemnitors"].find(
                {"booking_number": booking_number}, {"phone": 1}
            ):
                _add(ind.get("phone"))
        except Exception:
            pass

        return phones

    async def _send_bb_to_phones(self, phones: list, message: str,
                                  booking_number: str, context: str) -> int:
        """
        Send a message to all phones via BlueBubbles iMessage.
        Falls back to Twilio SMS if BB is unavailable.
        Returns number of successful sends.
        """
        sent = 0
        for phone in phones[:6]:
            success = await self._send_bb(phone, message)
            if success:
                sent += 1
                log.info("[FTAAlert][%s] BB message sent to ...%s", context, phone[-4:])
            else:
                fallback_sent = await self._send_twilio_fallback(phone, message)
                if fallback_sent:
                    sent += 1
                    log.info("[FTAAlert][%s] Twilio fallback sent to ...%s", context, phone[-4:])
                else:
                    log.warning("[FTAAlert][%s] All channels failed for ...%s", context, phone[-4:])

            try:
                await self.db["outbound_messages"].insert_one({
                    "booking_number": booking_number,
                    "phone": phone,
                    "message": message,
                    "context": context,
                    "channel": "bb_imessage",
                    "success": success,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                pass

        return sent

    async def _send_bb(self, phone: str, message: str) -> bool:
        """Send via BlueBubbles. Returns True on success."""
        try:
            from dashboard.services.bb_client import send_message_universal
            result = await send_message_universal(phone, message)
            return result.get("success", False)
        except Exception as e:
            log.debug("[FTAAlert] BB send failed for ...%s: %s", phone[-4:], e)
            return False

    async def _send_twilio_fallback(self, phone: str, message: str) -> bool:
        """
        Twilio SMS fallback — only used when BlueBubbles is unreachable.
        Returns True on success.
        """
        try:
            from dashboard.services.twilio_service import TwilioService
            twilio = TwilioService()
            if not twilio._is_configured():
                return False
            await twilio.send_sms(to=phone, body=message)
            return True
        except Exception as e:
            log.debug("[FTAAlert] Twilio fallback failed for ...%s: %s", phone[-4:], e)
            return False

    @staticmethod
    def _first_name(full_name: str) -> str:
        """Extract first name from 'Last, First' or 'First Last' format."""
        if not full_name:
            return "Client"
        if "," in full_name:
            parts = full_name.split(",", 1)
            return parts[1].strip().split()[0] if parts[1].strip() else parts[0].strip()
        return full_name.split()[0]

    async def _escalate_level3(self, bond: dict, fta_record: dict, hours_past: float) -> bool:
        """Level 3 (48h+): Flag for surrender, notify surety."""
        booking_number = bond.get("booking_number", "")
        defendant_name = bond.get("defendant_name", "Unknown")
        bond_amount = bond.get("bond_amount", 0)
        now_iso = datetime.now(timezone.utc).isoformat()

        # Flag bond for surrender review
        await self.db["active_bonds"].update_one(
            {"booking_number": booking_number},
            {"$set": {
                "surrender_flagged": True,
                "surrender_flagged_at": now_iso,
                "surrender_flag_reason": "fta_48h",
                "updated_at": now_iso,
            }}
        )

        await self.db["fta_alerts"].update_one(
            {"booking_number": booking_number, "court_date": fta_record["court_date"]},
            {"$set": {
                "escalation_level": 3,
                "last_escalated_at": now_iso,
                "level3_escalated_at": now_iso,
                "surrender_flagged": True,
            }}
        )

        try:
            from dashboard.api.notifications import create_notification
            await create_notification(
                notification_type="fta_surrender_flag",
                title=f"🔴 FTA Level 3 — SURRENDER REVIEW: {defendant_name}",
                message=(
                    f"Bond ${bond_amount:,.0f} flagged for surrender review. "
                    f"Missed court {round(hours_past, 0):.0f}h ago. "
                    f"Booking: {booking_number}. Immediate action required."
                ),
                entity_id=booking_number,
                entity_type="active_bond",
                metadata={"fta_level": 3, "bond_amount": bond_amount},
            )
        except Exception:
            pass

        try:
            from dashboard.services.telegram_service import get_telegram_service
            tg = get_telegram_service()
            await tg.send_staff_alert(
                f"🔴 *FTA LEVEL 3 — SURRENDER REVIEW*\n"
                f"Defendant: *{defendant_name}*\n"
                f"Booking: `{booking_number}`\n"
                f"Bond: ${bond_amount:,.0f}\n"
                f"Missed court {round(hours_past, 0):.0f}h ago\n"
                f"Status: *FLAGGED FOR SURRENDER REVIEW*\n"
                f"Action: Contact surety company immediately"
            )
        except Exception as e:
            log.debug("[FTAAlert] Level3 Telegram failed for %s: %s", booking_number, e)

        log.warning("[FTAAlert] Level 3 escalated for %s — flagged for surrender", booking_number)
        return True

    async def resolve_fta(self, booking_number: str, resolution: str,
                          agent: str = "system") -> dict:
        """
        Mark an FTA as resolved (defendant appeared, warrant recalled, etc.)

        Args:
            booking_number: Bond booking number
            resolution: appeared|warrant_recalled|surrendered|other
            agent: Agent name who resolved
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        result = await self.db["fta_alerts"].update_many(
            {"booking_number": booking_number, "resolved": False},
            {"$set": {
                "resolved": True,
                "resolution": resolution,
                "resolved_at": now_iso,
                "resolved_by": agent,
                "status": "resolved",
            }}
        )

        # Clear surrender flag if resolved
        if resolution in ("appeared", "warrant_recalled"):
            await self.db["active_bonds"].update_one(
                {"booking_number": booking_number},
                {"$set": {
                    "surrender_flagged": False,
                    "status": "monitoring",
                    "updated_at": now_iso,
                }}
            )

        return {
            "success": True,
            "booking_number": booking_number,
            "resolution": resolution,
            "records_updated": result.modified_count,
        }

    async def get_open_ftas(self, limit: int = 50) -> List[Dict]:
        """Return all open FTA alerts sorted by escalation level desc."""
        results = []
        cursor = (
            self.db["fta_alerts"]
            .find({"resolved": False}, {"_id": 0})
            .sort([("escalation_level", -1), ("detected_at", -1)])
            .limit(limit)
        )
        async for doc in cursor:
            results.append(doc)
        return results
