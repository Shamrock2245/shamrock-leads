
"""
ShamrockLeads — Payment Plans API
The Payment Agent: Track premiums, payment plans, delinquency, and revenue.

Endpoints:
  GET  /payments/plans                 — List all active payment plans
  GET  /payments/plans/<booking>       — Get plan for a specific bond
  POST /payments/plans                 — Create a new payment plan
  POST /payments/plans/<plan_id>/pay   — Record a payment against a plan (transaction_id required; deduped)
  GET  /payments/delinquent            — Get all delinquent plans (>30 days)
  GET  /payments/summary               — Revenue summary (daily/weekly/monthly)
  GET  /payments/premium-calc          — Quick premium calculator
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from datetime import datetime, timezone, timedelta
import uuid

from dashboard.extensions import get_collection
import logging

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


def _same_amount(a, b) -> bool:
    try:
        return round(float(a), 2) == round(float(b), 2)
    except (TypeError, ValueError):
        return False


async def _fail_claim(claims, plan_id: str, txn: str, reason: str) -> None:
    """Mark the claim failed (#179: retries then get payment_needs_review). Never raises."""
    try:
        await claims.update_one(
            {"plan_id": plan_id, "transaction_id": txn},
            {"$set": {"status": "failed", "failed_at": datetime.now(timezone.utc).isoformat(),
                      "failed_reason": reason}},
        )
    except Exception as exc:
        logger.error("[payment_plans] could not mark claim failed plan_id=%s err=%s", plan_id, type(exc).__name__)


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


_SETTLED_STATUSES = frozenset({"approved", "paid", "completed", "success"})


def _logged_row_settled(row: dict) -> bool:
    """Only a settled card payment may be linked: status approved / paid /
    completed / success. Declined, refunded, voided, pending, blank, or a
    webhook row whose event is not ``payment.completed`` fail closed."""
    status = str(row.get("status") or "").strip().lower()
    if status not in _SETTLED_STATUSES:
        return False
    raw = row.get("raw_payload")
    if isinstance(raw, dict) and raw.get("event_type") is not None:
        if str(raw.get("event_type")).strip().lower() != "payment.completed":
            return False
    return True


def _actor(request: Request) -> str:
    """Same rule as the AR router: session agent/email, else X-Actor, else 'staff'."""
    try:
        from dashboard.auth.pin_middleware import get_session_from_request

        sess = get_session_from_request(request) or {}
    except Exception:
        sess = {}
    name = str(sess.get("agent_name") or sess.get("email") or "").strip()
    if name:
        return name
    return str(request.headers.get("x-actor") or "").strip() or "staff"


async def _audit_link(actor: str, plan: dict, plan_id: str, txn: str, row: dict, amount: float) -> None:
    """Immutable audit_events record for the plan_id association (ar_service._audit pattern)."""
    now = datetime.now(timezone.utc)
    try:
        await get_collection("audit_events").insert_one({
            "event_type": "payment_plan_logged_payment_linked",
            "entity_type": "payment_plan",
            "entity_id": plan_id,
            "actor": actor,
            "agent_name": actor,
            "timestamp": now,
            "created_at": now,
            "booking_number": plan.get("booking_number"),
            "transaction_id": txn,
            "payment_id": str(row.get("_id")),
            "payment_source": row.get("source") or ("webhook" if isinstance(row.get("raw_payload"), dict) else ""),
            "plan_id_before": row.get("plan_id") or None,
            "plan_id_after": plan_id,
            "status_before": row.get("status"),
            "status_after": "completed",
            "amount": amount,
        })
    except Exception as exc:
        logger.warning("[payment_plans] audit write failed plan_id=%s err=%s", plan_id, type(exc).__name__)


@payment_plans_bp.post("/payments/plans/{plan_id}/pay")
async def record_payment(request: Request, plan_id):
    """Record a payment against an existing plan — at most once per transaction id.

    ``transaction_id`` is required (400 ``transaction_id_required``). The same
    transaction id on the same plan returns ``idempotent: true`` with no second
    payment row and no balance change, including under concurrent requests.
    """
    from pymongo.errors import DuplicateKeyError

    from dashboard.routers.events import publish_event
    try:
        data = await request.json()
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
    try:
        amount = float(data["amount"])
    except (TypeError, ValueError):
        return JSONResponse({"error": "invalid_amount"}, status_code=400)
    if not amount > 0 or amount == float("inf"):
        return JSONResponse({"error": "invalid_amount"}, status_code=400)

    plans_col = get_collection("payment_plans")
    payments_col = get_collection("payments")

    plan = await plans_col.find_one({"plan_id": plan_id})
    if not plan:
        return JSONResponse({"error": "Plan not found"}, status_code=404)

    try:
        claims = await _payment_claims_collection()
    except Exception:
        return JSONResponse({"error": "payment_dedupe_unavailable"}, status_code=503)

    # A claim, when one exists, decides: a payment row left behind by a
    # 'failed' or unfinished 'claimed' attempt is NOT proof the plan was updated.
    existing_claim = await claims.find_one({"transaction_id": txn})
    if existing_claim is not None:
        return await _idempotent_reply(plans_col, plan_id, txn, existing_claim, amount)

    # Plan payments recorded before this dedupe existed have no claim: treat
    # them as applied (on any plan). Rows without a plan_id (e.g. the receipt
    # poller's log of a card payment) are not plan applications and don't block.
    legacy = await payments_col.find_one(
        {"transaction_id": txn, "plan_id": {"$exists": True, "$nin": [None, ""]}}
    )
    if legacy:
        return await _idempotent_reply(plans_col, plan_id, txn, None, amount, legacy_row=legacy)

    # Link, don't duplicate: a card payment the receipt poller (or payment
    # webhook) already logged is a payments row with this transaction_id and
    # no plan_id. Attach plan_id to THAT row instead of inserting a second one.
    unlinked_filter = {
        "transaction_id": txn,
        "$or": [{"plan_id": {"$exists": False}}, {"plan_id": None}, {"plan_id": ""}],
    }
    unlinked_count = await payments_col.count_documents(unlinked_filter)
    if unlinked_count > 1:
        return JSONResponse(
            {"error": "logged_payment_ambiguous", "transaction_id": txn,
             "message": "More than one logged payment has this transaction id. Review before applying it to a plan."},
            status_code=409,
        )
    logged_row = await payments_col.find_one(unlinked_filter) if unlinked_count == 1 else None
    if logged_row is not None:
        if not _same_amount(logged_row.get("amount"), amount):
            return JSONResponse(
                {"error": "logged_payment_amount_mismatch", "transaction_id": txn,
                 "message": "The logged card payment with this transaction id has a different amount."},
                status_code=409,
            )
        if not _logged_row_settled(logged_row):
            return JSONResponse(
                {"error": "logged_payment_not_settled", "transaction_id": txn,
                 "message": "The logged card payment with this transaction id is not a settled payment (declined, refunded, voided, pending or no status)."},
                status_code=409,
            )
        row_booking = str(logged_row.get("booking_number") or "").strip()
        if row_booking and row_booking != str(plan.get("booking_number") or "").strip():
            return JSONResponse(
                {"error": "logged_payment_booking_mismatch", "transaction_id": txn,
                 "message": "The logged card payment with this transaction id belongs to another booking."},
                status_code=409,
            )

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
        return await _idempotent_reply(
            plans_col, plan_id, txn, existing or {"status": "unknown"}, amount)

    if logged_row is not None:
        # Conditional link: matches only while the row is still unlinked and
        # unchanged. Losing it means someone linked it meanwhile; never insert.
        link_filter = {
            "_id": logged_row["_id"],
            "transaction_id": txn,
            "amount": logged_row.get("amount"),
            "$or": [{"plan_id": {"$exists": False}}, {"plan_id": None}, {"plan_id": ""}],
        }
        # Revenue reports count rows with status completed/paid/success by
        # ``timestamp`` (the shape a plan installment row had). Give the linked
        # row that shape so the installment is still counted exactly once;
        # the poller's original status is kept in ``logged_status``.
        link_set = {
            "plan_id": plan_id,
            "plan_linked_at": now.isoformat(),
            "plan_linked_type": "installment",
            "type": "installment",
            "status": "completed",
            "logged_status": logged_row.get("status"),
        }
        if not logged_row.get("timestamp"):
            link_set["timestamp"] = now.isoformat()
        try:
            linked = await payments_col.update_one(link_filter, {"$set": link_set})
            link_ok = bool(getattr(linked, "modified_count", 0))
            current = None if link_ok else (await payments_col.find_one({"_id": logged_row["_id"]}) or {})
        except Exception as exc:
            # Write outcome unknown: fail the claim (retries -> payment_needs_review),
            # balance untouched, clean error.
            logger.warning("[payment_plans] link write failed plan_id=%s err=%s", plan_id, type(exc).__name__)
            await _fail_claim(claims, plan_id, txn, "logged_payment_link_error")
            return JSONResponse(
                {"error": "payment_record_failed", "transaction_id": txn,
                 "message": "Linking the logged card payment failed. Check the plan before recording it again."},
                status_code=500,
            )
        if link_ok:
            await _audit_link(_actor(request), plan, plan_id, txn, logged_row, amount)
        else:
            await _fail_claim(claims, plan_id, txn, "logged_payment_link_lost")
            other = current.get("plan_id")
            if other and other != plan_id:
                return JSONResponse(
                    {"error": "transaction_id_used_on_other_plan", "transaction_id": txn},
                    status_code=409,
                )
            return JSONResponse(
                {"error": "payment_needs_review", "transaction_id": txn,
                 "message": "The logged card payment changed while it was being linked. Check the plan before recording it again."},
                status_code=409,
            )

    try:
        if logged_row is None:
            await payments_col.insert_one({
                "booking_number": plan["booking_number"],
                "plan_id": plan_id,
                "amount": amount,
                "method": data.get("method", "Cash"),
                "type": "installment",
                "status": "completed",
                "timestamp": now.isoformat(),
                "transaction_id": txn,
                "notes": data.get("notes", ""),
            })
        # Atomic increments so two different transactions never overwrite each other.
        await plans_col.update_one(
            {"plan_id": plan_id},
            {
                "$inc": {
                    "balance_remaining": -amount,
                    "total_paid": amount,
                    "payments_made": 1,
                },
                "$set": {
                    "next_due_date": _next_due(plan.get("frequency", "weekly"), now).isoformat(),
                    "delinquent": False,
                    "delinquent_days": 0,
                    "updated_at": now.isoformat(),
                },
            },
        )
        await plans_col.update_one(
            {"plan_id": plan_id, "balance_remaining": {"$lte": 0}},
            {"$set": {"balance_remaining": 0, "status": "paid_in_full"}},
        )
        updated = await plans_col.find_one({"plan_id": plan_id}) or {}
    except Exception:
        await _fail_claim(claims, plan_id, txn, "payment_write_error")
        return JSONResponse({"error": "payment_record_failed", "transaction_id": txn}, status_code=500)

    new_balance = updated.get("balance_remaining", 0)
    status = updated.get("status", "active")
    await claims.update_one(
        {"plan_id": plan_id, "transaction_id": txn},
        {"$set": {"status": "applied", "applied_at": now.isoformat(), "balance_after": new_balance}},
    )

    await publish_event("payment_received", {
        "plan_id": plan_id,
        "amount": amount,
        "balance_remaining": new_balance,
        "status": status,
    })

    return {
        "success": True,
        "idempotent": False,
        "linked_logged_payment": logged_row is not None,
        "transaction_id": txn,
        "balance_remaining": new_balance,
        "total_paid": updated.get("total_paid"),
        "status": status,
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