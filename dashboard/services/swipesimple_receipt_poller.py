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

    reference = None
    ref_match = re.search(r"\b([A-Za-z0-9][A-Za-z0-9_\-]{0,48}-I\d{1,2})\b", text)
    if ref_match:
        reference = ref_match.group(1).strip()

    txn = None
    tm = _TX_RE.search(text)
    if tm:
        txn = tm.group(1).strip()

    return {
        "amount": amount,
        "customer_email": customer_email,
        "booking_number": booking,
        "reference_id": reference,
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
                # Mark read after a committed charge, a confirmed duplicate, a
                # posted installment, or a terminal plan refusal. A recorder
                # error leaves the Gmail message unread.
                # A charge replay that already has a reversal is needs_review.
                # Mark the receipt read and do not treat it as a new payment.
                review_hold = (
                    applied.get("success")
                    and applied.get("needs_review")
                    and not applied.get("committed")
                )
                posted = applied.get("success") and (
                    applied.get("committed")
                    or applied.get("duplicate")
                    or applied.get("plan_applied")
                    or review_hold
                )
                if posted or applied.get("terminal"):
                    if applied.get("committed") or applied.get("plan_applied"):
                        result["processed"] += 1
                        result["payments"].append(applied)
                    elif applied.get("terminal"):
                        result["skipped_other"] += 1
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

        # Idempotency is the plan claim when this receipt is an installment.
        # A booking mismatch returns terminal and does not insert a payment.
        # Anything else is recorded by the fail-closed provider writer.
        from fastapi.responses import JSONResponse

        from dashboard.services.payment_log_claims import (
            PaymentLogError,
            TransactionsUnavailable,
            record_provider_payment,
        )
        from dashboard.services.plan_payment_poster import post_gateway_event

        routed = await post_gateway_event(
            {
                "event_type": "payment.completed",
                "status": "approved",
                "transaction_id": txn,
                "amount": amount,
                "booking_number": booking,
                "reference_id": parsed.get("reference_id") or "",
                "custom_fields": {
                    "booking_number": booking,
                    "reference_id": parsed.get("reference_id") or "",
                    "plan_id": parsed.get("plan_id") or "",
                    "installment_n": parsed.get("installment_n"),
                },
            },
            actor="swipesimple_gmail_poll",
        )
        if isinstance(routed, JSONResponse):
            code = "booking_mismatch"
            try:
                body = routed.body
                if isinstance(body, bytes):
                    import json as _json
                    parsed_body = _json.loads(body.decode() or "{}")
                    code = str(parsed_body.get("error") or code)
            except Exception:
                pass
            logger.info("[SwipeSimplePoll] plan event refused code=%s", code)
            return {
                "success": False,
                "terminal": True,
                "error": code,
                "transaction_id": txn,
            }
        if isinstance(routed, dict) and routed.get("handled"):
            return {
                "success": True,
                "plan_applied": True,
                "idempotent": bool(routed.get("idempotent")),
                "committed": not bool(routed.get("idempotent")),
                "duplicate": bool(routed.get("idempotent")),
                "transaction_id": txn,
                "booking_number": booking,
                "amount": amount,
            }

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
        await _note_cosigner_down_payment(recorded, booking=booking, amount=amount)

        return {
            "success": True,
            "committed": bool(recorded.get("committed")),
            "duplicate": bool(recorded.get("duplicate")),
            "needs_review": bool(recorded.get("needs_review")),
            "amount": recorded.get("amount"),
            "booking_number": recorded.get("booking_number") or "",
            "transaction_id": recorded.get("transaction_id"),
        }


async def _note_cosigner_down_payment(recorded: Dict[str, Any], *, booking: Any, amount: Any) -> None:
    """The Gmail poller is the payment source when webhooks are not delivered.

    A clean charge for this booking notes the co-signer link. An installment
    already returned above. A review hold does not count as the down payment.
    """
    if recorded.get("event_kind") != "charge":
        return
    if recorded.get("needs_review"):
        return
    if not (recorded.get("committed") or recorded.get("duplicate")):
        return
    reference = str(recorded.get("booking_number") or booking or "").strip()
    if not reference or "-I" in reference:
        return
    try:
        from dashboard.services.cosigner_link_service import note_cosigner_paid

        await note_cosigner_paid(
            reference,
            str(recorded.get("transaction_id") or ""),
            amount=recorded.get("amount") if recorded.get("amount") is not None else amount,
        )
    except Exception as exc:
        logger.info("[SwipeSimplePoll] cosigner paid note skipped code=%s", type(exc).__name__)


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
    if recorded.get("needs_review") and not recorded.get("committed"):
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
