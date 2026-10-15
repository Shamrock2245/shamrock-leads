"""
Route a SwipeSimple paid, failed, or refunded event onto one payment plan.

The down-payment invoice uses the booking number as its reference. An
installment invoice uses ``{booking}-I{n}``. Only the installment shape
posts to the plan. The amount is the installment amount, and the balance
move is ``apply_plan_payment`` / ``reverse_plan_payment`` from #184/#185.

A booking that does not match the plan returns 409 and writes nothing.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from fastapi.responses import JSONResponse

from dashboard.extensions import get_collection
from dashboard.routers.payment_plans import (
    _amount_cents,
    apply_plan_payment,
    note_plan_payment_failure,
    reverse_plan_payment,
)
from dashboard.tenancy.context import current_tenant_id
from dashboard.tenancy.flag import multi_tenant_enabled

logger = logging.getLogger(__name__)

_INSTALLMENT_REF = re.compile(r"^(.+)-I(\d{1,2})$")

_PAID_EVENTS = frozenset({
    "payment.completed", "payment.approved", "payment.paid",
    "payment.captured", "payment.settled", "payment.success",
})
_PAID_STATUS = frozenset({
    "approved", "paid", "completed", "complete", "success",
    "succeeded", "captured", "settled",
})
_FAIL_EVENTS = frozenset({"payment.failed", "payment.declined"})
_FAIL_STATUS = frozenset({"declined", "failed", "error", "voided"})
_REFUND_EVENTS = frozenset({"payment.refunded", "payment.reversed"})
_REFUND_STATUS = frozenset({"refunded", "reversed"})


def _split_ref(value: str) -> Optional[tuple[str, int]]:
    match = _INSTALLMENT_REF.fullmatch((value or "").strip())
    if not match:
        return None
    number = int(match.group(2))
    if number <= 0:
        return None
    return match.group(1), number


def _custom(data: dict) -> dict:
    raw = data.get("custom_fields")
    return raw if isinstance(raw, dict) else {}


def _event_bits(data: dict) -> dict:
    """Normalize booking, plan id, and installment number. No writes."""
    custom = _custom(data)
    reference = str(
        custom.get("reference_id")
        or data.get("reference_id")
        or data.get("invoice_number")
        or ""
    ).strip()
    booking = str(custom.get("booking_number") or data.get("booking_number") or "").strip()
    plan_id = str(custom.get("plan_id") or data.get("plan_id") or "").strip()
    raw_n = custom.get("installment_n", custom.get("installment", data.get("installment_n")))
    split = _split_ref(reference) or _split_ref(booking)
    if split:
        ref_booking, ref_n = split
        if not reference:
            reference = f"{ref_booking}-I{ref_n}"
        if not booking or _split_ref(booking):
            booking = ref_booking
        if raw_n in (None, ""):
            raw_n = ref_n
    number = None
    if raw_n not in (None, ""):
        try:
            number = int(raw_n)
        except (TypeError, ValueError):
            number = None
        if number is not None and number <= 0:
            number = None
    return {
        "booking": booking,
        "plan_id": plan_id,
        "reference": reference,
        "installment_n": number,
        "reference_booking": split[0] if split else "",
    }


def _kind(data: dict) -> str:
    event = str(data.get("event_type") or "").strip().lower()
    status = str(data.get("status") or "").strip().lower()
    if event in _REFUND_EVENTS or status in _REFUND_STATUS:
        return "refund"
    if event in _FAIL_EVENTS or status in _FAIL_STATUS:
        return "failed"
    if event in _PAID_EVENTS or status in _PAID_STATUS:
        return "paid"
    return ""


def _scope(query: dict) -> Optional[dict]:
    if not multi_tenant_enabled():
        return query
    tenant = current_tenant_id()
    if not tenant:
        return None
    return {**query, "tenant_id": tenant}


def _mismatch(plan: dict, bits: dict) -> bool:
    plan_booking = str(plan.get("booking_number") or "").strip()
    event_booking = bits["booking"]
    ref_booking = bits["reference_booking"]
    if event_booking and plan_booking and event_booking != plan_booking:
        return True
    if ref_booking and plan_booking and ref_booking != plan_booking:
        return True
    return False


def _refuse_booking() -> JSONResponse:
    return JSONResponse(
        {
            "error": "booking_mismatch",
            "message": "This payment's booking number does not match the payment plan. Nothing was posted.",
        },
        status_code=409,
    )


async def _load_plan(bits: dict):
    """Return (plan, installment_n), 'ambiguous', 'no_tenant', or None."""
    plans = get_collection("payment_plans")
    if bits["plan_id"]:
        query = _scope({"plan_id": bits["plan_id"]})
        if query is None:
            return "no_tenant"
        return await plans.find_one(query), bits["installment_n"]
    if not bits["reference_booking"]:
        return None
    query = _scope({"booking_number": bits["reference_booking"]})
    if query is None:
        return "no_tenant"
    matched = []
    cursor = plans.find(query)
    async for plan in cursor:
        rows = plan.get("installments") or []
        if bits["installment_n"] is None:
            matched.append(plan)
            continue
        for row in rows:
            if isinstance(row, dict):
                try:
                    row_n = int(row.get("n") or 0)
                except (TypeError, ValueError):
                    continue
                if row_n == bits["installment_n"]:
                    matched.append(plan)
                    break
    if len(matched) == 1:
        return matched[0], bits["installment_n"]
    if len(matched) > 1:
        return "ambiguous"
    return None


def _installment_amount_ok(plan: dict, number: Optional[int], amount) -> bool:
    if number is None:
        return True
    for row in plan.get("installments") or []:
        if not isinstance(row, dict):
            continue
        try:
            row_n = int(row.get("n") or 0)
        except (TypeError, ValueError):
            continue
        if row_n != number:
            continue
        expected = row.get("amount_cents")
        if expected is None:
            return True
        got = _amount_cents(amount)
        return got is not None and int(got) == int(expected)
    return True


async def post_gateway_event(data: dict, *, actor: str):
    """Post one gateway event onto a plan, or report that it is not an installment.

    Returns ``{"handled": False}`` for down-payment and other non-plan events
    so the caller keeps its existing payment log. A booking mismatch is 409
    and does not write a payment, a claim, or an audit.
    """
    if not isinstance(data, dict):
        return {"handled": False}
    bits = _event_bits(data)
    is_installment = bool(bits["reference_booking"] or bits["installment_n"])
    if not is_installment and not bits["plan_id"]:
        return {"handled": False}
    if bits["plan_id"] and not is_installment:
        # A plan id on the down-payment invoice must not consume financed balance.
        return {"handled": False}
    kind = _kind(data)
    if not kind:
        return {"handled": False}

    loaded = await _load_plan(bits)
    if loaded == "no_tenant":
        return JSONResponse({"error": "tenant_required"}, status_code=403)
    if loaded == "ambiguous":
        return JSONResponse(
            {"error": "plan_ambiguous", "message": "More than one plan matches this installment."},
            status_code=409,
        )
    if loaded is None:
        return {"handled": False}
    plan, number = loaded
    if not plan:
        return {"handled": False}
    if _mismatch(plan, bits):
        logger.info(
            "[plan_payment] booking mismatch plan_id=%s installment=%s",
            plan.get("plan_id"),
            number,
        )
        return _refuse_booking()
    if kind == "paid" and not _installment_amount_ok(plan, number, data.get("amount")):
        return JSONResponse(
            {
                "error": "installment_amount_mismatch",
                "message": "The paid amount is not this installment. The balance was not changed.",
            },
            status_code=409,
        )

    payload = dict(data)
    if number is not None:
        payload["installment_n"] = number
    payload.setdefault("method", "Card")
    plan_id = plan.get("plan_id")
    if kind == "refund":
        result = await reverse_plan_payment(plan_id, payload, actor)
    elif kind == "failed":
        result = await note_plan_payment_failure(plan_id, payload, actor)
    else:
        result = await apply_plan_payment(plan_id, payload, actor)
    if isinstance(result, JSONResponse):
        return result
    body = dict(result or {})
    body["handled"] = True
    body["plan_id"] = plan_id
    return body
