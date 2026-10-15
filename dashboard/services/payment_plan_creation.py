"""One financed payment plan for a premium confirmation.

``ensure_payment_plan_for_confirmation`` is what Write Bond calls inside its
confirmation transaction (PR #197, ``386b7964``). The plan document uses that
service's fallback row shape (``source: premium_confirmation``) so installment
invoicing reads the same fields.

The plan ``_id`` and ``plan_id`` are the same deterministic, tenant-scoped key:
``sha256(f"{tenant_id}|{confirmation_id}")``. A row already stored under that
key is reused. A fallback row written first, found the way #197 finds its own
plan (booking number, ``source: premium_confirmation``, active or paid), is
the same plan: matching terms return ``created=False`` and a second row is
not inserted. Different terms raise ``PlanTermsConflict``.

This module does not create indexes and does not write ``payments``.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from dashboard.extensions import get_collection
from dashboard.routers.payment_plans import _amount_cents

logger = logging.getLogger(__name__)

PLAN_SOURCE = "premium_confirmation"
AUDIT_EVENT = "payment_plan_created"
FINGERPRINT_FIELD = "premium_confirmation_fingerprint"
_FREQUENCIES = frozenset({"weekly", "biweekly", "monthly"})
_PERIOD_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}
_ACTIVE = ("active", "paid")


class PlanTermsConflict(Exception):
    """A plan for this confirmation already exists with different terms."""


class TransactionsUnavailable(Exception):
    """No replica set or Atlas. Nothing was written."""

    def __init__(self) -> None:
        super().__init__("transactions_unavailable")


def plan_document_id(tenant_id: str, confirmation_id: str) -> str:
    """Mongo ``_id`` and ``plan_id`` for one tenant and confirmation.

    Stable across the confirmation writer and this function, so whichever
    runs first occupies the only key for that pair.
    """
    tenant = str(tenant_id or "").strip()
    confirmation = str(confirmation_id or "").strip()
    return hashlib.sha256(f"{tenant}|{confirmation}".encode()).hexdigest()


def audit_event_id(tenant_id: str, plan_id: str) -> str:
    """Deterministic, tenant-scoped ``audit_events._id``."""
    tenant = str(tenant_id or "").strip()
    return f"payment-plan-created:{tenant}:{plan_id}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sess(session) -> dict:
    return {"session": session} if session is not None else {}


def cents_to_amount(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    n = abs(int(cents))
    return f"{sign}{n // 100}.{n % 100:02d}"


def _split_cents(financed_cents: int, count: int) -> list[int]:
    """Installment cents. The remainder sits on the last installment."""
    base, extra = divmod(int(financed_cents), int(count))
    return [base + (extra if index == count - 1 else 0) for index in range(count)]


def _schedule(financed_cents: int, count: int, frequency: str, now: datetime) -> list[dict]:
    step = _PERIOD_DAYS[frequency]
    rows = []
    for index, cents in enumerate(_split_cents(financed_cents, count)):
        due = (now + timedelta(days=step * (index + 1))).date().isoformat()
        rows.append({
            "n": index + 1,
            "amount": cents_to_amount(cents),
            "amount_cents": cents,
            "due_date": due,
            "status": "scheduled",
        })
    return rows


def _require_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _validate(financed: Any, installments: Any, frequency: Any) -> tuple[int, int, str]:
    if isinstance(financed, bool) or isinstance(financed, float):
        raise ValueError(
            "Financed balance must be a positive amount with at most two decimal places."
        )
    cents = _amount_cents(financed)
    if cents is None:
        raise ValueError(
            "Financed balance must be a positive amount with at most two decimal places."
        )
    if isinstance(installments, bool) or not isinstance(installments, int):
        raise ValueError("Installments must be an integer of at least 1.")
    if installments < 1:
        raise ValueError("Installments must be an integer of at least 1.")
    if cents < installments:
        raise ValueError("Each installment must be at least one cent.")
    freq = str(frequency or "").strip().lower()
    if freq not in _FREQUENCIES:
        raise ValueError("Frequency must be weekly, biweekly, or monthly.")
    return cents, installments, freq


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stored_financed_cents(existing: dict) -> Optional[int]:
    parsed = _as_int(existing.get("balance_remaining_cents"))
    if parsed is not None:
        return parsed
    for key in ("financed_balance", "balance_remaining"):
        cents = _amount_cents(existing.get(key))
        if cents is not None:
            return cents
    rows = existing.get("installments")
    if not isinstance(rows, list) or not rows:
        return None
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            return None
        cents = _as_int(row.get("amount_cents"))
        if cents is None:
            cents = _amount_cents(row.get("amount"))
        if cents is None:
            return None
        total += cents
    return total


def _stored_count(existing: dict) -> Optional[int]:
    count = _as_int(existing.get("installment_count"))
    if count is not None:
        return count
    rows = existing.get("installments")
    if isinstance(rows, list):
        return len(rows)
    return None


def _terms_match(existing: dict, financed_cents: int, count: int, frequency: str) -> bool:
    if not isinstance(existing, dict):
        return False
    if _stored_count(existing) != count:
        return False
    if str(existing.get("frequency") or "").strip().lower() != frequency:
        return False
    if _stored_financed_cents(existing) != financed_cents:
        return False
    fingerprint = str(existing.get(FINGERPRINT_FIELD) or "").strip()
    if fingerprint:
        parts = fingerprint.split(":")
        if len(parts) != 5:
            return False
        try:
            fp_financed = int(parts[2])
            fp_count = int(parts[3])
        except ValueError:
            return False
        if fp_financed != financed_cents or fp_count != count or parts[4].strip().lower() != frequency:
            return False
    rows = existing.get("installments")
    if isinstance(rows, list) and rows:
        expected = _split_cents(financed_cents, count)
        got: list[int] = []
        for row in rows:
            if not isinstance(row, dict):
                return False
            cents = _as_int(row.get("amount_cents"))
            if cents is None:
                cents = _amount_cents(row.get("amount"))
            if cents is None:
                return False
            got.append(cents)
        if got != expected:
            return False
    return True


def _result(existing: dict, financed_cents: int, count: int, frequency: str, now: datetime) -> dict:
    plan_id = str(existing.get("plan_id") or existing.get("_id") or "")
    rows = existing.get("installments")
    if isinstance(rows, list) and rows:
        schedule = [dict(row) for row in rows if isinstance(row, dict)]
    else:
        schedule = _schedule(financed_cents, count, frequency, now)
    return {"plan_id": plan_id, "created": False, "schedule": schedule}


async def _find_one(name: str, query: dict, session) -> Optional[dict]:
    if not query:
        return None
    try:
        doc = await get_collection(name).find_one(query, **_sess(session))
    except Exception as exc:
        logger.warning(
            "[payment_plan] lookup failed collection=%s err_type=%s",
            name,
            type(exc).__name__,
        )
        return None
    return doc if isinstance(doc, dict) else None


def _copy_text(doc: dict, *keys: str) -> str:
    for key in keys:
        text = str(doc.get(key) or "").strip()
        if text:
            return text
    return ""


async def _case_facts(bond_case_id: str, tenant_id: str, session) -> dict:
    """Identity and confirmed amounts already stored on the case.

    Premium confirmation writes those fields before it calls this function,
    in the same transaction. Missing collections leave the facts empty.
    """
    facts: dict[str, Any] = {}
    if not bond_case_id:
        return facts
    query: dict[str, Any] = {"bond_case_id": bond_case_id}
    if tenant_id:
        query["tenant_id"] = tenant_id
    for name in ("bond_cases", "active_bonds", "paperwork_packets"):
        doc = await _find_one(name, query, session)
        if not doc:
            continue
        if not facts.get("booking_number"):
            facts["booking_number"] = _copy_text(doc, "booking_number", "Booking_Number")
        if not facts.get("defendant_name"):
            facts["defendant_name"] = _copy_text(doc, "defendant_name", "Defendant_Name")
        if not facts.get("surety_id"):
            facts["surety_id"] = _copy_text(doc, "surety_id", "surety")
        if not facts.get("packet_id"):
            facts["packet_id"] = _copy_text(doc, "packet_id")
        if not facts.get("total_premium"):
            premium = _copy_text(doc, "premium_confirmed_amount")
            if premium:
                facts["total_premium"] = premium
        if not facts.get("down_payment"):
            down = _copy_text(doc, "down_payment_amount")
            if down:
                facts["down_payment"] = down
        if not facts.get(FINGERPRINT_FIELD):
            fingerprint = _copy_text(doc, FINGERPRINT_FIELD)
            if fingerprint:
                facts[FINGERPRINT_FIELD] = fingerprint
    return facts


async def _find_existing(
    *,
    plan_id: str,
    tenant_id: str,
    bond_case_id: str,
    booking: str,
    session,
) -> Optional[dict]:
    plans = get_collection("payment_plans")
    for query in ({"_id": plan_id}, {"plan_id": plan_id}):
        found = await _find_one_in(plans, query, session)
        if found:
            return found
    scope: dict[str, Any] = {"source": PLAN_SOURCE, "status": {"$in": list(_ACTIVE)}}
    if tenant_id:
        scope["tenant_id"] = tenant_id
    if booking:
        found = await _find_one_in(plans, {**scope, "booking_number": booking}, session)
        if found:
            return found
    if bond_case_id:
        found = await _find_one_in(plans, {**scope, "bond_case_id": bond_case_id}, session)
        if found:
            return found
    return None


async def _find_one_in(collection, query: dict, session) -> Optional[dict]:
    doc = await collection.find_one(query, **_sess(session))
    return doc if isinstance(doc, dict) else None


def _money_cents(value: Any) -> Optional[int]:
    """Exact cents, including zero. Fractional cents are refused."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        dec = Decimal(text)
    except Exception:
        return None
    if not dec.is_finite() or dec < 0:
        return None
    if dec == 0:
        return 0
    return _amount_cents(text)


def _money_pair(amount: str) -> tuple[str, Optional[int]]:
    text = str(amount or "").strip()
    if not text:
        return "", None
    return text, _money_cents(text)


def _plan_document(
    *,
    plan_id: str,
    tenant_id: str,
    bond_case_id: str,
    confirmation_id: str,
    actor: str,
    financed_cents: int,
    count: int,
    frequency: str,
    schedule: list[dict],
    facts: dict,
    now: datetime,
) -> dict:
    """#197 fallback shape, plus the shared ``_id``."""
    now_iso = now.isoformat()
    premium, premium_cents = _money_pair(str(facts.get("total_premium") or ""))
    down, down_cents = _money_pair(str(facts.get("down_payment") or ""))
    plan = {
        "_id": plan_id,
        "plan_id": plan_id,
        "confirmation_id": confirmation_id,
        "booking_number": str(facts.get("booking_number") or ""),
        "packet_id": str(facts.get("packet_id") or ""),
        "bond_case_id": bond_case_id,
        "defendant_name": str(facts.get("defendant_name") or ""),
        "surety_id": str(facts.get("surety_id") or ""),
        "balance_remaining": cents_to_amount(financed_cents),
        "balance_remaining_cents": financed_cents,
        "financed_balance": cents_to_amount(financed_cents),
        "installment_count": count,
        "installments": schedule,
        "installment_amount": schedule[0]["amount"],
        "frequency": frequency,
        "next_due_date": schedule[0]["due_date"],
        "payments_made": 0,
        "total_paid": "0.00",
        "total_paid_cents": 0,
        "status": "active",
        "source": PLAN_SOURCE,
        "delinquent": False,
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": actor,
    }
    if premium and premium_cents is not None:
        plan["total_premium"] = premium
        plan["total_premium_cents"] = premium_cents
    if down and down_cents is not None:
        plan["down_payment"] = down
        plan["down_payment_cents"] = down_cents
    fingerprint = str(facts.get(FINGERPRINT_FIELD) or "").strip()
    if fingerprint:
        plan[FINGERPRINT_FIELD] = fingerprint
    if tenant_id:
        plan["tenant_id"] = tenant_id
    return plan


def _audit_document(
    *,
    plan_id: str,
    tenant_id: str,
    bond_case_id: str,
    confirmation_id: str,
    actor: str,
    booking: str,
    financed_cents: int,
    count: int,
    frequency: str,
    now: datetime,
) -> dict:
    now_iso = now.isoformat()
    event = {
        "_id": audit_event_id(tenant_id, plan_id),
        "event_type": AUDIT_EVENT,
        "action": AUDIT_EVENT,
        "entity_type": "payment_plan",
        "entity_id": plan_id,
        "plan_id": plan_id,
        "confirmation_id": confirmation_id,
        "bond_case_id": bond_case_id,
        "booking_number": booking,
        "actor": actor,
        "agent_name": "PaymentPlanCreation",
        "timestamp": now_iso,
        "created_at": now_iso,
        "reason": "payment plan created for premium confirmation",
        "old_state": None,
        "new_state": {
            "status": "active",
            "balance_remaining_cents": financed_cents,
            "installments": count,
            "frequency": frequency,
        },
    }
    if tenant_id:
        event["tenant_id"] = tenant_id
    return event


async def _apply(
    session,
    *,
    tenant_id: str,
    bond_case_id: str,
    confirmation_id: str,
    actor: str,
    financed_cents: int,
    count: int,
    frequency: str,
) -> dict:
    plan_id = plan_document_id(tenant_id, confirmation_id)
    now = _now()
    facts = await _case_facts(bond_case_id, tenant_id, session)
    booking = str(facts.get("booking_number") or "")
    existing = await _find_existing(
        plan_id=plan_id,
        tenant_id=tenant_id,
        bond_case_id=bond_case_id,
        booking=booking,
        session=session,
    )
    if existing:
        if not _terms_match(existing, financed_cents, count, frequency):
            raise PlanTermsConflict("financed terms differ")
        return _result(existing, financed_cents, count, frequency, now)

    schedule = _schedule(financed_cents, count, frequency, now)
    plan = _plan_document(
        plan_id=plan_id,
        tenant_id=tenant_id,
        bond_case_id=bond_case_id,
        confirmation_id=confirmation_id,
        actor=actor,
        financed_cents=financed_cents,
        count=count,
        frequency=frequency,
        schedule=schedule,
        facts=facts,
        now=now,
    )
    plans = get_collection("payment_plans")
    try:
        await plans.insert_one(plan, **_sess(session))
    except DuplicateKeyError:
        raced = await _find_existing(
            plan_id=plan_id,
            tenant_id=tenant_id,
            bond_case_id=bond_case_id,
            booking=booking,
            session=session,
        )
        if raced and _terms_match(raced, financed_cents, count, frequency):
            return _result(raced, financed_cents, count, frequency, now)
        raise PlanTermsConflict("financed terms differ") from None

    audit = _audit_document(
        plan_id=plan_id,
        tenant_id=tenant_id,
        bond_case_id=bond_case_id,
        confirmation_id=confirmation_id,
        actor=actor,
        booking=booking,
        financed_cents=financed_cents,
        count=count,
        frequency=frequency,
        now=now,
    )
    try:
        await get_collection("audit_events").insert_one(audit, **_sess(session))
    except DuplicateKeyError:
        pass
    logger.info("[payment_plan] created plan_id=%s installments=%s", plan_id, count)
    return {"plan_id": plan_id, "created": True, "schedule": schedule}


async def _transaction_client():
    """A replica set or Atlas client, else None. Never raises."""
    try:
        from dashboard.extensions import get_mongo_client

        client = get_mongo_client()
    except Exception:
        return None
    if client is None:
        return None
    try:
        from dashboard.routers.payment_plans import _supports_transactions

        if await _supports_transactions(client):
            return client
    except Exception:
        logger.warning("[payment_plan] transaction support check failed")
        return None
    return None


async def _run_transaction(client, work):
    started = client.start_session()
    if hasattr(started, "__await__"):
        started = await started
    async with started as session:
        return await session.with_transaction(work)


async def ensure_payment_plan_for_confirmation(
    *,
    tenant_id,
    bond_case_id,
    confirmation_id,
    financed: Decimal,
    installments: int,
    frequency: str,
    actor: str,
    session=None,
) -> dict:
    """Create or reuse the financed plan for one confirmation.

    When ``session`` is passed, reads and writes join that transaction.
    Without one, the insert and the audit run in ``with_transaction``
    (the driver retries transient transaction errors). No replica set
    raises ``TransactionsUnavailable`` and writes nothing.
    """
    tenant = str(tenant_id or "").strip()
    case_id = str(bond_case_id or "").strip()
    confirmation = _require_text(confirmation_id, "confirmation_id")
    who = str(actor or "").strip()
    financed_cents, count, freq = _validate(financed, installments, frequency)

    async def work(active_session):
        return await _apply(
            active_session,
            tenant_id=tenant,
            bond_case_id=case_id,
            confirmation_id=confirmation,
            actor=who,
            financed_cents=financed_cents,
            count=count,
            frequency=freq,
        )

    if session is not None:
        return await work(session)

    client = await _transaction_client()
    if client is None:
        raise TransactionsUnavailable()
    return await _run_transaction(client, work)
