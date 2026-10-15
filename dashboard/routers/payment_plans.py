
"""
ShamrockLeads — Payment Plans API
The Payment Agent: Track premiums, payment plans, delinquency, and revenue.

Endpoints:
  GET  /payments/plans                 — List all active payment plans
  GET  /payments/plans/<booking>       — Get plan for a specific bond
  POST /payments/plans                 — Create a new payment plan
  POST /payments/plans/<plan_id>/pay   — Record a payment against a plan (transaction_id required; links a committed row or inserts one)
  GET  /payments/delinquent            — Get all delinquent plans (>30 days)
  GET  /payments/summary               — Revenue summary (daily/weekly/monthly)
  GET  /payments/premium-calc          — Quick premium calculator
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from datetime import datetime, timezone, timedelta
import logging
import uuid
import json as _json
from decimal import Decimal

from dashboard.extensions import get_collection
from dashboard.tenancy.context import current_tenant_id
from dashboard.tenancy.flag import multi_tenant_enabled

logger = logging.getLogger(__name__)

payment_plans_bp = APIRouter(prefix="/api", tags=["payment_plans"])
# ── Premium Calculator ──────────────────────────────────────────────────────────

SURETY_RATES = {
    "osi": {
        "premium_rate": 0.10,       # 10% of bond
        "surety_rate": 0.075,       # 7.5% of premium to surety
        "buf_rate": 0.05,           # 5% of premium to BUF
        "min_premium": 100,         # Minimum premium
    },
    "palmetto": {
        "premium_rate": 0.10,       # 10% of bond
        "surety_rate": 0.10,        # 10% of premium to surety
        "buf_rate": 0.05,           # 5% of premium to BUF
        "min_premium": 100,
    },
}


def calculate_premium(bond_amount: float, surety_id: str = "osi") -> dict:
    """Calculate premium split for a given bond amount and surety."""
    rates = SURETY_RATES.get(surety_id.lower(), SURETY_RATES["osi"])
    premium = max(bond_amount * rates["premium_rate"], rates["min_premium"])
    surety_owed = premium * rates["surety_rate"]
    buf_owed = premium * rates["buf_rate"]
    agent_retains = premium - surety_owed - buf_owed

    return {
        "bond_amount": bond_amount,
        "surety_id": surety_id,
        "premium": round(premium, 2),
        "surety_owed": round(surety_owed, 2),
        "buf_owed": round(buf_owed, 2),
        "agent_retains": round(agent_retains, 2),
        "rates": {
            "premium_pct": rates["premium_rate"] * 100,
            "surety_pct": rates["surety_rate"] * 100,
            "buf_pct": rates["buf_rate"] * 100,
            "agent_pct": round((1 - rates["surety_rate"] - rates["buf_rate"]) * 100, 1),
        },
    }


@payment_plans_bp.get("/payments/premium-calc")
async def premium_calculator(bond_amount: int = Query(default=0), surety_id: str = Query(default='osi')):
    """Quick premium calculator — GET with query params."""
    bond_amount = float(bond_amount)
    surety_id = surety_id.lower()
    if bond_amount <= 0:
        return JSONResponse({"error": "bond_amount must be positive"}, status_code=400)
    return calculate_premium(bond_amount, surety_id)


# ── Payment Plans CRUD ──────────────────────────────────────────────────────────

@payment_plans_bp.get("/payments/plans")
async def list_plans(status: str = Query(default='')):
    """List all payment plans with optional status filter."""
    plans_col = get_collection("payment_plans")
    status = status.strip()
    query = {}
    if status:
        query["status"] = status

    cursor = plans_col.find(query).sort("created_at", -1).limit(200)
    results = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)

    return {"plans": results, "total": len(results)}


@payment_plans_bp.get("/payments/plans/{booking_number}")
async def get_plan(booking_number):
    """Get payment plan for a specific booking."""
    plans_col = get_collection("payment_plans")
    doc = await plans_col.find_one({"booking_number": booking_number})
    if not doc:
        return JSONResponse({"error": "No payment plan found"}, status_code=404)
    doc["_id"] = str(doc["_id"])

    # Get payment history
    payments_col = get_collection("payments")
    payments = []
    async for p in payments_col.find({"booking_number": booking_number}).sort("timestamp", -1):
        p["_id"] = str(p["_id"])
        payments.append(p)

    doc["payments"] = payments
    return doc


@payment_plans_bp.post("/payments/plans")
async def create_plan(request: Request):
    """Create a new payment plan."""
    from dashboard.routers.events import publish_event
    data = await request.json()
    required = ['booking_number', 'total_premium', 'down_payment']
    for field in required:
        if field not in data:
            return JSONResponse({"error": f"Missing required field: {field}"}, status_code=400)

    plans_col = get_collection("payment_plans")

    # Check for existing plan
    existing = await plans_col.find_one({"booking_number": data["booking_number"], "status": "active"})
    if existing:
        return JSONResponse({"error": "Active payment plan already exists for this booking"}, status_code=409)

    total_premium = float(data["total_premium"])
    down_payment = float(data["down_payment"])
    balance = total_premium - down_payment
    installment = float(data.get("installment_amount", 0))
    frequency = data.get("frequency", "weekly")  # weekly, biweekly, monthly

    # Calculate next due date based on frequency
    now = datetime.now(timezone.utc)
    if frequency == "weekly":
        next_due = now + timedelta(days=7)
    elif frequency == "biweekly":
        next_due = now + timedelta(days=14)
    else:
        next_due = now + timedelta(days=30)

    plan_doc = {
        "plan_id": str(uuid.uuid4())[:12],
        "booking_number": data["booking_number"],
        "defendant_name": data.get("defendant_name", ""),
        "indemnitor_name": data.get("indemnitor_name", ""),
        "indemnitor_phone": data.get("indemnitor_phone", ""),
        "bond_amount": float(data.get("bond_amount", 0)),
        "surety_id": data.get("surety_id", "osi"),
        "total_premium": total_premium,
        "down_payment": down_payment,
        "balance_remaining": balance,
        "installment_amount": installment,
        "frequency": frequency,
        "next_due_date": next_due.isoformat(),
        "payments_made": 1 if down_payment > 0 else 0,
        "total_paid": down_payment,
        "status": "active",
        "delinquent": False,
        "delinquent_days": 0,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "created_by": data.get("created_by", "dashboard"),
    }

    await plans_col.insert_one(plan_doc)
    plan_doc["_id"] = str(plan_doc.get("_id", ""))

    # Record the down payment
    if down_payment > 0:
        payments_col = get_collection("payments")
        await payments_col.insert_one({
            "booking_number": data["booking_number"],
            "plan_id": plan_doc["plan_id"],
            "amount": down_payment,
            "method": data.get("payment_method", "Cash"),
            "type": "down_payment",
            "status": "completed",
            "timestamp": now.isoformat(),
            "notes": "Initial down payment",
        })

    await publish_event("payment_plan_created", {
        "plan_id": plan_doc["plan_id"],
        "booking_number": data["booking_number"],
        "total_premium": total_premium,
        "down_payment": down_payment,
    })

    return JSONResponse(status_code=201, content={"success": True, "plan": plan_doc})


# ── Payment dedupe (one application per plan + transaction id) ───────────────
#
# Collection ``payment_plan_payment_claims`` with a unique index on
# (tenant_id, transaction_id) — one application of a transaction id per
# tenant, across ALL plans (a card txn / receipt # is one real payment).
# Tenant indexes are not applied yet, so today tenant_id is absent (null) and
# the index is effectively one-per-transaction-id. Created lazily and
# idempotently on first use
# (``create_index`` is a no-op when the index already exists). No migration.
# If the index cannot be ensured the request fails closed (503) and nothing
# is recorded. The insert of the claim is the atomic gate: only the request
# that inserts it may write the payment and move the balance.

PAYMENT_CLAIMS_COLLECTION = "payment_plan_payment_claims"
_payment_claim_index_ready = False


async def _payment_claims_collection():
    global _payment_claim_index_ready
    coll = get_collection(PAYMENT_CLAIMS_COLLECTION)
    if not _payment_claim_index_ready:
        await coll.create_index(
            [("tenant_id", 1), ("transaction_id", 1)],
            unique=True,
            name="uniq_tenant_txn",
        )
        _payment_claim_index_ready = True
    return coll


def _next_due(frequency: str, now: datetime) -> datetime:
    if frequency == "weekly":
        return now + timedelta(days=7)
    if frequency == "biweekly":
        return now + timedelta(days=14)
    return now + timedelta(days=30)


def _amount_cents(value) -> int | None:
    """Exact integer cents, or None when not a positive amount with at most
    two decimal places (fractional cents are refused, never rounded)."""
    from decimal import Decimal, InvalidOperation

    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or len(text) > 64:
        return None
    try:
        dec = Decimal(text)  # construction is exact (no context rounding)
    except (InvalidOperation, ValueError):
        return None
    if not dec.is_finite() or dec <= 0:
        return None
    # Exact integer arithmetic on the digits; no Decimal multiplication, so the
    # 28-digit context can never round fractional cents away.
    _sign, digits, exponent = dec.as_tuple()
    if exponent > 12:  # > 10^12 dollars: refuse before building a huge int
        return None
    shift = exponent + 2  # power of ten to apply to the digit string for cents
    if shift >= 0:
        cents = int("".join(map(str, digits)) or "0") * (10 ** shift)
    else:
        drop = digits[len(digits) + shift:] if -shift <= len(digits) else digits
        if any(d != 0 for d in drop):
            return None  # fractional cents
        kept = digits[: len(digits) + shift] if -shift < len(digits) else ()
        cents = int("".join(map(str, kept)) or "0")
    if cents <= 0 or cents > 10 ** 14:  # cap $1 trillion
        return None
    return cents


def _same_amount(a, b) -> bool:
    ca, cb = _amount_cents(a), _amount_cents(b)
    return ca is not None and ca == cb


async def _idempotent_reply(
    plans_col, plan_id: str, txn: str, claim: dict | None, amount: float,
    legacy_row: dict | None = None,
):
    """Success only when the earlier application is the SAME payment: same
    plan, same amount, and finished (``applied`` claim, or a legacy payment
    row when no claim exists). Anything else is refused, never reported paid."""
    prior = claim if claim is not None else (legacy_row or {})
    prior_plan = prior.get("plan_id")
    if prior_plan and prior_plan != plan_id:
        return JSONResponse(
            {"error": "transaction_id_used_on_other_plan", "transaction_id": txn,
             "message": "This transaction id is already recorded on another payment plan."},
            status_code=409,
        )
    status = (claim or {}).get("status") if claim is not None else "applied"
    if status == "failed":
        return JSONResponse(
            {"error": "payment_needs_review", "transaction_id": txn,
             "message": "An earlier attempt with this transaction id did not finish. Check the plan before recording it again."},
            status_code=409,
        )
    if status != "applied":
        # 'claimed' (another request is mid-write, or a worker died after the
        # claim) or unknown/missing claim doc: not confirmed, so not success.
        return JSONResponse(
            {"error": "payment_in_progress", "transaction_id": txn,
             "message": "This transaction id is being recorded or did not finish. Wait and retry; if it persists, check the plan before recording it again."},
            status_code=409,
        )
    if not _same_amount(prior.get("amount"), amount):
        return JSONResponse(
            {"error": "transaction_id_amount_mismatch", "transaction_id": txn,
             "message": "This transaction id was already recorded with a different amount."},
            status_code=409,
        )
    plan = await plans_col.find_one({"plan_id": plan_id}) or {}
    return {
        "success": True,
        "idempotent": True,
        "transaction_id": txn,
        "balance_remaining": plan.get("balance_remaining"),
        "total_paid": plan.get("total_paid"),
        "status": plan.get("status"),
    }


# ── Link a committed logged payment, or insert one, in one serialized path ──
#
# A logged row is linkable only when it is committed (no claim/log token
# fields), settled, the same exact cents, and the same non-blank booking
# number as the plan. Anything else is 409 and the balance does not move.
# No index is created on ``payments``.
#
# The claim insert is the gate, and only after a replica set or Atlas is
# confirmed. Standalone Mongo and a failed transaction probe return 503 and
# write nothing. Inside the transaction, booking, settled status, exact
# cents, and claim state are read and refused before the first write. Then
# the row write, the plan ``$inc`` (and the paid-in-full clamp), and the
# ``audit_events`` insert run in that same ``ClientSession.with_transaction``.
# The driver retries transient transaction errors. There is no fallback and
# no step resume. Refunds go through ``reverse_plan_payment`` (balance
# restored once). Declines are logged and do not move the balance.
# Webhook and the receipt poller call ``apply_plan_payment`` with the
# same claim. County/state backfill and email-only bond matching stay out.

_TOKEN_FIELDS = ("log_claim_token", "claim_token", "payment_claim_token")
_SETTLED_STATUSES = frozenset({
    "approved", "paid", "completed", "complete", "success", "succeeded",
    "captured", "settled",
})
_SETTLED_EVENTS = frozenset({
    "payment.approved", "payment.paid", "payment.completed", "payment.complete",
    "payment.success", "payment.succeeded", "payment.captured", "payment.settled",
})
_AUDIT_EVENT = "payment_plan_payment_applied"

# In-process guard so two concurrent requests in this process don't both run
# the transaction after one of them has inserted the claim. The key is
# (tenant, transaction id). Flag off uses one empty tenant for every request,
# so a transaction id still has a single hold. Flag on, two agencies with the
# same transaction id do not share a hold: the unique claim index already
# allows both inserts, and a shared hold would return payment_in_progress
# after the second claim was written, leaving it stuck as ``claimed``.
_inflight_txns: set[tuple[str, str]] = set()
_txn_support_cache: dict[int, bool] = {}
_last_apply_path: str = ""


class PlanPaymentRefused(Exception):
    """The payment must not be applied. The transaction, when one is open, aborts."""

    def __init__(self, error: str, message: str):
        super().__init__(error)
        self.error = error
        self.message = message


class TransactionsUnavailable(Exception):
    """No replica set, or the transaction probe failed. Record nothing."""


def _unavailable() -> JSONResponse:
    return JSONResponse(
        {"error": "transactions_unavailable",
         "message": "Recording a plan payment needs a MongoDB replica set or Atlas. Nothing was recorded."},
        status_code=503,
    )


class RetryableTxnError(Exception):
    """Signals a transient transaction error to a test session shim.

    Production uses ``ClientSession.with_transaction``, which retries
    PyMongo errors labeled ``TransientTransactionError`` and commit results
    labeled ``UnknownTransactionCommitResult``.
    """


def _inflight_key(txn: str) -> tuple[str, str]:
    """Hold identity. Flag off is one key per transaction id."""
    if not multi_tenant_enabled():
        return ("", txn)
    return (current_tenant_id() or "", txn)


def _try_hold(txn: str) -> bool:
    key = _inflight_key(txn)
    if key in _inflight_txns:
        return False
    _inflight_txns.add(key)
    return True


def _release(txn: str) -> None:
    _inflight_txns.discard(_inflight_key(txn))


def _sess(session) -> dict:
    return {"session": session} if session is not None else {}


def _booking(value) -> str:
    return str(value or "").strip()


def _plan_id_blank(row: dict) -> bool:
    if "plan_id" not in row or row.get("plan_id") is None:
        return True
    return str(row.get("plan_id")).strip() == ""


def _has_token(row: dict) -> bool:
    return any(field in row for field in _TOKEN_FIELDS)


def _event_type(row: dict) -> str:
    direct = row.get("event_type")
    if direct is not None and str(direct).strip():
        return str(direct).strip().lower()
    raw = row.get("raw_payload")
    if isinstance(raw, dict) and raw.get("event_type") is not None and str(raw.get("event_type")).strip():
        return str(raw.get("event_type")).strip().lower()
    return ""


def _settled(row: dict) -> bool:
    status = str(row.get("status") or "").strip().lower()
    if status not in _SETTLED_STATUSES:
        return False
    event = _event_type(row)
    if event and event not in _SETTLED_EVENTS:
        return False
    return True


def _same_booking(plan: dict, row: dict) -> bool:
    plan_booking = _booking(plan.get("booking_number"))
    row_booking = _booking(row.get("booking_number"))
    return bool(plan_booking) and plan_booking == row_booking


def _refuse(error: str, txn: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": error, "transaction_id": txn, "message": message},
        status_code=409,
    )


def _qualify_code(plan: dict, row: dict, amount):
    """``(error, message)`` when the logged row must not be linked."""
    if not _settled(row):
        return (
            "logged_payment_not_settled",
            "The logged payment with this transaction id is not settled "
            "(declined, refunded, voided, pending, or no status).",
        )
    if _amount_cents(row.get("amount")) is None or not _same_amount(row.get("amount"), amount):
        return (
            "logged_payment_amount_mismatch",
            "The logged payment with this transaction id has a different amount.",
        )
    if not _same_booking(plan, row):
        return (
            "logged_payment_booking_mismatch",
            "The logged payment must have the same non-blank booking number as this plan.",
        )
    return None


def _qualify(plan: dict, row: dict, amount, txn: str):
    """None when the logged row may be linked. Otherwise a 409 response."""
    code = _qualify_code(plan, row, amount)
    if code is None:
        return None
    return _refuse(code[0], txn, code[1])


async def _docs(coll, flt: dict, session=None) -> list:
    kwargs = _sess(session)
    cursor = coll.find(flt, **kwargs) if kwargs else coll.find(flt)
    out = []
    async for doc in cursor:
        out.append(doc)
    return out


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


async def _txn_client():
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
        logger.warning("[payment_plans] transaction support check failed")
        return None
    return None


def _actor(request: Request) -> str:
    try:
        from dashboard.auth.pin_middleware import get_session_from_request
        sess = get_session_from_request(request) or {}
    except Exception:
        sess = {}
    name = str(sess.get("agent_name") or sess.get("email") or "").strip()
    if name:
        return name
    header = str(request.headers.get("x-actor") or "").strip()
    return header or "staff"


def _link_pin(row: dict, txn: str) -> dict:
    """Update only while status, amount, booking, and an absent plan_id are unchanged."""
    pin = {
        "transaction_id": txn,
        "status": row.get("status"),
        "amount": row.get("amount"),
        "booking_number": row.get("booking_number"),
        "$or": [
            {"plan_id": {"$exists": False}},
            {"plan_id": None},
            {"plan_id": ""},
        ],
    }
    for field in _TOKEN_FIELDS:
        pin[field] = {"$exists": False}
    event = _event_type(row)
    if event:
        if row.get("event_type"):
            pin["event_type"] = row.get("event_type")
        else:
            pin["raw_payload.event_type"] = (row.get("raw_payload") or {}).get("event_type")
    return pin


def _link_set(row: dict, plan_id: str, now: datetime) -> dict:
    """Plan-collected shape. ``revenue_summary`` counts ``status: completed``."""
    fields = {
        "plan_id": plan_id,
        "type": "installment",
        "status": "completed",
        "logged_status": row.get("status"),
        "plan_linked_at": now.isoformat(),
    }
    if not row.get("timestamp"):
        fields["timestamp"] = row.get("created_at") or now.isoformat()
    return fields


async def _fail_claim(claims, plan_id: str, txn: str, reason: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        await claims.update_one(
            {"transaction_id": txn, "plan_id": plan_id, "status": {"$ne": "applied"}},
            {"$set": {"status": "failed", "failed_at": now, "failure_reason": reason}},
        )
    except Exception:
        logger.warning("[payment_plans] could not mark claim failed plan_id=%s", plan_id)


def _collected(plan: dict, txn: str) -> bool:
    ids = plan.get("collected_transaction_ids")
    return isinstance(ids, list) and txn in ids


def _balance_ok(plan: dict) -> bool:
    if not isinstance(plan, dict) or "balance_remaining" not in plan or "status" not in plan:
        return False
    try:
        bal = float(plan["balance_remaining"])
    except (TypeError, ValueError):
        return False
    if bal > 0:
        return True
    return bal == 0.0 and plan.get("status") == "paid_in_full"


async def _classify_rows(payments_col, plan: dict, txn: str, amount):
    """Return ``(target_or_None, error_response)``.

    ``target`` is the single committed unlinked row to link. None means insert.
    """
    rows = await _docs(payments_col, {"transaction_id": txn})
    if not rows:
        if not _booking(plan.get("booking_number")):
            return "error", _refuse(
                "logged_payment_booking_mismatch", txn,
                "The plan must have a non-blank booking number before a payment can be recorded.",
            )
        return "insert", None
    token_rows = [row for row in rows if _has_token(row)]
    committed = [row for row in rows if not _has_token(row)]
    if token_rows:
        error = "logged_payment_ambiguous" if len(rows) > 1 else "logged_payment_uncommitted"
        message = (
            "More than one payment row has this transaction id. Review it before applying it to a plan."
            if error == "logged_payment_ambiguous"
            else "This transaction id is on an uncommitted payment row. It cannot be applied to a plan."
        )
        return "error", _refuse(error, txn, message)
    if len(committed) > 1:
        return "error", _refuse(
            "logged_payment_ambiguous", txn,
            "More than one payment row has this transaction id. Review it before applying it to a plan.",
        )
    row = committed[0]
    if not _plan_id_blank(row):
        return "legacy", row
    refusal = _qualify(plan, row, amount, txn)
    if refusal is not None:
        return "error", refusal
    return "link", None


async def _assert_preconditions(claims, payments_col, plans_col, plan_id, txn, amount, session):
    """Read booking, settlement, cents, and claim state. No writes."""
    plan = await plans_col.find_one({"plan_id": plan_id}, **_sess(session))
    if not plan:
        raise RuntimeError("plan missing before payment write")
    claim = await claims.find_one({"transaction_id": txn}, **_sess(session)) or {}
    if claim.get("plan_id") != plan_id or claim.get("status") != "claimed":
        raise PlanPaymentRefused(
            "payment_in_progress",
            "This transaction id is being recorded or did not finish. Wait and retry; "
            "if it persists, check the plan before recording it again.",
        )
    if "amount" in claim and claim.get("amount") is not None and not _same_amount(claim.get("amount"), amount):
        raise PlanPaymentRefused(
            "transaction_id_amount_mismatch",
            "This transaction id was already recorded with a different amount.",
        )
    if _amount_cents(amount) is None:
        raise PlanPaymentRefused(
            "logged_payment_amount_mismatch",
            "Amount must be a positive number with at most two decimal places.",
        )
    if not _booking(plan.get("booking_number")):
        raise PlanPaymentRefused(
            "logged_payment_booking_mismatch",
            "The plan must have a non-blank booking number before a payment can be recorded.",
        )
    rows = await _docs(payments_col, {"transaction_id": txn}, session)
    token_rows = [row for row in rows if _has_token(row)]
    committed = [row for row in rows if not _has_token(row)]
    if token_rows:
        error = "logged_payment_ambiguous" if len(rows) > 1 else "logged_payment_uncommitted"
        message = (
            "More than one payment row has this transaction id. The balance was not changed."
            if error == "logged_payment_ambiguous"
            else "This transaction id is on an uncommitted payment row. It cannot be applied to a plan."
        )
        raise PlanPaymentRefused(error, message)
    if len(committed) > 1:
        raise PlanPaymentRefused(
            "logged_payment_ambiguous",
            "More than one payment row has this transaction id. The balance was not changed.",
        )
    if len(committed) == 1:
        row = committed[0]
        if not _plan_id_blank(row):
            if row.get("plan_id") != plan_id:
                raise PlanPaymentRefused(
                    "transaction_id_used_on_other_plan",
                    "This transaction id is already recorded on another payment plan.",
                )
            raise PlanPaymentRefused(
                "payment_needs_review",
                "This transaction id is already on a payment row for this plan. "
                "Check the plan before recording it again.",
            )
        code = _qualify_code(plan, row, amount)
        if code:
            raise PlanPaymentRefused(code[0], code[1])
        return plan, "link", row
    return plan, "insert", None


async def _write_payment_row(payments_col, plan, plan_id, txn, amount, data, now, mode, row, session):
    """Link or insert. Preconditions have already passed; this is the first payment write."""
    if mode == "link":
        await payments_col.update_one(_link_pin(row, txn), {"$set": _link_set(row, plan_id, now)}, **_sess(session))
        fresh_rows = await _docs(payments_col, {"transaction_id": txn, "plan_id": plan_id}, session)
        fresh = fresh_rows[0] if fresh_rows else None
        if (not fresh or fresh.get("status") != "completed"
                or not _same_amount(fresh.get("amount"), amount)
                or not _same_booking(plan, fresh)):
            raise PlanPaymentRefused(
                "logged_payment_link_lost",
                "The logged payment changed while it was being linked. Nothing was applied to the plan.",
            )
        return True
    booking = _booking(plan.get("booking_number"))
    if not booking:
        raise PlanPaymentRefused(
            "logged_payment_booking_mismatch",
            "The plan must have a non-blank booking number before a payment can be recorded.",
        )
    doc = {
        "booking_number": booking,
        "plan_id": plan_id,
        "amount": amount,
        "method": data.get("method", "Cash"),
        "type": "installment",
        "status": "completed",
        "timestamp": now.isoformat(),
        "transaction_id": txn,
        "notes": data.get("notes", ""),
    }
    installment_n = _installment_n(data)
    if installment_n is not None:
        doc["installment_n"] = installment_n
    await payments_col.insert_one(doc, **_sess(session))
    return False


def _whole_cents(value) -> int | None:
    """An integer cent field. Booleans are not cents."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _stored_dollars(value) -> float | None:
    """A stored dollar amount as a float, including zero.

    Numbers stay numbers. A decimal string such as ``1000.00`` becomes a
    float so ``$inc`` can add it. Fractional cents are refused.
    """
    from decimal import Decimal, InvalidOperation

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    if not isinstance(value, str):
        return None
    text = value.strip().replace(",", "").replace("$", "")
    if not text or len(text) > 64:
        return None
    try:
        dec = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not dec.is_finite():
        return None
    exponent = dec.as_tuple().exponent
    if isinstance(exponent, str) or exponent < -2:
        return None
    return float(dec)


def _numeric_money_patch(plan: dict) -> dict:
    """Dollar fields ``$inc`` can add.

    ``ensure_payment_plan_for_confirmation`` stores ``balance_remaining`` and
    ``total_paid`` as decimal strings. When the matching ``*_cents`` field is
    an integer, that integer is the source of truth and the dollar field
    becomes ``cents / 100``. A string with no cent field is parsed. Values
    that are already numbers are left alone.
    """
    patch = {}
    for dollars_key, cents_key in (
        ("balance_remaining", "balance_remaining_cents"),
        ("total_paid", "total_paid_cents"),
    ):
        current = plan.get(dollars_key)
        if not isinstance(current, str):
            if current is not None and _stored_dollars(current) is None:
                raise RuntimeError(f"{dollars_key} is not a numeric amount")
            continue
        cents = _whole_cents(plan.get(cents_key))
        if cents is not None:
            patch[dollars_key] = cents / 100
            continue
        number = _stored_dollars(current)
        if number is None:
            raise RuntimeError(f"{dollars_key} is not a numeric amount")
        patch[dollars_key] = number
    return patch


async def _coerce_plan_money(plans_col, plan_id, plan, session=None) -> dict:
    """Rewrite decimal-string balances to numbers inside the caller's transaction."""
    patch = _numeric_money_patch(plan)
    if not patch:
        return plan
    pinned = {"plan_id": plan_id}
    for key in patch:
        pinned[key] = plan.get(key)
    await plans_col.update_one(pinned, {"$set": patch}, **_sess(session))
    fresh = await plans_col.find_one({"plan_id": plan_id}, **_sess(session))
    if not isinstance(fresh, dict):
        raise RuntimeError("plan missing during balance update")
    return fresh


def _money_still_text(plan: dict) -> bool:
    return any(isinstance(plan.get(key), str) for key in ("balance_remaining", "total_paid"))


def _cent_increments(plan: dict, amount, *, paying: bool) -> dict:
    """Integer cent companions. Absent fields stay absent.

    A payment decreases ``balance_remaining_cents`` and increases
    ``total_paid_cents``. A refund does the opposite.
    """
    cents = _amount_cents(amount)
    if not cents:
        return {}
    balance_delta = -cents if paying else cents
    paid_delta = cents if paying else -cents
    inc = {}
    if _whole_cents(plan.get("balance_remaining_cents")) is not None:
        inc["balance_remaining_cents"] = balance_delta
    if _whole_cents(plan.get("total_paid_cents")) is not None:
        inc["total_paid_cents"] = paid_delta
    return inc


async def _close_if_paid_off(plans_col, plan_id, session=None) -> None:
    """Mark paid in full from integer cents when that field is the contract."""
    plan = await plans_col.find_one({"plan_id": plan_id}, **_sess(session)) or {}
    if _whole_cents(plan.get("balance_remaining_cents")) is not None:
        await plans_col.update_one(
            {"plan_id": plan_id, "balance_remaining_cents": {"$lte": 0}},
            {"$set": {
                "balance_remaining": 0,
                "balance_remaining_cents": 0,
                "status": "paid_in_full",
            }},
            **_sess(session),
        )
        return
    balance = plan.get("balance_remaining")
    if isinstance(balance, str):
        return
    await plans_col.update_one(
        {"plan_id": plan_id, "balance_remaining": {"$lte": 0}},
        {"$set": {"balance_remaining": 0, "status": "paid_in_full"}},
        **_sess(session),
    )


async def _apply_balance(plans_col, plan_id, txn, amount, now, session=None) -> None:
    """Idempotent ``$inc``. A second run sees the transaction id already collected.

    Decimal-string balances from ``ensure_payment_plan_for_confirmation`` are
    coerced to numbers in this same transaction before ``$inc``. Integer
    ``balance_remaining_cents`` stays the source of truth.
    """
    for _ in range(3):
        plan = await plans_col.find_one({"plan_id": plan_id}, **_sess(session))
        if not plan:
            raise RuntimeError("plan missing during balance update")
        if _collected(plan, txn):
            break
        raw_ids = plan.get("collected_transaction_ids", None)
        if raw_ids is None and "collected_transaction_ids" not in plan:
            pin = {"collected_transaction_ids": {"$exists": False}}
            ids = []
        elif isinstance(raw_ids, list):
            pin = {"collected_transaction_ids": raw_ids}
            ids = list(raw_ids)
        else:
            raise RuntimeError("collected_transaction_ids unreadable")
        if txn in ids:
            break
        if _money_still_text(plan):
            plan = await _coerce_plan_money(plans_col, plan_id, plan, session)
            if _money_still_text(plan):
                continue
        await plans_col.update_one(
            {"plan_id": plan_id, **pin},
            {
                "$inc": {
                    "balance_remaining": -amount,
                    "total_paid": amount,
                    "payments_made": 1,
                    **_cent_increments(plan, amount, paying=True),
                },
                "$set": {
                    "collected_transaction_ids": ids + [txn],
                    "next_due_date": _next_due(plan.get("frequency", "weekly"), now).isoformat(),
                    "delinquent": False,
                    "delinquent_days": 0,
                    "updated_at": now.isoformat(),
                },
            },
            **_sess(session),
        )
    await _close_if_paid_off(plans_col, plan_id, session)
    fresh = await plans_col.find_one({"plan_id": plan_id}, **_sess(session))
    if not _collected(fresh or {}, txn) or not _balance_ok(fresh or {}):
        raise RuntimeError("balance update did not stick")


def _installment_n(data: dict | None):
    """Installment number from a pay body, or None when this is not one row."""
    if not isinstance(data, dict):
        return None
    raw = data.get("installment_n")
    if raw in (None, ""):
        return None
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number > 36:
        return None
    return number


async def _mark_installment_paid(plans_col, plan_id, data, txn, now, session=None) -> None:
    """Stamp the schedule row paid and point next_due_date at the next open row."""
    number = _installment_n(data)
    if number is None:
        return
    plan = await plans_col.find_one({"plan_id": plan_id}, **_sess(session)) or {}
    rows = plan.get("installments")
    if not isinstance(rows, list):
        return
    next_due = None
    changed = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            row_n = int(row.get("n") or 0)
        except (TypeError, ValueError):
            continue
        if row_n == number and row.get("status") != "reversed":
            row["status"] = "paid"
            row["paid_transaction_id"] = txn
            row["paid_at"] = now.isoformat()
            changed = True
        elif row.get("status") in {"scheduled", "invoiced"} and not next_due:
            next_due = row.get("due_date")
    if not changed:
        return
    patch = {"installments": rows}
    if next_due:
        patch["next_due_date"] = next_due
    await plans_col.update_one({"plan_id": plan_id}, {"$set": patch}, **_sess(session))


def _audit_id(plan_id: str, txn: str) -> str:
    """Deterministic ``audit_events`` ``_id``.

    Flag off stays ``plan-pay:{plan_id}:{txn}``. Flag on includes the tenant
    so two agencies with the same plan id and transaction id do not collide
    on the global ``_id`` index and abort the second transaction. The
    tenant-scoped lookup cannot see the other agency's row.
    """
    if not multi_tenant_enabled():
        return f"plan-pay:{plan_id}:{txn}"
    return f"plan-pay:{current_tenant_id() or ''}:{plan_id}:{txn}"


async def _audit(audit_col, actor: str, plan, plan_id: str, txn: str, amount, linked: bool, before: dict, after: dict, session=None) -> None:
    from pymongo.errors import DuplicateKeyError

    existing = await audit_col.find_one(
        {"event_type": _AUDIT_EVENT, "entity_id": plan_id, "transaction_id": txn},
        **_sess(session),
    )
    if existing:
        return
    now = datetime.now(timezone.utc)
    try:
        await audit_col.insert_one({
            # _id is unique on audit_events without adding an index on payments.
            "_id": _audit_id(plan_id, txn),
            "event_type": _AUDIT_EVENT,
            "entity_type": "payment_plan",
            "entity_id": plan_id,
            "actor": actor,
            "agent_name": actor,
            "timestamp": now,
            "created_at": now,
            "booking_number": _booking(plan.get("booking_number")),
            "transaction_id": txn,
            "plan_id": plan_id,
            "amount": amount,
            "linked_existing_row": linked,
            "reason": "plan payment applied",
            "confidence": 1.0,
            "old_state": {
                "balance_remaining": before.get("balance_remaining"),
                "status": before.get("status"),
            },
            "new_state": {
                "balance_remaining": after.get("balance_remaining"),
                "status": after.get("status"),
            },
        }, **_sess(session))
    except DuplicateKeyError:
        return


async def _mark_applied(claims, plans_col, plan_id, txn, now, session=None) -> dict:
    fresh = await plans_col.find_one({"plan_id": plan_id}, **_sess(session)) or {}
    await claims.update_one(
        {"transaction_id": txn, "plan_id": plan_id, "status": {"$ne": "failed"}},
        {"$set": {
            "status": "applied",
            "applied_at": now.isoformat(),
            "balance_after": fresh.get("balance_remaining"),
        }},
        **_sess(session),
    )
    return fresh


async def _apply_in_transaction(claims, payments_col, plans_col, audit_col, plan_id, txn, amount, data, actor, now, session) -> None:
    """Validate, then write the row, the balance, the audit, and the claim, in that order."""
    plan, mode, row = await _assert_preconditions(
        claims, payments_col, plans_col, plan_id, txn, amount, session,
    )
    before = {
        "balance_remaining": plan.get("balance_remaining"),
        "status": plan.get("status"),
    }
    linked = await _write_payment_row(
        payments_col, plan, plan_id, txn, amount, data, now, mode, row, session,
    )
    await _apply_balance(plans_col, plan_id, txn, amount, now, session)
    await _mark_installment_paid(plans_col, plan_id, data, txn, now, session)
    fresh = await plans_col.find_one({"plan_id": plan_id}, **_sess(session)) or {}
    after = {
        "balance_remaining": fresh.get("balance_remaining"),
        "status": fresh.get("status"),
    }
    await _audit(
        audit_col, actor, plan, plan_id, txn, amount, linked, before, after, session,
    )
    await _mark_applied(claims, plans_col, plan_id, txn, now, session)


async def _transaction_client():
    """A client that can run a multi-document transaction, or None. Never raises."""
    try:
        client = await _txn_client()
    except Exception:
        logger.warning("[payment_plans] transaction support check failed")
        return None
    if client is None:
        return None
    try:
        if await _supports_transactions(client):
            return client
    except Exception:
        logger.warning("[payment_plans] transaction support check failed")
        return None
    return None


async def _run_apply(client, work) -> str:
    """One transaction. Refuses to write when the server cannot run one."""
    global _last_apply_path
    if client is None:
        raise TransactionsUnavailable()
    try:
        supported = await _supports_transactions(client)
    except Exception:
        supported = False
    if not supported:
        raise TransactionsUnavailable()
    _last_apply_path = "transaction"

    async def _callback(session):
        await work(session)

    started = client.start_session()
    if hasattr(started, "__await__"):
        started = await started
    async with started as session:
        await session.with_transaction(_callback)
    return _last_apply_path


async def _continue_claim(actor, claims, plans_col, payments_col, plan, plan_id, txn, amount, data, client):
    """Apply inside the transaction the caller already confirmed is available."""
    try:
        audit_col = get_collection("audit_events")
        now = datetime.now(timezone.utc)

        async def work(session):
            await _apply_in_transaction(
                claims, payments_col, plans_col, audit_col, plan_id, txn,
                amount, data, actor, now, session,
            )

        await _run_apply(client, work)
    except TransactionsUnavailable:
        await _fail_claim(claims, plan_id, txn, "transactions_unavailable")
        return _unavailable()
    except PlanPaymentRefused as exc:
        await _fail_claim(claims, plan_id, txn, exc.error)
        return _refuse(exc.error, txn, exc.message)
    except Exception:
        logger.warning("[payment_plans] plan payment failed plan_id=%s", plan_id)
        await _fail_claim(claims, plan_id, txn, "payment_write_error")
        return JSONResponse({"error": "payment_record_failed", "transaction_id": txn}, status_code=500)

    fresh = await plans_col.find_one({"plan_id": plan_id}) or {}
    try:
        from dashboard.routers.events import publish_event
        await publish_event("payment_received", {
            "plan_id": plan_id,
            "amount": amount,
            "balance_remaining": fresh.get("balance_remaining"),
            "status": fresh.get("status"),
        })
    except Exception:
        logger.warning("[payment_plans] payment event publish failed plan_id=%s", plan_id)
    return {
        "success": True,
        "idempotent": False,
        "transaction_id": txn,
        "balance_remaining": fresh.get("balance_remaining"),
        "total_paid": fresh.get("total_paid"),
        "status": fresh.get("status"),
    }


async def _enter_existing(claims, plans_col, payments_col, plan, plan_id, txn, amount, data, claim):
    """Idempotent success or 409. A claim that is not applied is not resumed."""
    if claim.get("plan_id") and claim.get("plan_id") != plan_id:
        return _refuse(
            "transaction_id_used_on_other_plan", txn,
            "This transaction id is already recorded on another payment plan.",
        )
    status = claim.get("status")
    if status not in ("applied", "failed"):
        if "amount" in claim and claim.get("amount") is not None and not _same_amount(claim.get("amount"), amount):
            return _refuse(
                "transaction_id_amount_mismatch", txn,
                "This transaction id was already recorded with a different amount.",
            )
    return await _idempotent_reply(plans_col, plan_id, txn, claim, amount)


async def apply_plan_payment(plan_id, data, actor: str):
    """Apply one settled amount to a plan. Webhook, poller, and ``/pay`` share this.

    ``actor`` is the session user or the gateway name. It is never taken from
    the body. The claim, the payment row, the balance, and the audit commit
    in one transaction, or none of them do.
    """
    from pymongo.errors import DuplicateKeyError

    if not data or not isinstance(data, dict) or "amount" not in data:
        return JSONResponse({"error": "Missing amount"}, status_code=400)

    txn = str(data.get("transaction_id") or "").strip()
    if not txn:
        return JSONResponse(
            {"error": "transaction_id_required",
             "message": "Send the card transaction id, or the receipt number for cash or check."},
            status_code=400,
        )
    amount_cents = _amount_cents(data.get("amount"))
    if amount_cents is None:
        return JSONResponse(
            {"error": "invalid_amount",
             "message": "Amount must be a positive number with at most two decimal places."},
            status_code=400,
        )
    amount = amount_cents / 100

    plans_col = get_collection("payment_plans")
    payments_col = get_collection("payments")

    plan = await plans_col.find_one({"plan_id": plan_id})
    if not plan:
        return JSONResponse({"error": "Plan not found"}, status_code=404)

    client = await _transaction_client()
    if client is None:
        return _unavailable()

    try:
        claims = await _payment_claims_collection()
    except Exception:
        return JSONResponse({"error": "payment_dedupe_unavailable"}, status_code=503)

    # A finished claim decides. Anything still claimed is not resumed and is not success.
    existing_claim = await claims.find_one({"transaction_id": txn})
    if existing_claim is not None:
        return await _enter_existing(
            claims, plans_col, payments_col, plan, plan_id, txn, amount, data, existing_claim,
        )

    kind, payload = await _classify_rows(payments_col, plan, txn, amount)
    if kind == "error":
        return payload
    if kind == "legacy":
        # A plan payment recorded before claims existed. Rows with no plan_id
        # are not this branch; those are linked below instead of inserted again.
        return await _idempotent_reply(plans_col, plan_id, txn, None, amount, legacy_row=payload)

    now = datetime.now(timezone.utc)
    try:
        await claims.insert_one({
            "plan_id": plan_id,
            "transaction_id": txn,
            "booking_number": plan.get("booking_number"),
            "amount": amount,
            "status": "claimed",
            "claimed_at": now.isoformat(),
        })
    except DuplicateKeyError:
        existing = await claims.find_one({"transaction_id": txn})
        return await _enter_existing(
            claims, plans_col, payments_col, plan, plan_id, txn, amount, data,
            existing or {"status": "unknown"},
        )

    # Hold synchronously after the insert returns, before the next await, so a
    # concurrent request for this same tenant and transaction id observes the
    # guard. Another tenant's identical transaction id is a different hold.
    if not _try_hold(txn):
        return _refuse(
            "payment_in_progress", txn,
            "This transaction id is being recorded or did not finish. Wait and retry; "
            "if it persists, check the plan before recording it again.",
        )
    try:
        return await _continue_claim(
            actor, claims, plans_col, payments_col, plan, plan_id, txn, amount, data, client,
        )
    finally:
        _release(txn)


@payment_plans_bp.post("/payments/plans/{plan_id}/pay")
async def record_payment(request: Request, plan_id):
    """Record a payment against an existing plan — at most once per transaction id.

    ``transaction_id`` is required (400 ``transaction_id_required``). The same
    transaction id on the same plan returns ``idempotent: true`` with no second
    payment row and no balance change, including under concurrent requests.
    A committed logged payment with the same id is linked instead of inserted again.
    """
    try:
        # Parse JSON numbers as Decimal straight from the raw body: request.json()
        # would turn {"amount": 1.00000000000000000000000000001} into the float
        # 1.0 before validation, hiding the fractional cents.
        data = _json.loads(await request.body(), parse_float=Decimal)
    except Exception:
        data = None
    return await apply_plan_payment(plan_id, data, _actor(request))


_REVERSED_AUDIT = "payment_plan_payment_reversed"
_FAILED_AUDIT = "payment_plan_payment_failed"
_REVERSE_REJECTED = "payment_plan_payment_reverse_rejected"


def _scoped_audit_id(prefix: str, plan_id: str, txn: str) -> str:
    if not multi_tenant_enabled():
        return f"{prefix}:{plan_id}:{txn}"
    return f"{prefix}:{current_tenant_id() or ''}:{plan_id}:{txn}"


async def _insert_audit_once(audit_col, doc: dict, session=None) -> bool:
    """Insert one audit row. A duplicate ``_id`` is a replay, not a second event."""
    from pymongo.errors import DuplicateKeyError

    existing = await audit_col.find_one({"_id": doc["_id"]}, **_sess(session))
    if existing:
        return False
    try:
        await audit_col.insert_one(doc, **_sess(session))
    except DuplicateKeyError:
        return False
    return True


async def note_plan_payment_failure(plan_id, data, actor: str):
    """Log one decline. The balance, the claim, and the payment row stay put."""
    txn = str((data or {}).get("transaction_id") or "").strip()
    if not txn:
        return JSONResponse(
            {"error": "transaction_id_required",
             "message": "A declined payment still needs its transaction id."},
            status_code=400,
        )
    plans_col = get_collection("payment_plans")
    plan = await plans_col.find_one({"plan_id": plan_id})
    if not plan:
        return JSONResponse({"error": "Plan not found"}, status_code=404)
    before = plan.get("balance_remaining")
    audit_col = get_collection("audit_events")
    now = datetime.now(timezone.utc)
    wrote = await _insert_audit_once(audit_col, {
        "_id": _scoped_audit_id("plan-fail", plan_id, txn),
        "event_type": _FAILED_AUDIT,
        "entity_type": "payment_plan",
        "entity_id": plan_id,
        "actor": actor,
        "agent_name": actor,
        "timestamp": now,
        "created_at": now,
        "booking_number": _booking(plan.get("booking_number")),
        "transaction_id": txn,
        "plan_id": plan_id,
        "amount": (data or {}).get("amount"),
        "reason": "plan payment declined",
        "confidence": 1.0,
        "old_state": {"balance_remaining": before, "status": plan.get("status")},
        "new_state": {"balance_remaining": before, "status": plan.get("status")},
    })
    fresh = await plans_col.find_one({"plan_id": plan_id}) or {}
    return {
        "success": True,
        "idempotent": not wrote,
        "balance_unchanged": True,
        "transaction_id": txn,
        "balance_remaining": fresh.get("balance_remaining"),
        "status": fresh.get("status"),
    }


def _refund_ids(data: dict) -> tuple[str, str]:
    original = str(data.get("original_transaction_id") or data.get("transaction_id") or "").strip()
    refund_txn = str(data.get("refund_transaction_id") or "").strip()
    if not refund_txn or refund_txn == original:
        refund_txn = f"{original}:refund"
    return original, refund_txn


async def _restore_balance(plans_col, plan_id, original_txn, amount, now, session=None) -> None:
    """Put one applied amount back. A second call sees the transaction id already gone."""
    plan = await plans_col.find_one({"plan_id": plan_id}, **_sess(session))
    if not plan:
        raise RuntimeError("plan missing during refund")
    ids = plan.get("collected_transaction_ids")
    if not isinstance(ids, list) or original_txn not in ids:
        return
    if _money_still_text(plan):
        plan = await _coerce_plan_money(plans_col, plan_id, plan, session)
        if _money_still_text(plan):
            raise RuntimeError("plan money field is not numeric")
        ids = plan.get("collected_transaction_ids")
        if not isinstance(ids, list) or original_txn not in ids:
            return
    remaining = [item for item in ids if item != original_txn]
    await plans_col.update_one(
        {"plan_id": plan_id, "collected_transaction_ids": ids},
        {
            "$inc": {
                "balance_remaining": amount,
                "total_paid": -amount,
                "payments_made": -1,
                **_cent_increments(plan, amount, paying=False),
            },
            "$set": {
                "collected_transaction_ids": remaining,
                "status": "active",
                "updated_at": now.isoformat(),
            },
        },
        **_sess(session),
    )
    fresh = await plans_col.find_one({"plan_id": plan_id}, **_sess(session)) or {}
    if _collected(fresh, original_txn):
        raise RuntimeError("refund did not stick")


async def _mark_installment_reversed(plans_col, plan_id, data, original_txn, now, session=None) -> None:
    number = _installment_n(data)
    plan = await plans_col.find_one({"plan_id": plan_id}, **_sess(session)) or {}
    rows = plan.get("installments")
    if not isinstance(rows, list):
        return
    changed = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        paid_txn = str(row.get("paid_transaction_id") or "")
        row_n = _installment_n({"installment_n": row.get("n")})
        if (number is not None and row_n == number) or (paid_txn and paid_txn == original_txn):
            if row.get("status") != "reversed":
                row["status"] = "reversed"
                row["reversed_at"] = now.isoformat()
                changed = True
    if changed:
        await plans_col.update_one(
            {"plan_id": plan_id},
            {"$set": {"installments": rows}},
            **_sess(session),
        )


async def reverse_plan_payment(plan_id, data, actor: str):
    """Restore a plan balance once for a refund of an applied payment.

    The refund has its own transaction id. Replaying that id does not move
    the balance again. A decline is not a refund; this function is not called
    for those.
    """
    from pymongo.errors import DuplicateKeyError

    if not isinstance(data, dict):
        return JSONResponse({"error": "Missing amount"}, status_code=400)
    original, refund_txn = _refund_ids(data)
    if not original:
        return JSONResponse(
            {"error": "transaction_id_required",
             "message": "A refund needs the original transaction id."},
            status_code=400,
        )
    plans_col = get_collection("payment_plans")
    payments_col = get_collection("payments")
    plan = await plans_col.find_one({"plan_id": plan_id})
    if not plan:
        return JSONResponse({"error": "Plan not found"}, status_code=404)

    client = await _transaction_client()
    if client is None:
        return _unavailable()
    try:
        claims = await _payment_claims_collection()
    except Exception:
        return JSONResponse({"error": "payment_dedupe_unavailable"}, status_code=503)

    existing_refund = await claims.find_one({"transaction_id": refund_txn})
    if existing_refund is not None and existing_refund.get("status") == "applied":
        fresh = await plans_col.find_one({"plan_id": plan_id}) or {}
        return {
            "success": True,
            "idempotent": True,
            "transaction_id": refund_txn,
            "balance_remaining": fresh.get("balance_remaining"),
            "status": fresh.get("status"),
        }

    original_claim = await claims.find_one({"transaction_id": original, "plan_id": plan_id})
    if not original_claim or original_claim.get("status") not in {"applied", "reversed"}:
        audit_col = get_collection("audit_events")
        now = datetime.now(timezone.utc)
        await _insert_audit_once(audit_col, {
            "_id": _scoped_audit_id("plan-rev-reject", plan_id, refund_txn),
            "event_type": _REVERSE_REJECTED,
            "entity_type": "payment_plan",
            "entity_id": plan_id,
            "actor": actor,
            "agent_name": actor,
            "timestamp": now,
            "created_at": now,
            "booking_number": _booking(plan.get("booking_number")),
            "transaction_id": refund_txn,
            "plan_id": plan_id,
            "reason": "refund rejected; payment was not applied",
            "confidence": 1.0,
            "old_state": {"balance_remaining": plan.get("balance_remaining"), "status": plan.get("status")},
            "new_state": {"balance_remaining": plan.get("balance_remaining"), "status": plan.get("status")},
        })
        return _refuse(
            "payment_not_applied", refund_txn,
            "This refund does not match an applied plan payment. The balance was not changed.",
        )
    if original_claim.get("status") == "reversed":
        fresh = await plans_col.find_one({"plan_id": plan_id}) or {}
        return {
            "success": True,
            "idempotent": True,
            "transaction_id": refund_txn,
            "balance_remaining": fresh.get("balance_remaining"),
            "status": fresh.get("status"),
        }
    amount = original_claim.get("amount")
    if _amount_cents(amount) is None:
        return _refuse(
            "invalid_amount", refund_txn,
            "The applied payment amount cannot be reversed.",
        )
    if data.get("amount") not in (None, "") and not _same_amount(data.get("amount"), amount):
        return _refuse(
            "transaction_id_amount_mismatch", refund_txn,
            "This refund amount does not match the applied payment. The balance was not changed.",
        )

    now = datetime.now(timezone.utc)
    if existing_refund is None:
        try:
            await claims.insert_one({
                "plan_id": plan_id,
                "transaction_id": refund_txn,
                "original_transaction_id": original,
                "booking_number": plan.get("booking_number"),
                "amount": amount,
                "status": "claimed",
                "kind": "reversal",
                "claimed_at": now.isoformat(),
            })
        except DuplicateKeyError:
            existing_refund = await claims.find_one({"transaction_id": refund_txn}) or {}
            if existing_refund.get("status") == "applied":
                fresh = await plans_col.find_one({"plan_id": plan_id}) or {}
                return {
                    "success": True,
                    "idempotent": True,
                    "transaction_id": refund_txn,
                    "balance_remaining": fresh.get("balance_remaining"),
                    "status": fresh.get("status"),
                }
            return _refuse(
                "payment_in_progress", refund_txn,
                "This refund is already being recorded. The balance was not changed again.",
            )

    if not _try_hold(refund_txn):
        return _refuse(
            "payment_in_progress", refund_txn,
            "This refund is already being recorded. The balance was not changed again.",
        )
    try:
        audit_col = get_collection("audit_events")

        async def work(session):
            claim = await claims.find_one(
                {"transaction_id": original, "plan_id": plan_id}, **_sess(session),
            )
            if not claim or claim.get("status") != "applied":
                return
            before = await plans_col.find_one({"plan_id": plan_id}, **_sess(session)) or {}
            await _restore_balance(plans_col, plan_id, original, amount, now, session)
            await payments_col.update_one(
                {"transaction_id": original, "plan_id": plan_id},
                {"$set": {
                    "status": "reversed",
                    "reversed_at": now.isoformat(),
                    "reversal_transaction_id": refund_txn,
                }},
                **_sess(session),
            )
            await _mark_installment_reversed(plans_col, plan_id, data, original, now, session)
            fresh = await plans_col.find_one({"plan_id": plan_id}, **_sess(session)) or {}
            await _insert_audit_once(audit_col, {
                "_id": _scoped_audit_id("plan-rev", plan_id, refund_txn),
                "event_type": _REVERSED_AUDIT,
                "entity_type": "payment_plan",
                "entity_id": plan_id,
                "actor": actor,
                "agent_name": actor,
                "timestamp": now,
                "created_at": now,
                "booking_number": _booking(before.get("booking_number")),
                "transaction_id": refund_txn,
                "original_transaction_id": original,
                "plan_id": plan_id,
                "amount": amount,
                "reason": "plan payment reversed",
                "confidence": 1.0,
                "old_state": {
                    "balance_remaining": before.get("balance_remaining"),
                    "status": before.get("status"),
                },
                "new_state": {
                    "balance_remaining": fresh.get("balance_remaining"),
                    "status": fresh.get("status"),
                },
            }, session=session)
            await claims.update_one(
                {"transaction_id": original, "plan_id": plan_id, "status": "applied"},
                {"$set": {
                    "status": "reversed",
                    "reversed_at": now.isoformat(),
                    "reversal_transaction_id": refund_txn,
                }},
                **_sess(session),
            )
            await claims.update_one(
                {"transaction_id": refund_txn, "plan_id": plan_id},
                {"$set": {"status": "applied", "applied_at": now.isoformat()}},
                **_sess(session),
            )

        try:
            await _run_apply(client, work)
        except TransactionsUnavailable:
            await _fail_claim(claims, plan_id, refund_txn, "transactions_unavailable")
            return _unavailable()
        except Exception:
            logger.warning("[payment_plans] plan refund failed plan_id=%s", plan_id)
            await _fail_claim(claims, plan_id, refund_txn, "refund_write_error")
            return JSONResponse({"error": "payment_reverse_failed", "transaction_id": refund_txn}, status_code=500)
    finally:
        _release(refund_txn)

    fresh = await plans_col.find_one({"plan_id": plan_id}) or {}
    return {
        "success": True,
        "idempotent": False,
        "transaction_id": refund_txn,
        "balance_remaining": fresh.get("balance_remaining"),
        "balance_remaining_cents": fresh.get("balance_remaining_cents"),
        "status": fresh.get("status"),
    }


# ── Delinquency Detection ──────────────────────────────────────────────────────

@payment_plans_bp.get("/payments/delinquent")
async def get_delinquent(days: str = Query(default='30')):
    """Get all payment plans that are past due (>30 days since last payment or due date)."""
    plans_col = get_collection("payment_plans")
    now = datetime.now(timezone.utc)
    threshold = days
    try:
        threshold_days = int(threshold)
    except ValueError:
        threshold_days = 30

    cutoff = (now - timedelta(days=threshold_days)).isoformat()

    # Find active plans where next_due_date is past the cutoff
    cursor = plans_col.find({
        "status": "active",
        "next_due_date": {"$lt": cutoff},
    }).sort("next_due_date", 1)

    delinquent = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        # Calculate days overdue
        try:
            due_dt = datetime.fromisoformat(doc["next_due_date"].replace("Z", "+00:00"))
            doc["days_overdue"] = (now - due_dt).days
        except (ValueError, TypeError):
            doc["days_overdue"] = threshold_days
        delinquent.append(doc)

    return {"delinquent": delinquent, "total": len(delinquent), "threshold_days": threshold_days}


# ── Revenue Summary ─────────────────────────────────────────────────────────────

@payment_plans_bp.get("/payments/summary")
async def revenue_summary():
    """Revenue summary: total collected, outstanding, by period."""
    payments_col = get_collection("payments")
    plans_col = get_collection("payment_plans")
    now = datetime.now(timezone.utc)

    # Total collected (all time)
    pipeline_total = [
        {"$match": {"status": "completed"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]
    total_result = []
    async for doc in payments_col.aggregate(pipeline_total):
        total_result.append(doc)
    total_collected = total_result[0]["total"] if total_result else 0
    total_payments = total_result[0]["count"] if total_result else 0

    # This month
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    pipeline_month = [
        {"$match": {"status": "completed", "timestamp": {"$gte": month_start.isoformat()}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]
    month_result = []
    async for doc in payments_col.aggregate(pipeline_month):
        month_result.append(doc)
    month_collected = month_result[0]["total"] if month_result else 0

    # Outstanding balance (all active plans)
    pipeline_outstanding = [
        {"$match": {"status": "active"}},
        {"$group": {"_id": None, "total_outstanding": {"$sum": "$balance_remaining"}, "plans": {"$sum": 1}}},
    ]
    outstanding_result = []
    async for doc in plans_col.aggregate(pipeline_outstanding):
        outstanding_result.append(doc)
    total_outstanding = outstanding_result[0]["total_outstanding"] if outstanding_result else 0
    active_plans = outstanding_result[0]["plans"] if outstanding_result else 0

    # Delinquent count
    cutoff_30 = (now - timedelta(days=30)).isoformat()
    delinquent_count = await plans_col.count_documents({
        "status": "active",
        "next_due_date": {"$lt": cutoff_30},
    })

    return {
        "total_collected": round(total_collected, 2),
        "total_payments": total_payments,
        "month_collected": round(month_collected, 2),
        "total_outstanding": round(total_outstanding, 2),
        "active_plans": active_plans,
        "delinquent_count": delinquent_count,
        "updated_at": now.isoformat(),
    }