"""
ShamrockLeads — SwipeSimple Receipt Gmail Poller
================================================
Same pattern as Bail School GAS (`BailSchoolPayments.js` pollSwipeSimpleReceipts):

  SwipeSimple has unreliable/no public outbound webhooks for every channel.
  We poll admin Gmail for unread receipts from noreply@swipesimple.com,
  parse amount + customer identity, and log/apply payments for **bond leads**.

School amounts ($199 / $649) are **skipped** here (handled by GAS school unlock).
Bond premiums and other amounts are written to `payments` + bond_cases when matched.

Uses existing `GmailReaderService` (Google free API tier) — no new vendor.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Bail School prices — leave for GAS poller (do not double-unlock courses)
SCHOOL_AMOUNTS = {199.0, 649.0}

SWIPESIMPLE_FROM = "noreply@swipesimple.com"

_AMOUNT_RE = re.compile(r"\$([0-9,]+\.[0-9]{2})")
_EMAIL_RE = re.compile(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})")
_BOOKING_RE = re.compile(
    r"(?:booking|book(?:ing)?\s*(?:#|num(?:ber)?)?)\s*[:#]?\s*([A-Za-z0-9\-]+)",
    re.I,
)
_TX_RE = re.compile(
    r"(?:transaction|txn|confirmation|ref(?:erence)?)\s*(?:id|#|num(?:ber)?)?\s*[:#]?\s*([A-Za-z0-9\-]+)",
    re.I,
)

_SYSTEM_EMAILS = {
    "noreply@swipesimple.com",
    "admin@shamrockbailbonds.biz",
    "support@swipesimple.com",
}


def parse_swipesimple_receipt(subject: str, body: str) -> Dict[str, Any]:
    """
    Extract amount, customer email, optional booking, transaction id from receipt text.
    """
    text = f"{subject or ''}\n{body or ''}"
    amount = None
    m = _AMOUNT_RE.search(text)
    if m:
        try:
            amount = float(m.group(1).replace(",", ""))
        except ValueError:
            amount = None

    customer_email = None
    for match in _EMAIL_RE.finditer(text):
        found = match.group(1).lower()
        if found in _SYSTEM_EMAILS:
            continue
        if "swipesimple" in found:
            continue
        customer_email = found
        break

    booking = None
    bm = _BOOKING_RE.search(text)
    if bm:
        booking = bm.group(1).strip()

    txn = None
    tm = _TX_RE.search(text)
    if tm:
        txn = tm.group(1).strip()

    return {
        "amount": amount,
        "customer_email": customer_email,
        "booking_number": booking,
        "transaction_id": txn,
        "subject": subject or "",
    }


class SwipeSimpleReceiptPoller:
    """
    Poll Gmail for SwipeSimple receipts and apply bond-side payment updates.
    """

    def __init__(self, db=None):
        self.db = db

    def _get_db(self):
        if self.db is not None:
            return self.db
        from dashboard.extensions import get_db

        return get_db()

    async def poll(self, *, max_messages: int = 20, mark_read: bool = True) -> Dict[str, Any]:
        from dashboard.services.gmail_reader import GmailReaderService

        reader = GmailReaderService()
        result: Dict[str, Any] = {
            "ok": True,
            "configured": reader.is_configured,
            "scanned": 0,
            "processed": 0,
            "skipped_school": 0,
            "skipped_other": 0,
            "errors": 0,
            "payments": [],
        }
        if not reader.is_configured:
            result["ok"] = False
            result["error"] = "gmail_not_configured"
            return result

        emails = reader.search_messages(
            f"from:{SWIPESIMPLE_FROM} is:unread newer_than:2d",
            max_results=max_messages,
        )
        result["scanned"] = len(emails)

        db = self._get_db()
        for email_data in emails:
            mid = email_data.get("message_id") or ""
            try:
                parsed = parse_swipesimple_receipt(
                    email_data.get("subject") or "",
                    email_data.get("body") or "",
                )
                amount = parsed.get("amount")
                if amount is None:
                    result["skipped_other"] += 1
                    if mark_read and mid:
                        reader.mark_as_read(mid)
                    continue

                # School receipts → leave for GAS (or mark read if already handled)
                if float(amount) in SCHOOL_AMOUNTS:
                    result["skipped_school"] += 1
                    # Do not mark read — school GAS poller owns those threads
                    continue

                applied = await self._apply_bond_payment(
                    db,
                    parsed,
                    gmail_message_id=mid,
                    raw_subject=email_data.get("subject") or "",
                )
                if applied.get("success"):
                    result["processed"] += 1
                    result["payments"].append(applied)
                    if mark_read and mid:
                        reader.mark_as_read(mid)
                else:
                    result["skipped_other"] += 1
                    if applied.get("error"):
                        result["errors"] += 1
                        logger.warning(
                            "[SwipeSimplePoll] skip mid=%s: %s",
                            mid[:12],
                            applied.get("error"),
                        )
            except Exception as exc:
                result["errors"] += 1
                logger.exception("[SwipeSimplePoll] message failed: %s", exc)

        if result["processed"]:
            logger.info(
                "[SwipeSimplePoll] processed=%s school_skip=%s errors=%s",
                result["processed"],
                result["skipped_school"],
                result["errors"],
            )
        return result

    async def _apply_bond_payment(
        self,
        db,
        parsed: Dict[str, Any],
        *,
        gmail_message_id: str,
        raw_subject: str,
    ) -> Dict[str, Any]:
        amount = parsed.get("amount")
        customer_email = parsed.get("customer_email")
        booking = parsed.get("booking_number") or ""
        txn = parsed.get("transaction_id") or gmail_message_id
        now = datetime.now(timezone.utc).isoformat()

        # Idempotency
        payments = db["payments"]
        existing = await payments.find_one(
            {
                "$or": [
                    {"transaction_id": txn},
                    {"gmail_message_id": gmail_message_id},
                ]
            }
        )
        if existing:
            return {"success": True, "duplicate": True, "transaction_id": txn}

        match_query: Dict[str, Any] = {}
        if booking:
            match_query = {"booking_number": booking}
        elif customer_email:
            match_query = {
                "$or": [
                    {"indemnitor_email": {"$regex": f"^{re.escape(customer_email)}$", "$options": "i"}},
                    {"Indemnitor_Email": {"$regex": f"^{re.escape(customer_email)}$", "$options": "i"}},
                ]
            }

        bond = None
        if match_query:
            bond = await db["bond_cases"].find_one(match_query)
            if not bond:
                bond = await db["active_bonds"].find_one(match_query)
            if not bond and customer_email:
                bond = await db["intake_queue"].find_one(
                    {
                        "$or": [
                            {"indemnitor_email": {"$regex": f"^{re.escape(customer_email)}$", "$options": "i"}},
                            {"indemnitor.email": {"$regex": f"^{re.escape(customer_email)}$", "$options": "i"}},
                        ]
                    }
                )

        booking_number = booking or (bond or {}).get("booking_number") or ""

        doc = {
            "transaction_id": txn,
            "gmail_message_id": gmail_message_id,
            "source": "swipesimple_gmail_poll",
            "amount": amount,
            "status": "approved",
            "customer_email": customer_email,
            "customer_name": "",
            "booking_number": booking_number,
            "subject": raw_subject[:200],
            "matched_bond": bool(bond),
            "created_at": now,
        }
        await payments.insert_one(doc)

        if booking_number:
            payment_update = {
                "last_payment_amount": amount,
                "last_payment_at": now,
                "last_payment_status": "approved",
                "last_transaction_id": txn,
                "last_payment_source": "swipesimple_gmail_poll",
            }
            await db["bond_cases"].update_one(
                {"booking_number": booking_number},
                {"$set": payment_update},
            )
            await db["active_bonds"].update_one(
                {"booking_number": booking_number},
                {"$set": payment_update},
            )

        # Audit (no PII dump of full body)
        try:
            await db["audit_events"].insert_one(
                {
                    "source": "swipesimple_gmail_poll",
                    "event_type": "payment.approved",
                    "timestamp": now,
                    "payload": {
                        "amount": amount,
                        "booking_number": booking_number,
                        "transaction_id": txn,
                        "matched": bool(bond),
                        "has_customer_email": bool(customer_email),
                    },
                }
            )
        except Exception:
            pass

        # Slack (optional)
        try:
            import httpx

            slack = os.getenv("SLACK_WEBHOOK_LEADS") or os.getenv("SLACK_WEBHOOK_URL") or ""
            if slack:
                async with httpx.AsyncClient(timeout=8) as client:
                    await client.post(
                        slack,
                        json={
                            "text": (
                                f":moneybag: *SwipeSimple receipt (Gmail poll)* — "
                                f"${amount:.2f}"
                                f"{f' | Booking: {booking_number}' if booking_number else ''}"
                                f"{' | unmatched' if not bond else ''}"
                            )
                        },
                    )
        except Exception as exc:
            logger.debug("[SwipeSimplePoll] slack failed: %s", exc)

        return {
            "success": True,
            "amount": amount,
            "booking_number": booking_number,
            "transaction_id": txn,
            "matched": bool(bond),
        }


async def run_swipesimple_receipt_poll(config: Optional[dict] = None) -> Dict[str, Any]:
    """Entry point for cron / automation_control."""
    cfg = config or {}
    poller = SwipeSimpleReceiptPoller()
    return await poller.poll(
        max_messages=int(cfg.get("limit") or 20),
        mark_read=bool(cfg.get("mark_read", True)),
    )
