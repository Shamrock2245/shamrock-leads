"""Record one provider payment in a single MongoDB transaction.

The Gmail SwipeSimple receipt poller and ``POST /api/webhooks/payment`` both
call ``record_provider_payment``. The claim insert, the ``payments`` insert,
the ``bond_cases`` / ``active_bonds`` ``last_payment_*`` update, and the
``audit_events`` insert run inside one ``ClientSession.with_transaction``.
The driver retries transient transaction errors. A crash rolls the whole
transaction back: no claim, no payment row, no bond stamp, no audit.

The dedupe is a unique index on ``payment_log_claims``
``(tenant_id, txn_key, event_kind)``. ``txn_key`` is the stripped transaction
id. ``event_kind`` is ``charge``, ``refund``, or ``failed``, so a
``payment.refunded`` or ``payment.failed`` that reuses the charge's
transaction id is its own row. A ``DuplicateKeyError`` on that insert means
this kind is already recorded, so the caller returns duplicate and writes
nothing else. There is no index on ``payments``. There is no provisional
claim, no token field on the payment row, and no stale-claim takeover.

A refund or failure does not change a plan balance. When it links to a
booking or a plan, the same transaction sets ``needs_review`` on that bond
case and that plan. After the commit, one staff Slack alert is sent for that
``(tenant, txn_key, kind)``. A Slack failure is logged and does not roll the
payment back.

Out of scope: reversing a plan balance, reclaiming a stale claim, and
backfilling rows that were logged before this claim existed. A recorded row
of the same kind whose amount, settled status, or booking disagrees with the
new payload is a 409 and is left unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

logger = logging.getLogger(__name__)

LOG_CLAIMS_COLLECTION = "payment_log_claims"
_INDEX_NAME = "uniq_tenant_txn_kind"
_TOKEN_FIELDS = ("log_claim_token", "claim_token", "payment_claim_token")

# Same settled vocabulary as plan /pay. ``approved`` (poller and webhook) and
# ``completed`` (a plan row) are one settled charge.
_SETTLED = frozenset({
    "approved", "paid", "completed", "complete", "success", "succeeded",
    "captured", "settled",
})
_DECLINED = frozenset({"declined", "failed", "denied", "voided", "error"})
_REFUNDED = frozenset({"refunded", "refund", "reversed"})

_index_ready = False
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
    """The claim insert collided. The transaction aborts; the caller re-reads."""


class RetryableTxnError(Exception):
    """Signals a transient transaction error to a test session shim.

    Production uses ``ClientSession.with_transaction``, which retries
    PyMongo errors labeled ``TransientTransactionError`` and commit results
    labeled ``UnknownTransactionCommitResult``.
    """


def normalize_txn_key(value: Any) -> str:
    """Stripped transaction id. Blank, missing, and booleans are empty."""
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def amount_cents(value: Any) -> Optional[int]:
    """Exact integer cents, or None when the value is not a finite amount
    with at most two decimal places. Zero and negative are refused.
    Fractional cents are refused, never rounded.

    Same digit arithmetic as plan ``/pay``: no Decimal multiplication, so the
    28-digit context cannot round fractional cents away.
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


def event_kind(status: Any, event_type: Any = None) -> str:
    """``charge``, ``refund``, or ``failed``.

    A provider refund or failure is its own kind even when it reuses the
    charge's transaction id. Event type wins when it names one of those,
    and so does the status. Anything else settled (or blank) is a charge.
    """
    event = str(event_type or "").strip().lower()
    fact = _status_fact(status)
    refund_event = event.endswith((".refunded", ".refund", ".reversed")) or "refund" in event
    failed_event = event.endswith((".failed", ".declined", ".denied", ".voided", ".error"))
    if refund_event or fact == "refunded":
        return "refund"
    if failed_event or fact == "declined":
        return "failed"
    if fact in (None, "settled"):
        return "charge"
    return fact


def row_kind(row: dict) -> str:
    """Kind stored on the row, or inferred for a row logged before kinds existed."""
    stored = str(row.get("event_kind") or "").strip()
    if stored:
        return stored
    event = row.get("event_type") or (row.get("raw_payload") or {}).get("event_type")
    return event_kind(row.get("status"), event)


def _review_reason(kind: str) -> str:
    if kind == "refund":
        return "provider_refund_unreversed"
    return "provider_failure_unreversed"


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


def _booking(value: Any) -> str:
    return str(value or "").strip()


def _row_cents(row: dict) -> Optional[int]:
    if row.get("amount") is None:
        return None
    return amount_cents(row.get("amount"))


def conflict_reason(rows: list, *, cents: int, status: Any, booking: str) -> Optional[str]:
    """Why these recorded rows must not accept the payload.

    None means there is no recorded row (the caller may insert) or the single
    row agrees on every fact both sides carry. Amount is exact cents. Settled
    statuses (``approved`` and ``completed``) are one fact. A booking is a
    conflict only when both sides name a different non-blank booking.
    """
    if not rows:
        return None
    if len(rows) > 1:
        return "more than one payment row has this transaction id"
    row = rows[0]
    if row.get("amount") is not None:
        recorded = _row_cents(row)
        if recorded is None or recorded != cents:
            return "the recorded amount does not match this payload"
    recorded_status = _status_fact(row.get("status"))
    incoming_status = _status_fact(status)
    if recorded_status and incoming_status and recorded_status != incoming_status:
        return "the recorded status does not match this payload"
    recorded_booking = _booking(row.get("booking_number"))
    if recorded_booking and booking and recorded_booking.casefold() != booking.casefold():
        return "the recorded booking number does not match this payload"
    return None


def _plain(value: Any) -> Any:
    """BSON-safe copy. ``Decimal`` becomes its exact digit string."""
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
    """Deterministic ``audit_events`` ``_id``.

    Flag off is ``payment-log:{txn_key}:{kind}``. Flag on includes the tenant
    so two agencies with the same transaction id do not collide on the global
    ``_id`` index. The kind keeps a refund from colliding with the charge.
    No index is added on ``payments``.
    """
    from dashboard.tenancy.context import current_tenant_id
    from dashboard.tenancy.flag import multi_tenant_enabled

    if not multi_tenant_enabled():
        return f"payment-log:{txn_key}:{kind}"
    return f"payment-log:{current_tenant_id() or ''}:{txn_key}:{kind}"


async def _claims(db):
    global _index_ready
    coll = db[LOG_CLAIMS_COLLECTION]
    if not _index_ready:
        await coll.create_index(
            [("tenant_id", 1), ("txn_key", 1), ("event_kind", 1)],
            unique=True,
            name=_INDEX_NAME,
        )
        _index_ready = True
    return coll


async def _supports_transactions(client) -> bool:
    """True on a replica set or mongos (Atlas). Standalone is false.

    Cached per client object. A failed hello is not cached, so a later
    successful check can still enable transactions.
    """
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
    """Normalize before any database access. Blank txn and inexact cents are 400."""
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


async def _read_rows(db, txn_key: str, session=None, kind: Optional[str] = None) -> list:
    rows = await _docs(db["payments"], {"transaction_id": txn_key}, session)
    if kind is None:
        return rows
    return [row for row in rows if row_kind(row) == kind]


def _raise_if_conflict(rows: list, *, cents: int, status: Any, booking: str, txn_key: str) -> None:
    reason = conflict_reason(rows, cents=cents, status=status, booking=booking)
    if reason:
        raise PaymentLogError(
            "payment_conflict",
            409,
            "This transaction id is already recorded and the payload does not match "
            f"({reason}). Nothing was written.",
        )


def _review_flag(kind: str, amount: float, txn_key: str) -> dict:
    """Visible flag. Does not touch a plan balance."""
    return {
        "needs_review": True,
        "needs_review_reason": _review_reason(kind),
        "needs_review_event_kind": kind,
        "needs_review_amount": amount,
        "needs_review_transaction_id": txn_key,
    }


def _linked_booking(booking: str, siblings: list) -> str:
    if booking:
        return booking
    for row in siblings:
        found = _booking(row.get("booking_number"))
        if found:
            return found
    return ""


def _linked_plan_ids(siblings: list, plans: list) -> list:
    found: list[str] = []
    for row in list(siblings) + list(plans):
        plan_id = str(row.get("plan_id") or "").strip()
        if plan_id and plan_id not in found:
            found.append(plan_id)
    return found


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
    """Re-read, then write the claim, the payment, the bond stamps, and the audit.

    A refund or failure also sets ``needs_review`` on the linked bond case and
    plan. It does not change ``balance_remaining``. The returned dict is the
    staff alert, sent only after this transaction commits.
    """
    from pymongo.errors import DuplicateKeyError

    rows = await _read_rows(db, txn_key, session, kind=kind)
    _raise_if_conflict(rows, cents=cents, status=status, booking=booking, txn_key=txn_key)
    if rows:
        raise AlreadyRecorded()

    siblings = await _docs(db["payments"], {"transaction_id": txn_key}, session)

    # The unique index was ensured before this transaction opened.
    claims = db[LOG_CLAIMS_COLLECTION]
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    try:
        await claims.insert_one({
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

    doc = {
        "transaction_id": txn_key,
        "event_kind": kind,
        "booking_number": booking,
        "amount": amount,
        "amount_cents": cents,
        "status": status,
        "source": source,
        "created_at": now_iso,
    }
    for key, value in extra.items():
        if key in _TOKEN_FIELDS or key in doc:
            continue
        doc[key] = _plain(value)
    for field in _TOKEN_FIELDS:
        doc.pop(field, None)
    await db["payments"].insert_one(doc, **_sess(session))

    linked_booking = _linked_booking(booking, siblings)
    plan_rows: list = []
    if linked_booking:
        plan_rows = await _docs(
            db["payment_plans"], {"booking_number": linked_booking}, session,
        )
    plan_ids = _linked_plan_ids(siblings, plan_rows)
    review = kind in ("refund", "failed")
    flag = _review_flag(kind, amount, txn_key) if review else None

    if linked_booking:
        payment_update = {
            "last_payment_amount": amount,
            "last_payment_at": now_iso,
            "last_payment_status": status,
            "last_transaction_id": txn_key,
            "last_payment_source": source,
        }
        if flag:
            payment_update.update(flag)
        await db["bond_cases"].update_one(
            {"booking_number": linked_booking},
            {"$set": payment_update},
            **_sess(session),
        )
        await db["active_bonds"].update_one(
            {"booking_number": linked_booking},
            {"$set": payment_update},
            **_sess(session),
        )

    # The flag is the review signal. Balance fields are not in this update.
    if flag:
        for plan_id in plan_ids:
            await db["payment_plans"].update_one(
                {"plan_id": plan_id},
                {"$set": flag},
                **_sess(session),
            )

    audit_id = _audit_id(txn_key, kind)
    try:
        await db["audit_events"].insert_one({
            "_id": audit_id,
            "source": source,
            "event_type": str(extra.get("event_type") or f"payment.{status or 'recorded'}"),
            "entity_type": "payment",
            "entity_id": txn_key,
            "actor": source,
            "agent_name": source,
            "timestamp": now,
            "created_at": now,
            "booking_number": linked_booking or booking,
            "transaction_id": txn_key,
            "amount": amount,
            "event_kind": kind,
            "reason": "provider payment recorded",
            "confidence": 1.0,
            "payload": {
                "amount": amount,
                "booking_number": linked_booking or booking,
                "transaction_id": txn_key,
                "status": status,
                "event_kind": kind,
            },
        }, **_sess(session))
    except DuplicateKeyError:
        # The claim and the payment are in this same transaction. An audit id
        # collision aborts all of them; it is not a recorded duplicate.
        raise PaymentLogError(
            "payment_record_failed",
            500,
            "The payment audit could not be recorded. Nothing was written.",
        ) from None

    if not review:
        return None
    return {
        "kind": kind,
        "amount": amount,
        "booking": linked_booking,
        "plan_ids": plan_ids,
    }


def _notify_staff_review(hit: dict) -> None:
    """One #alerts post after commit. Failure is logged and does not roll back.

    The message is the plan id, booking id, amount, and event kind. Nothing
    else: no name, phone, email, or card. Once per committed
    ``(tenant, txn_key, kind)`` because a replay returns duplicate and does
    not call this.
    """
    kind = hit.get("kind") or ""
    booking = hit.get("booking") or "none"
    plan_ids = hit.get("plan_ids") or []
    plans = ", ".join(plan_ids) if plan_ids else "none"
    amount = hit.get("amount")
    try:
        amount_text = f"{float(amount):.2f}"
    except (TypeError, ValueError):
        amount_text = ""
    message = f"event_kind {kind}; amount {amount_text}; booking {booking}; plan {plans}"
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
                "plan_id": plans,
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
    """After a duplicate claim, re-read. A conflicting row is 409, not success.

    ``DuplicateKeyError`` means this event kind is already recorded: the claim
    and the payment commit together, and this attempt writes nothing. A
    recorded row of the same kind that disagrees is still 409. There is no
    stale-claim takeover and no second staff alert.
    """
    rows = await _read_rows(db, txn_key, kind=kind)
    _raise_if_conflict(rows, cents=cents, status=status, booking=booking, txn_key=txn_key)
    return {
        "ok": True,
        "duplicate": True,
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

    Validation (blank txn, exact cents, booking) happens before any write.
    An existing recorded row is compared before the transaction and again
    inside it. Transactions unavailable raises 503 and writes nothing.
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

    # A recorded row of this kind is classified before the claim index is touched.
    # A charge does not block a later refund or failure that shares its txn id.
    existing = await _read_rows(db, txn_key, kind=kind)
    if existing:
        _raise_if_conflict(
            existing, cents=cents, status=provider_status, booking=booking, txn_key=txn_key,
        )
        return {
            "ok": True,
            "duplicate": True,
            "transaction_id": txn_key,
            "amount": amount_value,
            "booking_number": booking,
            "status": provider_status,
            "event_kind": kind,
        }

    try:
        await _claims(db)
    except PaymentLogError:
        raise
    except Exception:
        logger.warning("[payment_log] claim index unavailable source=%s", source)
        raise PaymentLogError(
            "transactions_unavailable",
            503,
            "The payment claim index could not be ensured. Nothing was recorded.",
        ) from None

    review: dict = {}

    async def work(session):
        review["hit"] = await _apply(
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
    if review.get("hit"):
        _notify_staff_review(review["hit"])

    return {
        "ok": True,
        "duplicate": False,
        "transaction_id": txn_key,
        "amount": amount_value,
        "booking_number": booking,
        "status": provider_status,
        "event_kind": kind,
    }
