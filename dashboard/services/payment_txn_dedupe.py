"""One new payments row per transaction id.

The receipt poller, the payment webhook, and ``POST /payments/plans/{id}/pay``
all insert into ``payments``. They must not create an index on that
collection: production already has duplicate rows, and a unique or partial
index there can fail on deploy.

The atomic gate is a row in ``payment_log_claims``. Its unique index on
``(tenant_id, transaction_id)`` matches ``payment_plan_payment_claims``
(``uniq_tenant_txn``). Tenant indexes are not applied yet, so ``tenant_id``
is absent and the index is one claim per transaction id. Only the caller
that inserts the claim may insert a payments row. A loser does not insert.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

PAYMENT_LOG_CLAIMS_COLLECTION = "payment_log_claims"
_index_ready = False


async def ensure_payment_log_claims(claims) -> None:
    """Create the unique index on the claims collection. Never on payments.

    Raises when the index cannot be created. Callers fail closed and write
    nothing.
    """
    global _index_ready
    if _index_ready:
        return
    await claims.create_index(
        [("tenant_id", 1), ("transaction_id", 1)],
        unique=True,
        name="uniq_payment_log_txn",
    )
    _index_ready = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _insert_claim(claims, txn: str, source: str) -> bool:
    """True when this caller owns the transaction id. False when it is taken."""
    try:
        await claims.insert_one({
            "transaction_id": txn,
            "source": source,
            "status": "claimed",
            "claimed_at": _now(),
        })
    except DuplicateKeyError:
        return False
    return True


async def _mark_logged(claims, txn: str, payment_id, source: str) -> None:
    """Point the claim at the one payments row. The unique key already blocks a second insert."""
    try:
        await claims.update_one(
            {"transaction_id": txn},
            {"$set": {
                "status": "logged",
                "source": source,
                "payment_id": None if payment_id is None else str(payment_id),
                "logged_at": _now(),
            }},
        )
    except Exception as exc:
        # The claim row is still held, so a retry cannot insert another payments row.
        logger.warning(
            "[payment_log] claim mark failed txn_set=%s err=%s",
            bool(txn), type(exc).__name__,
        )


async def _release_if_no_row(payments, claims, txn: str) -> None:
    """Drop a claim only when this caller can see that no payments row was stored."""
    try:
        existing = await payments.find_one({"transaction_id": txn})
    except Exception:
        return
    if existing is not None:
        return
    try:
        await claims.delete_one({"transaction_id": txn, "status": "claimed"})
    except Exception:
        return


async def hold_payment_log(claims, txn: str, *, source: str, payment_id) -> None:
    """Hold the log claim for a payments row that already exists.

    Linking that row does not insert a second one. The claim still has to
    exist so a later poller or webhook insert loses. Raises when the claims
    index or the claim write fails for a reason other than the id already
    being claimed.
    """
    await ensure_payment_log_claims(claims)
    if await _insert_claim(claims, txn, source):
        await _mark_logged(claims, txn, payment_id, source)


async def insert_payment_once(payments, doc: dict, *, claims, source: str) -> bool:
    """Insert ``doc`` when this caller wins the log claim.

    Return False when the transaction id is already claimed. The payments
    collection is not indexed. Any other error propagates; the claim is
    released only when a follow-up read shows no payments row for the id.
    """
    txn = str(doc.get("transaction_id") or "").strip()
    stored = {k: v for k, v in doc.items() if k != "txn_dedupe_key"}
    if txn:
        await ensure_payment_log_claims(claims)
        if not await _insert_claim(claims, txn, source):
            return False
    try:
        await payments.insert_one(stored)
    except Exception:
        if txn:
            await _release_if_no_row(payments, claims, txn)
        raise
    if txn:
        await _mark_logged(claims, txn, stored.get("_id"), source)
    return True
