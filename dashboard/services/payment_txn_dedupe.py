"""One payments row per transaction id, enforced at insert time.

The receipt poller, the payment webhook, and ``POST /payments/plans/{id}/pay``
all write ``payments``. A post-write count cannot close the race between them.
New rows carry ``txn_dedupe_key`` (the transaction id) and a partial unique
index rejects a second insert. Rows already stored without the key are left
as they are: the index does not cover them, so creating it does not rewrite
or drop existing duplicates.
"""
from __future__ import annotations

from pymongo.errors import DuplicateKeyError

TXN_DEDUPE_FIELD = "txn_dedupe_key"
_index_ready = False


async def ensure_payment_txn_dedupe(coll) -> None:
    """Create the partial unique index once per process. Raises on failure."""
    global _index_ready
    if _index_ready:
        return
    await coll.create_index(
        [(TXN_DEDUPE_FIELD, 1)],
        unique=True,
        name="uniq_payment_txn_dedupe",
        # Empty and missing keys stay out of the index (legacy rows, down
        # payments with no transaction id).
        partialFilterExpression={TXN_DEDUPE_FIELD: {"$gt": ""}},
    )
    _index_ready = True


async def insert_payment_once(coll, doc: dict) -> bool:
    """Insert ``doc``. Return False when this transaction id is already stored.

    Any other error, including an index that cannot be created, propagates.
    The caller fails closed and does not record a second row.
    """
    txn = str(doc.get("transaction_id") or "").strip()
    stored = doc
    if txn:
        await ensure_payment_txn_dedupe(coll)
        if stored.get(TXN_DEDUPE_FIELD) != txn:
            stored = {**doc, TXN_DEDUPE_FIELD: txn}
    try:
        await coll.insert_one(stored)
    except DuplicateKeyError:
        return False
    return True
