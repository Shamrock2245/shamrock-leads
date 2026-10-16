
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

def _audit_actor(request: Request) -> str:
    """Who an immutable financial audit row names.

    The signed session wins: ``agent_name``, then ``email``, then ``role``
    (same order as the AR and family-tree routers). A trusted machine key
    (``GAS_API_KEY`` / ``LEADS_INTERNAL_TOKEN``) with no session is recorded
    as ``machine_key``. The request body is never an identity.
    """
    from dashboard.auth.pin_middleware import get_session_from_request, is_machine_auth_valid

    sess = get_session_from_request(request) or {}
    if sess.get("auth"):
        name = str(sess.get("agent_name") or sess.get("email") or sess.get("role") or "").strip()
        return name or "dashboard"
    if is_machine_auth_valid(request):
        return "machine_key"
    return "dashboard"


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

    # Check for existing plan. needs_review is a plan whose linked payment
    # could not be detached after a failed finalization; another plan would
    # leave two records for one booking.
    existing = await plans_col.find_one({
        "booking_number": data["booking_number"],
        "status": {"$in": ["active", "needs_review"]},
    })
    if existing:
        if existing.get("status") == "needs_review":
            return JSONResponse(
                {"error": "payment_needs_review", "plan_id": existing.get("plan_id"),
                 "message": "A payment plan for this booking needs review before another can be created."},
                status_code=409,
            )
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

    dp_txn = str(data.get("down_payment_transaction_id") or "").strip()
    if dp_txn and down_payment > 0:
        err = await _create_plan_with_down_payment_txn(
            plans_col, plan_doc, dp_txn, down_payment,
            data.get("payment_method", "Card"), now, _audit_actor(request))
        if err is not None:
            return err
        plan_doc["_id"] = ""
    else:
        await plans_col.insert_one(plan_doc)
        plan_doc["_id"] = str(plan_doc.get("_id", ""))

        # Record the down payment (cash/check or card without a provider txn id)
        if down_payment > 0:
            payments_col = get_collection("payments")
            await payments_col.insert_one({
                "booking_number": data["booking_number"],
                "plan_id": plan_doc["plan_id"],
                "amount": down_payment,
                "method": data.get("payment_method", "Cash"),
                "type": "down_payment",
                "transaction_id": "",
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
# Same collection, key, and index name as the provider recorder's lock (#187).
# The unique index is on this collection. Nothing here creates an index on
# ``payments``.
TXN_LOCKS_COLLECTION = "payment_txn_locks"
_TXN_LOCK_INDEX = "uniq_tenant_txn_lock"
_DOWN_PAYMENT_AUDIT = "payment_plan_down_payment_recorded"
_CREATE_ROLLBACK_AUDIT = "payment_plan_create_rolled_back"
_payment_claim_index_ready = False
_plan_lock_index_ready = False


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


_SETTLED_STATUSES = {"approved", "paid", "completed", "success"}
_UNLINKED = {"$or": [{"plan_id": {"$exists": False}}, {"plan_id": None}, {"plan_id": ""}]}


def _conflict(error: str, txn: str, message: str, status_code: int = 409):
    return JSONResponse(
        {"error": error, "transaction_id": txn, "message": message},
        status_code=status_code,
    )


def _needs_review_response(txn: str, plan_id: str, message: str) -> JSONResponse:
    """Same shape for a kept link and for an incomplete rollback."""
    return JSONResponse(
        {"error": "payment_needs_review", "transaction_id": txn, "plan_id": plan_id,
         "message": message},
        status_code=500,
    )


def _row_settled(row: dict) -> bool:
    if str(row.get("status") or "").strip().lower() not in _SETTLED_STATUSES:
        return False
    raw = row.get("raw_payload")
    if isinstance(raw, dict) and raw.get("event_type") not in (None, "payment.completed"):
        return False
    return True


def _lock_tenant_value():
    """Lock tenant. Flag off stores None (one agency). Flag on stores the tenant."""
    if not multi_tenant_enabled():
        return None
    return current_tenant_id() or ""


async def _ensure_txn_lock_index() -> None:
    """Unique ``(tenant_id, txn_key)`` on ``payment_txn_locks``. Not on ``payments``."""
    global _plan_lock_index_ready
    if _plan_lock_index_ready:
        return
    coll = get_collection(TXN_LOCKS_COLLECTION)
    await coll.create_index(
        [("tenant_id", 1), ("txn_key", 1)],
        unique=True,
        name=_TXN_LOCK_INDEX,
    )
    _plan_lock_index_ready = True


async def _acquire_txn_lock(txn: str, session) -> None:
    """Upsert the shared lock and bump ``version`` before any sibling read.

    Same document #187's ``acquire_txn_lock`` writes, so a provider charge
    and this plan create serialize on one transaction id.
    """
    coll = get_collection(TXN_LOCKS_COLLECTION)
    key = {"tenant_id": _lock_tenant_value(), "txn_key": txn}
    await coll.update_one(
        key,
        {"$inc": {"version": 1}, "$setOnInsert": dict(key)},
        upsert=True,
        **_sess(session),
    )


def _refuse_down_payment(rows: list, booking: str, amount, txn: str):
    """``(error, message)`` when these rows must not become this down payment.

    ``None`` when there is no row (insert) or exactly one linkable row.
    """
    linked = [row for row in rows if not _plan_id_blank(row)]
    if linked:
        return (
            "transaction_id_used_on_other_plan",
            "This transaction id is already recorded on a payment plan.",
        )
    open_rows = [row for row in rows if _plan_id_blank(row)]
    if len(open_rows) > 1:
        return (
            "logged_payment_ambiguous",
            "More than one logged payment has this transaction id.",
        )
    if not open_rows:
        return None
    row = open_rows[0]
    if not _row_settled(row):
        return (
            "logged_payment_not_settled",
            "The logged payment for this transaction id is not settled.",
        )
    if not _same_amount(row.get("amount"), amount):
        return (
            "transaction_id_amount_mismatch",
            "The logged payment for this transaction id has a different amount.",
        )
    rb = str(row.get("booking_number") or "").strip()
    if not rb:
        return (
            "logged_payment_unowned",
            "The logged payment for this transaction id is not matched to a booking. "
            "Assign it to the booking first.",
        )
    if rb != str(booking).strip():
        return (
            "logged_payment_booking_mismatch",
            "The logged payment for this transaction id is for another booking.",
        )
    return None


class _CreateRefused(Exception):
    """A down-payment create must not proceed. The open transaction aborts."""

    def __init__(self, error: str, message: str):
        super().__init__(error)
        self.error = error
        self.message = message


def _down_payment_audit(actor, plan_id, txn, booking, amount, payment_id, now) -> dict:
    payment_s = str(payment_id)
    return {
        "_id": _scoped_audit_id("plan-create", plan_id, txn),
        "event_type": _DOWN_PAYMENT_AUDIT,
        "entity_type": "payment_plan",
        "entity_id": plan_id,
        "actor": actor,
        "agent_name": actor,
        "timestamp": now,
        "created_at": now,
        "booking_number": booking,
        "transaction_id": txn,
        "payment_id": payment_s,
        "plan_id": plan_id,
        "payment_type": "down_payment",
        "amount": amount,
        "reason": "plan down payment inserted for a provider transaction id "
                  "before a poller or webhook row existed",
        "confidence": 1.0,
        "match_method": "exact_transaction_id",
        "old_state": {"plan_id": None, "status": None, "payment_id": None},
        "new_state": {"plan_id": plan_id, "status": "completed", "payment_id": payment_s},
    }


async def _insert_audit(doc: dict, session=None) -> None:
    from pymongo.errors import DuplicateKeyError

    try:
        await get_collection("audit_events").insert_one(doc, **_sess(session))
    except DuplicateKeyError:
        return


async def _create_plan_with_down_payment_txn(plans_col, plan_doc, txn, amount, method, now, actor):
    """Create a plan whose down payment carries a provider transaction id.

    Same rule as /pay (#179/#184): one application of a txn id per tenant.
    Transaction support is checked first. A standalone server or a failed
    probe returns 503 ``transactions_unavailable`` and writes nothing.

    The claim in ``payment_plan_payment_claims`` is the gate, inserted only
    after that probe. Inside ``ClientSession.with_transaction`` (the driver
    retries transient errors) the first write is the ``payment_txn_locks``
    upsert, then the plan, the payment insert or link, the audit, and the
    claim mark. A new inserted down payment gets a deterministic tenant-scoped
    audit (``plan-create:...``, the #185 id rule). If that audit cannot be
    saved, the transaction aborts and the claim is released.

    A failure after an audit was written runs ``_undo_plan_create``, which
    records a compensating audit. Returns a JSONResponse on refusal, else None.
    """
    from pymongo.errors import DuplicateKeyError

    client = await _transaction_client()
    if client is None:
        return _unavailable()

    payments_col = get_collection("payments")
    plan_id = plan_doc["plan_id"]
    booking = plan_doc["booking_number"]
    try:
        claims = await _payment_claims_collection()
    except Exception:
        return JSONResponse({"error": "payment_dedupe_unavailable"}, status_code=503)
    try:
        await _ensure_txn_lock_index()
    except Exception:
        logger.warning("[payment_plans] txn lock index unavailable")
        return _unavailable()

    if await claims.find_one({"transaction_id": txn}) is not None:
        return _conflict("transaction_id_used_on_other_plan", txn,
                         "This transaction id is already recorded on a payment plan.")
    if await payments_col.find_one(
            {"transaction_id": txn, "plan_id": {"$exists": True, "$nin": [None, ""]}}):
        return _conflict("transaction_id_used_on_other_plan", txn,
                         "This transaction id is already recorded on a payment plan.")

    rows = [r async for r in _iter_find(payments_col, {"transaction_id": txn, **_UNLINKED})]
    refusal = _refuse_down_payment(rows, booking, amount, txn)
    if refusal is not None:
        return _conflict(refusal[0], txn, refusal[1])

    try:
        await claims.insert_one({
            "plan_id": plan_id, "transaction_id": txn, "booking_number": booking,
            "amount": amount, "kind": "down_payment", "status": "claimed",
            "claimed_at": now.isoformat(),
        })
    except DuplicateKeyError:
        return _conflict("transaction_id_used_on_other_plan", txn,
                         "This transaction id is already recorded on a payment plan.")
    except Exception:
        return JSONResponse({"error": "payment_dedupe_unavailable"}, status_code=503)

    from bson import ObjectId

    state = {
        "plan_inserted": False,
        "link_set": None,
        "inserted_id": None,
        "row": None,
        "payment_id": None,
        "audit_written": False,
    }

    async def work(session):
        state["plan_inserted"] = False
        state["link_set"] = None
        state["inserted_id"] = None
        state["row"] = None
        state["payment_id"] = None
        state["audit_written"] = False
        await _acquire_txn_lock(txn, session)
        fresh_rows = await _docs(payments_col, {"transaction_id": txn}, session)
        code = _refuse_down_payment(fresh_rows, booking, amount, txn)
        if code is not None:
            raise _CreateRefused(code[0], code[1])
        open_rows = [row for row in fresh_rows if _plan_id_blank(row)]
        row = open_rows[0] if open_rows else None
        state["row"] = row
        await plans_col.insert_one(dict(plan_doc), **_sess(session))
        state["plan_inserted"] = True
        if row is not None:
            pin = {"_id": row["_id"], "transaction_id": txn, "amount": row.get("amount"),
                   "status": row.get("status"), "booking_number": row.get("booking_number"),
                   **_UNLINKED}
            raw = row.get("raw_payload")
            if isinstance(raw, dict) and "event_type" in raw:
                pin["raw_payload.event_type"] = raw["event_type"]
            else:
                pin["raw_payload.event_type"] = {"$exists": False}
            candidate = {
                "plan_id": plan_id, "booking_number": booking, "type": "down_payment",
                "logged_status": row.get("status"), "status": "completed",
                # Provider payment time, not the link time (revenue windows).
                "timestamp": row.get("timestamp") or row.get("created_at") or now.isoformat(),
                "plan_linked_at": now.isoformat(),
            }
            res = await payments_col.update_one(pin, {"$set": candidate}, **_sess(session))
            if not getattr(res, "matched_count", 0):
                raise _LinkLost()
            state["link_set"] = candidate
            state["payment_id"] = row["_id"]
            own_id = row["_id"]
        else:
            own_id = ObjectId()
            state["inserted_id"] = own_id
            state["payment_id"] = own_id
            await payments_col.insert_one({
                "_id": own_id,
                "booking_number": booking, "plan_id": plan_id, "amount": amount,
                "method": method, "type": "down_payment", "transaction_id": txn,
                "status": "completed", "timestamp": now.isoformat(),
                "notes": "Initial down payment",
            }, **_sess(session))
            await _insert_audit(
                _down_payment_audit(actor, plan_id, txn, booking, amount, own_id, now),
                session,
            )
            state["audit_written"] = True
        siblings = [
            item for item in await _docs(payments_col, {"transaction_id": txn}, session)
            if item.get("_id") != own_id
        ]
        if siblings:
            raise _Ambiguous()
        if state["link_set"] is not None:
            await _insert_audit({
                "event_type": "payment_plan_logged_payment_linked",
                "entity_type": "payment_plan", "entity_id": plan_id,
                "actor": actor,
                "agent_name": actor,
                "timestamp": now, "created_at": now,
                "booking_number": booking, "transaction_id": txn,
                "payment_id": str(row.get("_id")), "payment_type": "down_payment",
                "plan_id_before": None, "plan_id_after": plan_id,
                "status_before": row.get("status"), "status_after": "completed",
                "amount": amount,
                "reason": "plan down payment linked to logged card payment: exact transaction id, "
                          "same amount, same booking, settled status, only row for the id",
                "confidence": 1.0, "match_method": "exact_transaction_id",
                "old_state": {"plan_id": None, "status": row.get("status")},
                "new_state": {"plan_id": plan_id, "status": "completed"},
            }, session)
            state["audit_written"] = True
        await claims.update_one(
            {"plan_id": plan_id, "transaction_id": txn},
            {"$set": {"status": "applied", "applied_at": now.isoformat()}},
            **_sess(session),
        )

    try:
        await _run_apply(client, work)
    except Exception as exc:
        outcome = await _undo_plan_create(
            plans_col, payments_col, claims, plan_id, txn,
            state["row"], state["link_set"], state["inserted_id"],
            state["plan_inserted"], actor=actor, compensate=state["audit_written"],
            amount=amount, booking=booking, payment_id=state["payment_id"],
        )
        if outcome != "undone":
            if outcome == "retained":
                message = (
                    "The payment stayed linked to the plan, but the plan could not be finished. "
                    "Review it before recording this payment again."
                )
            else:
                message = (
                    "The plan could not be finished and the rollback did not complete. "
                    "Review it before recording this payment again."
                )
            return _needs_review_response(txn, plan_id, message)
        if isinstance(exc, (_LinkLost, _CreateRefused)):
            error = exc.error if isinstance(exc, _CreateRefused) else "logged_payment_changed"
            message = (
                exc.message if isinstance(exc, _CreateRefused)
                else "The logged payment changed while the plan was being created. Try again."
            )
            return _conflict(error, txn, message)
        if isinstance(exc, _Ambiguous):
            return _conflict(
                "logged_payment_ambiguous", txn,
                "Another logged payment with this transaction id appeared while the plan was being created. Review before trying again.",
            )
        if isinstance(exc, TransactionsUnavailable):
            return _unavailable()
        return _conflict("payment_record_failed", txn,
                         "The plan could not be created. Nothing was recorded.", 500)
    return None


class _Ambiguous(Exception):
    pass


class _LinkLost(Exception):
    pass


async def _iter_find(coll, flt):
    cursor = coll.find(flt)
    if hasattr(cursor, "__aiter__"):
        async for d in cursor:
            yield d
    else:
        for d in await cursor.to_list(length=None):
            yield d


async def _delete_if_present(coll, flt, identity) -> bool:
    """True when no document with ``identity`` remains.

    ``delete_one`` matching nothing is success only when that document is
    already gone. A row that is still there was not cleared.
    """
    res = await coll.delete_one(flt)
    if getattr(res, "deleted_count", 0):
        return True
    return await coll.find_one(identity) is None


async def _write_create_rollback_audit(actor, plan_id, txn, booking, amount, payment_id,
                                      linked: bool, outcome: str) -> bool:
    """Compensating audit for a plan-create that did not finish. False if it cannot be saved."""
    from pymongo.errors import DuplicateKeyError

    now = datetime.now(timezone.utc)
    payment_s = None if payment_id is None else str(payment_id)
    doc = {
        "_id": _scoped_audit_id("plan-create-rollback", plan_id, txn),
        "event_type": _CREATE_ROLLBACK_AUDIT,
        "entity_type": "payment_plan",
        "entity_id": plan_id,
        "actor": actor,
        "agent_name": actor,
        "timestamp": now,
        "created_at": now,
        "booking_number": booking,
        "transaction_id": txn,
        "payment_id": payment_s,
        "plan_id": plan_id,
        "amount": amount,
        "linked_existing_row": linked,
        "reason": "plan create finalization failed; compensating rollback",
        "confidence": 1.0,
        "old_state": {
            "plan_id": plan_id,
            "payment_id": payment_s,
            "linked_existing_row": linked,
            "claim_status": "claimed",
            "status": "completed",
        },
        "new_state": {
            "outcome": outcome,
            "plan_id": None if outcome == "undone" else plan_id,
            "plan_status": None if outcome == "undone" else "needs_review",
            "claim_status": "deleted" if outcome == "undone" else "failed",
        },
    }
    try:
        await get_collection("audit_events").insert_one(doc)
        return True
    except DuplicateKeyError:
        return True
    except Exception as exc:
        logger.error("[payment_plans] compensating audit failed plan_id=%s err=%s",
                     plan_id, type(exc).__name__)
        return False


def _rollback_outcome(plan_inserted: bool, detached: bool, fully_undone: bool) -> str:
    if plan_inserted and not detached:
        return "retained"
    if fully_undone:
        return "undone"
    return "incomplete"


async def _undo_plan_create(plans_col, payments_col, claims, plan_id, txn, row,
                            link_set, inserted_id, plan_inserted, actor="",
                            compensate=False, amount=None, booking="", payment_id=None):
    """Best-effort rollback in reverse order, then a compensating audit.

    The link revert matches only while every field this request wrote is
    unchanged (a newer write, e.g. the reconciliation service, is kept).
    When that revert matches nothing but the row no longer carries this
    ``plan_id``, the transaction abort already detached it. When the row
    still carries this ``plan_id``, the plan is kept as ``needs_review``.
    A delete that throws, or that matches nothing while the row is still
    there, is an incomplete rollback: any surviving plan is marked
    ``needs_review`` and the claim stays blocked.

    When ``compensate`` is set, an audit was already written for this
    attempt. A compensating ``payment_plan_create_rolled_back`` event is
    appended. If that event cannot be saved, the claim stays ``failed``
    and the outcome is ``incomplete``.

    The claim is deleted (txn free again) only when everything was undone
    and the compensating audit, when one was required, was saved.

    Returns ``"undone"``, ``"retained"`` (plan kept because the payment could
    not be detached), or ``"incomplete"``.
    """
    ok = True
    detached = link_set is None
    payment_cleared = inserted_id is None
    plan_cleared = not plan_inserted
    try:
        if link_set is not None and row is not None:
            restore = {k: row[k] for k in link_set if k in row}
            unset = {k: "" for k in link_set if k not in row}
            upd = {"$unset": unset} if unset else {}
            if restore:
                upd["$set"] = restore
            res = await payments_col.update_one({"_id": row["_id"], **link_set}, upd)
            detached = bool(getattr(res, "matched_count", 0))
            if not detached:
                fresh = await payments_col.find_one({"_id": row["_id"]})
                still_ours = fresh is not None and str(fresh.get("plan_id") or "") == str(plan_id)
                if not still_ours:
                    # Transaction abort (or a prior revert) already dropped the link.
                    detached = True
                else:
                    ok = False
                    logger.error("[payment_plans] down-payment link revert matched nothing plan_id=%s", plan_id)
        if ok and detached and inserted_id is not None:
            payment_cleared = await _delete_if_present(
                payments_col, {"_id": inserted_id, "plan_id": plan_id}, {"_id": inserted_id})
            if not payment_cleared:
                ok = False
                logger.error("[payment_plans] down-payment row delete did not clear plan_id=%s", plan_id)
        if ok and detached and plan_inserted:
            plan_cleared = await _delete_if_present(
                plans_col, {"plan_id": plan_id}, {"plan_id": plan_id})
            if not plan_cleared:
                ok = False
                logger.error("[payment_plans] plan delete did not clear plan_id=%s", plan_id)
    except Exception as exc:
        ok = False
        logger.error("[payment_plans] plan create rollback failed plan_id=%s err=%s", plan_id, type(exc).__name__)
    if plan_inserted and not plan_cleared:
        try:
            await plans_col.update_one(
                {"plan_id": plan_id},
                {"$set": {"status": "needs_review",
                          "review_reason": "plan_create_rollback_incomplete"}},
            )
        except Exception as exc:
            ok = False
            logger.error("[payment_plans] could not mark plan needs_review plan_id=%s err=%s",
                         plan_id, type(exc).__name__)
    fully_undone = ok and detached and payment_cleared and plan_cleared
    outcome = _rollback_outcome(plan_inserted, detached, fully_undone)
    if compensate:
        saved = await _write_create_rollback_audit(
            actor, plan_id, txn, booking, amount, payment_id,
            linked=link_set is not None, outcome=outcome,
        )
        if not saved:
            fully_undone = False
            outcome = "incomplete"
    try:
        if fully_undone:
            await claims.delete_one({"plan_id": plan_id, "transaction_id": txn})
        else:
            await claims.update_one({"plan_id": plan_id, "transaction_id": txn},
                                    {"$set": {"status": "failed",
                                              "failed_reason": "plan_create_rollback_incomplete",
                                              "failed_at": datetime.now(timezone.utc).isoformat()}})
    except Exception:
        fully_undone = False
        outcome = "incomplete"
        try:
            await claims.update_one({"plan_id": plan_id, "transaction_id": txn},
                                    {"$set": {"status": "failed",
                                              "failed_reason": "plan_create_rollback_incomplete",
                                              "failed_at": datetime.now(timezone.utc).isoformat()}})
        except Exception:
            pass
    if outcome == "retained":
        return "retained"
    if fully_undone and outcome == "undone":
        return "undone"
    return "incomplete"


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
# no step resume. The first write inside the transaction is the shared
# ``payment_txn_locks`` upsert. An applied-claim retry reads that claim and
# then the refund or failure siblings inside the same locked transaction.
# Any such sibling is 409 ``payment_needs_review`` and does not change the
# balance. Refunds of a plan payment go through ``reverse_plan_payment``
# (balance restored once). Declines are logged and do not move the balance.
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


def _reversal_sibling(rows: list) -> bool:
    from dashboard.services.payment_log_claims import has_reversal_sibling
    return has_reversal_sibling(rows)


def _reversed_refusal(txn: str):
    return _refuse(
        "payment_needs_review",
        txn,
        "This transaction id was refunded or failed and needs review. "
        "The plan balance was not changed.",
    )


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
    if _reversal_sibling(rows):
        return "error", _reversed_refusal(txn)
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


def _scoped_audit_id(kind: str, plan_id: str, txn: str) -> str:
    """Deterministic ``audit_events`` ``_id``. Same tenant rule as #185.

    Flag off is ``{kind}:{plan_id}:{txn}``. Flag on is
    ``{kind}:{tenant_id}:{plan_id}:{txn}`` so two agencies with the same plan
    id and transaction id do not collide on the global ``_id`` index. No
    index is added on ``payments``.
    """
    if not multi_tenant_enabled():
        return f"{kind}:{plan_id}:{txn}"
    return f"{kind}:{current_tenant_id() or ''}:{plan_id}:{txn}"


def _audit_id(plan_id: str, txn: str) -> str:
    """Plan ``/pay`` audit id (``plan-pay:...``)."""
    return _scoped_audit_id("plan-pay", plan_id, txn)


async def _audit(
    audit_col, actor: str, plan, plan_id: str, txn: str, amount, linked: bool,
    before: dict, after: dict, session=None,
) -> None:
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
    """Lock, re-read refund and failure siblings, then write. A sibling refuses before any balance change."""
    from dashboard.services.payment_log_claims import LOCKS_COLLECTION, acquire_txn_lock

    await acquire_txn_lock(get_collection(LOCKS_COLLECTION), txn, session)
    if _reversal_sibling(await _docs(payments_col, {"transaction_id": txn}, session)):
        raise PlanPaymentRefused(
            "payment_needs_review",
            "This transaction id was refunded or failed and needs review. "
            "The plan balance was not changed.",
        )
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


async def _locked_prior_decision(client, claims, payments_col, txn):
    """Lock, read the claim, then re-read reversal siblings before deciding.

    The idempotent retry of an applied claim stays inside this transaction.
    A refund that is visible before the decision returns 409. The balance is
    not changed. A refusal aborts, so the lock bump is not committed.
    """
    from dashboard.services.payment_log_claims import LOCKS_COLLECTION, acquire_txn_lock

    found = {}

    async def work(session):
        await acquire_txn_lock(get_collection(LOCKS_COLLECTION), txn, session)
        claim = await claims.find_one({"transaction_id": txn}, **_sess(session))
        rows = await _docs(payments_col, {"transaction_id": txn}, session)
        if _reversal_sibling(rows):
            raise PlanPaymentRefused(
                "payment_needs_review",
                "This transaction id was refunded or failed and needs review. "
                "The plan balance was not changed.",
            )
        found["claim"] = claim

    await _run_apply(client, work)
    return found.get("claim")


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

    try:
        from dashboard.services.payment_log_claims import LOCKS_COLLECTION, ensure_txn_lock_index
        await ensure_txn_lock_index(get_collection(LOCKS_COLLECTION))
    except Exception:
        logger.warning("[payment_plans] txn lock index unavailable")
        return _unavailable()

    # The claim lookup and the reversal read share one lock. A sibling that
    # lands before this decision is 409 and does not change the balance or
    # the claim, including an idempotent retry of an applied claim.
    try:
        existing_claim = await _locked_prior_decision(client, claims, payments_col, txn)
    except TransactionsUnavailable:
        return _unavailable()
    except PlanPaymentRefused as exc:
        return _refuse(exc.error, txn, exc.message)

    # A finished claim decides. Anything still claimed is not resumed and is not success.
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
        try:
            existing = await _locked_prior_decision(client, claims, payments_col, txn)
        except TransactionsUnavailable:
            return _unavailable()
        except PlanPaymentRefused as exc:
            return _refuse(exc.error, txn, exc.message)
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