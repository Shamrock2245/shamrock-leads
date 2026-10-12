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
        claim = await claims.find_one({"transaction_id": txn, "kind": kind})
        return {"action": "existing", "row": row, "claim": claim}
    if kind == CHARGE:
        plan_claim = await db[PLAN_CLAIMS_COLLECTION].find_one({"transaction_id": txn})
        if plan_claim is not None:
            return {"action": "existing", "row": None, "claim": None, "plan_claim": plan_claim}

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


async def ensure_claim(db, txn: str, kind: str) -> Optional[Dict[str, Any]]:
    """Return the claim doc for (txn, kind), creating a ``logged`` one for a
    charge recorded by someone else (poller row, plan claim, legacy row) so
    webhook step flags have somewhere to live. None if the store is down."""
    txn = str(txn or "").strip()
    try:
        claims = await _claims(db)
        try:
            await claims.insert_one({"transaction_id": txn, "kind": kind, "source": "existing_row",
                                     "status": "logged",
                                     "claimed_at": datetime.now(timezone.utc).isoformat()})
        except Exception:
            pass
        return await claims.find_one({"transaction_id": txn, "kind": kind})
    except Exception as exc:
        logger.warning("[payment_log_claims] ensure_claim failed err=%s", type(exc).__name__)
        return None


async def take_step(db, txn: str, kind: str, step: str) -> bool:
    """Atomically take a once-only webhook step (``wh_<step>`` flag). True for
    exactly one caller; False if already taken or on error (fail closed: a
    customer text or alert is never sent twice)."""
    try:
        res = await db[LOG_CLAIMS_COLLECTION].update_one(
            {"transaction_id": str(txn).strip(), "kind": kind, f"wh_{step}": {"$exists": False}},
            {"$set": {f"wh_{step}": datetime.now(timezone.utc).isoformat()}},
        )
        return bool(getattr(res, "modified_count", 0))
    except Exception as exc:
        logger.warning("[payment_log_claims] take_step %s failed err=%s", step, type(exc).__name__)
        return False


async def step_done(claim: Optional[Dict[str, Any]], step: str) -> bool:
    return bool((claim or {}).get(f"wh_{step}"))


async def mark_step(db, txn: str, kind: str, step: str) -> None:
    """Record a retryable step (event / audit / completed) as done. Never raises."""
    try:
        await db[LOG_CLAIMS_COLLECTION].update_one(
            {"transaction_id": str(txn).strip(), "kind": kind},
            {"$set": {f"wh_{step}": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception as exc:
        logger.warning("[payment_log_claims] mark_step %s failed err=%s", step, type(exc).__name__)
