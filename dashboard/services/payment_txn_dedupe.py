"""One new payments row per transaction id.

The receipt poller, the payment webhook, and ``POST /payments/plans/{id}/pay``
all insert into ``payments``. They must not create an index on that
collection: production already has duplicate rows, and a unique or partial
index there can fail on deploy.

The atomic gate is a row in ``payment_log_claims``. Its unique index on
``(tenant_id, transaction_id)`` matches ``payment_plan_payment_claims``
(``uniq_tenant_txn``). Tenant indexes are not applied yet, so ``tenant_id``
is absent and the index is one claim per transaction id. Only the caller
that owns the claim may insert a payments row. A loser does not insert.

A claim with no payments row is not a duplicate. A live claim (still inside
``ORPHAN_CLAIM_AFTER``) raises ``PaymentLogNotReady`` so the caller retries
without marking the receipt read. An older claim is reclaimed and the row
is inserted once.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

PAYMENT_LOG_CLAIMS_COLLECTION = "payment_log_claims"
# A claim younger than this may still belong to a writer that is about to
# insert. Older than this, with no payments row, it is an orphan.
ORPHAN_CLAIM_AFTER = timedelta(seconds=30)
_RECLAIMABLE_STATUSES = ("claimed", "inserting", "logged")
_index_ready = False


class PaymentLogNotReady(Exception):
    """The log claim is held and no payments row is visible yet.

    This is not a duplicate. Callers must not mark the receipt handled.
    """


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


def _parse_stamp(raw) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _activity_stamp(claim: dict):
    """The stamp that says a writer is still using this claim.

    A logged claim is judged by ``logged_at``. ``claimed_at`` on that claim
    is the original take, which is already old by the time the row is marked.
    """
    status = str(claim.get("status") or "")
    raw = claim.get("logged_at") if status == "logged" else claim.get("claimed_at")
    return raw if isinstance(raw, str) and raw.strip() else None


def _claim_is_orphan(claim: dict) -> bool:
    """True when this claim is old enough that its writer is no longer inserting."""
    stamp = _parse_stamp(_activity_stamp(claim))
    if stamp is None:
        return True
    return datetime.now(timezone.utc) - stamp >= ORPHAN_CLAIM_AFTER


def _version_pin(claim: dict) -> dict:
    """Match this exact claim version so a live writer is not overwritten."""
    status = str(claim.get("status") or "")
    pin = {"transaction_id": claim.get("transaction_id"), "status": status}
    if "reclaim_token" in claim:
        pin["reclaim_token"] = claim.get("reclaim_token")
    else:
        pin["reclaim_token"] = {"$exists": False}
    stamp_field = "logged_at" if status == "logged" else "claimed_at"
    raw = claim.get(stamp_field)
    if isinstance(raw, str) and raw.strip():
        pin[stamp_field] = raw
    else:
        pin[stamp_field] = {"$exists": False}
    return pin


async def _insert_claim(claims, txn: str, source: str) -> str | None:
    """The new owner's token, or None when the transaction id is already claimed."""
    token = uuid.uuid4().hex
    try:
        await claims.insert_one({
            "transaction_id": txn,
            "source": source,
            "status": "claimed",
            "claimed_at": _now(),
            "reclaim_token": token,
        })
    except DuplicateKeyError:
        return None
    return token


async def _mark_logged(claims, txn: str, payment_id, source: str, token: str | None = None) -> None:
    """Point the claim at the one payments row. The unique key already blocks a second insert."""
    flt: dict = {"transaction_id": txn}
    if token:
        flt["reclaim_token"] = token
    try:
        await claims.update_one(
            flt,
            {"$set": {
                "status": "logged",
                "source": source,
                "payment_id": None if payment_id is None else str(payment_id),
                "logged_at": _now(),
            }},
        )
    except Exception as exc:
        # The claim row is still held, so a retry cannot insert another payments row
        # while this token is current. A later read of the payments row still dedupes.
        logger.warning(
            "[payment_log] claim mark failed txn_set=%s err=%s",
            bool(txn), type(exc).__name__,
        )


async def _release_if_no_row(payments, claims, txn: str, token: str) -> None:
    """Drop this caller's claim only when no payments row was stored.

    A payments read that fails leaves the claim in place. The next attempt
    reclaims it once it is older than ``ORPHAN_CLAIM_AFTER`` and still has
    no row. The delete is pinned to this token so a reclaimer is not dropped.
    """
    try:
        existing = await payments.find_one({"transaction_id": txn})
    except Exception:
        return
    if existing is not None:
        return
    try:
        await claims.delete_one({
            "transaction_id": txn,
            "reclaim_token": token,
            "status": {"$in": ["claimed", "inserting"]},
        })
    except Exception:
        return


async def _existing_payment(payments, txn: str):
    """The payments row for this transaction, or None when there is none.

    Raises when the read fails. ``payment_log_claims`` starts empty on deploy
    while production already has payments rows, so a missing claim is not
    proof that this transaction has no row.
    """
    try:
        return await payments.find_one({"transaction_id": txn})
    except Exception as exc:
        logger.warning(
            "[payment_log] existing payment read failed txn_set=%s err=%s",
            bool(txn), type(exc).__name__,
        )
        raise


async def _begin_insert(claims, txn: str, token: str) -> bool:
    """Refresh the claim so a concurrent reclaim does not treat it as abandoned.

    False when this token no longer owns the claim. The caller must not insert.
    """
    res = await claims.update_one(
        {"transaction_id": txn, "reclaim_token": token, "status": "claimed"},
        {"$set": {"status": "inserting", "claimed_at": _now()}},
    )
    return bool(getattr(res, "matched_count", 0) or getattr(res, "modified_count", 0))


async def _reclaim_orphan(payments, claims, txn: str, source: str) -> str | None:
    """Take an abandoned claim so this caller may insert.

    None when a payments row exists (a real duplicate; the claim is marked
    logged). Raises ``PaymentLogNotReady`` when the claim is still live, the
    payments read fails, or another writer took the claim first.
    """
    existing = await _existing_payment(payments, txn)
    if existing is not None:
        await _mark_logged(claims, txn, existing.get("_id"), source)
        return None
    try:
        claim = await claims.find_one({"transaction_id": txn})
    except Exception as exc:
        logger.warning(
            "[payment_log] claim read failed txn_set=%s err=%s",
            bool(txn), type(exc).__name__,
        )
        raise
    if not isinstance(claim, dict):
        token = await _insert_claim(claims, txn, source)
        if token:
            return token
        existing = await _existing_payment(payments, txn)
        if existing is not None:
            await _mark_logged(claims, txn, existing.get("_id"), source)
            return None
        raise PaymentLogNotReady()
    status = str(claim.get("status") or "")
    if status not in _RECLAIMABLE_STATUSES or not _claim_is_orphan(claim):
        raise PaymentLogNotReady()
    token = uuid.uuid4().hex
    try:
        res = await claims.update_one(
            _version_pin(claim),
            {"$set": {
                "status": "claimed",
                "source": source,
                "claimed_at": _now(),
                "reclaim_token": token,
            }, "$unset": {"payment_id": "", "logged_at": ""}},
        )
    except Exception as exc:
        logger.warning(
            "[payment_log] orphan reclaim failed txn_set=%s err=%s",
            bool(txn), type(exc).__name__,
        )
        raise
    if not (getattr(res, "matched_count", 0) or getattr(res, "modified_count", 0)):
        existing = await _existing_payment(payments, txn)
        if existing is not None:
            await _mark_logged(claims, txn, existing.get("_id"), source)
            return None
        raise PaymentLogNotReady()
    existing = await _existing_payment(payments, txn)
    if existing is not None:
        await _mark_logged(claims, txn, existing.get("_id"), source, token)
        return None
    return token


async def hold_payment_log(claims, txn: str, *, source: str, payment_id) -> None:
    """Hold the log claim for a payments row that already exists.

    Linking that row does not insert a second one. The claim still has to
    exist so a later poller or webhook insert loses. Raises when the claims
    index or the claim write fails for a reason other than the id already
    being claimed.
    """
    await ensure_payment_log_claims(claims)
    token = await _insert_claim(claims, txn, source)
    if token:
        await _mark_logged(claims, txn, payment_id, source, token)


async def insert_payment_once(payments, doc: dict, *, claims, source: str) -> bool:
    """Insert ``doc`` when this caller owns the log claim and no row exists yet.

    Return False when a payments row for the transaction id already exists.
    The existing row keeps its claim so a later refund or completed webhook
    updates that row instead of inserting another. A claim that has no row
    is reclaimed once it is no longer a live writer. A live claim raises
    ``PaymentLogNotReady`` instead of looking like a duplicate. The payments
    collection is not indexed.
    """
    txn = str(doc.get("transaction_id") or "").strip()
    stored = {k: v for k, v in doc.items() if k != "txn_dedupe_key"}
    token = None
    if txn:
        await ensure_payment_log_claims(claims)
        existing = await _existing_payment(payments, txn)
        if existing is not None:
            await hold_payment_log(claims, txn, source=source, payment_id=existing.get("_id"))
            return False
        token = await _insert_claim(claims, txn, source)
        if token is None:
            token = await _reclaim_orphan(payments, claims, txn, source)
            if token is None:
                return False
        existing = await _existing_payment(payments, txn)
        if existing is not None:
            await _mark_logged(claims, txn, existing.get("_id"), source, token)
            return False
        if not await _begin_insert(claims, txn, token):
            existing = await _existing_payment(payments, txn)
            if existing is not None:
                await _mark_logged(claims, txn, existing.get("_id"), source, token)
                return False
            raise PaymentLogNotReady()
    try:
        await payments.insert_one(stored)
    except Exception:
        if txn and token:
            await _release_if_no_row(payments, claims, txn, token)
        raise
    if txn and token:
        await _mark_logged(claims, txn, stored.get("_id"), source, token)
    return True
