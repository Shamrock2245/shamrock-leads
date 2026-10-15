"""
ShamrockLeads — SwipeSimple Receipt Gmail Poller
================================================
Same pattern as Bail School GAS (`BailSchoolPayments.js` pollSwipeSimpleReceipts):

  SwipeSimple has unreliable/no public outbound webhooks for every channel.
  We poll admin Gmail for unread receipts from noreply@swipesimple.com,
  parse amount + customer identity, and log/apply payments for **bond leads**.

School amounts ($199 / $649) are **skipped** here (handled by GAS school unlock).
Bond premiums are recorded in one transaction. ``last_payment_*`` is stamped
only when the receipt states a booking, exactly one bond matches it, and no
refund or failure sibling exists. A blank booking is not inferred from email.

Uses existing `GmailReaderService` (Google free API tier) — no new vendor.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

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
                # Mark read only after the payment transaction commits, or when
                # this receipt was already committed on an earlier run.
                if applied.get("success") and (applied.get("committed") or applied.get("duplicate")):
                    if applied.get("committed"):
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
        # Only a booking written on the receipt counts. Do not copy one off a bond.
        booking = parsed.get("booking_number") or ""
        txn = parsed.get("transaction_id") or gmail_message_id

        from dashboard.services.payment_log_claims import (
            PaymentLogError,
            TransactionsUnavailable,
            record_provider_payment,
        )

        try:
            recorded = await record_provider_payment(
                db,
                source="swipesimple_gmail_poll",
                transaction_id=txn,
                amount=amount,
                status="approved",
                booking_number=booking,
                extra={
                    "event_type": "payment.approved",
                    "gmail_message_id": gmail_message_id,
                    "customer_email": customer_email or "",
                    "subject": (raw_subject or "")[:200],
                },
            )
        except TransactionsUnavailable as exc:
            return {"success": False, "committed": False, "error": exc.error}
        except PaymentLogError as exc:
            return {"success": False, "committed": False, "error": exc.error}

        await _notify_clean_charge(recorded)
        await _maybe_send_customer_receipt(
            db,
            recorded,
            phone=str(parsed.get("indemnitor_phone") or ""),
        )

        return {
            "success": True,
            "committed": bool(recorded.get("committed")),
            "duplicate": bool(recorded.get("duplicate")),
            "amount": recorded.get("amount"),
            "booking_number": recorded.get("booking_number") or "",
            "transaction_id": recorded.get("transaction_id"),
        }


async def _notify_clean_charge(recorded: Dict[str, Any]) -> None:
    """One #leads note after a clean poller commit. A review or a replay does not post."""
    if not (
        recorded.get("committed")
        and recorded.get("event_kind") == "charge"
        and recorded.get("stamped")
        and not recorded.get("needs_review")
    ):
        return
    slack = os.getenv("SLACK_WEBHOOK_LEADS") or ""
    if not slack:
        return
    booking = str(recorded.get("booking_number") or "")
    try:
        amount_text = f"{float(recorded.get('amount')):.2f}"
    except (TypeError, ValueError):
        amount_text = ""
    text = f":moneybag: *SwipeSimple receipt (Gmail poll)* — ${amount_text}"
    if booking:
        text += f" | Booking: {booking}"
    try:
        import httpx
        from core.booking_identity import redact_internal_keys

        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(slack, json=redact_internal_keys({"text": text}))
    except Exception:
        logger.debug("[SwipeSimplePoll] payment-received note failed")


async def _maybe_send_customer_receipt(db, recorded: Dict[str, Any], *, phone: str) -> None:
    """Send the BlueBubbles receipt only if this caller wins the sending claim."""
    phone = str(phone or "").strip()
    if not phone:
        return
    if recorded.get("event_kind") != "charge":
        return
    if not (recorded.get("committed") or recorded.get("duplicate")):
        return
    txn = str(recorded.get("transaction_id") or "")
    amount = recorded.get("amount")
    try:
        amount_text = f"{float(amount):.2f}"
    except (TypeError, ValueError):
        amount_text = ""
    from dashboard.services.payment_log_claims import send_customer_receipt_once

    await send_customer_receipt_once(
        db,
        txn,
        phone,
        (
            "✅ Payment received! Thank you.\n"
            f"Amount: ${amount_text}\n"
            f"Transaction: {txn}\n"
            "Shamrock Bail Bonds — (239) 332-2245"
        ),
    )


async def run_swipesimple_receipt_poll(config: Optional[dict] = None) -> Dict[str, Any]:
    """Entry point for cron / automation_control."""
    cfg = config or {}
    poller = SwipeSimpleReceiptPoller()
    return await poller.poll(
        max_messages=int(cfg.get("limit") or 20),
        mark_read=bool(cfg.get("mark_read", True)),
    )
