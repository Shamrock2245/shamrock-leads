"""Staff premium confirmation at Write Bond.

The writing agent confirms the premium, the down payment, and the financed
balance once. DocuSeal completion then invoices the down payment and a
payment plan can be created for the financed balance. Nothing here sends a
message or charges a card.

Invoicing Desk owns plan creation (`dashboard.services.payment_plan_creation.
ensure_payment_plan_for_confirmation`, queued behind #187). This module does
not create that file. The import is lazy, so either PR can land first. When
the function is missing, or it raises ``NotImplementedError``, a financed
balance fails closed with 503 and this route writes nothing.

Pending Brendan decisions live in the constants below. Change them here only.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from fastapi import Request

from dashboard.tenancy.context import current_tenant_id
from dashboard.tenancy.flag import multi_tenant_enabled

logger = logging.getLogger(__name__)

# ── Brendan decisions (one place) ──────────────────────────────────────────
# Who may confirm: the writing agent (session role ``sub_agent``) and
# staff / admin / god_admin. Recovery may not. Flip this set when the
# "sub-agent" decision changes.
CONFIRM_ROLES = frozenset({"god_admin", "admin", "staff", "sub_agent"})

# Florida filed premium: 10% of the bond, and at least $100. A premium
# below that requires a reason. Ceiling to the cent so a fractional 10%
# is never treated as already met.
FILED_RATE_PERCENT = 10
FILED_MINIMUM_CENTS = 10_000

FREQUENCIES = frozenset({"weekly", "biweekly", "monthly"})
MAX_INSTALLMENTS = 120

# New fields. The trio names stay the constants in packet_payment_link_service.
DOWN_FIELD = "premium_confirmed_down"
FINANCED_FIELD = "premium_confirmed_financed"
INSTALLMENTS_FIELD = "premium_confirmed_installments"
FREQUENCY_FIELD = "premium_confirmed_frequency"
CONFIRMATION_ID_FIELD = "premium_confirmation_id"
HISTORY_FIELD = "premium_confirmation_history"

AUDIT_EVENT = "premium_confirmed"
PLAN_MODULE = "dashboard.services.payment_plan_creation"
PLAN_FUNC = "ensure_payment_plan_for_confirmation"

_COLLECTIONS = ("paperwork_packets", "bond_cases", "active_bonds")


class PremiumConfirmError(Exception):
    """Mapped to an HTTP response by the route. Nothing was committed."""

    def __init__(self, status: int, error: str, message: str):
        self.status = status
        self.error = error
        self.message = message
        super().__init__(error)


class PlanTermsConflict(Exception):
    """Financed terms disagree with a plan that already exists.

    Invoicing's class, when that module lands, is recognized by this name
    so this file does not import theirs.
    """


class PlanCreationUnavailable(Exception):
    """No plan function, or Mongo cannot run the confirmation transaction."""


def filed_minimum_cents(bond_cents: int) -> int:
    """10% of the bond in cents, at least $100, rounded up to the cent."""
    ten = (bond_cents * FILED_RATE_PERCENT + 99) // 100
    return max(ten, FILED_MINIMUM_CENTS)


def money_cents(value: Any, *, allow_zero: bool = False) -> int | None:
    """Exact integer cents via the #180 parser (``payment_plans._amount_cents``).

    Positive amounts are that function unchanged. Zero is accepted only when
    ``allow_zero`` is set and the same Decimal construction is an exact zero.
    Sub-cent values, negatives, and non-numbers are ``None``.
    """
    from dashboard.routers.payment_plans import _amount_cents

    if isinstance(value, bool) or value is None:
        return None
    parsed = _amount_cents(value)
    if parsed is not None:
        return parsed
    if not allow_zero:
        return None
    text = str(value).strip()
    if not text or len(text) > 64:
        return None
    try:
        dec = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not dec.is_finite():
        return None
    _sign, digits, _exponent = dec.as_tuple()
    if any(digit != 0 for digit in digits):
        return None
    return 0


def cents_to_amount(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def confirmation_id_for(
    *,
    tenant_id: str,
    bond_case_id: str,
    premium_cents: int,
    down_cents: int,
    financed_cents: int,
    installments: int,
    frequency: str,
) -> str:
    """Stable id so a replay of the same terms does not create a second plan."""
    raw = "|".join((
        tenant_id or "",
        bond_case_id,
        str(premium_cents),
        str(down_cents),
        str(financed_cents),
        str(installments),
        frequency,
    ))
    digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
    return f"pcf-{digest}"


def load_plan_creator():
    """The Invoicing function, or None when that module is not on this branch.

    A present stub that raises ``NotImplementedError`` is still returned.
    The caller maps that exception to 503 inside the transaction so the
    confirmation write rolls back with it.
    """
    import importlib

    try:
        module = importlib.import_module(PLAN_MODULE)
    except ImportError:
        return None
    fn = getattr(module, PLAN_FUNC, None)
    if not callable(fn):
        return None
    return fn


def confirmation_is_complete(doc: Mapping[str, Any] | None) -> bool:
    """True when the staff trio is set on this document."""
    if not isinstance(doc, Mapping):
        return False
    from dashboard.services.packet_payment_link_service import confirmed_premium

    return confirmed_premium(doc, None) > 0


def confirmed_down_amount(doc: Mapping[str, Any] | None) -> Decimal | None:
    """Down payment on a confirmed document, including ``0.00``.

    ``None`` when the trio is missing or the down payment is not exact cents.
    """
    if not confirmation_is_complete(doc):
        return None
    cents = money_cents((doc or {}).get(DOWN_FIELD), allow_zero=True)
    if cents is None:
        return None
    return Decimal(cents_to_amount(cents))


def _case_filter(bond_case_id: str) -> dict:
    return {"$or": [
        {"bond_case_id": bond_case_id},
        {"Bond_Case_ID": bond_case_id},
    ]}


def _booking_filter(booking_number: str) -> dict:
    return {"$or": [
        {"booking_number": booking_number},
        {"Booking_Number": booking_number},
    ]}


async def _rows(name: str, filt: dict) -> list[dict]:
    from dashboard.extensions import get_collection

    cursor = get_collection(name).find(filt)
    if hasattr(cursor, "to_list"):
        rows = await cursor.to_list(length=50)
    elif hasattr(cursor, "__aiter__"):
        rows = [row async for row in cursor]
    else:
        rows = list(cursor or [])
    return [row for row in rows if isinstance(row, Mapping)]


def _actor(request: Request) -> str:
    """Session user only. Body fields are never read here."""
    from dashboard.auth.pin_middleware import get_session_from_request

    sess = get_session_from_request(request)
    if not sess or not sess.get("auth"):
        raise PremiumConfirmError(401, "auth_required", "Sign in to confirm the premium.")
    role = str(sess.get("role") or "")
    if role not in CONFIRM_ROLES:
        raise PremiumConfirmError(
            403,
            "staff_required",
            "Confirming a premium requires an agent or a staff session.",
        )
    actor = str(sess.get("email") or sess.get("agent_name") or "").strip()
    if not actor:
        raise PremiumConfirmError(
            403,
            "staff_required",
            "Confirming a premium requires an agent or a staff session.",
        )
    return actor


def _parse_body(data: Mapping[str, Any]) -> dict:
    premium = money_cents(data.get("premium"), allow_zero=False)
    down = money_cents(data.get("down"), allow_zero=True)
    financed = money_cents(data.get("financed"), allow_zero=True)
    if premium is None or down is None or financed is None:
        raise PremiumConfirmError(
            400,
            "invalid_amount",
            "Amount must be a number with at most two decimal places.",
        )
    if premium != down + financed:
        raise PremiumConfirmError(
            400,
            "premium_mismatch",
            "Premium must equal the down payment plus the financed balance, to the cent.",
        )
    installments = _installments(data.get("installments"), financed)
    if installments is None:
        raise PremiumConfirmError(
            400,
            "invalid_installments",
            "Installments must be a whole number (at least 1 when a balance is financed).",
        )
    frequency = str(data.get("frequency") or "").strip().lower()
    if frequency not in FREQUENCIES:
        raise PremiumConfirmError(
            400,
            "invalid_frequency",
            "Frequency must be weekly, biweekly, or monthly.",
        )
    reason = str(data.get("reason") or "").strip()
    bond_case_id = str(data.get("bond_case_id") or data.get("Bond_Case_ID") or "").strip()
    booking_number = str(data.get("booking_number") or data.get("Booking_Number") or "").strip()
    if not bond_case_id and not booking_number:
        raise PremiumConfirmError(
            400,
            "bond_case_required",
            "Send the bond case id for the premium you are confirming.",
        )
    return {
        "premium_cents": premium,
        "down_cents": down,
        "financed_cents": financed,
        "installments": installments,
        "frequency": frequency,
        "reason": reason,
        "bond_case_id": bond_case_id,
        "booking_number": booking_number,
    }


def _installments(value: Any, financed_cents: int) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            return None
        number = int(value)
    elif isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        return None
    if financed_cents == 0:
        return 0 if number == 0 else None
    if number < 1 or number > MAX_INSTALLMENTS:
        return None
    return number


def _bond_cents(docs: list[Mapping[str, Any]]) -> int | None:
    for doc in docs:
        for key in ("bond_amount", "Bond_Amount", "bond"):
            if key not in doc:
                continue
            cents = money_cents(doc.get(key), allow_zero=False)
            if cents is not None:
                return cents
    return None


def _terms_match(doc: Mapping[str, Any], parsed: Mapping[str, Any]) -> bool:
    if not confirmation_is_complete(doc):
        return False
    from dashboard.services.packet_payment_link_service import (
        PREMIUM_CONFIRMED_AMOUNT_FIELD,
    )

    if money_cents(doc.get(PREMIUM_CONFIRMED_AMOUNT_FIELD), allow_zero=False) != parsed["premium_cents"]:
        return False
    if money_cents(doc.get(DOWN_FIELD), allow_zero=True) != parsed["down_cents"]:
        return False
    if money_cents(doc.get(FINANCED_FIELD), allow_zero=True) != parsed["financed_cents"]:
        return False
    try:
        stored_installments = int(doc.get(INSTALLMENTS_FIELD))
    except (TypeError, ValueError):
        return False
    if stored_installments != parsed["installments"]:
        return False
    if str(doc.get(FREQUENCY_FIELD) or "") != parsed["frequency"]:
        return False
    return True


def _has_prior(doc: Mapping[str, Any]) -> bool:
    from dashboard.services.packet_payment_link_service import (
        PREMIUM_CONFIRMED_AMOUNT_FIELD,
    )

    return money_cents(doc.get(PREMIUM_CONFIRMED_AMOUNT_FIELD), allow_zero=False) is not None


def _history_entry(doc: Mapping[str, Any], now_iso: str, reason: str) -> dict:
    from dashboard.services.packet_payment_link_service import (
        PREMIUM_CONFIRMED_AMOUNT_FIELD,
        PREMIUM_CONFIRMED_AT_FIELD,
        PREMIUM_CONFIRMED_BY_FIELD,
    )

    return {
        PREMIUM_CONFIRMED_AMOUNT_FIELD: doc.get(PREMIUM_CONFIRMED_AMOUNT_FIELD),
        PREMIUM_CONFIRMED_AT_FIELD: doc.get(PREMIUM_CONFIRMED_AT_FIELD),
        PREMIUM_CONFIRMED_BY_FIELD: doc.get(PREMIUM_CONFIRMED_BY_FIELD),
        DOWN_FIELD: doc.get(DOWN_FIELD),
        FINANCED_FIELD: doc.get(FINANCED_FIELD),
        INSTALLMENTS_FIELD: doc.get(INSTALLMENTS_FIELD),
        FREQUENCY_FIELD: doc.get(FREQUENCY_FIELD),
        CONFIRMATION_ID_FIELD: doc.get(CONFIRMATION_ID_FIELD),
        "replaced_at": now_iso,
        "replaced_reason": reason,
    }


async def _resolve_case(parsed: Mapping[str, Any]) -> tuple[str, dict, dict, dict]:
    bond_case_id = str(parsed["bond_case_id"] or "")
    if not bond_case_id:
        rows = await _rows("bond_cases", _booking_filter(str(parsed["booking_number"])))
        ids = {
            str(row.get("bond_case_id") or row.get("Bond_Case_ID") or "").strip()
            for row in rows
        }
        ids.discard("")
        if len(ids) != 1:
            raise PremiumConfirmError(
                409 if len(ids) > 1 else 404,
                "ambiguous_bond_case" if len(ids) > 1 else "case_not_found",
                "This booking does not match one bond case." if len(ids) > 1
                else "No bond case was found for this confirmation.",
            )
        bond_case_id = next(iter(ids))
    found: dict[str, dict] = {}
    for name in _COLLECTIONS:
        rows = await _rows(name, _case_filter(bond_case_id))
        if len(rows) != 1:
            raise PremiumConfirmError(
                404,
                "case_not_found",
                "No bond case was found for this confirmation.",
            )
        found[name] = rows[0]
    return bond_case_id, found["paperwork_packets"], found["bond_cases"], found["active_bonds"]


def _bond_data(bond_case_id: str, packet: Mapping[str, Any], case: Mapping[str, Any], active: Mapping[str, Any]) -> dict:
    source = case or active or packet
    name = str(
        source.get("indemnitor_name")
        or source.get("Indemnitor_Name")
        or (source.get("indemnitor") or {}).get("name")
        or ""
    ).strip()
    people = source.get("indemnitors")
    if not isinstance(people, list) or not people:
        people = [{"name": name, "role": "indemnitor"}] if name else []
    booking = str(
        source.get("booking_number")
        or source.get("Booking_Number")
        or packet.get("booking_number")
        or ""
    ).strip()
    packet_id = str(packet.get("packet_id") or packet.get("Packet_ID") or "").strip()
    return {
        "bond_case_id": bond_case_id,
        "booking_number": booking,
        "packet_id": packet_id,
        "indemnitor_name": name,
        "indemnitors": people,
        "county": source.get("county") or source.get("County") or "",
        "self_indemnitor": source.get("self_indemnitor"),
    }


async def _require_identity(bond_data: Mapping[str, Any]) -> None:
    from dashboard.services.identity_verification_service import (
        IndemnitorIdentityError,
        require_verified_indemnitors,
    )

    try:
        await require_verified_indemnitors(
            bond_data=bond_data,
            indemnitors=bond_data.get("indemnitors"),
            bond_case_id=str(bond_data.get("bond_case_id") or ""),
            booking_number=str(bond_data.get("booking_number") or ""),
            packet_id=str(bond_data.get("packet_id") or ""),
            staff_test_case=False,
        )
    except IndemnitorIdentityError as exc:
        raise PremiumConfirmError(422, exc.code, str(exc)) from exc


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


async def _apply_writes(bond_case_id: str, update: dict, session) -> None:
    from dashboard.extensions import get_collection

    kwargs = {"session": session} if session is not None else {}
    for name in _COLLECTIONS:
        result = await get_collection(name).update_many(_case_filter(bond_case_id), update, **kwargs)
        matched = getattr(result, "matched_count", None)
        if matched == 0:
            raise PremiumConfirmError(
                404,
                "case_not_found",
                "No bond case was found for this confirmation.",
            )


async def _insert_audit(doc: dict, session) -> None:
    from dashboard.extensions import get_collection
    from pymongo.errors import DuplicateKeyError

    kwargs = {"session": session} if session is not None else {}
    try:
        await get_collection("audit_events").insert_one(doc, **kwargs)
    except DuplicateKeyError:
        return


def _audit_doc(
    *,
    tenant_id: str,
    actor: str,
    bond_case_id: str,
    booking_number: str,
    confirmation_id: str,
    parsed: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    now: datetime,
    reason: str,
) -> dict:
    return {
        "_id": f"premium-confirmed:{tenant_id}:{bond_case_id}:{confirmation_id}",
        "event_type": AUDIT_EVENT,
        "action": AUDIT_EVENT,
        "entity_type": "bond_case",
        "entity_id": bond_case_id,
        "actor": actor,
        "agent_name": actor,
        "timestamp": now,
        "created_at": now,
        "bond_case_id": bond_case_id,
        "booking_number": booking_number,
        "confirmation_id": confirmation_id,
        "reason": reason or "premium confirmed",
        "confidence": 1.0,
        "old_state": dict(prior) if prior else None,
        "new_state": {
            "premium": cents_to_amount(parsed["premium_cents"]),
            "down": cents_to_amount(parsed["down_cents"]),
            "financed": cents_to_amount(parsed["financed_cents"]),
            "installments": parsed["installments"],
            "frequency": parsed["frequency"],
        },
    }


def _field_update(actor: str, parsed: Mapping[str, Any], confirmation_id: str, now_iso: str, prior_doc: Mapping[str, Any] | None, reason: str) -> dict:
    from dashboard.services.packet_payment_link_service import (
        PREMIUM_CONFIRMED_AMOUNT_FIELD,
        PREMIUM_CONFIRMED_AT_FIELD,
        PREMIUM_CONFIRMED_BY_FIELD,
    )

    fields = {
        PREMIUM_CONFIRMED_AMOUNT_FIELD: cents_to_amount(parsed["premium_cents"]),
        PREMIUM_CONFIRMED_AT_FIELD: now_iso,
        PREMIUM_CONFIRMED_BY_FIELD: actor,
        DOWN_FIELD: cents_to_amount(parsed["down_cents"]),
        FINANCED_FIELD: cents_to_amount(parsed["financed_cents"]),
        INSTALLMENTS_FIELD: parsed["installments"],
        FREQUENCY_FIELD: parsed["frequency"],
        CONFIRMATION_ID_FIELD: confirmation_id,
    }
    update: dict[str, Any] = {"$set": fields}
    if prior_doc is not None and _has_prior(prior_doc):
        update["$push"] = {HISTORY_FIELD: _history_entry(prior_doc, now_iso, reason)}
    return update


async def _commit(
    *,
    client,
    tenant_id: str,
    actor: str,
    bond_case_id: str,
    booking_number: str,
    parsed: Mapping[str, Any],
    prior_doc: Mapping[str, Any] | None,
    confirmation_id: str,
    creator,
) -> dict:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    reason = str(parsed["reason"] or "")
    update = _field_update(actor, parsed, confirmation_id, now_iso, prior_doc, reason)
    audit = _audit_doc(
        tenant_id=tenant_id,
        actor=actor,
        bond_case_id=bond_case_id,
        booking_number=booking_number,
        confirmation_id=confirmation_id,
        parsed=parsed,
        prior=_history_entry(prior_doc, now_iso, reason) if prior_doc is not None and _has_prior(prior_doc) else None,
        now=now,
        reason=reason,
    )
    plan_result: dict = {}

    async def work(session):
        nonlocal plan_result
        await _apply_writes(bond_case_id, update, session)
        await _insert_audit(audit, session)
        if parsed["financed_cents"] <= 0:
            return
        try:
            result = creator(
                tenant_id=tenant_id,
                bond_case_id=bond_case_id,
                confirmation_id=confirmation_id,
                financed=Decimal(cents_to_amount(parsed["financed_cents"])),
                installments=parsed["installments"],
                frequency=parsed["frequency"],
                actor=actor,
                session=session,
            )
            if hasattr(result, "__await__"):
                result = await result
        except NotImplementedError as exc:
            raise PlanCreationUnavailable() from exc
        if not isinstance(result, dict) or not result.get("plan_id"):
            raise PlanCreationUnavailable()
        plan_result = result

    if client is None:
        await work(None)
    else:
        started = client.start_session()
        if hasattr(started, "__await__"):
            started = await started
        async with started as session:
            await session.with_transaction(work)
    return plan_result


def _success(parsed: Mapping[str, Any], actor: str, confirmation_id: str, idempotent: bool, plan: dict | None) -> dict:
    body = {
        "success": True,
        "idempotent": idempotent,
        "confirmation_id": confirmation_id,
        "premium": cents_to_amount(parsed["premium_cents"]),
        "down": cents_to_amount(parsed["down_cents"]),
        "financed": cents_to_amount(parsed["financed_cents"]),
        "installments": parsed["installments"],
        "frequency": parsed["frequency"],
        "premium_confirmed_by": actor,
    }
    if plan:
        body["plan"] = {
            "plan_id": plan.get("plan_id"),
            "created": bool(plan.get("created")),
            "schedule": plan.get("schedule") or [],
        }
    return body


async def confirm_premium(request: Request) -> dict:
    """Confirm one case. Raises ``PremiumConfirmError`` and writes nothing on failure.

    Off when ``SAAS_MULTI_TENANT`` is off, so other agencies see no change.
    """
    if not multi_tenant_enabled():
        raise PremiumConfirmError(
            404,
            "premium_confirmation_disabled",
            "Premium confirmation is not enabled.",
        )
    import json

    try:
        data = json.loads(await request.body(), parse_float=Decimal)
    except Exception:
        raise PremiumConfirmError(400, "invalid_body", "Send a JSON body.") from None
    if not isinstance(data, dict):
        raise PremiumConfirmError(400, "invalid_body", "Send a JSON body.")

    actor = _actor(request)
    parsed = _parse_body(data)
    bond_case_id, packet, case, active = await _resolve_case(parsed)
    bond_data = _bond_data(bond_case_id, packet, case, active)
    await _require_identity(bond_data)

    bond_cents = _bond_cents([case, active, packet])
    if bond_cents is None:
        raise PremiumConfirmError(
            422,
            "bond_amount_unavailable",
            "The bond amount on this case is missing, so the filed rate cannot be checked.",
        )
    if parsed["premium_cents"] < filed_minimum_cents(bond_cents) and not parsed["reason"]:
        raise PremiumConfirmError(
            400,
            "reason_required",
            "This premium is below the Florida filed rate (10% of the bond, $100 minimum). Add a reason to confirm it.",
        )

    tenant_id = current_tenant_id() or ""
    confirmation_id = confirmation_id_for(
        tenant_id=tenant_id,
        bond_case_id=bond_case_id,
        premium_cents=parsed["premium_cents"],
        down_cents=parsed["down_cents"],
        financed_cents=parsed["financed_cents"],
        installments=parsed["installments"],
        frequency=parsed["frequency"],
    )
    if _terms_match(case, parsed):
        return _success(parsed, str(case.get("premium_confirmed_by") or actor), confirmation_id, True, None)

    if _has_prior(case) and not parsed["reason"]:
        raise PremiumConfirmError(
            409,
            "premium_change_requires_reason",
            "This case already has a confirmed premium. Send a reason to change it.",
        )

    creator = None
    client = None
    if parsed["financed_cents"] > 0:
        creator = load_plan_creator()
        if creator is None:
            raise PremiumConfirmError(
                503,
                "plan_unavailable",
                "The payment plan could not be created. Nothing was saved.",
            )
        client = await _transaction_client()
        if client is None:
            raise PremiumConfirmError(
                503,
                "transactions_unavailable",
                "Recording a confirmed premium needs a MongoDB replica set or Atlas. Nothing was saved.",
            )

    booking_number = str(bond_data.get("booking_number") or "")
    try:
        plan = await _commit(
            client=client,
            tenant_id=tenant_id,
            actor=actor,
            bond_case_id=bond_case_id,
            booking_number=booking_number,
            parsed=parsed,
            prior_doc=case if _has_prior(case) else None,
            confirmation_id=confirmation_id,
            creator=creator,
        )
    except PremiumConfirmError:
        raise
    except PlanCreationUnavailable:
        raise PremiumConfirmError(
            503,
            "plan_unavailable",
            "The payment plan could not be created. Nothing was saved.",
        ) from None
    except Exception as exc:
        if _is_terms_conflict(exc):
            raise PremiumConfirmError(
                409,
                "plan_terms_conflict",
                "A payment plan for this case already uses different terms. Nothing was changed.",
            ) from None
        if isinstance(exc, ValueError):
            message = str(exc).strip() or "The payment plan terms were rejected."
            if len(message) > 240:
                message = "The payment plan terms were rejected."
            raise PremiumConfirmError(400, "invalid_plan_terms", message) from None
        logger.warning("[premium_confirm] write failed error_type=%s", type(exc).__name__)
        raise PremiumConfirmError(
            503,
            "premium_confirm_failed",
            "The premium could not be confirmed. Nothing was saved.",
        ) from None

    return _success(parsed, actor, confirmation_id, False, plan or None)
