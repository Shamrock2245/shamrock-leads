"""Transaction-id claims for the provider payment *loggers* (receipt poller and
POST /api/webhooks/payment), so they never insert a second ``payments`` row
for a charge that is already recorded or being recorded.

Separate collection ``payment_log_claims``, unique index ``uniq_tenant_txn_kind``
on (tenant_id, transaction_id, kind). ``kind`` is ``charge`` for a settled
charge (poller receipt, webhook ``payment.completed``) and the normalized
webhook event otherwise (``payment.refunded`` …), so refund / failure events
are still logged once each and are never swallowed by the charge.

The plan claims (``payment_plan_payment_claims``, #179/#181) stay the plan
side's gate: a logger that finds a plan claim or any existing row for the
charge skips its insert (and may attach to the existing row) instead of
duplicating it. There is deliberately NO unique index on ``payments`` itself
(prod has historical duplicates; that needs a cleanup and an explicit OK).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

LOG_CLAIMS_COLLECTION = "payment_log_claims"
PLAN_CLAIMS_COLLECTION = "payment_plan_payment_claims"
CHARGE = "charge"

_index_ready = False


def claim_kind(event_type: Optional[str]) -> str:
    ev = str(event_type or "").strip().lower()
    if not ev or ev == "payment.completed":
        return CHARGE
    return ev


async def _claims(db) -> Any:
    global _index_ready
    coll = db[LOG_CLAIMS_COLLECTION]
    if not _index_ready:
        await coll.create_index(
            [("tenant_id", 1), ("transaction_id", 1), ("kind", 1)],
            unique=True, name="uniq_tenant_txn_kind",
        )
        _index_ready = True
    return coll


def _existing_filter(txn: str, kind: str) -> Dict[str, Any]:
    if kind == CHARGE:
        # Any row for this txn that is not a separate non-charge webhook event
        # (a poller row, a webhook payment.completed row, a plan row).
        return {"transaction_id": txn, "$or": [
            {"raw_payload.event_type": {"$exists": False}},
            {"raw_payload.event_type": {"$in": ["payment.completed", "PAYMENT.COMPLETED"]}},
        ]}
    return {"transaction_id": txn, "raw_payload.event_type": kind}


async def begin(db, txn: str, kind: str, source: str) -> Dict[str, Any]:
    """Decide whether this logger may insert a payments row.

    Returns ``{"action": "insert"}`` (claim held: insert, then ``finish``),
    ``{"action": "existing", "row": <doc or None>, "claim": <doc or None>}``
    (already recorded or claimed by someone else: do not insert), or
    ``{"action": "unavailable"}`` (claim store down: caller must not insert).
    """
    from pymongo.errors import DuplicateKeyError

    txn = str(txn or "").strip()
    if not txn:
        return {"action": "insert", "unclaimed": True}
    try:
        claims = await _claims(db)
    except Exception as exc:
        logger.warning("[payment_log_claims] index unavailable err=%s", type(exc).__name__)
        return {"action": "unavailable"}

    payments = db["payments"]
    row = await payments.find_one(_existing_filter(txn, kind))
    if row is not None:
        return {"action": "existing", "row": row, "claim": None}
    if kind == CHARGE:
        plan_claim = await db[PLAN_CLAIMS_COLLECTION].find_one({"transaction_id": txn})
        if plan_claim is not None:
            return {"action": "existing", "row": None, "claim": plan_claim}

    now = datetime.now(timezone.utc).isoformat()
    try:
        await claims.insert_one({"transaction_id": txn, "kind": kind, "source": source,
                                 "status": "claimed", "claimed_at": now})
    except DuplicateKeyError:
        claim = await claims.find_one({"transaction_id": txn, "kind": kind})
        row = await payments.find_one(_existing_filter(txn, kind))
        return {"action": "existing", "row": row, "claim": claim}
    except Exception as exc:
        logger.warning("[payment_log_claims] claim insert failed err=%s", type(exc).__name__)
        return {"action": "unavailable"}
    return {"action": "insert"}


async def finish(db, txn: str, kind: str, row_id, *, ok: bool) -> bool:
    """After the insert attempt. ``ok=False``: the insert failed, release the
    claim so a retry can log it. ``ok=True``: post-write recheck: if a plan
    /pay claim or another row for the same charge appeared meanwhile (the plan
    side writes outside this claim), remove our row so the charge is recorded
    once, and return False. Never raises."""
    txn = str(txn or "").strip()
    if not txn:
        return ok
    try:
        claims = db[LOG_CLAIMS_COLLECTION]
        if not ok:
            await claims.delete_one({"transaction_id": txn, "kind": kind, "status": "claimed"})
            return False
        flt = _existing_filter(txn, kind)
        flt["_id"] = {"$ne": row_id}
        other = await db["payments"].find_one(flt)
        if other is not None and kind == CHARGE and str(other.get("plan_id") or "").strip():
            # Plan row won the race: it is canonical, drop ours.
            await db["payments"].delete_one({"_id": row_id})
            await claims.update_one({"transaction_id": txn, "kind": kind},
                                    {"$set": {"status": "superseded_by_plan_row"}})
            return False
        await claims.update_one({"transaction_id": txn, "kind": kind},
                                {"$set": {"status": "logged", "payment_id": str(row_id),
                                          "logged_at": datetime.now(timezone.utc).isoformat()}})
        return True
    except Exception as exc:
        logger.warning("[payment_log_claims] finish failed err=%s", type(exc).__name__)
        return ok


async def mark_webhook_seen(db, txn: str, kind: str, event_type: str) -> bool:
    """For a webhook whose charge was already logged by someone else: record
    the confirmation once. True the first time (caller continues its side
    effects once); False for a retry of the same webhook (caller stops)."""
    txn = str(txn or "").strip()
    now = datetime.now(timezone.utc).isoformat()
    try:
        claims = await _claims(db)
        # Make sure there is a claim doc to hang the flag on (legacy row / plan claim).
        try:
            await claims.insert_one({"transaction_id": txn, "kind": kind, "source": "existing_row",
                                     "status": "logged", "claimed_at": now})
        except Exception:
            pass
        res = await claims.update_one(
            {"transaction_id": txn, "kind": kind, "webhook_seen_at": {"$exists": False}},
            {"$set": {"webhook_seen_at": now, "webhook_event_type": event_type}},
        )
        return bool(getattr(res, "modified_count", 0))
    except Exception as exc:
        logger.warning("[payment_log_claims] webhook mark failed err=%s", type(exc).__name__)
        return False
