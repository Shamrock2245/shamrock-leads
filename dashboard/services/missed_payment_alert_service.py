"""
Missed Payment Alert Service — ShamrockLeads
=============================================
Scans active payment plans for overdue installments and fires:
  - BlueBubbles iMessage to the defendant/indemnitor with a geo check-in link
  - SSE event: payment_missed
  - MongoDB log in missed_payment_alerts collection

Alert thresholds (configurable via env):
  MISSED_PAYMENT_GRACE_DAYS  — days past due before first alert (default: 3)
  MISSED_PAYMENT_ESCALATE_DAYS — days past due before escalation alert (default: 7)

Called by cron.py every 12 hours.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from dashboard.extensions import get_collection

log = logging.getLogger("shamrock.missed_payment")

_PUBLIC_URL = os.getenv("DASHBOARD_PUBLIC_URL", "https://shamrockbailbonds.biz")
_GRACE_DAYS = int(os.getenv("MISSED_PAYMENT_GRACE_DAYS", "3"))
_ESCALATE_DAYS = int(os.getenv("MISSED_PAYMENT_ESCALATE_DAYS", "7"))


class MissedPaymentAlertService:
    """Scans payment plans and sends BB alerts for overdue installments."""

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────
    async def scan_and_alert(self) -> dict:
        """
        Scan all active payment plans for overdue installments.
        Returns: { scanned, alerted, escalated, errors }
        """
        plans_col = get_collection("payment_plans")
        alerts_col = get_collection("missed_payment_alerts")
        now = datetime.now(timezone.utc)
        grace_cutoff = (now - timedelta(days=_GRACE_DAYS)).isoformat()

        cursor = plans_col.find({
            "status": "active",
            "next_due_date": {"$lt": grace_cutoff},
        }).sort("next_due_date", 1)

        scanned = alerted = escalated = errors = 0

        async for plan in cursor:
            scanned += 1
            booking = plan.get("booking_number", "")
            plan_id = str(plan.get("_id", ""))

            try:
                # Calculate days overdue
                due_str = plan.get("next_due_date", "")
                try:
                    due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
                    days_overdue = (now - due_dt).days
                except (ValueError, TypeError):
                    days_overdue = _GRACE_DAYS

                # Check if we already sent an alert for this installment recently (within 24h)
                recent = await alerts_col.find_one({
                    "booking_number": booking,
                    "plan_id": plan_id,
                    "due_date": due_str,
                    "alerted_at": {"$gt": (now - timedelta(hours=24)).isoformat()},
                })
                if recent:
                    continue

                is_escalation = days_overdue >= _ESCALATE_DAYS
                amount_due = plan.get("installment_amount", plan.get("amount_due", 0))

                # Get contact info from active_bonds
                bond = await get_collection("active_bonds").find_one(
                    {"booking_number": booking}, {"_id": 0}
                )

                # Determine best phone to contact
                phone = self._best_phone(plan, bond)
                if not phone:
                    log.warning("[MissedPayment] No phone for booking %s", booking)
                    continue

                # Generate geo link
                geo_url = await self._make_geo_link(booking, phone, "missed_payment")

                # Build and send message
                msg = self._build_message(plan, bond, amount_due, days_overdue, geo_url, is_escalation)
                sent = await self._send_bb(phone, msg)

                # Log the alert
                alert_doc = {
                    "booking_number": booking,
                    "plan_id": plan_id,
                    "due_date": due_str,
                    "days_overdue": days_overdue,
                    "amount_due": amount_due,
                    "phone": phone,
                    "is_escalation": is_escalation,
                    "bb_sent": sent,
                    "alerted_at": now.isoformat(),
                }
                await alerts_col.insert_one(alert_doc)

                # Emit SSE event
                try:
                    from dashboard.routers.events import emit_event
                    defendant_name = (bond or {}).get("defendant_name", plan.get("defendant_name", ""))
                    await emit_event("payment_missed", {
                        "booking_number": booking,
                        "defendant_name": defendant_name,
                        "amount_due": amount_due,
                        "days_overdue": days_overdue,
                        "is_escalation": is_escalation,
                    })
                except Exception:
                    pass

                if is_escalation:
                    escalated += 1
                else:
                    alerted += 1

                log.warning(
                    "[MissedPayment] %s booking=%s days_overdue=%d amount=$%.0f bb=%s",
                    "ESCALATION" if is_escalation else "ALERT",
                    booking, days_overdue, amount_due, sent,
                )

            except Exception as e:
                errors += 1
                log.error("[MissedPayment] Error processing plan %s: %s", plan_id, e)

        log.info(
            "[MissedPayment] Scan complete: scanned=%d alerted=%d escalated=%d errors=%d",
            scanned, alerted, escalated, errors,
        )
        return {"scanned": scanned, "alerted": alerted, "escalated": escalated, "errors": errors}

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────
    def _best_phone(self, plan: dict, bond: Optional[dict]) -> str:
        """Return the best available phone number to contact."""
        candidates = []
        if bond:
            candidates += [
                bond.get("defendant_phone"),
                bond.get("defendant_cell"),
                bond.get("indemnitor_phone"),
                bond.get("indemnitor_cell"),
                bond.get("indemnitor_phone_1"),
            ]
        candidates += [
            plan.get("defendant_phone"),
            plan.get("contact_phone"),
        ]
        for p in candidates:
            if p and str(p).strip():
                return str(p).strip()
        return ""

    async def _make_geo_link(self, booking: str, phone: str, recipient: str) -> str:
        """Generate a geo check-in token and store it in geo_pings."""
        token = secrets.token_urlsafe(12)
        try:
            geo_col = get_collection("geo_pings")
            await geo_col.insert_one({
                "token": token,
                "booking_number": booking,
                "phone": phone,
                "recipient": recipient,
                "status": "pending",
                "ping_count": 0,
                "pings": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            log.warning("[MissedPayment] geo_pings insert failed: %s", e)
            return ""
        return f"{_PUBLIC_URL.rstrip('/')}/g/{token}"

    def _build_message(
        self,
        plan: dict,
        bond: Optional[dict],
        amount_due: float,
        days_overdue: int,
        geo_url: str,
        is_escalation: bool,
    ) -> str:
        """Build the BB iMessage text for a missed payment alert."""
        defendant_name = (bond or {}).get("defendant_name", plan.get("defendant_name", ""))
        first_name = defendant_name.split(",")[-1].strip().split()[0] if defendant_name else "there"
        booking = plan.get("booking_number", "")
        amount_str = f"${amount_due:,.0f}" if amount_due else "your scheduled payment"
        geo_line = f"\n\nConfirm your location: {geo_url}" if geo_url else ""

        if is_escalation:
            return (
                f"URGENT — {first_name}, this is Shamrock Bail Bonds. "
                f"Your payment of {amount_str} for booking #{booking} is now {days_overdue} days past due. "
                f"Failure to pay may result in bond forfeiture and a warrant for your arrest. "
                f"You must contact us immediately at (239) 332-2245 to make arrangements."
                f"{geo_line}\n\n☘️ Shamrock Bail Bonds (239) 332-2245"
            )
        else:
            return (
                f"Hi {first_name} — this is Shamrock Bail Bonds. "
                f"A payment of {amount_str} for booking #{booking} was due {days_overdue} day{'s' if days_overdue != 1 else ''} ago and has not been received. "
                f"Please call us at (239) 332-2245 or reply to this message to make your payment."
                f"{geo_line}\n\n☘️ Shamrock Bail Bonds (239) 332-2245"
            )

    async def _send_bb(self, phone: str, message: str) -> bool:
        """Send via BlueBubbles; return True on success."""
        try:
            from dashboard.services.bb_client import send_message_universal
            result = await send_message_universal(phone, message)
            return result.get("success", False)
        except Exception as e:
            log.warning("[MissedPayment] BB send failed to %s: %s", phone, e)
            return False
