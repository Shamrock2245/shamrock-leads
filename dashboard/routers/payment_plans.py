
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
import json as _json
from decimal import Decimal

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

    raw_county = str(data.get("county") or data.get("County") or "").strip()
    raw_state = str(data.get("state") or data.get("State") or "").strip()
    # Optional. A logged-receipt link refuses the plan until both are set,
    # because booking number alone is not unique across counties and states.
    # ``Lee (FL)`` is stored as county Lee and state FL, the same shape the
    # receipt poller copies off a bond. A label whose state disagrees with the
    # state field is rejected.
    stored_place: dict = {}
    if raw_county or raw_state:
        from dashboard.services.swipesimple_receipt_poller import jurisdiction_from_bond

        parsed_place = jurisdiction_from_bond({
            "county": raw_county,
            "state": raw_state,
            "booking_number": data["booking_number"],
        })
        if parsed_place:
            stored_place = {"county": parsed_place["county"], "state": parsed_place["state"]}
        elif raw_county and raw_state:
            return JSONResponse(
                {"error": "invalid_jurisdiction",
                 "message": "County and state disagree. Send a county and a state, or a County (ST) label whose state matches."},
                status_code=400,
            )
        else:
            if raw_county:
                stored_place["county"] = raw_county
            if raw_state:
                stored_place["state"] = raw_state

    plan_doc = {
        "plan_id": str(uuid.uuid4())[:12],
        "booking_number": data["booking_number"],
        **stored_place,
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


def _booking_token(value) -> str:
    return str(value or "").strip()


def _place_token(value) -> str:
    """County and state compare case-insensitively. No abbreviation guessing."""
    return str(value or "").strip().casefold()


def _identity_sources(doc: dict, *, nested: bool) -> list[dict]:
    """Where a logged receipt may carry booking, county, and state.

    The payment webhook keeps county (and any state the link sent) under
    ``custom_fields``, and stores that same object on ``raw_payload``. A plan
    carries identity only on its own document.
    """
    sources = [doc]
    if not nested:
        return sources
    cf = doc.get("custom_fields")
    if isinstance(cf, dict):
        sources.append(cf)
    raw = doc.get("raw_payload")
    if isinstance(raw, dict) and isinstance(raw.get("custom_fields"), dict):
        sources.append(raw["custom_fields"])
    return sources


def _agreed_token(sources: list[dict], keys: tuple[str, ...], normalize) -> str | None:
    """The one non-blank value every source shares, or None if missing or split."""
    found: list[str] = []
    for src in sources:
        for key in keys:
            if key not in src:
                continue
            token = normalize(src.get(key))
            if token:
                found.append(token)
    if not found or len(set(found)) != 1:
        return None
    return found[0]


def _place_pair(src: dict) -> tuple[str, str] | None:
    """Casefolded ``(county, state)`` from one document, peeling ``Name (ST)``.

    Returns None when this source names a place that is incomplete or whose
    label state disagrees with its state field. Returns an empty tuple when
    the source does not carry a place at all.
    """
    raw_county = ""
    raw_state = ""
    for key in ("county", "County"):
        if key in src and str(src.get(key) or "").strip():
            raw_county = str(src.get(key)).strip()
            break
    for key in ("state", "State"):
        if key in src and str(src.get(key) or "").strip():
            raw_state = str(src.get(key)).strip()
            break
    if not raw_county and not raw_state:
        return ("", "")
    from dashboard.services.swipesimple_receipt_poller import jurisdiction_from_bond

    parsed = jurisdiction_from_bond({"county": raw_county, "state": raw_state})
    if not parsed:
        return None
    return parsed["county"].casefold(), parsed["state"].casefold()


def _booking_identity(doc: dict, *, nested: bool) -> tuple[str, str, str] | None:
    """``(booking_number, county, state)`` or None when it cannot be verified.

    Booking numbers stay case-sensitive. County and state must both be present
    and must not disagree across the receipt's stored copies. A ``Lee (FL)``
    label is the same county as ``Lee`` when the state is FL. Missing or
    conflicting jurisdiction is unverified — never treated as a match.
    """
    if not isinstance(doc, dict):
        return None
    sources = _identity_sources(doc, nested=nested)
    booking = _agreed_token(sources, ("booking_number",), _booking_token)
    places: list[tuple[str, str]] = []
    for src in sources:
        pair = _place_pair(src)
        if pair is None:
            return None
        if pair != ("", ""):
            places.append(pair)
    if not booking or not places or len(set(places)) != 1:
        return None
    county, state = places[0]
    return booking, county, state


def _text_or_none(value) -> str | None:
    text = str(value or "").strip()
    return text or None


async def _audit_jurisdiction_backfill(
    actor: str, plan_id: str, booking: str,
    before: dict, after: dict,
) -> bool:
    """Immutable record of a plan's county/state backfill. False when it was not saved."""
    now = datetime.now(timezone.utc)
    try:
        await get_collection("audit_events").insert_one({
            "event_type": "payment_plan_jurisdiction_backfilled",
            "entity_type": "payment_plan",
            "entity_id": plan_id,
            "actor": actor,
            "agent_name": actor,
            "timestamp": now,
            "created_at": now,
            "booking_number": booking,
            "county_before": before.get("county"),
            "state_before": before.get("state"),
            "county_after": after.get("county"),
            "state_after": after.get("state"),
            "reason": "legacy plan missing county or state; copied from the one bond "
                      "whose booking number, county, and state all agree",
            "confidence": 1.0,
            "match_method": "unique_bond_booking_identity",
        })
    except Exception as exc:
        logger.warning(
            "[payment_plans] jurisdiction audit failed plan_id=%s err=%s",
            plan_id, type(exc).__name__,
        )
        return False
    return True


async def _revert_jurisdiction(plans_col, plan_id: str, to_set: dict) -> bool:
    """Undo a backfill this request wrote, only while those values are unchanged."""
    try:
        res = await plans_col.update_one(
            {"plan_id": plan_id, **to_set},
            {"$unset": {key: "" for key in to_set}},
        )
        return bool(getattr(res, "modified_count", 0))
    except Exception as exc:
        logger.error(
            "[payment_plans] jurisdiction revert failed plan_id=%s err=%s",
            plan_id, type(exc).__name__,
        )
        return False


def _backfill_error(txn: str, error: str) -> JSONResponse:
    if error == "jurisdiction_backfill_unaudited":
        message = ("The plan's county and state were filled from the bond but the audit "
                   "record could not be saved and the change could not be undone. "
                   "Check the plan before applying this payment.")
    else:
        message = ("The plan's county and state were not kept because the audit record "
                   "could not be saved. Try again shortly.")
    return JSONResponse(
        {"error": error, "transaction_id": txn, "message": message},
        status_code=503,
    )


async def _plan_with_bond_jurisdiction(plans_col, plan: dict, actor: str, txn: str):
    """Fill a plan's missing county and state from the one complete bond identity.

    Returns ``(plan, None)`` or ``(plan, error_response)``. Every bond with this
    booking number must supply the same booking number, county, and state. An
    incomplete bond is ambiguous: nothing is written and nothing is linked.
    A backfill that does land is audited (old and new values, actor, reason,
    confidence) before the caller may link. If that audit cannot be saved, the
    backfill is reverted. If the revert also fails, the plan is marked so a
    later request still will not link until the audit is saved.
    """
    plan_id = plan.get("plan_id")
    if plan.get("jurisdiction_backfill_unaudited"):
        previous = plan.get("jurisdiction_backfill_previous")
        if not isinstance(previous, dict):
            previous = {}
        after = {
            "county": _text_or_none(plan.get("county") or plan.get("County")),
            "state": _text_or_none(plan.get("state") or plan.get("State")),
        }
        if not await _audit_jurisdiction_backfill(
            actor, plan_id, _booking_token(plan.get("booking_number")), previous, after,
        ):
            return plan, _backfill_error(txn, "jurisdiction_backfill_unaudited")
        try:
            await plans_col.update_one(
                {"plan_id": plan_id, "jurisdiction_backfill_unaudited": True},
                {"$unset": {
                    "jurisdiction_backfill_unaudited": "",
                    "jurisdiction_backfill_previous": "",
                }},
            )
        except Exception as exc:
            logger.error(
                "[payment_plans] could not clear unaudited jurisdiction flag plan_id=%s err=%s",
                plan_id, type(exc).__name__,
            )
            return plan, _backfill_error(txn, "jurisdiction_backfill_unaudited")
        plan = {
            k: v for k, v in plan.items()
            if k not in ("jurisdiction_backfill_unaudited", "jurisdiction_backfill_previous")
        }

    if _booking_identity(plan, nested=False):
        return plan, None
    booking = _booking_token(plan.get("booking_number"))
    if not booking:
        return plan, None
    from dashboard.services.swipesimple_receipt_poller import (
        _docs_for_query,
        agreed_jurisdiction,
    )

    bonds = []
    for name in ("bond_cases", "active_bonds", "intake_queue"):
        found = await _docs_for_query(get_collection(name), {"booking_number": booking})
        if found is None:
            return plan, None
        bonds.extend(found)
    jurisdiction = agreed_jurisdiction(bonds)
    if not jurisdiction:
        return plan, None
    if jurisdiction.get("booking_number") and jurisdiction["booking_number"] != booking:
        return plan, None
    before = {
        "county": _text_or_none(plan.get("county") or plan.get("County")),
        "state": _text_or_none(plan.get("state") or plan.get("State")),
    }
    plan_county = _place_token(before["county"])
    plan_state = _place_token(before["state"])
    if plan_county and plan_county != _place_token(jurisdiction["county"]):
        return plan, None
    if plan_state and plan_state != _place_token(jurisdiction["state"]):
        return plan, None
    to_set = {}
    if not plan_county:
        to_set["county"] = jurisdiction["county"]
    if not plan_state:
        to_set["state"] = jurisdiction["state"]
    if not to_set:
        return plan, None
    after = {
        "county": to_set.get("county", before["county"]),
        "state": to_set.get("state", before["state"]),
    }
    missing = []
    for key in to_set:
        missing.append({"$or": [{key: {"$exists": False}}, {key: None}, {key: ""}]})
    try:
        updated = await plans_col.update_one(
            {"plan_id": plan_id, "$and": missing},
            {"$set": to_set},
        )
    except Exception as exc:
        logger.warning(
            "[payment_plans] plan jurisdiction write failed plan_id=%s err=%s",
            plan_id, type(exc).__name__,
        )
        return plan, None
    if not getattr(updated, "modified_count", 0):
        try:
            fresh = await plans_col.find_one({"plan_id": plan_id})
        except Exception:
            return plan, None
        return fresh or plan, None
    if await _audit_jurisdiction_backfill(actor, plan_id, booking, before, after):
        return {**plan, **to_set}, None
    if await _revert_jurisdiction(plans_col, plan_id, to_set):
        return plan, _backfill_error(txn, "jurisdiction_backfill_audit_failed")
    try:
        await plans_col.update_one(
            {"plan_id": plan_id, **to_set},
            {"$set": {
                "jurisdiction_backfill_unaudited": True,
                "jurisdiction_backfill_previous": before,
            }},
        )
    except Exception as exc:
        logger.error(
            "[payment_plans] could not flag unaudited jurisdiction plan_id=%s err=%s",
            plan_id, type(exc).__name__,
        )
    return {**plan, **to_set}, _backfill_error(txn, "jurisdiction_backfill_unaudited")


def _identity_pins(row: dict) -> dict:
    """Pin every jurisdiction field the link read, including fields that were absent.

    A county or state added or changed after the check must make the conditional
    update miss.
    """
    pins: dict = {}
    for key in ("county", "County", "state", "State"):
        if key in row:
            pins[key] = row.get(key)
        else:
            pins[key] = {"$exists": False}
    nested = (
        ("custom_fields", row.get("custom_fields") if isinstance(row.get("custom_fields"), dict) else None),
        (
            "raw_payload.custom_fields",
            (row.get("raw_payload") or {}).get("custom_fields")
            if isinstance(row.get("raw_payload"), dict) else None,
        ),
    )
    for prefix, cf in nested:
        for key in ("county", "state", "booking_number"):
            path = f"{prefix}.{key}"
            if isinstance(cf, dict) and key in cf:
                pins[path] = cf.get(key)
            else:
                pins[path] = {"$exists": False}
    return pins


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


async def _audit_link(actor: str, plan: dict, plan_id: str, txn: str, row: dict, amount: float) -> bool:
    """Immutable audit_events record for the plan_id association (ar_service._audit
    shape). Returns False when it could not be written; the caller then undoes the link."""
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
            "reason": "logged card payment linked to plan: exact transaction id, same amount, "
                      "same booking number, county, and state, settled status, only unlinked row for the id",
            "county": plan.get("county") or plan.get("County"),
            "state": plan.get("state") or plan.get("State"),
            "confidence": 1.0,
            "match_method": "exact_transaction_id",
        })
    except Exception as exc:
        logger.warning("[payment_plans] audit write failed plan_id=%s err=%s", plan_id, type(exc).__name__)
        return False
    return True


async def _revert_link(payments_col, row: dict, plan_id: str, link_set: dict) -> bool:
    """Undo a link this request made (only while it is still exactly as we wrote
    it). Returns True when reverted. Never raises."""
    restore_set = {k: row[k] for k in link_set if k in row}
    restore_unset = {k: "" for k in link_set if k not in row}
    update: dict = {}
    if restore_set:
        update["$set"] = restore_set
    if restore_unset:
        update["$unset"] = restore_unset
    try:
        # Pin every value this request wrote: if another writer (e.g. the
        # reconciliation service setting status 'reconciled') changed any of
        # them since, the revert matches nothing and newer state is kept.
        res = await payments_col.update_one({"_id": row["_id"], **link_set}, update)
        if not getattr(res, "modified_count", 0):
            logger.error("[payment_plans] link revert matched nothing plan_id=%s", plan_id)
            return False
    except Exception as exc:
        logger.error("[payment_plans] link revert failed plan_id=%s err=%s", plan_id, type(exc).__name__)
        return False
    return True


async def _audit_unlink(actor: str, plan_id: str, txn: str, row: dict, reason: str) -> None:
    """Best-effort audit of a compensating unlink. Never raises."""
    now = datetime.now(timezone.utc)
    try:
        await get_collection("audit_events").insert_one({
            "event_type": "payment_plan_logged_payment_unlinked",
            "entity_type": "payment_plan",
            "entity_id": plan_id,
            "actor": actor,
            "agent_name": actor,
            "timestamp": now,
            "created_at": now,
            "transaction_id": txn,
            "payment_id": str(row.get("_id")),
            "plan_id_before": plan_id,
            "plan_id_after": None,
            "status_before": "completed",
            "status_after": row.get("status"),
            "reason": reason,
            "confidence": 1.0,
        })
    except Exception as exc:
        logger.error("[payment_plans] unlink audit failed plan_id=%s err=%s", plan_id, type(exc).__name__)


async def _other_rows_for_txn(payments_col, txn: str, own_id) -> bool:
    """Post-write recheck: does any OTHER payments row carry this txn? Raises on DB error."""
    return await payments_col.find_one({"transaction_id": txn, "_id": {"$ne": own_id}}) is not None


def _plan_payment_confirmed(plan) -> bool:
    """True only when balance and status were read and agree.

    A positive balance is still open. A non-positive balance counts only after
    the paid-in-full clamp: exactly 0 and status ``paid_in_full``. Missing
    fields are not confirmed — never substitute 0 or ``active``.
    """
    if not isinstance(plan, dict):
        return False
    if "balance_remaining" not in plan or "status" not in plan:
        return False
    try:
        bal = float(plan["balance_remaining"])
    except (TypeError, ValueError):
        return False
    if bal > 0:
        return True
    return bal == 0.0 and plan["status"] == "paid_in_full"


def _plan_needs_paid_in_full_clamp(plan) -> bool:
    """True when a read plan is at or below zero and not yet clamped."""
    if not isinstance(plan, dict) or "balance_remaining" not in plan:
        return False
    try:
        bal = float(plan["balance_remaining"])
    except (TypeError, ValueError):
        return False
    if bal > 0:
        return False
    return not (bal == 0.0 and plan.get("status") == "paid_in_full")


async def _clamp_paid_in_full(plans_col, plan_id: str) -> None:
    """Set balance to 0 and status paid_in_full when the balance is already <= 0.

    A raised write is logged and left for the caller to reconcile by reading
    the plan. Never raises.
    """
    try:
        await plans_col.update_one(
            {"plan_id": plan_id, "balance_remaining": {"$lte": 0}},
            {"$set": {"balance_remaining": 0, "status": "paid_in_full"}},
        )
    except Exception as exc:
        logger.warning(
            "[payment_plans] paid-in-full clamp failed plan_id=%s err=%s",
            plan_id, type(exc).__name__,
        )


async def _read_plan(plans_col, plan_id: str):
    """Return the plan document, or None when the read failed or matched nothing.

    None is unknown. Callers must not replace it with ``{}``.
    """
    try:
        return await plans_col.find_one({"plan_id": plan_id})
    except Exception as exc:
        logger.warning(
            "[payment_plans] plan readback failed plan_id=%s err=%s",
            plan_id, type(exc).__name__,
        )
        return None


async def _confirm_applied_plan(plans_col, plan_id: str):
    """Clamp a settled plan and read it back after the balance ``$inc``.

    Returns ``(plan, None)`` when balance and status were read and are
    consistent. Returns ``(None, reason)`` when they could not be confirmed
    after one retry. Does not undo the payment: the ``$inc`` already landed,
    and removing the payment row would leave a reduced balance with no evidence.
    """
    saw_unclamped = False
    for _ in range(2):
        await _clamp_paid_in_full(plans_col, plan_id)
        updated = await _read_plan(plans_col, plan_id)
        if _plan_payment_confirmed(updated):
            return updated, None
        # Last observation wins: a later read that fails is a readback failure,
        # even if an earlier read showed an unclamped balance.
        saw_unclamped = _plan_needs_paid_in_full_clamp(updated)
    reason = "paid_in_full_clamp_failed" if saw_unclamped else "plan_readback_failed"
    return None, reason


async def _classify_logged_payment(plans_col, payments_col, plan, txn, amount, actor):
    """Decide whether ``/pay`` links a logged row or inserts one.

    Returns ``(plan, row, error)``. ``error`` is a refusal (no claim yet when
    the caller has not claimed). ``row`` is the single unlinked logged payment
    to link, or None when the caller should insert. A plan jurisdiction
    backfill, when it happens, is audited before this returns a row.
    """
    unlinked_filter = {
        "transaction_id": txn,
        "$or": [{"plan_id": {"$exists": False}}, {"plan_id": None}, {"plan_id": ""}],
    }
    unlinked_count = await payments_col.count_documents(unlinked_filter)
    if unlinked_count > 1:
        return plan, None, JSONResponse(
            {"error": "logged_payment_ambiguous", "transaction_id": txn,
             "message": "More than one logged payment has this transaction id. Review before applying it to a plan."},
            status_code=409,
        )
    logged_row = await payments_col.find_one(unlinked_filter) if unlinked_count == 1 else None
    if logged_row is None:
        return plan, None, None
    if not _same_amount(logged_row.get("amount"), amount):
        return plan, None, JSONResponse(
            {"error": "logged_payment_amount_mismatch", "transaction_id": txn,
             "message": "The logged card payment with this transaction id has a different amount."},
            status_code=409,
        )
    if not _logged_row_settled(logged_row):
        return plan, None, JSONResponse(
            {"error": "logged_payment_not_settled", "transaction_id": txn,
             "message": "The logged card payment with this transaction id is not a settled payment (declined, refunded, voided, pending or no status)."},
            status_code=409,
        )
    if not str(logged_row.get("booking_number") or "").strip():
        # The poller stores "" when it could not match the receipt to a bond.
        return plan, None, JSONResponse(
            {"error": "logged_payment_unowned", "transaction_id": txn,
             "message": "The logged card payment with this transaction id is not matched to a booking. Assign it to the booking before applying it to a plan."},
            status_code=409,
        )
    plan, backfill_error = await _plan_with_bond_jurisdiction(plans_col, plan, actor, txn)
    if backfill_error is not None:
        return plan, None, backfill_error
    row_identity = _booking_identity(logged_row, nested=True)
    plan_identity = _booking_identity(plan, nested=False)
    if row_identity is None or plan_identity is None:
        return plan, None, JSONResponse(
            {"error": "logged_payment_jurisdiction_unverified", "transaction_id": txn,
             "message": "The logged card payment cannot be matched to this plan until both have the same booking number, county, and state. Assign the receipt's county and state before applying it."},
            status_code=409,
        )
    if row_identity != plan_identity:
        return plan, None, JSONResponse(
            {"error": "logged_payment_booking_mismatch", "transaction_id": txn,
             "message": "The logged card payment does not match this plan's booking, county, and state."},
            status_code=409,
        )
    return plan, logged_row, None


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

    # One payments row per transaction id. The poller and the webhook take the
    # same partial unique index before they insert. If it cannot be created,
    # nothing is claimed.
    try:
        from dashboard.services.payment_txn_dedupe import ensure_payment_txn_dedupe

        await ensure_payment_txn_dedupe(payments_col)
    except Exception:
        return JSONResponse({"error": "payment_dedupe_unavailable"}, status_code=503)

    # Link, don't duplicate: a card payment the receipt poller (or payment
    # webhook) already logged is a payments row with this transaction_id and
    # no plan_id. Attach plan_id to THAT row instead of inserting a second one.
    # Booking number is not unique across counties or states. Both sides need
    # booking + county + state. A legacy plan copies those from the one bond
    # whose booking identity is complete and unanimous, and that copy is audited.
    actor = _actor(request)
    plan, logged_row, pre_error = await _classify_logged_payment(
        plans_col, payments_col, plan, txn, amount, actor,
    )
    if pre_error is not None:
        return pre_error

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

    # Re-read after the claim. A row that arrived, vanished, or became
    # ambiguous since the pre-check fails closed. A row that appeared where
    # there was none is linked instead of inserted.
    plan, logged_after, post_error = await _classify_logged_payment(
        plans_col, payments_col, plan, txn, amount, actor,
    )
    if post_error is not None:
        await _fail_claim(claims, plan_id, txn, "logged_payment_recheck_failed")
        return post_error
    if logged_row is None and logged_after is not None:
        logged_row = logged_after
    elif logged_row is not None and (
        logged_after is None or logged_after.get("_id") != logged_row.get("_id")
    ):
        await _fail_claim(claims, plan_id, txn, "logged_payment_ambiguous")
        return JSONResponse(
            {"error": "logged_payment_ambiguous", "transaction_id": txn,
             "message": "The logged card payment changed while it was being claimed. Review before applying it to a plan."},
            status_code=409,
        )
    else:
        logged_row = logged_after

    if logged_row is not None:
        # Conditional link: matches only while the row is still unlinked and
        # unchanged. Losing it means someone linked it meanwhile; never insert.
        link_filter = {
            "_id": logged_row["_id"],
            "transaction_id": txn,
            "amount": logged_row.get("amount"),
            # Pin the settlement facts that were validated: if another writer
            # (e.g. the reconciliation service) changed the status or webhook
            # event meanwhile, the link must not match (fail closed).
            "status": logged_row.get("status"),
            # Pin the validated booking too: a reassignment in between must
            # not be overwritten.
            "booking_number": logged_row.get("booking_number"),
            # Still unlinked, and the transaction-id key is free or already ours.
            # A second payments row that owns txn_dedupe_key makes the $set raise
            # DuplicateKeyError instead of linking.
            "$and": [
                {"$or": [{"plan_id": {"$exists": False}}, {"plan_id": None}, {"plan_id": ""}]},
                {"$or": [
                    {"txn_dedupe_key": {"$exists": False}},
                    {"txn_dedupe_key": None},
                    {"txn_dedupe_key": ""},
                    {"txn_dedupe_key": txn},
                ]},
            ],
            # Pin county/state on the row and under custom_fields. A jurisdiction
            # that appears or changes after this check must not still match.
            **_identity_pins(logged_row),
        }
        _raw = logged_row.get("raw_payload")
        if isinstance(_raw, dict) and "event_type" in _raw:
            link_filter["raw_payload.event_type"] = _raw.get("event_type")
        else:
            link_filter["raw_payload.event_type"] = {"$exists": False}
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
            # Holds the partial unique key so a later poller or webhook insert
            # of this transaction id cannot add a second row.
            "txn_dedupe_key": txn,
        }
        if not logged_row.get("timestamp"):
            # Keep the provider's payment time (Codex P2 on #182): revenue
            # windows read ``timestamp``, so it must not become the link time.
            link_set["timestamp"] = logged_row.get("created_at") or now.isoformat()
        try:
            linked = await payments_col.update_one(link_filter, {"$set": link_set})
            link_ok = bool(getattr(linked, "modified_count", 0))
            current = None if link_ok else (await payments_col.find_one({"_id": logged_row["_id"]}) or {})
        except DuplicateKeyError:
            # Another payments row already holds this transaction id. The update
            # did not apply. Do not guess, and do not decrement the balance.
            await _fail_claim(claims, plan_id, txn, "logged_payment_ambiguous")
            return JSONResponse(
                {"error": "logged_payment_ambiguous", "transaction_id": txn,
                 "message": "Another logged payment with this transaction id is already stored. Review before applying it to a plan."},
                status_code=409,
            )
        except Exception as exc:
            # Write outcome unknown (timeout / unknown write concern): the link
            # may have persisted (Codex P1). Read back; if the row carries THIS
            # link, revert it (guarded on every field we wrote). Then fail the
            # claim (retries -> payment_needs_review); balance untouched.
            logger.warning("[payment_plans] link write failed plan_id=%s err=%s", plan_id, type(exc).__name__)
            reason = "logged_payment_link_error"
            try:
                persisted = await payments_col.find_one({"_id": logged_row["_id"]}) or {}
            except Exception:
                persisted = None
            if persisted is None:
                reason = "logged_payment_link_unknown"
            elif (persisted.get("plan_id") == plan_id
                    and persisted.get("plan_linked_at") == link_set["plan_linked_at"]):
                if not await _revert_link(payments_col, logged_row, plan_id, link_set):
                    reason = "logged_payment_link_unreverted"
            await _fail_claim(claims, plan_id, txn, reason)
            return JSONResponse(
                {"error": "payment_record_failed", "transaction_id": txn,
                 "message": "Linking the logged card payment failed. Check the plan before recording it again."},
                status_code=500,
            )
        if not link_ok:
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
        own_id = logged_row["_id"]
    else:
        from bson import ObjectId

        from dashboard.services.payment_txn_dedupe import insert_payment_once

        own_id = ObjectId()
        try:
            inserted = await insert_payment_once(payments_col, {
                "_id": own_id,
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
        except Exception:
            await _fail_claim(claims, plan_id, txn, "payment_write_error")
            return JSONResponse({"error": "payment_record_failed", "transaction_id": txn}, status_code=500)
        if not inserted:
            # The poller or webhook inserted this transaction id after the
            # recheck. Link that row when it is the one settled unlinked match.
            # Otherwise fail closed: do not insert a second row.
            plan, logged_row, adopt_error = await _classify_logged_payment(
                plans_col, payments_col, plan, txn, amount, actor,
            )
            if adopt_error is not None or logged_row is None:
                await _fail_claim(claims, plan_id, txn, "logged_payment_ambiguous")
                if adopt_error is not None:
                    return adopt_error
                return JSONResponse(
                    {"error": "logged_payment_ambiguous", "transaction_id": txn,
                     "message": "Another logged payment with this transaction id appeared while this one was being recorded. Review before applying it to a plan."},
                    status_code=409,
                )
            link_filter = {
                "_id": logged_row["_id"],
                "transaction_id": txn,
                "amount": logged_row.get("amount"),
                "status": logged_row.get("status"),
                "booking_number": logged_row.get("booking_number"),
                "$and": [
                    {"$or": [{"plan_id": {"$exists": False}}, {"plan_id": None}, {"plan_id": ""}]},
                    {"$or": [
                        {"txn_dedupe_key": {"$exists": False}},
                        {"txn_dedupe_key": None},
                        {"txn_dedupe_key": ""},
                        {"txn_dedupe_key": txn},
                    ]},
                ],
                **_identity_pins(logged_row),
            }
            _raw = logged_row.get("raw_payload")
            if isinstance(_raw, dict) and "event_type" in _raw:
                link_filter["raw_payload.event_type"] = _raw.get("event_type")
            else:
                link_filter["raw_payload.event_type"] = {"$exists": False}
            link_set = {
                "plan_id": plan_id,
                "plan_linked_at": now.isoformat(),
                "plan_linked_type": "installment",
                "type": "installment",
                "status": "completed",
                "logged_status": logged_row.get("status"),
                "txn_dedupe_key": txn,
            }
            if not logged_row.get("timestamp"):
                link_set["timestamp"] = logged_row.get("created_at") or now.isoformat()
            try:
                linked = await payments_col.update_one(link_filter, {"$set": link_set})
                if not getattr(linked, "modified_count", 0):
                    await _fail_claim(claims, plan_id, txn, "logged_payment_link_lost")
                    return JSONResponse(
                        {"error": "payment_needs_review", "transaction_id": txn,
                         "message": "The logged card payment changed while it was being linked. Check the plan before recording it again."},
                        status_code=409,
                    )
            except DuplicateKeyError:
                await _fail_claim(claims, plan_id, txn, "logged_payment_ambiguous")
                return JSONResponse(
                    {"error": "logged_payment_ambiguous", "transaction_id": txn,
                     "message": "Another logged payment with this transaction id is already stored. Review before applying it to a plan."},
                    status_code=409,
                )
            except Exception as exc:
                # Same unknown-outcome rule as the direct link: read the row
                # back before deciding the link did not persist.
                logger.warning(
                    "[payment_plans] adopted link write failed plan_id=%s err=%s",
                    plan_id, type(exc).__name__,
                )
                reason = "logged_payment_link_error"
                try:
                    persisted = await payments_col.find_one({"_id": logged_row["_id"]}) or {}
                except Exception:
                    persisted = None
                if persisted is None:
                    reason = "logged_payment_link_unknown"
                elif (persisted.get("plan_id") == plan_id
                        and persisted.get("plan_linked_at") == link_set["plan_linked_at"]):
                    if not await _revert_link(payments_col, logged_row, plan_id, link_set):
                        reason = "logged_payment_link_unreverted"
                await _fail_claim(claims, plan_id, txn, reason)
                return JSONResponse(
                    {"error": "payment_record_failed", "transaction_id": txn,
                     "message": "Linking the logged card payment failed. Check the plan before recording it again."},
                    status_code=500,
                )
            own_id = logged_row["_id"]

    # actor was taken before the claim so the jurisdiction backfill could name it.

    async def _undo(reason: str) -> bool:
        """Compensate this request's payment write. True when fully undone."""
        if logged_row is not None:
            ok = await _revert_link(payments_col, logged_row, plan_id, link_set)
            if ok:
                await _audit_unlink(actor, plan_id, txn, logged_row, reason)
            return ok
        try:
            res = await payments_col.delete_one({"_id": own_id, "plan_id": plan_id, "transaction_id": txn})
            return bool(getattr(res, "deleted_count", 0))
        except Exception as exc:
            logger.error("[payment_plans] row undo failed plan_id=%s err=%s", plan_id, type(exc).__name__)
            return False

    if logged_row is not None:
        # Audit the link before any later compensation. A clash undo emits
        # payment_plan_logged_payment_unlinked; that event must follow this
        # link record (Codex P2). If the audit insert fails, revert with no
        # unlink event — there is no link record to pair it with — fail the
        # claim and return an error. Balance untouched.
        if not await _audit_link(actor, plan, plan_id, txn, logged_row, amount):
            if not await _revert_link(payments_col, logged_row, plan_id, link_set):
                # The link is still on the row. Do not say the payment was not applied.
                await _fail_claim(claims, plan_id, txn, "audit_write_failed_link_unreverted")
                return JSONResponse(
                    {"error": "audit_write_failed_link_unreverted", "transaction_id": txn,
                     "message": "The audit record could not be saved and the link could not be undone. Check this payment before recording it again."},
                    status_code=503,
                )
            await _fail_claim(claims, plan_id, txn, "audit_write_failed")
            return JSONResponse(
                {"error": "audit_write_failed", "transaction_id": txn,
                 "message": "The payment was not applied because its audit record could not be saved. Try again shortly."},
                status_code=503,
            )

    # Post-write recheck (Codex P1): the poller/webhook insert into `payments`
    # outside this claim. If any other row for this txn exists now (one that
    # arrived after the pre-check), this application is ambiguous: undo our
    # write before the balance moves and refuse. The link audit above already
    # ran, so the unlink audit is the matching transition.
    try:
        clash = await _other_rows_for_txn(payments_col, txn, own_id)
    except Exception:
        clash = True
    if clash:
        if not await _undo("another payments row for this transaction id appeared during the write"):
            await _fail_claim(claims, plan_id, txn, "logged_payment_ambiguous_unreverted")
            return JSONResponse(
                {"error": "logged_payment_ambiguous_unreverted", "transaction_id": txn,
                 "message": "Another payment with this transaction id appeared, and this one could not be undone. Check the payment before recording it again."},
                status_code=500,
            )
        await _fail_claim(claims, plan_id, txn, "logged_payment_ambiguous")
        return JSONResponse(
            {"error": "logged_payment_ambiguous", "transaction_id": txn,
             "message": "Another logged payment with this transaction id appeared while this one was being recorded. Review before applying it to a plan."},
            status_code=409,
        )

    plan_write_landed = True
    try:
        # Atomic increments so two different transactions never overwrite each other.
        # applied_transaction_ids is set in the same update, so a driver error
        # after the write can be told apart from one where the write did not land.
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
                "$addToSet": {"applied_transaction_ids": txn},
            },
        )
    except Exception as exc:
        # The client may not know whether the $inc landed. Read the marker
        # before undoing the payment. If the plan cannot be read, keep the
        # payment: deleting it would drop the only evidence of a balance that
        # may already have moved.
        logger.warning("[payment_plans] plan update failed plan_id=%s err=%s", plan_id, type(exc).__name__)
        fresh = await _read_plan(plans_col, plan_id)
        applied = (
            isinstance(fresh, dict)
            and txn in list(fresh.get("applied_transaction_ids") or [])
        )
        if not isinstance(fresh, dict):
            await _fail_claim(claims, plan_id, txn, "plan_update_unknown")
            return JSONResponse(
                {"error": "plan_update_unknown", "transaction_id": txn,
                 "message": "The plan balance update did not confirm. The payment row was kept. Check the plan before recording it again."},
                status_code=500,
            )
        if not applied:
            plan_write_landed = False
    if not plan_write_landed:
        if not await _undo("plan balance update failed after the payment was linked"):
            await _fail_claim(claims, plan_id, txn, "plan_update_error_unreverted")
            return JSONResponse(
                {"error": "payment_record_failed", "transaction_id": txn,
                 "message": "The plan balance did not update and the payment could not be undone. Check the plan before recording it again."},
                status_code=500,
            )
        await _fail_claim(claims, plan_id, txn, "plan_update_error")
        return JSONResponse({"error": "payment_record_failed", "transaction_id": txn}, status_code=500)

    # The $inc landed, so the payment stays (undoing the row would drop the
    # evidence of a balance that already moved). The paid-in-full clamp is
    # retried once and then checked against a real read. If the plan is still
    # active with a non-positive balance, or the plan cannot be read, fail
    # closed: no success, and no guessed balance_after / status (Codex P1).
    updated, unconfirmed = await _confirm_applied_plan(plans_col, plan_id)
    if updated is None:
        await _fail_claim(claims, plan_id, txn, unconfirmed)
        if unconfirmed == "paid_in_full_clamp_failed":
            message = ("The installment was recorded, but the plan could not be marked paid in full. "
                       "Check the plan before recording it again.")
        else:
            message = ("The installment was recorded, but the plan could not be read back. "
                       "Check the plan before recording it again.")
        return JSONResponse(
            {"error": unconfirmed, "transaction_id": txn, "message": message},
            status_code=500,
        )

    new_balance = updated["balance_remaining"]
    status = updated["status"]
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