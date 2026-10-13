"""Transaction-id claims for the provider payment *loggers* (receipt poller and
POST /api/webhooks/payment), so they never insert a second ``payments`` row
for a charge that is already recorded or being recorded.

Separate collection ``payment_log_claims``, unique index ``uniq_tenant_txn_kind``
on (tenant_id, transaction_id, kind). ``kind`` is ``charge`` for a settled
charge (poller receipt, webhook ``payment.completed``) and the normalized
webhook event otherwise (``payment.refunded`` …), so refund / failure events
are still logged once each and are never swallowed by the charge.

The plan claims (``payment_plan_payment_claims``, #179/#181) stay the plan
side's gate. A logger skips its insert only when that claim is ``applied``
or a ``payments`` row for the charge already exists. A ``failed`` plan claim
recorded no payment, so the logger may record the provider charge. A plan
claim still ``claimed`` is in progress: the logger does not insert and does
not treat the charge as done. There is deliberately NO unique index on
``payments`` itself (prod has historical duplicates; that needs a cleanup
and an explicit OK).

A logger claim left ``claimed`` with no payment row (worker killed after the
claim insert) is reclaimable once ``claimed_at`` is at least ``STALE_AFTER``
old. Takeover is one conditional update matching that ``claimed_at``, so a
second worker cannot also insert.

Plan ``/pay`` inserts through the same charge claim (``coordinate_plan_insert``).
The plan claim collection alone is not that gate: a plan writer and a logger
can each see no opposite claim and each insert a payments row. Only the holder
of the charge claim may insert. If the logger already logged the charge, plan
``/pay`` applies onto that row and does not insert another.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

LOG_CLAIMS_COLLECTION = "payment_log_claims"
PLAN_CLAIMS_COLLECTION = "payment_plan_payment_claims"
CHARGE = "charge"
# A live poll or webhook finishes in seconds. 15 minutes matches the invoice
# claim window: long enough that an in-flight writer is not stolen, short
# enough that a killed worker is recovered on a later poll or provider retry.
STALE_AFTER = timedelta(minutes=15)

_index_ready = False


def _claimed_at_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str) and value.strip():
        try:
            ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def claim_is_stale(claimed_at: Any, *, now: Optional[datetime] = None) -> bool:
    """True when ``claimed_at`` is old enough to take over.

    Missing or unparseable timestamps are not stale: we cannot tell whether
    the owner is still writing, so the caller leaves the claim alone.
    """
    ts = _claimed_at_utc(claimed_at)
    if ts is None:
        return False
    return (now or datetime.now(timezone.utc)) - ts >= STALE_AFTER


def _modified(res: Any) -> int:
    return int(getattr(res, "modified_count", 0) or 0)


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


def _event_type_equals_ci(value: str) -> Dict[str, Any]:
    """Case-insensitive equality. Legacy webhook rows kept the provider's
    original casing (``PAYMENT.REFUNDED``, ``Payment.Failed``); ``claim_kind``
    stores the lowercase kind."""
    return {"$regex": f"^{re.escape(value)}$", "$options": "i"}


def _existing_filter(txn: str, kind: str) -> Dict[str, Any]:
    if kind == CHARGE:
        # Any row for this txn that is not a separate non-charge webhook event
        # (a poller row, a webhook payment.completed row, a plan row).
        return {"transaction_id": txn, "$or": [
            {"raw_payload.event_type": {"$exists": False}},
            {"raw_payload.event_type": _event_type_equals_ci("payment.completed")},
        ]}
    return {"transaction_id": txn, "raw_payload.event_type": _event_type_equals_ci(kind)}


def _money_cents(value: Any) -> Optional[int]:
    """Integer cents, or None when the value is missing or not a whole-cent amount."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dec = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not dec.is_finite():
        return None
    cents = dec * 100
    if cents != cents.to_integral_value():
        return None
    return int(cents)


# Plan /pay stores a settled installment as ``completed``. The poller and the
# webhook store the same settled card charge as ``approved``. Those are one
# payment fact. Declined and refunded are different facts.
_SETTLED_STATUSES = frozenset({"approved", "completed", "paid", "success"})
_DECLINED_STATUSES = frozenset({"declined", "failed", "denied"})
_REFUNDED_STATUSES = frozenset({"refunded", "refund"})


def _status_fact(value: Any) -> Optional[str]:
    status = str(value or "").strip().lower()
    if not status:
        return None
    if status in _SETTLED_STATUSES:
        return "settled"
    if status in _DECLINED_STATUSES:
        return "declined"
    if status in _REFUNDED_STATUSES:
        return "refunded"
    return status


def recorded_charge_conflicts(
    recorded: Optional[Dict[str, Any]],
    *,
    amount: Any,
    status: Any,
    booking_number: Any,
) -> bool:
    """True when an existing payment row disagrees with a payload about to
    drive bond updates, a receipt, or an audit.

    A fact that the row does not carry is not a conflict. A fact both sides
    carry must match: amount in whole cents, status (``completed`` and
    ``approved`` are the same settled charge), and booking number.
    """
    row = recorded or {}
    if row.get("amount") is not None:
        recorded_cents = _money_cents(row.get("amount"))
        incoming_cents = _money_cents(amount)
        if recorded_cents is None or incoming_cents is None or recorded_cents != incoming_cents:
            return True
    recorded_status = _status_fact(row.get("status"))
    incoming_status = _status_fact(status)
    if recorded_status and recorded_status != incoming_status:
        return True
    recorded_booking = str(row.get("booking_number") or "").strip()
    incoming_booking = str(booking_number or "").strip()
    if recorded_booking and incoming_booking and recorded_booking.casefold() != incoming_booking.casefold():
        return True
    return False


def _plan_claim_outcome(plan_claim: Dict[str, Any]) -> str:
    """``recorded`` (applied), ``failed`` (no payment; logger may record it),
    or ``in_progress`` (claimed / unknown: do not insert, do not mark done)."""
    status = str(plan_claim.get("status") or "").strip().lower()
    if status == "applied":
        return "recorded"
    if status == "failed":
        return "failed"
    return "in_progress"


async def _reconcile_stale_logged(claims, txn: str, kind: str, claim: Optional[Dict[str, Any]],
                                  row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Payment row exists but the claim is still a stale ``claimed``. The
    writer died after the insert. Flip that one claim to ``logged`` so a
    retry can finish the audit. Does not insert."""
    if (
        not claim
        or str(claim.get("status") or "") != "claimed"
        or not claim_is_stale(claim.get("claimed_at"))
        or row.get("_id") is None
    ):
        return claim
    now = datetime.now(timezone.utc).isoformat()
    try:
        await claims.update_one(
            {"transaction_id": txn, "kind": kind, "status": "claimed",
             "claimed_at": claim.get("claimed_at")},
            {"$set": {"status": "logged", "payment_id": str(row.get("_id")),
                      "logged_at": now, "reconciled_at": now}},
        )
    except Exception as exc:
        logger.warning("[payment_log_claims] reconcile failed err=%s", type(exc).__name__)
        return claim
    return await claims.find_one({"transaction_id": txn, "kind": kind})


async def _reclaim_stale(claims, txn: str, kind: str, source: str, prior_claimed_at: Any) -> Optional[str]:
    """Atomically take a stale ``claimed`` doc. The new ``claimed_at`` is the
    winner's token; a loser matches nothing and must not insert."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        res = await claims.update_one(
            {"transaction_id": txn, "kind": kind, "status": "claimed",
             "claimed_at": prior_claimed_at},
            {"$set": {"source": source, "status": "claimed", "claimed_at": now,
                      "reclaimed_at": now}},
        )
    except Exception as exc:
        logger.warning("[payment_log_claims] reclaim failed err=%s", type(exc).__name__)
        return None
    if _modified(res) != 1:
        return None
    logger.info("[payment_log_claims] reclaimed stale claim")
    return now


async def begin(db, txn: str, kind: str, source: str) -> Dict[str, Any]:
    """Decide whether this logger may insert a payments row.

    Returns ``{"action": "insert", "claim_token": ...}`` (this caller holds
    the claim: insert, then ``finish`` with the same token),
    ``{"action": "existing", ...}`` (already recorded: do not insert),
    ``{"action": "in_progress", ...}`` (someone else holds a fresh claim, or
    a plan ``/pay`` is still ``claimed``: do not insert and do not mark the
    charge done), ``{"action": "rejected"}`` (the id is empty after strip:
    do not insert and do not open a claim), or ``{"action": "unavailable"}``
    (claim store down).
    """
    from pymongo.errors import DuplicateKeyError

    txn = str(txn or "").strip()
    if not txn:
        # A blank id cannot be claimed. An unclaimed insert would let every
        # retry write another payments row.
        return {"action": "rejected"}
    try:
        claims = await _claims(db)
    except Exception as exc:
        logger.warning("[payment_log_claims] index unavailable err=%s", type(exc).__name__)
        return {"action": "unavailable"}

    payments = db["payments"]
    row = await payments.find_one(_existing_filter(txn, kind))
    if row is not None:
        claim = await claims.find_one({"transaction_id": txn, "kind": kind})
        claim = await _reconcile_stale_logged(claims, txn, kind, claim, row)
        return {"action": "existing", "row": row, "claim": claim}
    if kind == CHARGE:
        plan_claim = await db[PLAN_CLAIMS_COLLECTION].find_one({"transaction_id": txn})
        if plan_claim is not None:
            outcome = _plan_claim_outcome(plan_claim)
            if outcome == "recorded":
                return {"action": "existing", "row": None, "claim": None, "plan_claim": plan_claim}
            if outcome == "in_progress":
                return {"action": "in_progress", "plan_claim": plan_claim}
            # failed: the plan attempt wrote no payment. Record the provider charge.

    now = datetime.now(timezone.utc).isoformat()
    try:
        await claims.insert_one({"transaction_id": txn, "kind": kind, "source": source,
                                 "status": "claimed", "claimed_at": now})
    except DuplicateKeyError:
        claim = await claims.find_one({"transaction_id": txn, "kind": kind})
        row = await payments.find_one(_existing_filter(txn, kind))
        if row is not None:
            claim = await _reconcile_stale_logged(claims, txn, kind, claim, row)
            return {"action": "existing", "row": row, "claim": claim}
        if (
            claim
            and str(claim.get("status") or "") == "claimed"
            and claim_is_stale(claim.get("claimed_at"))
        ):
            token = await _reclaim_stale(claims, txn, kind, source, claim.get("claimed_at"))
            if token:
                row = await payments.find_one(_existing_filter(txn, kind))
                if row is not None:
                    claim = await claims.find_one({"transaction_id": txn, "kind": kind})
                    claim = await _reconcile_stale_logged(claims, txn, kind, claim, row)
                    return {"action": "existing", "row": row, "claim": claim}
                return {"action": "insert", "claim_token": token, "reclaimed": True}
            claim = await claims.find_one({"transaction_id": txn, "kind": kind})
        if claim and str(claim.get("status") or "") == "claimed" and row is None:
            return {"action": "in_progress", "claim": claim}
        return {"action": "existing", "row": row, "claim": claim}
    except Exception as exc:
        logger.warning("[payment_log_claims] claim insert failed err=%s", type(exc).__name__)
        return {"action": "unavailable"}
    return {"action": "insert", "claim_token": now}


async def coordinate_plan_insert(db, txn: str) -> Dict[str, Any]:
    """Whether plan ``/pay`` may insert a payments row for this charge.

    Returns ``insert`` (this caller holds the logger charge claim and must
    insert, then ``finish`` with the token), ``use_existing`` (a charge row
    is already recorded: apply the plan onto that row, do not insert),
    ``in_progress`` (a logger holds a fresh claim: do not insert a plan claim
    or a payment), ``needs_review`` (the claim says logged but no row exists),
    ``rejected`` (the id is empty after strip: do not insert), or
    ``unavailable``.
    """
    from pymongo.errors import DuplicateKeyError

    txn = str(txn or "").strip()
    if not txn:
        return {"action": "rejected"}
    try:
        claims = await _claims(db)
    except Exception as exc:
        logger.warning("[payment_log_claims] plan coordinate index err=%s", type(exc).__name__)
        return {"action": "unavailable"}

    async def _from_existing(claim: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        row = await db["payments"].find_one(_existing_filter(txn, CHARGE))
        if row is not None:
            if (
                claim
                and str(claim.get("status") or "") == "claimed"
                and claim_is_stale(claim.get("claimed_at"))
            ):
                await _reconcile_stale_logged(claims, txn, CHARGE, claim, row)
            return {"action": "use_existing", "row": row}
        if claim is None:
            return None
        status = str(claim.get("status") or "")
        if status == "claimed" and claim_is_stale(claim.get("claimed_at")):
            token = await _reclaim_stale(claims, txn, CHARGE, "plan_pay", claim.get("claimed_at"))
            if not token:
                return {"action": "in_progress"}
            row = await db["payments"].find_one(_existing_filter(txn, CHARGE))
            if row is not None:
                current = await claims.find_one({"transaction_id": txn, "kind": CHARGE})
                await _reconcile_stale_logged(claims, txn, CHARGE, current, row)
                return {"action": "use_existing", "row": row}
            return {"action": "insert", "claim_token": token}
        if status == "logged":
            return {"action": "needs_review"}
        return {"action": "in_progress"}

    try:
        decided = await _from_existing(await claims.find_one({"transaction_id": txn, "kind": CHARGE}))
    except Exception as exc:
        logger.warning("[payment_log_claims] plan coordinate read err=%s", type(exc).__name__)
        return {"action": "unavailable"}
    if decided is not None:
        return decided

    now = datetime.now(timezone.utc).isoformat()
    try:
        await claims.insert_one({
            "transaction_id": txn, "kind": CHARGE, "source": "plan_pay",
            "status": "claimed", "claimed_at": now,
        })
    except DuplicateKeyError:
        try:
            decided = await _from_existing(
                await claims.find_one({"transaction_id": txn, "kind": CHARGE}))
        except Exception as exc:
            logger.warning("[payment_log_claims] plan coordinate reread err=%s", type(exc).__name__)
            return {"action": "unavailable"}
        return decided or {"action": "in_progress"}
    except Exception as exc:
        logger.warning("[payment_log_claims] plan coordinate insert err=%s", type(exc).__name__)
        return {"action": "unavailable"}

    try:
        row = await db["payments"].find_one(_existing_filter(txn, CHARGE))
    except Exception as exc:
        logger.warning("[payment_log_claims] plan coordinate row err=%s", type(exc).__name__)
        await finish(db, txn, CHARGE, None, ok=False, claim_token=now)
        return {"action": "unavailable"}
    if row is not None:
        try:
            await claims.update_one(
                {"transaction_id": txn, "kind": CHARGE, "status": "claimed", "claimed_at": now},
                {"$set": {"status": "logged", "payment_id": str(row.get("_id")),
                          "logged_at": now}},
            )
        except Exception as exc:
            logger.warning("[payment_log_claims] plan coordinate link err=%s", type(exc).__name__)
            return {"action": "in_progress"}
        return {"action": "use_existing", "row": row}
    return {"action": "insert", "claim_token": now}


def _owned_filter(txn: str, kind: str, claim_token: Optional[str]) -> Dict[str, Any]:
    flt: Dict[str, Any] = {"transaction_id": txn, "kind": kind, "status": "claimed"}
    if claim_token:
        flt["claimed_at"] = claim_token
    return flt


async def finish(db, txn: str, kind: str, row_id, *, ok: bool,
                 claim_token: Optional[str] = None) -> bool:
    """After the insert attempt. ``ok=False``: the insert failed, release only
    the claim this caller still holds (matching ``claim_token`` when set).
    ``ok=True``: mark that claim logged. If a plan row won the race, or this
    caller no longer holds the token (a stale takeover), delete our row so
    the charge is recorded once, and return False. Never raises."""
    txn = str(txn or "").strip()
    if not txn:
        return ok
    try:
        claims = db[LOG_CLAIMS_COLLECTION]
        owned = _owned_filter(txn, kind, claim_token)
        if not ok:
            await claims.delete_one(owned)
            return False
        flt = _existing_filter(txn, kind)
        flt["_id"] = {"$ne": row_id}
        other = await db["payments"].find_one(flt)
        if other is not None and kind == CHARGE and str(other.get("plan_id") or "").strip():
            # Plan row won the race: it is canonical, drop ours.
            await db["payments"].delete_one({"_id": row_id})
            await claims.update_one(owned, {"$set": {"status": "superseded_by_plan_row"}})
            return False
        res = await claims.update_one(
            owned,
            {"$set": {"status": "logged", "payment_id": str(row_id),
                      "logged_at": datetime.now(timezone.utc).isoformat()}},
        )
        if _modified(res) == 1:
            return True
        # The update may have landed on a double that does not report
        # modified_count. Keep the row only when this claim now points at it.
        current = await claims.find_one({"transaction_id": txn, "kind": kind})
        if (
            current
            and current.get("status") == "logged"
            and str(current.get("payment_id") or "") == str(row_id)
        ):
            return True
        if row_id is not None:
            await db["payments"].delete_one({"_id": row_id})
        return False
    except Exception as exc:
        logger.warning("[payment_log_claims] finish failed err=%s", type(exc).__name__)
        return ok


async def still_held(db, txn: str, kind: str, claim_token: str) -> bool:
    """True when this caller still owns the claim. False on a lost takeover
    or a read error (do not insert)."""
    try:
        claim = await db[LOG_CLAIMS_COLLECTION].find_one(
            _owned_filter(str(txn or "").strip(), kind, claim_token))
        return claim is not None
    except Exception as exc:
        logger.warning("[payment_log_claims] still_held failed err=%s", type(exc).__name__)
        return False


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


async def reserve_step(db, txn: str, kind: str, step: str) -> str:
    """Atomically reserve a retryable webhook step (SSE event, audit).

    ``run`` — this caller holds it and must do the work, then ``mark_step``,
    or ``release_step`` if the work fails. ``done`` — already finished.
    ``busy`` — another delivery holds it (or the store errored): do not do
    the work and do not mark the webhook complete. A hold older than
    ``STALE_AFTER`` can be taken, so a killed worker does not block the step.
    """
    txn = str(txn or "").strip()
    hold = f"wh_{step}_hold"
    try:
        coll = db[LOG_CLAIMS_COLLECTION]
        current = await coll.find_one({"transaction_id": txn, "kind": kind})
        if await step_done(current, step):
            return "done"
        cutoff = (datetime.now(timezone.utc) - STALE_AFTER).isoformat()
        res = await coll.update_one(
            {"transaction_id": txn, "kind": kind, f"wh_{step}": {"$exists": False},
             "$or": [
                 {hold: {"$exists": False}},
                 {hold: {"$lt": cutoff}},
             ]},
            {"$set": {hold: datetime.now(timezone.utc).isoformat()}},
        )
        if _modified(res) == 1:
            return "run"
        current = await coll.find_one({"transaction_id": txn, "kind": kind})
        if await step_done(current, step):
            return "done"
        return "busy"
    except Exception as exc:
        logger.warning("[payment_log_claims] reserve_step %s failed err=%s", step, type(exc).__name__)
        return "busy"


async def release_step(db, txn: str, kind: str, step: str) -> None:
    """Drop a retryable-step hold after the work failed. Never raises."""
    try:
        await db[LOG_CLAIMS_COLLECTION].update_one(
            {"transaction_id": str(txn or "").strip(), "kind": kind},
            {"$unset": {f"wh_{step}_hold": ""}},
        )
    except Exception as exc:
        logger.warning("[payment_log_claims] release_step %s failed err=%s", step, type(exc).__name__)


async def mark_step(db, txn: str, kind: str, step: str) -> None:
    """Record a retryable step (event / audit / completed) as done. Never raises."""
    try:
        await db[LOG_CLAIMS_COLLECTION].update_one(
            {"transaction_id": str(txn).strip(), "kind": kind},
            {"$set": {f"wh_{step}": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception as exc:
        logger.warning("[payment_log_claims] mark_step %s failed err=%s", step, type(exc).__name__)
