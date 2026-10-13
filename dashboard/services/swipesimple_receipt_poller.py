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

# Same shape as dashboard.extensions.parse_registered_county: ``Lee (FL)`` carries
# the state in the label. A bare county name does not imply a state.
_COUNTY_LABEL_RE = re.compile(r"^(.+?)\s*\(([A-Za-z]{2})\)$")

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


def _first_text(doc: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        token = str(doc.get(key) or "").strip()
        if token:
            return token
    return ""


def jurisdiction_from_bond(bond: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Booking identity ``_booking_identity`` can compare, copied off a bond.

    Returns county, state, and booking number when the bond actually has them.
    A ``County (ST)`` label supplies the state. A bare county with no state
    field is unverified and returns None. Never invents a state.
    """
    if not isinstance(bond, dict):
        return None
    raw_county = _first_text(bond, "county", "County")
    raw_state = _first_text(bond, "state", "State")
    bare, label_state = raw_county, ""
    labeled = _COUNTY_LABEL_RE.match(raw_county)
    if labeled:
        bare = labeled.group(1).strip()
        label_state = labeled.group(2).upper()
    if raw_state and label_state and raw_state.casefold() != label_state.casefold():
        return None
    state = raw_state or label_state
    booking = _first_text(bond, "booking_number")
    if not bare or not state:
        return None
    out = {"county": bare, "state": state}
    if booking:
        out["booking_number"] = booking
    return out


def agreed_jurisdiction(bonds: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """The one complete booking identity every bond shares, or None.

    Every candidate must supply booking number, county, and state, and those
    three must all agree. A bond missing any of them is not skipped: the match
    is ambiguous and this returns None. Callers then do not stamp a receipt
    or backfill a plan.
    """
    if not bonds:
        return None
    found: List[Dict[str, str]] = []
    for bond in bonds:
        item = jurisdiction_from_bond(bond)
        if not item or not item.get("booking_number"):
            return None
        found.append(item)
    identities = {
        (item["booking_number"], item["county"].casefold(), item["state"].casefold())
        for item in found
    }
    if len(identities) != 1:
        return None
    return found[0]


async def _docs_for_query(coll, query: Dict[str, Any], limit: int = 20) -> Optional[List[Dict[str, Any]]]:
    """Matching documents, or None when the read failed or the set is too large."""
    try:
        count = await coll.count_documents(query)
    except Exception:
        return None
    if not count:
        return []
    if count > limit:
        return None
    try:
        cursor = coll.find(query)
        if hasattr(cursor, "to_list"):
            return await cursor.to_list(length=limit)
        docs: List[Dict[str, Any]] = []
        async for doc in cursor:
            docs.append(doc)
        return docs
    except Exception:
        return None


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

        bonds: List[Dict[str, Any]] = []
        jurisdiction_known = True
        if booking:
            for collection in ("bond_cases", "active_bonds", "intake_queue"):
                found = await _docs_for_query(db[collection], {"booking_number": booking})
                if found is None:
                    jurisdiction_known = False
                    break
                bonds.extend(found)
        if not bonds and jurisdiction_known and customer_email:
            email_query = {
                "$or": [
                    {"indemnitor_email": {"$regex": f"^{re.escape(customer_email)}$", "$options": "i"}},
                    {"Indemnitor_Email": {"$regex": f"^{re.escape(customer_email)}$", "$options": "i"}},
                ]
            }
            bond = await db["bond_cases"].find_one(email_query)
            if not bond:
                bond = await db["active_bonds"].find_one(email_query)
            if not bond:
                bond = await db["intake_queue"].find_one(
                    {
                        "$or": [
                            {"indemnitor_email": {"$regex": f"^{re.escape(customer_email)}$", "$options": "i"}},
                            {"indemnitor.email": {"$regex": f"^{re.escape(customer_email)}$", "$options": "i"}},
                        ]
                    }
                )
            if bond:
                bonds = [bond]

        jurisdiction = agreed_jurisdiction(bonds) if jurisdiction_known else None
        bond = bonds[0] if bonds else None
        booking_number = booking or (jurisdiction or {}).get("booking_number") or (bond or {}).get("booking_number") or ""
        # A receipt booking that disagrees with the bond's booking is not that bond.
        if jurisdiction and jurisdiction.get("booking_number") and booking and jurisdiction["booking_number"] != booking:
            jurisdiction = None

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
        if jurisdiction:
            # Same keys _booking_identity reads. Omitted when the match is
            # missing or split across counties/states, so /pay fails closed.
            doc["county"] = jurisdiction["county"]
            doc["state"] = jurisdiction["state"]
        if "_id" not in doc:
            from bson import ObjectId
            doc["_id"] = ObjectId()
        # Unique txn_dedupe_key: a plan /pay or webhook insert of this id wins
        # the race instead of leaving two payments rows.
        try:
            from dashboard.services.payment_txn_dedupe import insert_payment_once

            inserted = await insert_payment_once(payments, doc)
        except Exception as exc:
            logger.warning(
                "[SwipeSimplePoll] payment dedupe unavailable txn=%s err=%s",
                txn, type(exc).__name__,
            )
            return {"success": False, "error": "payment_dedupe_unavailable", "transaction_id": txn}
        if not inserted:
            return {"success": True, "duplicate": True, "transaction_id": txn}

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
