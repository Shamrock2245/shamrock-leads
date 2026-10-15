"""Staff premium confirmation at Write Bond.

The writing agent confirms the premium, the down payment, and the financed
balance once. DocuSeal completion and the payment plan use those numbers.
The 10% estimate is never treated as confirmed.

Who may confirm: the same desk that writes the bond — god_admin, admin,
staff, and sub_agent. Recovery may not. The actor is the session user.
``created_by`` / ``premium_confirmed_by`` in the body are ignored.

A plan is created exactly once when the financed balance is greater than
zero. A replay of the same amounts returns that plan and does not write a
second audit row. A different amount is refused unless ``reason`` is set;
the prior confirmation is kept on ``premium_confirmation_history``.

A premium under the filed floor (10% of the bond, and at least $100, for
OSI and Palmetto) is allowed only with ``reason``.

``SAAS_MULTI_TENANT`` off: rows are matched the way the rest of Write Bond
matches them. The flag is not turned on here, and a confirmation never
copies one agency's row onto another.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from dashboard.extensions import get_collection
from dashboard.routers.payment_plans import _amount_cents
from dashboard.services.packet_payment_link_service import (
    PREMIUM_CONFIRMED_AMOUNT_FIELD,
    PREMIUM_CONFIRMED_AT_FIELD,
    PREMIUM_CONFIRMED_BY_FIELD,
)
from dashboard.tenancy.context import current_tenant_id
from dashboard.tenancy.flag import multi_tenant_enabled

logger = logging.getLogger(__name__)

AUDIT_ACTION = "premium_confirmed"
PLAN_SOURCE = "premium_confirmation"
DOWN_FIELD = "down_payment_amount"
FINANCED_FIELD = "financed_balance"
HISTORY_FIELD = "premium_confirmation_history"
FINGERPRINT_FIELD = "premium_confirmation_fingerprint"
FREQUENCIES = frozenset({"weekly", "biweekly", "monthly"})
_PERIOD_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}
_MIN_PREMIUM_CENTS = 10000  # $100 filed minimum, OSI and Palmetto
_FILED_RATE_NUM = 10
_FILED_RATE_DEN = 100

_CONFIRM_KEYS = (
    PREMIUM_CONFIRMED_AMOUNT_FIELD,
    PREMIUM_CONFIRMED_AT_FIELD,
    PREMIUM_CONFIRMED_BY_FIELD,
    DOWN_FIELD,
    FINANCED_FIELD,
    "premium_installments",
    "premium_frequency",
    FINGERPRINT_FIELD,
    "premium_is_estimate",
)


class PremiumConfirmationError(Exception):
    def __init__(self, error: str, status: int, message: str = ""):
        super().__init__(error)
        self.error = error
        self.status = status
        self.message = message or error


class PlanTermsConflict(Exception):
    """Financed terms disagree with a plan that already exists.

    Invoicing's class, when ``payment_plan_creation`` lands, is recognized
    by this name so this file does not import theirs.
    """


PLAN_MODULE = "dashboard.services.payment_plan_creation"
PLAN_FUNC = "ensure_payment_plan_for_confirmation"


def load_plan_creator():
    """Invoicing's plan function, or None when that module is not on this branch.

    A missing module keeps this service's own plan writer, which is what the
    installment stack records. A present function that raises
    ``NotImplementedError`` fails closed inside the confirmation transaction.
    """
    import importlib

    try:
        module = importlib.import_module(PLAN_MODULE)
    except ImportError:
        return None
    fn = getattr(module, PLAN_FUNC, None)
    return fn if callable(fn) else None


def non_negative_cents(value: Any) -> Optional[int]:
    """Exact cents, or None. Zero is allowed. Sub-cent values are refused.

    Positive amounts use the same parser as ``POST /payments/plans/{id}/pay``
    (#180 / #184): fractional cents are not rounded.
    """
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
        _sign, digits, exponent = dec.as_tuple()
        if exponent >= 0:
            return 0
        drop = digits[len(digits) + exponent:] if -exponent <= len(digits) else digits
        if any(d != 0 for d in drop):
            return None
        return 0
    return _amount_cents(text)


def cents_to_amount(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    n = abs(int(cents))
    return f"{sign}{n // 100}.{n % 100:02d}"


def confirmation_complete(doc: Optional[dict]) -> bool:
    if not isinstance(doc, dict):
        return False
    amount = non_negative_cents(doc.get(PREMIUM_CONFIRMED_AMOUNT_FIELD))
    at = str(doc.get(PREMIUM_CONFIRMED_AT_FIELD) or "").strip()
    by = str(doc.get(PREMIUM_CONFIRMED_BY_FIELD) or "").strip()
    return amount is not None and amount > 0 and bool(at) and bool(by)


def invoice_due_cents(doc: Optional[dict]) -> Optional[int]:
    """Amount to invoice now: the confirmed down payment, else the premium.

    A confirmed down of zero means nothing is due on the signing invoice
    (the financed balance is collected on the plan). Returns None when the
    trio is missing.
    """
    if not confirmation_complete(doc):
        return None
    if DOWN_FIELD in doc and not _blank(doc.get(DOWN_FIELD)):
        return non_negative_cents(doc.get(DOWN_FIELD))
    return non_negative_cents(doc.get(PREMIUM_CONFIRMED_AMOUNT_FIELD))


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _scope(query: dict) -> dict:
    """Tenant predicate only when the flag is on. Flag off is unchanged."""
    if not multi_tenant_enabled():
        return query
    tenant = str(current_tenant_id() or "").strip()
    if not tenant:
        raise PremiumConfirmationError("tenant_required", 403, "No tenant on this session.")
    return {**query, "tenant_id": tenant}


def filed_floor_cents(bond_amount: Any) -> int:
    """max($100, 10% of the bond). Missing bond amount uses the $100 floor."""
    bond = non_negative_cents(bond_amount)
    if bond is None or bond <= 0:
        return _MIN_PREMIUM_CENTS
    ten = (bond * _FILED_RATE_NUM) // _FILED_RATE_DEN
    return max(_MIN_PREMIUM_CENTS, ten)


def _schedule(financed_cents: int, count: int, frequency: str, now: datetime) -> list[dict]:
    base, extra = divmod(financed_cents, count)
    step = _PERIOD_DAYS[frequency]
    rows = []
    for index in range(count):
        cents = base + (extra if index == count - 1 else 0)
        due = (now + timedelta(days=step * (index + 1))).date().isoformat()
        rows.append({
            "n": index + 1,
            "amount": cents_to_amount(cents),
            "amount_cents": cents,
            "due_date": due,
            "status": "scheduled",
        })
    return rows


def _fingerprint(premium: int, down: int, financed: int, count: int, frequency: str) -> str:
    return f"{premium}:{down}:{financed}:{count}:{frequency}"


def _prior_snapshot(doc: dict) -> dict:
    return {
        PREMIUM_CONFIRMED_AMOUNT_FIELD: doc.get(PREMIUM_CONFIRMED_AMOUNT_FIELD),
        PREMIUM_CONFIRMED_AT_FIELD: doc.get(PREMIUM_CONFIRMED_AT_FIELD),
        PREMIUM_CONFIRMED_BY_FIELD: doc.get(PREMIUM_CONFIRMED_BY_FIELD),
        DOWN_FIELD: doc.get(DOWN_FIELD),
        FINANCED_FIELD: doc.get(FINANCED_FIELD),
        "premium_installments": doc.get("premium_installments"),
        "premium_frequency": doc.get("premium_frequency"),
        FINGERPRINT_FIELD: doc.get(FINGERPRINT_FIELD),
    }


def _session_kwargs(session) -> dict:
    return {"session": session} if session is not None else {}


async def _find_one(name: str, query: dict, session=None) -> Optional[dict]:
    if not query:
        return None
    try:
        return await get_collection(name).find_one(_scope(query), **_session_kwargs(session))
    except PremiumConfirmationError:
        raise
    except Exception as exc:
        logger.warning("[premium_confirm] lookup failed collection=%s err_type=%s", name, type(exc).__name__)
        raise PremiumConfirmationError("confirmation_lookup_failed", 503) from exc


async def _update(name: str, query: dict, update: dict, session=None) -> None:
    try:
        await get_collection(name).update_one(_scope(query), update, **_session_kwargs(session))
    except PremiumConfirmationError:
        raise
    except Exception as exc:
        logger.warning("[premium_confirm] update failed collection=%s err_type=%s", name, type(exc).__name__)
        raise PremiumConfirmationError("confirmation_write_failed", 503) from exc


async def _transaction_client():
    """A replica set or Atlas client, else None. Never raises."""
    try:
        from dashboard.extensions import get_mongo_client
        client = get_mongo_client()
    except Exception:
        return None
    try:
        from dashboard.routers.payment_plans import _supports_transactions
        if await _supports_transactions(client):
            return client
    except Exception:
        logger.warning("[premium_confirm] transaction support check failed")
        return None
    return None


def _is_terms_conflict(exc: BaseException) -> bool:
    return isinstance(exc, PlanTermsConflict) or type(exc).__name__ == "PlanTermsConflict"


async def _require_identity(
    *,
    packet: Optional[dict],
    bond_case: Optional[dict],
    active: Optional[dict],
    booking: str,
    packet_id: str,
    bond_case_id: str,
) -> None:
    """#165 gate. A synthetic TEST- packet is not exempt on this route."""
    from dashboard.services.identity_verification_service import (
        IndemnitorIdentityError,
        require_verified_indemnitors,
    )

    source = bond_case or active or packet or {}
    name = str(source.get("indemnitor_name") or source.get("Indemnitor_Name") or "").strip()
    people = source.get("indemnitors")
    if not isinstance(people, list) or not people:
        people = [{"name": name, "role": "indemnitor"}] if name else []
    case_id = bond_case_id or str(source.get("bond_case_id") or source.get("Bond_Case_ID") or "")
    pkt = packet_id or str((packet or {}).get("packet_id") or "")
    bond_data = {
        "bond_case_id": case_id,
        "booking_number": booking,
        "packet_id": pkt,
        "indemnitor_name": name,
        "indemnitors": people,
        "county": source.get("county") or source.get("County") or "",
        "self_indemnitor": source.get("self_indemnitor"),
    }
    try:
        await require_verified_indemnitors(
            bond_data=bond_data,
            indemnitors=people,
            bond_case_id=case_id,
            booking_number=booking,
            packet_id=pkt,
            staff_test_case=False,
        )
    except IndemnitorIdentityError as exc:
        raise PremiumConfirmationError(exc.code, 422, str(exc)) from exc


async def confirmed_case_doc(packet: Optional[dict]) -> Optional[dict]:
    """The packet when it is confirmed, else the bond case or active bond.

    Write Bond can confirm the bond case before DocuSeal materializes the
    packet. Completion then invoices that confirmation instead of treating
    the new packet as unconfirmed.
    """
    if confirmation_complete(packet):
        return packet
    if not isinstance(packet, dict):
        return None
    booking = str(packet.get("booking_number") or packet.get("Booking_Number") or "").strip()
    bond_case_id = str(packet.get("bond_case_id") or packet.get("Bond_Case_ID") or "").strip()
    try:
        if bond_case_id:
            case = await _find_one("bond_cases", {"bond_case_id": bond_case_id})
            if confirmation_complete(case):
                return case
        if booking:
            case = await _find_one("bond_cases", {"booking_number": booking})
            if confirmation_complete(case):
                return case
            active = await _find_one("active_bonds", {"booking_number": booking})
            if confirmation_complete(active):
                return active
    except PremiumConfirmationError:
        return None
    return None


async def _commit_confirmation(
    *,
    rows,
    packet,
    bond_case,
    active,
    actor: str,
    booking: str,
    packet_id: str,
    bond_case_id: str,
    premium: int,
    down: int,
    financed: int,
    count: int,
    frequency: str,
    fingerprint: str,
    changed: bool,
    reason: str,
    below: bool,
    now: datetime,
    now_iso: str,
) -> Optional[dict]:
    """Write the trio, the plan, and the audit in one transaction.

    Paid-in-full (financed == 0) uses the same transaction. A failure rolls
    every write back. No transaction client means 503 and nothing is written.
    """
    client = await _transaction_client()
    if client is None:
        raise PremiumConfirmationError(
            "transactions_unavailable",
            503,
            "Recording a confirmed premium needs a MongoDB replica set or Atlas. Nothing was saved.",
        )
    fields = {
        PREMIUM_CONFIRMED_AMOUNT_FIELD: cents_to_amount(premium),
        PREMIUM_CONFIRMED_AT_FIELD: now_iso,
        PREMIUM_CONFIRMED_BY_FIELD: actor,
        DOWN_FIELD: cents_to_amount(down),
        FINANCED_FIELD: cents_to_amount(financed),
        "premium_installments": count,
        "premium_frequency": frequency if financed else "",
        FINGERPRINT_FIELD: fingerprint,
        "premium_is_estimate": False,
    }
    held: dict[str, Any] = {}

    async def work(session):
        for name, doc in rows:
            if not isinstance(doc, dict):
                continue
            query = _identity_query(doc, booking=booking, packet_id=packet_id, bond_case_id=bond_case_id)
            if not query:
                continue
            update: dict[str, Any] = {"$set": fields}
            if changed and confirmation_complete(doc):
                update["$push"] = {HISTORY_FIELD: {**_prior_snapshot(doc), "replaced_at": now_iso, "reason": reason}}
            await _update(name, query, update, session=session)
            doc.update(fields)
        plan = await _ensure_plan(
            booking=booking,
            packet=packet,
            bond_case=bond_case,
            active=active,
            actor=actor,
            premium=premium,
            down=down,
            financed=financed,
            count=count,
            frequency=frequency if financed else "",
            fingerprint=fingerprint,
            create=financed > 0,
            replace=changed,
            reason=reason,
            session=session,
        )
        audit = {
            "event_id": str(uuid.uuid4()),
            "action": AUDIT_ACTION,
            "entity_type": "bond",
            "entity_id": booking or bond_case_id or packet_id,
            "actor": actor,
            "agent_name": "PremiumConfirmation",
            "timestamp": now_iso,
            "reason": reason,
            "details": {
                "premium": cents_to_amount(premium),
                "down": cents_to_amount(down),
                "financed": cents_to_amount(financed),
                "installments": count,
                "frequency": frequency if financed else "",
                "below_filed_rate": below,
                "changed": changed,
                "plan_id": (plan or {}).get("plan_id"),
                "packet_id": packet_id or (packet or {}).get("packet_id") or "",
                "bond_case_id": bond_case_id or (bond_case or {}).get("bond_case_id") or "",
            },
        }
        if multi_tenant_enabled():
            audit["tenant_id"] = current_tenant_id()
        try:
            await get_collection("audit_events").insert_one(audit, **_session_kwargs(session))
        except Exception as exc:
            logger.warning("[premium_confirm] audit insert failed err_type=%s", type(exc).__name__)
            raise PremiumConfirmationError("confirmation_audit_failed", 503) from exc
        held["plan"] = plan
        return plan

    started = client.start_session()
    if hasattr(started, "__await__"):
        started = await started
    try:
        async with started as session:
            await session.with_transaction(work)
    except PremiumConfirmationError:
        raise
    except Exception as exc:
        if _is_terms_conflict(exc):
            raise PremiumConfirmationError(
                "plan_terms_conflict",
                409,
                "A payment plan for this case already uses different terms. Nothing was changed.",
            ) from None
        if isinstance(exc, ValueError):
            message = str(exc).strip() or "The payment plan terms were rejected."
            if len(message) > 240:
                message = "The payment plan terms were rejected."
            raise PremiumConfirmationError("invalid_plan_terms", 400, message) from None
        logger.warning("[premium_confirm] transaction failed err_type=%s", type(exc).__name__)
        raise PremiumConfirmationError(
            "confirmation_write_failed",
            503,
            "The premium could not be confirmed. Nothing was saved.",
        ) from None
    return held.get("plan")


def _identity_query(doc: dict, *, booking: str, packet_id: str, bond_case_id: str) -> dict:
    if doc.get("packet_id"):
        return {"packet_id": doc.get("packet_id")}
    if doc.get("_id") is not None and packet_id and str(doc.get("packet_id") or "") == packet_id:
        return {"packet_id": packet_id}
    if bond_case_id and doc.get("bond_case_id"):
        return {"bond_case_id": doc.get("bond_case_id")}
    if booking and doc.get("booking_number"):
        return {"booking_number": doc.get("booking_number")}
    if doc.get("_id") is not None:
        return {"_id": doc.get("_id")}
    return {}


async def confirm_premium(actor: str, body: dict) -> dict:
    """Write the confirmation, audit it, and create the financed plan once."""
    actor = str(actor or "").strip()
    if not actor:
        raise PremiumConfirmationError("staff_required", 403)
    if not isinstance(body, dict):
        raise PremiumConfirmationError("invalid_body", 400)

    premium = non_negative_cents(body.get("premium"))
    down = non_negative_cents(body.get("down", body.get("down_payment")))
    if premium is None or down is None or premium <= 0:
        raise PremiumConfirmationError(
            "invalid_amount",
            400,
            "Premium and down payment must be non-negative amounts with at most two decimal places.",
        )
    if down > premium:
        raise PremiumConfirmationError("down_payment_exceeds_premium", 400, "Down payment cannot exceed the premium.")
    if "financed" in body and not _blank(body.get("financed")):
        financed = non_negative_cents(body.get("financed"))
        if financed is None:
            raise PremiumConfirmationError(
                "invalid_amount",
                400,
                "Financed balance must be a non-negative amount with at most two decimal places.",
            )
        if financed != premium - down:
            raise PremiumConfirmationError(
                "amounts_do_not_balance",
                400,
                "Premium must equal the down payment plus the financed balance.",
            )
    else:
        financed = premium - down

    frequency = str(body.get("frequency") or "weekly").strip().lower()
    count = 0
    if financed > 0:
        if frequency not in FREQUENCIES:
            raise PremiumConfirmationError("invalid_frequency", 400, "Frequency must be weekly, biweekly, or monthly.")
        raw_count = body.get("installments", body.get("installment_count"))
        if isinstance(raw_count, bool) or raw_count in (None, ""):
            raise PremiumConfirmationError("installments_required", 400, "A financed balance needs an installment count.")
        try:
            count = int(str(raw_count).strip())
        except (TypeError, ValueError):
            count = 0
        if count < 1 or count > 36:
            raise PremiumConfirmationError("invalid_installments", 400, "Installments must be from 1 to 36.")
        if financed < count:
            raise PremiumConfirmationError(
                "installment_below_one_cent",
                400,
                "Each installment must be at least one cent.",
            )
    elif body.get("frequency") not in (None, "", "weekly") and frequency not in FREQUENCIES:
        raise PremiumConfirmationError("invalid_frequency", 400, "Frequency must be weekly, biweekly, or monthly.")

    packet_id = str(body.get("packet_id") or "").strip()
    booking = str(body.get("booking_number") or "").strip()
    bond_case_id = str(body.get("bond_case_id") or "").strip()
    if not packet_id and not booking and not bond_case_id:
        raise PremiumConfirmationError("case_required", 400, "Send a packet, booking number, or bond case.")

    packet = await _find_one("paperwork_packets", {"packet_id": packet_id}) if packet_id else None
    if packet is None and booking:
        packet = await _find_one("paperwork_packets", {"booking_number": booking})
    if packet and not booking:
        booking = str(packet.get("booking_number") or "").strip()
    if packet and not bond_case_id:
        bond_case_id = str(packet.get("bond_case_id") or "").strip()

    bond_case = await _find_one("bond_cases", {"bond_case_id": bond_case_id}) if bond_case_id else None
    if bond_case is None and booking:
        bond_case = await _find_one("bond_cases", {"booking_number": booking})
    if bond_case and not booking:
        booking = str(bond_case.get("booking_number") or "").strip()

    active = await _find_one("active_bonds", {"booking_number": booking}) if booking else None

    rows = [("paperwork_packets", packet), ("bond_cases", bond_case), ("active_bonds", active)]
    if not any(doc for _name, doc in rows):
        raise PremiumConfirmationError("case_not_found", 404, "No packet or bond matches that case.")

    await _require_identity(
        packet=packet,
        bond_case=bond_case,
        active=active,
        booking=booking,
        packet_id=packet_id,
        bond_case_id=bond_case_id,
    )

    bond_amount = None
    for doc in (active, bond_case, packet):
        if isinstance(doc, dict) and not _blank(doc.get("bond_amount")):
            bond_amount = doc.get("bond_amount")
            break
        if isinstance(doc, dict) and not _blank(doc.get("amount")):
            bond_amount = doc.get("amount")
            break
    reason = str(body.get("reason") or "").strip()
    below = premium < filed_floor_cents(bond_amount)
    if below and not reason:
        raise PremiumConfirmationError(
            "premium_below_filed_rate",
            409,
            "Premium is under the filed rate (10% of the bond, minimum $100). Send a reason to confirm it.",
        )

    fingerprint = _fingerprint(premium, down, financed, count, frequency if financed else "")
    existing = next((doc for _name, doc in rows if confirmation_complete(doc)), None)
    changed = False
    if existing is not None:
        prior_fp = str(existing.get(FINGERPRINT_FIELD) or "")
        if prior_fp == fingerprint or (
            not prior_fp
            and non_negative_cents(existing.get(PREMIUM_CONFIRMED_AMOUNT_FIELD)) == premium
            and non_negative_cents(existing.get(DOWN_FIELD)) == down
        ):
            plan = await _ensure_plan(
                booking=booking,
                packet=packet,
                bond_case=bond_case,
                active=active,
                actor=actor,
                premium=premium,
                down=down,
                financed=financed,
                count=count,
                frequency=frequency if financed else "",
                fingerprint=fingerprint,
                create=financed > 0,
            )
            return {
                "ok": True,
                "idempotent": True,
                "premium_confirmed_amount": cents_to_amount(premium),
                "down_payment_amount": cents_to_amount(down),
                "financed_balance": cents_to_amount(financed),
                "premium_confirmed_by": existing.get(PREMIUM_CONFIRMED_BY_FIELD),
                "plan_id": (plan or {}).get("plan_id"),
                "booking_number": booking,
            }
        if not reason:
            raise PremiumConfirmationError(
                "premium_change_requires_reason",
                409,
                "This case already has a confirmed premium. Send a reason to change it.",
            )
        changed = True
        if financed > 0 and booking:
            started = await get_collection("payment_plans").find_one(
                _scope({"booking_number": booking, "source": PLAN_SOURCE, "status": {"$in": ["active", "paid"]}})
            )
            if isinstance(started, dict) and _plan_has_collections(started):
                raise PremiumConfirmationError(
                    "plan_payments_started",
                    409,
                    "Payments have already posted to this plan. The financed balance was not changed.",
                )

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    plan = await _commit_confirmation(
        rows=rows,
        packet=packet,
        bond_case=bond_case,
        active=active,
        actor=actor,
        booking=booking,
        packet_id=packet_id,
        bond_case_id=bond_case_id,
        premium=premium,
        down=down,
        financed=financed,
        count=count,
        frequency=frequency,
        fingerprint=fingerprint,
        changed=changed,
        reason=reason,
        below=below,
        now=now,
        now_iso=now_iso,
    )

    logger.info(
        "[premium_confirm] confirmed booking=%s changed=%s below_floor=%s plan=%s",
        booking or bond_case_id or packet_id,
        changed,
        below,
        bool(plan),
    )
    return {
        "ok": True,
        "idempotent": False,
        "changed": changed,
        "premium_confirmed_amount": cents_to_amount(premium),
        "premium_confirmed_at": now_iso,
        "premium_confirmed_by": actor,
        "down_payment_amount": cents_to_amount(down),
        "financed_balance": cents_to_amount(financed),
        "installments": count,
        "plan_id": (plan or {}).get("plan_id"),
        "booking_number": booking,
        "below_filed_rate": below,
    }


async def _ensure_plan(
    *,
    booking: str,
    packet: Optional[dict],
    bond_case: Optional[dict],
    active: Optional[dict],
    actor: str,
    premium: int,
    down: int,
    financed: int,
    count: int,
    frequency: str,
    fingerprint: str,
    create: bool,
    replace: bool = False,
    reason: str = "",
    session=None,
) -> Optional[dict]:
    if not create:
        return None
    creator = load_plan_creator()
    if creator is not None:
        confirmation_id = "pcf-" + hashlib.sha256(
            f"{current_tenant_id() or ''}|{booking}|{fingerprint}".encode()
        ).hexdigest()[:32]
        case_id = str(
            (bond_case or {}).get("bond_case_id")
            or (packet or {}).get("bond_case_id")
            or ""
        )
        try:
            result = creator(
                tenant_id=current_tenant_id() or "",
                bond_case_id=case_id,
                confirmation_id=confirmation_id,
                financed=Decimal(cents_to_amount(financed)),
                installments=count,
                frequency=frequency,
                actor=actor,
                session=session,
            )
            if hasattr(result, "__await__"):
                result = await result
        except NotImplementedError as exc:
            raise PremiumConfirmationError(
                "plan_unavailable",
                503,
                "The payment plan could not be created. Nothing was saved.",
            ) from exc
        except PremiumConfirmationError:
            raise
        except Exception as exc:
            if _is_terms_conflict(exc) or isinstance(exc, ValueError):
                raise
            logger.warning("[premium_confirm] plan creator failed err_type=%s", type(exc).__name__)
            raise PremiumConfirmationError("plan_write_failed", 503) from exc
        if not isinstance(result, dict) or not result.get("plan_id"):
            raise PremiumConfirmationError(
                "plan_unavailable",
                503,
                "The payment plan could not be created. Nothing was saved.",
            )
        return result
    if not booking:
        raise PremiumConfirmationError("booking_required_for_plan", 400, "A financed plan needs a booking number.")
    plans = get_collection("payment_plans")
    query = _scope({"booking_number": booking, "source": PLAN_SOURCE, "status": {"$in": ["active", "paid"]}})
    try:
        existing = await plans.find_one(query, **_session_kwargs(session))
    except Exception as exc:
        logger.warning("[premium_confirm] plan lookup failed err_type=%s", type(exc).__name__)
        raise PremiumConfirmationError("plan_lookup_failed", 503) from exc
    now = datetime.now(timezone.utc)
    installments = _schedule(financed, count, frequency, now)
    if existing:
        if str(existing.get(FINGERPRINT_FIELD) or "") == fingerprint and not replace:
            return existing
        if _plan_has_collections(existing):
            raise PremiumConfirmationError(
                "plan_payments_started",
                409,
                "Payments have already posted to this plan. The financed balance was not changed.",
            )
        if not replace and str(existing.get(FINGERPRINT_FIELD) or "") not in ("", fingerprint):
            raise PremiumConfirmationError("premium_change_requires_reason", 409)
        try:
            await plans.update_one(
                {"plan_id": existing.get("plan_id")},
                {"$set": {
                    "total_premium": cents_to_amount(premium),
                    "total_premium_cents": premium,
                    "down_payment": cents_to_amount(down),
                    "down_payment_cents": down,
                    "balance_remaining": cents_to_amount(financed),
                    "balance_remaining_cents": financed,
                    FINANCED_FIELD: cents_to_amount(financed),
                    "installment_count": count,
                    "installments": installments,
                    "installment_amount": installments[0]["amount"] if installments else cents_to_amount(0),
                    "frequency": frequency,
                    "next_due_date": installments[0]["due_date"] if installments else "",
                    FINGERPRINT_FIELD: fingerprint,
                    "updated_at": now.isoformat(),
                    "updated_by": actor,
                    "change_reason": reason,
                }},
                **_session_kwargs(session),
            )
        except Exception as exc:
            logger.warning("[premium_confirm] plan update failed err_type=%s", type(exc).__name__)
            raise PremiumConfirmationError("plan_write_failed", 503) from exc
        existing.update({
            "balance_remaining_cents": financed,
            "installments": installments,
            FINGERPRINT_FIELD: fingerprint,
        })
        return existing

    source_doc = active or bond_case or packet or {}
    plan = {
        "plan_id": uuid.uuid4().hex[:12],
        "booking_number": booking,
        "packet_id": (packet or {}).get("packet_id") or "",
        "bond_case_id": (bond_case or {}).get("bond_case_id") or "",
        "defendant_name": str(source_doc.get("defendant_name") or source_doc.get("Defendant_Name") or ""),
        "surety_id": str(source_doc.get("surety_id") or source_doc.get("surety") or ""),
        "total_premium": cents_to_amount(premium),
        "total_premium_cents": premium,
        "down_payment": cents_to_amount(down),
        "down_payment_cents": down,
        "balance_remaining": cents_to_amount(financed),
        "balance_remaining_cents": financed,
        FINANCED_FIELD: cents_to_amount(financed),
        "installment_count": count,
        "installments": installments,
        "installment_amount": installments[0]["amount"],
        "frequency": frequency,
        "next_due_date": installments[0]["due_date"],
        "payments_made": 0,
        "total_paid": "0.00",
        "total_paid_cents": 0,
        "status": "active",
        "source": PLAN_SOURCE,
        FINGERPRINT_FIELD: fingerprint,
        "delinquent": False,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "created_by": actor,
    }
    if multi_tenant_enabled():
        plan["tenant_id"] = current_tenant_id()
    # The down payment is invoiced. It is not marked paid here.
    try:
        await plans.insert_one(plan, **_session_kwargs(session))
    except Exception as exc:
        logger.warning("[premium_confirm] plan insert failed err_type=%s", type(exc).__name__)
        raise PremiumConfirmationError("plan_write_failed", 503) from exc
    return plan


def _plan_has_collections(plan: dict) -> bool:
    paid = non_negative_cents(plan.get("total_paid"))
    if paid and paid > 0:
        return True
    if plan.get("collected_transaction_ids"):
        return True
    for row in plan.get("installments") or []:
        if isinstance(row, dict) and str(row.get("status") or "") in {"paid", "applied"}:
            return True
    return False
