"""Record one provider payment in a single MongoDB transaction.

Charge writers (the receipt poller and ``POST /api/webhooks/payment``) and a
webhook refund or failure all call ``record_provider_payment``. Plan ``/pay``
takes the same ``payment_txn_locks`` lock. The unique index is on that lock
collection, keyed by ``tenant_id`` + ``txn_key``. Nothing here creates an
index on ``payments``.

Inside ``ClientSession.with_transaction`` (the driver retries transient
transaction errors), the first write is the lock upsert (``$inc`` version).
Sibling rows are read only after that lock.

A charge then inserts its ``payments`` row, a tenant-scoped claim, and one
deterministic audit. ``last_payment_*`` is stamped on a bond only when the
booking was explicitly provided, exactly one bond document matches it, and
no refund or failure sibling exists. Anything else is fail closed: the
charge row is stored with ``needs_review`` and the bond is not stamped.
Staff assign bookings. A blank booking is never inferred and never stamped.

A refund or failure inserts only its own ``payments`` row (``event_kind``
``refund`` or ``failure``) and an audit, then sets ``needs_review`` on the
one matched bond and the one originating plan when each is unambiguous. It
does not write ``last_payment_*``, a plan balance, or claim state. One
staff Slack alert is sent after the commit. A Slack failure is logged and
does not roll the payment back.

If the server cannot run a transaction, the caller gets 503
``transactions_unavailable`` and this function writes nothing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

logger = logging.getLogger(__name__)

LOG_CLAIMS_COLLECTION = "payment_log_claims"
LOCKS_COLLECTION = "payment_txn_locks"
_CLAIM_INDEX = "uniq_tenant_txn_kind"
_LOCK_INDEX = "uniq_tenant_txn_lock"
_TOKEN_FIELDS = ("log_claim_token", "claim_token", "payment_claim_token")
_STAMP_FIELDS = (
    "last_payment_amount",
    "last_payment_at",
    "last_payment_status",
    "last_transaction_id",
    "last_payment_source",
)
_BOND_COLLECTIONS = ("bond_cases", "active_bonds")
REVERSAL_KINDS = frozenset({"refund", "failure"})

_SETTLED = frozenset({
    "approved", "paid", "completed", "complete", "success", "succeeded",
    "captured", "settled",
})
_DECLINED = frozenset({"declined", "failed", "failure", "denied", "voided", "error"})
_REFUNDED = frozenset({"refunded", "refund", "reversed"})

_index_ready = False
_lock_index_ready = False
_txn_support_cache: dict[int, bool] = {}


class PaymentLogError(Exception):
    """The payment must not be recorded. Nothing in this attempt is committed."""

    def __init__(self, error: str, status_code: int, message: str):
        super().__init__(error)
        self.error = error
        self.status_code = status_code
        self.message = message


class TransactionsUnavailable(PaymentLogError):
    def __init__(self):
        super().__init__(
            "transactions_unavailable",
            503,
            "Recording a payment needs a MongoDB replica set or Atlas. Nothing was recorded.",
        )


class AlreadyRecorded(Exception):
    """This event kind is already stored. The transaction aborts; the caller re-reads."""


class RetryableTxnError(Exception):
    """Signals a transient transaction error to a test session shim.

    Production uses ``ClientSession.with_transaction``, which retries
    PyMongo errors labeled ``TransientTransactionError``.
    """


def normalize_txn_key(value: Any) -> str:
    """Stripped transaction id. Blank, missing, and booleans are empty."""
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def amount_cents(value: Any) -> Optional[int]:
    """Exact integer cents, or None when the value is not a positive finite
    amount with at most two decimal places. Fractional cents are refused.
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or len(text) > 64:
        return None
    try:
        dec = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not dec.is_finite() or dec <= 0:
        return None
    _sign, digits, exponent = dec.as_tuple()
    if exponent > 12:
        return None
    shift = exponent + 2
    if shift >= 0:
        cents = int("".join(map(str, digits)) or "0") * (10 ** shift)
    else:
        drop = digits[len(digits) + shift:] if -shift <= len(digits) else digits
        if any(d != 0 for d in drop):
            return None
        kept = digits[: len(digits) + shift] if -shift < len(digits) else ()
        cents = int("".join(map(str, kept)) or "0")
    if cents <= 0 or cents > 10 ** 14:
        return None
    return cents


def dollars_from_cents(cents: int) -> float:
    return float(Decimal(cents) / Decimal(100))


def _status_fact(value: Any) -> Optional[str]:
    status = str(value or "").strip().lower()
    if not status:
        return None
    if status in _SETTLED:
        return "settled"
    if status in _DECLINED:
        return "declined"
    if status in _REFUNDED:
        return "refunded"
    return status


def event_kind(status: Any, event_type: Any = None) -> str:
    """``charge``, ``refund``, or ``failure``.

    Event type wins when it names a refund or a failure, and so does the
    status. Anything else settled, or blank, is a charge.
    """
    event = str(event_type or "").strip().lower()
    fact = _status_fact(status)
    refund_event = (
        "refund" in event
        or event.endswith((".refunded", ".refund", ".reversed"))
    )
    failed_event = event.endswith((
        ".failed", ".failure", ".declined", ".denied", ".voided", ".error",
    )) or event in {"failed", "failure", "declined"}
    if refund_event or fact == "refunded":
        return "refund"
    if failed_event or fact == "declined":
        return "failure"
    if fact in (None, "settled"):
        return "charge"
    return fact


def row_kind(row: dict) -> str:
    """Kind stored on the row, or inferred for a row logged before kinds existed."""
    stored = str(row.get("event_kind") or "").strip().lower()
    if stored == "failed":
        return "failure"
    if stored:
        return stored
    event = row.get("event_type")
    raw = row.get("raw_payload")
    if not event and isinstance(raw, dict):
        event = raw.get("event_type")
    return event_kind(row.get("status"), event)


def has_reversal_sibling(rows: list) -> bool:
    """True when any row is a refund or a failure."""
    return any(row_kind(row) in REVERSAL_KINDS for row in rows)


def _booking(value: Any) -> str:
    return str(value or "").strip()


def _row_cents(row: dict) -> Optional[int]:
    if row.get("amount") is None:
        return None
    return amount_cents(row.get("amount"))


def _tenant_value():
    """Lock and claim tenant.

    Flag off stores ``None`` (one agency). Flag on stores the current tenant
    so two agencies do not share a transaction id.
    """
    from dashboard.tenancy.context import current_tenant_id
    from dashboard.tenancy.flag import multi_tenant_enabled

    if not multi_tenant_enabled():
        return None
    return current_tenant_id() or ""


def _lock_key(txn_key: str) -> dict:
    return {"tenant_id": _tenant_value(), "txn_key": txn_key}


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _sess(session) -> dict:
    return {"session": session} if session is not None else {}


async def _docs(coll, flt: dict, session=None) -> list:
    kwargs = _sess(session)
    cursor = coll.find(flt, **kwargs) if kwargs else coll.find(flt)
    out = []
    async for doc in cursor:
        out.append(doc)
    return out


def _audit_id(txn_key: str, kind: str) -> str:
    """Deterministic ``audit_events`` ``_id``. No index is added on ``payments``."""
    from dashboard.tenancy.context import current_tenant_id
    from dashboard.tenancy.flag import multi_tenant_enabled

    if not multi_tenant_enabled():
        return f"payment-log:{txn_key}:{kind}"
    return f"payment-log:{current_tenant_id() or ''}:{txn_key}:{kind}"


def _stamp_view(doc: Optional[dict]) -> dict:
    if not doc:
        return {field: None for field in _STAMP_FIELDS}
    return {field: doc.get(field) for field in _STAMP_FIELDS}


def _review_reason(kind: str) -> str:
    if kind == "refund":
        return "provider_refund"
    return "provider_failure"


async def _claims(db):
    global _index_ready
    coll = db[LOG_CLAIMS_COLLECTION]
    if not _index_ready:
        await coll.create_index(
            [("tenant_id", 1), ("txn_key", 1), ("event_kind", 1)],
            unique=True,
            name=_CLAIM_INDEX,
        )
        _index_ready = True
    return coll


async def ensure_txn_lock_index(coll) -> None:
    """Unique ``(tenant_id, txn_key)`` on ``payment_txn_locks``. Not on ``payments``."""
    global _lock_index_ready
    if _lock_index_ready:
        return
    await coll.create_index(
        [("tenant_id", 1), ("txn_key", 1)],
        unique=True,
        name=_LOCK_INDEX,
    )
    _lock_index_ready = True


async def acquire_txn_lock(coll, txn_key: str, session) -> None:
    """Upsert the shared lock and bump ``version`` before any sibling read."""
    key = _lock_key(txn_key)
    await coll.update_one(
        key,
        {"$inc": {"version": 1}, "$setOnInsert": dict(key)},
        upsert=True,
        **_sess(session),
    )


async def _supports_transactions(client) -> bool:
    """True on a replica set or mongos (Atlas). Standalone is false."""
    key = id(client)
    if key in _txn_support_cache:
        return _txn_support_cache[key]
    hello = None
    admin = getattr(client, "admin", None)
    if admin is not None:
        for command in ("hello", "ismaster"):
            try:
                hello = await admin.command(command)
                break
            except Exception:
                hello = None
    if not isinstance(hello, dict):
        return False
    supported = bool(hello.get("setName") or hello.get("msg") == "isdbgrid")
    _txn_support_cache[key] = supported
    return supported


async def _transaction_client():
    """The app Mongo client when it can run a multi-document transaction, else None."""
    try:
        from dashboard.extensions import get_mongo_client
        client = get_mongo_client()
    except Exception:
        return None
    try:
        if await _supports_transactions(client):
            return client
    except Exception:
        logger.warning("[payment_log] transaction support check failed")
        return None
    return None


async def _run_apply(client, work) -> None:
    """One transaction. Refuses to write when the server cannot run one."""
    if client is None:
        raise TransactionsUnavailable()
    try:
        supported = await _supports_transactions(client)
    except Exception:
        supported = False
    if not supported:
        raise TransactionsUnavailable()

    async def _callback(session):
        await work(session)

    started = client.start_session()
    if hasattr(started, "__await__"):
        started = await started
    async with started as session:
        await session.with_transaction(_callback)


def _validate(transaction_id: Any, amount: Any, booking_number: Any) -> tuple[str, int, str, float]:
    txn_key = normalize_txn_key(transaction_id)
    if not txn_key:
        raise PaymentLogError(
            "transaction_id_required",
            400,
            "A non-blank transaction id is required.",
        )
    cents = amount_cents(amount)
    if cents is None:
        raise PaymentLogError(
            "invalid_amount",
            400,
            "Amount must be a positive number with at most two decimal places.",
        )
    return txn_key, cents, _booking(booking_number), dollars_from_cents(cents)


def _conflict_reason(rows: list, *, cents: int, status: Any, booking: str) -> Optional[str]:
    """Why a recorded row of this same kind must not accept the payload."""
    if not rows:
        return None
    if len(rows) > 1:
        return "more than one payment row has this transaction id and event kind"
    row = rows[0]
    if row.get("amount") is not None:
        recorded = _row_cents(row)
        if recorded is None or recorded != cents:
            return "the recorded amount does not match this payload"
    recorded_status = _status_fact(row.get("status"))
    incoming = _status_fact(status)
    if recorded_status and incoming and recorded_status != incoming:
        return "the recorded status does not match this payload"
    recorded_booking = _booking(row.get("booking_number"))
    if recorded_booking and booking and recorded_booking.casefold() != booking.casefold():
        return "the recorded booking number does not match this payload"
    return None


def _raise_if_conflict(rows: list, *, cents: int, status: Any, booking: str) -> None:
    reason = _conflict_reason(rows, cents=cents, status=status, booking=booking)
    if reason:
        raise PaymentLogError(
            "payment_conflict",
            409,
            "This transaction id is already recorded and the payload does not match "
            f"({reason}). Nothing was written.",
        )


async def _read_rows(db, txn_key: str, session=None, kind: Optional[str] = None) -> list:
    rows = await _docs(db["payments"], {"transaction_id": txn_key}, session)
    if kind is None:
        return rows
    return [row for row in rows if row_kind(row) == kind]


async def _matching_bonds(db, booking: str, session) -> list:
    """``(collection, doc)`` for an explicit booking. Empty when the booking is blank."""
    if not booking:
        return []
    found = []
    for name in _BOND_COLLECTIONS:
        for doc in await _docs(db[name], {"booking_number": booking}, session):
            found.append((name, doc))
    return found


def _sibling_bookings(rows: list) -> list:
    found = []
    folded = set()
    for row in rows:
        booking = _booking(row.get("booking_number"))
        if not booking:
            continue
        key = booking.casefold()
        if key in folded:
            continue
        folded.add(key)
        found.append(booking)
    return found


def _one_plan_id(rows: list) -> str:
    """The single sibling ``plan_id``, or empty when there isn't exactly one."""
    found = []
    for row in rows:
        plan_id = str(row.get("plan_id") or "").strip()
        if plan_id and plan_id not in found:
            found.append(plan_id)
    if len(found) == 1:
        return found[0]
    return ""


def _charge_block_reason(booking: str, bonds: list, siblings: list) -> str:
    """Empty when the charge may stamp. Otherwise a fail-closed reason."""
    if not booking:
        return "booking_not_provided"
    if len(bonds) != 1:
        return "booking_not_unique"
    for other in _sibling_bookings(siblings):
        if other.casefold() != booking.casefold():
            return "cross_booking"
    if has_reversal_sibling(siblings):
        return "reversal_sibling"
    return ""


def _review_flag(kind: str, txn_key: str) -> dict:
    return {
        "needs_review": True,
        "needs_review_reason": _review_reason(kind),
        "needs_review_event_kind": kind,
        "needs_review_transaction_id": txn_key,
    }


async def _flag_bond(db, booking: str, flag: dict, session) -> Optional[dict]:
    """Set ``needs_review`` on the one matching bond. Does not stamp ``last_payment_*``."""
    bonds = await _matching_bonds(db, booking, session)
    if len(bonds) != 1:
        return None
    name, current = bonds[0]
    await db[name].update_one(
        {"booking_number": booking, **({"_id": current["_id"]} if current.get("_id") is not None else {})},
        {"$set": flag},
        **_sess(session),
    )
    return {
        "collection": name,
        "booking_number": booking,
        "old_needs_review": current.get("needs_review"),
        "new_needs_review": True,
    }


async def _apply(
    db,
    *,
    txn_key: str,
    cents: int,
    amount: float,
    status: str,
    booking: str,
    kind: str,
    source: str,
    extra: dict,
    session,
) -> Optional[dict]:
    """Lock, then write this event. Returns the staff alert, or None for a charge."""
    from pymongo.errors import DuplicateKeyError

    await acquire_txn_lock(db[LOCKS_COLLECTION], txn_key, session)

    same_kind = await _read_rows(db, txn_key, session, kind=kind)
    _raise_if_conflict(same_kind, cents=cents, status=status, booking=booking)
    if same_kind:
        raise AlreadyRecorded()

    siblings = await _read_rows(db, txn_key, session)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    review_note = ""
    stamped = False
    stamp_audit = None

    if kind == "charge":
        bonds = await _matching_bonds(db, booking, session)
        review_note = _charge_block_reason(booking, bonds, siblings)
        claims = db[LOG_CLAIMS_COLLECTION]
        try:
            await claims.insert_one({
                "tenant_id": _tenant_value(),
                "txn_key": txn_key,
                "event_kind": kind,
                "source": source,
                "amount_cents": cents,
                "booking_number": booking,
                "payment_status": status,
                "status": "recorded",
                "recorded_at": now_iso,
            }, **_sess(session))
        except DuplicateKeyError:
            raise AlreadyRecorded() from None
        if not review_note:
            coll_name, current = bonds[0]
            payment_update = {
                "last_payment_amount": amount,
                "last_payment_at": now_iso,
                "last_payment_status": status or "approved",
                "last_transaction_id": txn_key,
                "last_payment_source": source,
            }
            filt = {"booking_number": booking}
            if current.get("_id") is not None:
                filt["_id"] = current["_id"]
            await db[coll_name].update_one(filt, {"$set": payment_update}, **_sess(session))
            stamped = True
            stamp_audit = {
                "collection": coll_name,
                "booking_number": booking,
                "old_last_payment": _stamp_view(current),
                "new_last_payment": _stamp_view(payment_update),
            }

    doc = {
        "transaction_id": txn_key,
        "tenant_id": _tenant_value(),
        "event_kind": kind,
        "booking_number": booking,
        "amount": amount,
        "amount_cents": cents,
        "status": status,
        "source": source,
        "created_at": now_iso,
    }
    if kind == "charge" and review_note:
        doc["needs_review"] = True
        doc["needs_review_reason"] = review_note
    if kind in REVERSAL_KINDS:
        doc["needs_review"] = True
        doc["needs_review_reason"] = _review_reason(kind)
    for key, value in extra.items():
        if key in _TOKEN_FIELDS or key in doc:
            continue
        doc[key] = _plain(value)
    for field in _TOKEN_FIELDS:
        doc.pop(field, None)
    await db["payments"].insert_one(doc, **_sess(session))

    bond_review = None
    plan_id = ""
    if kind in REVERSAL_KINDS:
        flag = _review_flag(kind, txn_key)
        # Flag the bond the event names when that booking is exactly one
        # document. A blank event booking may use the single sibling booking.
        # A disagreement is not a guess: no bond is flagged.
        flag_booking = ""
        sibling_bookings = _sibling_bookings(siblings)
        if booking and (not sibling_bookings or (
            len(sibling_bookings) == 1 and sibling_bookings[0].casefold() == booking.casefold()
        )):
            flag_booking = booking
        elif not booking and len(sibling_bookings) == 1:
            flag_booking = sibling_bookings[0]
        if flag_booking:
            bond_review = await _flag_bond(db, flag_booking, flag, session)
        plan_id = _one_plan_id(siblings)
        if plan_id:
            await db["payment_plans"].update_one(
                {"plan_id": plan_id},
                {"$set": flag},
                **_sess(session),
            )

    audit = {
        "_id": _audit_id(txn_key, kind),
        "source": source,
        "event_type": str(extra.get("event_type") or f"payment.{status or kind}"),
        "entity_type": "payment",
        "entity_id": txn_key,
        "actor": source,
        "agent_name": source,
        "timestamp": now,
        "created_at": now,
        "booking_number": booking,
        "transaction_id": txn_key,
        "amount": amount,
        "event_kind": kind,
        "stamped": stamped,
        "reason": "provider payment recorded",
        "confidence": 1.0,
    }
    if stamp_audit:
        audit["old_last_payment"] = stamp_audit["old_last_payment"]
        audit["new_last_payment"] = stamp_audit["new_last_payment"]
        audit["stamped_collection"] = stamp_audit["collection"]
    if bond_review:
        audit["bond_review"] = bond_review
    if plan_id and kind in REVERSAL_KINDS:
        audit["plan_id"] = plan_id
        audit["plan_needs_review"] = True
    try:
        await db["audit_events"].insert_one(audit, **_sess(session))
    except DuplicateKeyError:
        raise PaymentLogError(
            "payment_record_failed",
            500,
            "The payment audit could not be recorded. Nothing was written.",
        ) from None

    if kind not in REVERSAL_KINDS:
        return None
    return {
        "kind": kind,
        "amount": amount,
        "booking": booking or "none",
        "plan_id": plan_id or "none",
    }


def _notify_staff_review(hit: dict) -> None:
    """One #alerts post after commit. Failure is logged and does not roll back.

    The message is the event kind, amount, booking, and plan id. No name,
    phone, email, or card.
    """
    kind = hit.get("kind") or ""
    booking = hit.get("booking") or "none"
    plan_id = hit.get("plan_id") or "none"
    amount = hit.get("amount")
    try:
        amount_text = f"{float(amount):.2f}"
    except (TypeError, ValueError):
        amount_text = ""
    message = f"event_kind {kind}; amount {amount_text}; booking {booking}; plan {plan_id}"
    try:
        from writers.slack_notifier import SlackNotifier
        delivered = SlackNotifier().notify_alert(
            title="Provider payment needs review",
            message=message,
            level="warning",
            details={
                "event_kind": kind,
                "amount": amount_text,
                "booking_number": booking,
                "plan_id": plan_id,
            },
            source="payment_log",
        )
        if not delivered:
            logger.warning("[payment_log] staff review alert was not delivered kind=%s", kind)
    except Exception:
        logger.warning("[payment_log] staff review alert failed kind=%s", kind)


async def _duplicate_or_conflict(
    db, *, txn_key: str, cents: int, status: Any, booking: str, amount: float, kind: str,
) -> dict:
    rows = await _read_rows(db, txn_key, kind=kind)
    if not rows:
        raise PaymentLogError(
            "payment_needs_review",
            409,
            "This transaction id was claimed without a payment row. Nothing was written.",
        )
    _raise_if_conflict(rows, cents=cents, status=status, booking=booking)
    return {
        "ok": True,
        "duplicate": True,
        "committed": False,
        "transaction_id": txn_key,
        "amount": amount,
        "booking_number": booking,
        "status": status,
        "event_kind": kind,
    }


async def record_provider_payment(
    db,
    *,
    source: str,
    transaction_id: Any,
    amount: Any,
    status: Any,
    booking_number: Any = "",
    extra: Optional[dict] = None,
) -> dict:
    """Insert one provider payment, or return duplicate, or raise ``PaymentLogError``.

    Validation runs before any write. Transactions unavailable raises 503
    and writes nothing, including no lock and no claim.
    """
    txn_key, cents, booking, amount_value = _validate(transaction_id, amount, booking_number)
    provider_status = str(status or "").strip()
    fields = dict(extra or {})
    kind = event_kind(provider_status, fields.get("event_type"))

    try:
        client = await _transaction_client()
    except Exception:
        logger.warning("[payment_log] transaction support check failed source=%s", source)
        raise TransactionsUnavailable() from None
    if client is None:
        raise TransactionsUnavailable()

    try:
        if kind == "charge":
            await _claims(db)
        await ensure_txn_lock_index(db[LOCKS_COLLECTION])
    except PaymentLogError:
        raise
    except Exception:
        logger.warning("[payment_log] claim index unavailable source=%s", source)
        raise PaymentLogError(
            "transactions_unavailable",
            503,
            "The payment lock index could not be ensured. Nothing was recorded.",
        ) from None

    alert: dict = {}

    async def work(session):
        alert["hit"] = await _apply(
            db,
            txn_key=txn_key,
            cents=cents,
            amount=amount_value,
            status=provider_status,
            booking=booking,
            kind=kind,
            source=source,
            extra=fields,
            session=session,
        )

    try:
        await _run_apply(client, work)
    except AlreadyRecorded:
        return await _duplicate_or_conflict(
            db, txn_key=txn_key, cents=cents, status=provider_status,
            booking=booking, amount=amount_value, kind=kind,
        )
    except PaymentLogError:
        raise
    except Exception:
        logger.warning("[payment_log] record failed source=%s", source)
        raise PaymentLogError(
            "payment_record_failed",
            500,
            "The payment could not be recorded. Nothing was written.",
        ) from None

    # After commit. A Slack failure must not undo the payment.
    if alert.get("hit"):
        try:
            _notify_staff_review(alert["hit"])
        except Exception:
            logger.warning("[payment_log] staff review alert failed kind=%s", kind)

    return {
        "ok": True,
        "duplicate": False,
        "committed": True,
        "transaction_id": txn_key,
        "amount": amount_value,
        "booking_number": booking,
        "status": provider_status,
        "event_kind": kind,
    }
