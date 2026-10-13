
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
# The claim insert is the gate. The row write, the plan ``$inc`` (and the
# paid-in-full clamp), and the ``audit_events`` insert then run inside one
# ``ClientSession.with_transaction`` when the deployment is a replica set or
# Atlas (mongos). The driver retries transient transaction errors.
# Standalone Mongo has no multi-document transactions: the same steps run
# one at a time and the claim records ``row_linked``, ``balance_applied``,
# then ``audited`` so a later request resumes. A partial link whose row no
# longer matches, and whose balance was not moved, is reversed instead.
# This path does not implement refund/reversal lifecycle, county/state
# backfill, email-only bond matching, or poller/webhook claim changes.

_TOKEN_FIELDS = ("log_claim_token", "claim_token", "payment_claim_token")
_SETTLED_STATUSES = frozenset({
    "approved", "paid", "completed", "complete", "success", "succeeded",
    "captured", "settled",
})
_SETTLED_EVENTS = frozenset({
    "payment.approved", "payment.paid", "payment.completed", "payment.complete",
    "payment.success", "payment.succeeded", "payment.captured", "payment.settled",
})
_RESUME_STEPS = ("row_linked", "balance_applied", "audited")
_AUDIT_EVENT = "payment_plan_payment_applied"

# In-process guard so two concurrent requests in this process don't both run
# the steps. A dead process drops the set; the claim step is what a retry reads.
_inflight_txns: set[str] = set()
_txn_support_cache: dict[int, bool] = {}
_last_apply_path: str = ""


class PlanPaymentRefused(Exception):
    """The payment must not be applied. The transaction, when one is open, aborts."""

    def __init__(self, error: str, message: str):
        super().__init__(error)
        self.error = error
        self.message = message


class RetryableTxnError(Exception):
    """Signals a transient transaction error to a test session shim.

    Production uses ``ClientSession.with_transaction``, which retries
    PyMongo errors labeled ``TransientTransactionError`` and commit results
    labeled ``UnknownTransactionCommitResult``.
    """


def _try_hold(txn: str) -> bool:
    if txn in _inflight_txns:
        return False
    _inflight_txns.add(txn)
    return True


def _release(txn: str) -> None:
    _inflight_txns.discard(txn)


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


def _our_row_ok(row: dict, plan: dict, amount) -> bool:
    """A row this claim already attached still matches the plan and the cents."""
    if not _same_amount(row.get("amount"), amount):
        return False
    row_booking = _booking(row.get("booking_number"))
    plan_booking = _booking(plan.get("booking_number"))
    if row_booking and row_booking != plan_booking:
        return False
    if not row_booking and not plan_booking:
        return False
    return True


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
            {"$set": {"status": "failed", "failed_at": now, "failure_reason": reason, "step": reason}},
        )
    except Exception:
        logger.warning("[payment_plans] could not mark claim failed plan_id=%s", plan_id)


async def _set_step(claims, plan_id: str, txn: str, step: str, session=None) -> None:
    await claims.update_one(
        {"transaction_id": txn, "plan_id": plan_id, "status": "claimed"},
        {"$set": {"step": step}},
        **_sess(session),
    )


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


async def _ensure_row(payments_col, plan, plan_id, txn, amount, data, now, session=None):
    """Link the committed row, or insert one. Idempotent when this claim already wrote it."""
    rows = await _docs(payments_col, {"transaction_id": txn}, session)
    token_rows = [row for row in rows if _has_token(row)]
    committed = [row for row in rows if not _has_token(row)]
    if token_rows:
        error = "logged_payment_ambiguous" if len(rows) > 1 else "logged_payment_uncommitted"
        raise PlanPaymentRefused(
            error,
            "This transaction id is on an uncommitted payment row.",
        )
    ours = [row for row in committed if row.get("plan_id") == plan_id]
    others = [row for row in committed if not _plan_id_blank(row) and row.get("plan_id") != plan_id]
    unlinked = [row for row in committed if _plan_id_blank(row)]
    if others or len(committed) > 1:
        raise PlanPaymentRefused(
            "logged_payment_ambiguous" if len(committed) > 1 else "transaction_id_used_on_other_plan",
            "This transaction id is already on another payment row.",
        )
    if ours:
        if not _our_row_ok(ours[0], plan, amount):
            raise PlanPaymentRefused(
                "logged_payment_booking_mismatch",
                "The payment row for this transaction id no longer matches this plan.",
            )
        if str(ours[0].get("status") or "").strip().lower() != "completed" or not ours[0].get("timestamp"):
            fields = {"status": "completed", "type": "installment"}
            if not ours[0].get("timestamp"):
                fields["timestamp"] = ours[0].get("created_at") or now.isoformat()
            if "logged_status" not in ours[0] and ours[0].get("status"):
                fields["logged_status"] = ours[0].get("status")
            await payments_col.update_one(
                {"transaction_id": txn, "plan_id": plan_id, "amount": ours[0].get("amount")},
                {"$set": fields},
                **_sess(session),
            )
        return
    if len(unlinked) > 1:
        raise PlanPaymentRefused(
            "logged_payment_ambiguous",
            "More than one logged payment has this transaction id.",
        )
    if len(unlinked) == 1:
        row = unlinked[0]
        code = _qualify_code(plan, row, amount)
        if code:
            raise PlanPaymentRefused(code[0], code[1])
        linked = _link_set(row, plan_id, now)
        await payments_col.update_one(_link_pin(row, txn), {"$set": linked}, **_sess(session))
        fresh_rows = await _docs(payments_col, {"transaction_id": txn, "plan_id": plan_id}, session)
        fresh = fresh_rows[0] if fresh_rows else None
        if (not fresh or fresh.get("status") != "completed"
                or not _same_amount(fresh.get("amount"), amount)
                or not _same_booking(plan, fresh)):
            raise PlanPaymentRefused(
                "logged_payment_link_lost",
                "The logged payment changed while it was being linked. Nothing was applied to the plan.",
            )
        return
    if committed:
        raise PlanPaymentRefused(
            "logged_payment_ambiguous",
            "This transaction id matches a payment row that cannot be linked.",
        )
    booking = _booking(plan.get("booking_number"))
    await payments_col.insert_one({
        "booking_number": booking,
        "plan_id": plan_id,
        "amount": amount,
        "method": data.get("method", "Cash"),
        "type": "installment",
        "status": "completed",
        "timestamp": now.isoformat(),
        "transaction_id": txn,
        "notes": data.get("notes", ""),
    }, **_sess(session))


async def _reverse_uncollected_link(payments_col, plan_id: str, txn: str, row: dict) -> None:
    """Detach a link this claim made when the balance was not moved."""
    restored = row.get("logged_status")
    if restored is None:
        restored = row.get("status")
    await payments_col.update_one(
        {"transaction_id": txn, "plan_id": plan_id},
        {"$set": {"plan_id": "", "status": restored}},
    )


async def _reconcile_partial(payments_col, plans_col, plan, plan_id, txn, amount):
    """Reverse a crashed link that no longer matches, before any new balance write.

    Runs outside the money transaction so an abort cannot undo the detach.
    """
    rows = await _docs(payments_col, {"transaction_id": txn})
    ours = [row for row in rows if row.get("plan_id") == plan_id and not _has_token(row)]
    if not ours:
        return
    row = ours[0]
    if _our_row_ok(row, plan, amount):
        return
    current = await plans_col.find_one({"plan_id": plan_id}) or {}
    if _collected(current, txn):
        raise PlanPaymentRefused(
            "payment_needs_review",
            "This payment no longer matches the plan, and the balance was already moved. "
            "Check the plan before recording it again.",
        )
    await _reverse_uncollected_link(payments_col, plan_id, txn, row)
    if not _same_amount(row.get("amount"), amount):
        error = "logged_payment_amount_mismatch"
        message = "The payment row amount no longer matches. The link was removed and the balance was not changed."
    else:
        error = "logged_payment_booking_mismatch"
        message = "The payment row booking no longer matches this plan. The link was removed and the balance was not changed."
    raise PlanPaymentRefused(error, message)


async def _apply_balance(plans_col, plan_id, txn, amount, now, session=None) -> None:
    """Idempotent ``$inc``. A second run sees the transaction id already collected."""
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
        await plans_col.update_one(
            {"plan_id": plan_id, **pin},
            {
                "$inc": {
                    "balance_remaining": -amount,
                    "total_paid": amount,
                    "payments_made": 1,
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
    await plans_col.update_one(
        {"plan_id": plan_id, "balance_remaining": {"$lte": 0}},
        {"$set": {"balance_remaining": 0, "status": "paid_in_full"}},
        **_sess(session),
    )
    fresh = await plans_col.find_one({"plan_id": plan_id}, **_sess(session))
    if not _collected(fresh or {}, txn) or not _balance_ok(fresh or {}):
        raise RuntimeError("balance update did not stick")


async def _guard_before_balance(payments_col, plan, plan_id, txn, amount, session=None) -> None:
    """Do not move the balance unless this claim still owns exactly one matching row."""
    rows = await _docs(payments_col, {"transaction_id": txn}, session)
    committed = [row for row in rows if not _has_token(row)]
    if len(committed) != 1:
        raise PlanPaymentRefused(
            "logged_payment_ambiguous",
            "More than one payment row has this transaction id. The balance was not changed.",
        )
    row = committed[0]
    if row.get("plan_id") != plan_id or not _our_row_ok(row, plan, amount):
        raise PlanPaymentRefused(
            "logged_payment_link_lost",
            "The payment row changed before the balance was updated. The balance was not changed.",
        )
    if str(row.get("status") or "").strip().lower() != "completed":
        raise PlanPaymentRefused(
            "logged_payment_not_settled",
            "The payment row is no longer a collected plan payment. The balance was not changed.",
        )


async def _audit(audit_col, actor: str, plan, plan_id: str, txn: str, amount, linked: bool, session=None) -> None:
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
            "_id": f"plan-pay:{plan_id}:{txn}",
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
        }, **_sess(session))
    except DuplicateKeyError:
        return


async def _mark_applied(claims, plans_col, plan_id, txn, now, session=None) -> dict:
    fresh = await plans_col.find_one({"plan_id": plan_id}, **_sess(session)) or {}
    await claims.update_one(
        {"transaction_id": txn, "plan_id": plan_id, "status": {"$ne": "failed"}},
        {"$set": {
            "status": "applied",
            "step": "applied",
            "applied_at": now.isoformat(),
            "balance_after": fresh.get("balance_remaining"),
        }},
        **_sess(session),
    )
    return fresh


async def _apply_steps(claims, payments_col, plans_col, audit_col, plan, plan_id, txn, amount, data, actor, now, session=None) -> None:
    claim = await claims.find_one({"transaction_id": txn, "plan_id": plan_id}, **_sess(session)) or {}
    step = str(claim.get("step") or "")
    if step in ("", "claimed"):
        await _ensure_row(payments_col, plan, plan_id, txn, amount, data, now, session)
        await _set_step(claims, plan_id, txn, "row_linked", session)
        step = "row_linked"
    if step == "row_linked":
        await _guard_before_balance(payments_col, plan, plan_id, txn, amount, session)
        await _apply_balance(plans_col, plan_id, txn, amount, now, session)
        await _set_step(claims, plan_id, txn, "balance_applied", session)
        step = "balance_applied"
    if step == "balance_applied":
        linked_rows = await _docs(payments_col, {"transaction_id": txn, "plan_id": plan_id}, session)
        linked = bool(linked_rows) and linked_rows[0].get("logged_status") is not None
        await _audit(audit_col, actor, plan, plan_id, txn, amount, linked, session)
        await _set_step(claims, plan_id, txn, "audited", session)
        step = "audited"
    if step == "audited":
        await _mark_applied(claims, plans_col, plan_id, txn, now, session)
        return
    if step == "applied":
        if str(claim.get("status") or "") != "applied":
            await _mark_applied(claims, plans_col, plan_id, txn, now, session)
        return
    raise PlanPaymentRefused(
        "payment_needs_review",
        "This transaction id is on a payment claim that cannot be resumed. Check the plan before recording it again.",
    )


async def _run_apply(client, work) -> str:
    """One transaction when the server supports it. Otherwise the same steps, committed one by one."""
    global _last_apply_path
    if client is None:
        _last_apply_path = "fallback"
        await work(None)
        return _last_apply_path
    _last_apply_path = "transaction"

    async def _callback(session):
        await work(session)

    started = client.start_session()
    if hasattr(started, "__await__"):
        started = await started
    async with started as session:
        await session.with_transaction(_callback)
    return _last_apply_path


async def _continue_claim(request, claims, plans_col, payments_col, plan, plan_id, txn, amount, data, *, inserted: bool):
    """Link or insert, move the balance, and audit. Caller holds the in-process guard."""
    claim = await claims.find_one({"transaction_id": txn, "plan_id": plan_id}) or {}
    started = str(claim.get("step") or "")
    try:
        await _reconcile_partial(payments_col, plans_col, plan, plan_id, txn, amount)
        audit_col = get_collection("audit_events")
        now = datetime.now(timezone.utc)

        async def work(session):
            await _apply_steps(
                claims, payments_col, plans_col, audit_col, plan, plan_id, txn,
                amount, data, _actor(request), now, session,
            )

        await _run_apply(await _txn_client(), work)
    except PlanPaymentRefused as exc:
        current = await plans_col.find_one({"plan_id": plan_id}) or {}
        if not _collected(current, txn):
            rows = await _docs(payments_col, {"transaction_id": txn})
            ours = [
                row for row in rows
                if row.get("plan_id") == plan_id and row.get("logged_status") is not None
                and not _has_token(row)
            ]
            if ours and exc.error in (
                "logged_payment_ambiguous", "logged_payment_link_lost",
                "logged_payment_not_settled", "logged_payment_booking_mismatch",
                "logged_payment_amount_mismatch",
            ):
                await _reverse_uncollected_link(payments_col, plan_id, txn, ours[0])
        await _fail_claim(claims, plan_id, txn, exc.error)
        return _refuse(exc.error, txn, exc.message)
    except Exception:
        logger.warning("[payment_plans] plan payment failed plan_id=%s", plan_id)
        current = await claims.find_one({"transaction_id": txn, "plan_id": plan_id}) or {}
        if current.get("status") == "claimed" and str(current.get("step") or "") in _RESUME_STEPS:
            return JSONResponse({"error": "payment_record_failed", "transaction_id": txn}, status_code=500)
        await _fail_claim(claims, plan_id, txn, "payment_write_error")
        return JSONResponse({"error": "payment_record_failed", "transaction_id": txn}, status_code=500)

    fresh = await plans_col.find_one({"plan_id": plan_id}) or {}
    idempotent = (not inserted) and started in ("balance_applied", "audited", "applied")
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
        "idempotent": idempotent,
        "transaction_id": txn,
        "balance_remaining": fresh.get("balance_remaining"),
        "total_paid": fresh.get("total_paid"),
        "status": fresh.get("status"),
    }


async def _enter_existing(request, claims, plans_col, payments_col, plan, plan_id, txn, amount, data, claim):
    """Idempotent success, 409, or a resume of a crashed claim."""
    if claim.get("plan_id") and claim.get("plan_id") != plan_id:
        return _refuse(
            "transaction_id_used_on_other_plan", txn,
            "This transaction id is already recorded on another payment plan.",
        )
    status = claim.get("status")
    step = claim.get("step")
    resumable = status == "claimed" or step in _RESUME_STEPS
    if status in ("applied", "failed") or not resumable:
        return await _idempotent_reply(plans_col, plan_id, txn, claim, amount)
    if "amount" in claim and claim.get("amount") is not None and not _same_amount(claim.get("amount"), amount):
        return _refuse(
            "transaction_id_amount_mismatch", txn,
            "This transaction id was already recorded with a different amount.",
        )
    if not _try_hold(txn):
        return _refuse(
            "payment_in_progress", txn,
            "This transaction id is being recorded or did not finish. Wait and retry; "
            "if it persists, check the plan before recording it again.",
        )
    try:
        return await _continue_claim(
            request, claims, plans_col, payments_col, plan, plan_id, txn, amount, data, inserted=False,
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
    from pymongo.errors import DuplicateKeyError

    try:
        # Parse JSON numbers as Decimal straight from the raw body: request.json()
        # would turn {"amount": 1.00000000000000000000000000001} into the float
        # 1.0 before validation, hiding the fractional cents.
        data = _json.loads(await request.body(), parse_float=Decimal)
    except Exception:
        data = None
    if not data or not isinstance(data, dict) or 'amount' not in data:
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

    try:
        claims = await _payment_claims_collection()
    except Exception:
        return JSONResponse({"error": "payment_dedupe_unavailable"}, status_code=503)

    # A claim, when one exists, decides. A payment row left by an unfinished
    # claim is not treated as already paid; a retry resumes from the claim step.
    existing_claim = await claims.find_one({"transaction_id": txn})
    if existing_claim is not None:
        return await _enter_existing(
            request, claims, plans_col, payments_col, plan, plan_id, txn, amount, data, existing_claim,
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
            request, claims, plans_col, payments_col, plan, plan_id, txn, amount, data,
            existing or {"status": "unknown"},
        )

    # Hold synchronously after the insert returns, before the next await, so a
    # concurrent request observes the guard.
    if not _try_hold(txn):
        return _refuse(
            "payment_in_progress", txn,
            "This transaction id is being recorded or did not finish. Wait and retry; "
            "if it persists, check the plan before recording it again.",
        )
    try:
        return await _continue_claim(
            request, claims, plans_col, payments_col, plan, plan_id, txn, amount, data, inserted=True,
        )
    finally:
        _release(txn)


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