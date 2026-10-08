"""Agency SaaS billing. Stripe test mode only. No live charges.

Premium collection (SwipeSimple) is a different ledger and is not touched here.
Prices are read from environment price ids. This module does not invent amounts.
MRR is the sum of recurring cents stored from ``invoice.paid`` on the agency's
one subscription. Add-ons are items on that subscription, not a second customer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from dashboard.tenancy.constants import SHAMROCK_TENANT_ID
from dashboard.tenancy.context import bind_job_tenant, bind_platform_job, current_tenant_id
from dashboard.tenancy.flag import multi_tenant_enabled

PLAN_CATALOG = (
    {"code": "setup", "kind": "one_time", "label": "Setup fee", "env_price": "STRIPE_PRICE_SETUP"},
    {"code": "monthly", "kind": "recurring", "label": "Monthly plan", "env_price": "STRIPE_PRICE_MONTHLY"},
    {"code": "county_addon", "kind": "recurring", "label": "Per-county lead add-on", "env_price": "STRIPE_PRICE_COUNTY"},
    {"code": "state_addon", "kind": "recurring", "label": "Per-state lead add-on", "env_price": "STRIPE_PRICE_STATE"},
    {"code": "usage", "kind": "metered", "label": "Usage", "env_price": "STRIPE_PRICE_USAGE"},
)
_PLAN_BY_CODE = {row["code"]: row for row in PLAN_CATALOG}
_FULL_PERIOD_MRR = frozenset({"subscription_cycle", "subscription_create"})
_ACTIVE_MRR = frozenset({"active"})
_BLOCKED_ITEM_STATUS = frozenset({"past_due", "suspended", "canceled"})
_SIGNATURE_TOLERANCE_SECONDS = 300
_AUDIT_FIELDS = (
    "status",
    "mrr_cents",
    "plan_code",
    "failure_count",
    "customer_id",
    "subscription_id",
)


class BillingError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BillingDisabled(BillingError):
    def __init__(self):
        super().__init__("disabled")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def suspend_after_failures() -> int:
    raw = (os.getenv("STRIPE_SUSPEND_AFTER_FAILURES") or "2").strip()
    try:
        count = int(raw)
    except ValueError:
        return 2
    return count if count >= 1 else 2


def trial_days() -> int:
    raw = (os.getenv("STRIPE_TRIAL_DAYS") or "").strip()
    if not raw:
        return 0
    try:
        days = int(raw)
    except ValueError:
        return 0
    return days if 0 < days <= 60 else 0


def catalog() -> list[dict[str, Any]]:
    """Plan shape for the console. Amounts stay unset until a price id is configured."""
    rows = []
    for plan in PLAN_CATALOG:
        price_id = (os.getenv(plan["env_price"]) or "").strip()
        configured = price_id.startswith("price_")
        rows.append(
            {
                "code": plan["code"],
                "kind": plan["kind"],
                "label": plan["label"],
                "configured": configured,
                "price_id": price_id if configured else "",
                "amount_cents": None,
            }
        )
    return rows


def _api_key() -> str:
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if key.startswith("sk_live_") or key.startswith("rk_live_"):
        raise BillingError("live_key_rejected")
    if not key.startswith("sk_test_"):
        raise BillingError("stripe_test_key_required")
    return key


def _blank_billing() -> dict[str, Any]:
    return {
        "provider": "",
        "plan_code": "",
        "status": "unbilled",
        "mrr_cents": 0,
        "currency": "usd",
        "customer_id": "",
        "subscription_id": "",
        "trial_end": "",
        "card_brand": "",
        "card_last4": "",
        "failure_count": 0,
        "invoices": [],
        "event_ids": [],
        "items": [],
        "comped_by": "",
    }


def _billing_of(doc: dict | None) -> dict[str, Any]:
    current = dict(_blank_billing())
    stored = (doc or {}).get("billing") or {}
    if isinstance(stored, dict):
        current.update({key: stored.get(key, current[key]) for key in current})
    current["invoices"] = list(current.get("invoices") or [])
    current["event_ids"] = list(current.get("event_ids") or [])
    current["items"] = [dict(row) for row in (current.get("items") or []) if isinstance(row, dict)]
    try:
        current["mrr_cents"] = int(current.get("mrr_cents") or 0)
    except (TypeError, ValueError):
        current["mrr_cents"] = 0
    try:
        current["failure_count"] = int(current.get("failure_count") or 0)
    except (TypeError, ValueError):
        current["failure_count"] = 0
    return current


def public_billing_view(doc: dict) -> dict[str, Any]:
    billing = _billing_of(doc)
    status = billing["status"] or "unbilled"
    return {
        "tenant_id": doc.get("tenant_id"),
        "legal_name": doc.get("legal_name"),
        "plan_code": billing["plan_code"],
        "status": status,
        "mrr_cents": billing["mrr_cents"] if status in _ACTIVE_MRR else 0,
        "stored_mrr_cents": billing["mrr_cents"],
        "currency": billing["currency"] or "usd",
        "trial_end": billing["trial_end"],
        "past_due": status == "past_due",
        "suspended": status == "suspended",
        "card_brand": billing["card_brand"],
        "card_last4": billing["card_last4"],
        "failure_count": billing["failure_count"],
        "invoices": [
            {
                "id": row.get("id"),
                "status": row.get("status"),
                "amount_cents": row.get("amount_cents"),
                "hosted_url": row.get("hosted_url") or "",
            }
            for row in billing["invoices"]
            if isinstance(row, dict)
        ],
    }


def mrr_cents_for(doc: dict) -> int:
    view = public_billing_view(doc)
    return int(view["mrr_cents"] or 0)


def _remember_invoice(billing: dict, invoice: dict) -> None:
    invoice_id = str(invoice.get("id") or "").strip()
    if not invoice_id:
        return
    amount = invoice.get("amount_paid")
    if amount is None:
        amount = invoice.get("amount_due")
    try:
        amount_cents = int(amount or 0)
    except (TypeError, ValueError):
        amount_cents = 0
    url = str(invoice.get("hosted_invoice_url") or "")
    if url and not url.startswith("https://"):
        url = ""
    row = {
        "id": invoice_id,
        "status": str(invoice.get("status") or ""),
        "amount_cents": amount_cents,
        "hosted_url": url,
    }
    invoices = [item for item in billing["invoices"] if item.get("id") != invoice_id]
    invoices.append(row)
    billing["invoices"] = invoices[-12:]
    card = ((invoice.get("payment_method_details") or {}).get("card") or {})
    brand = str(card.get("brand") or "").strip().lower()
    last4 = str(card.get("last4") or "").strip()
    if brand and len(brand) <= 20:
        billing["card_brand"] = brand
    if len(last4) == 4 and last4.isdigit():
        billing["card_last4"] = last4


def _mark_event(billing: dict, event_id: str) -> bool:
    if not event_id:
        return False
    if event_id in billing["event_ids"]:
        return True
    billing["event_ids"] = (billing["event_ids"] + [event_id])[-50:]
    return False


def livemode_explicitly_enabled() -> bool:
    """Live Stripe events stay rejected unless an operator sets this on purpose."""
    return (os.getenv("STRIPE_ALLOW_LIVEMODE") or "").strip() == "1"


def _identity_conflict(billing: dict, *, customer: Any = "", subscription: Any = "") -> bool:
    """True when the event names a different Customer or subscription than the one we store."""
    incoming_customer = str(customer or "")
    incoming_subscription = str(subscription or "")
    stored_customer = str(billing.get("customer_id") or "")
    stored_subscription = str(billing.get("subscription_id") or "")
    if stored_customer and incoming_customer and stored_customer != incoming_customer:
        return True
    if stored_subscription and incoming_subscription and stored_subscription != incoming_subscription:
        return True
    return False


def _remember_identity(billing: dict, *, customer: Any = "", subscription: Any = "") -> None:
    incoming_customer = str(customer or "")
    incoming_subscription = str(subscription or "")
    if incoming_customer and not billing.get("customer_id"):
        billing["customer_id"] = incoming_customer
    elif incoming_customer and billing.get("customer_id") == incoming_customer:
        billing["customer_id"] = incoming_customer
    if incoming_subscription and not billing.get("subscription_id"):
        billing["subscription_id"] = incoming_subscription
    elif incoming_subscription and billing.get("subscription_id") == incoming_subscription:
        billing["subscription_id"] = incoming_subscription


def _subscription_mrr_cents(invoice: dict) -> int | None:
    """Sum full-period recurring lines. Prorations and one-time lines are not MRR."""
    lines = invoice.get("lines") or {}
    data = lines.get("data") if isinstance(lines, dict) else None
    if not isinstance(data, list) or not data:
        return None
    total = 0
    saw = False
    for line in data:
        if not isinstance(line, dict) or line.get("proration") is True:
            continue
        price = line.get("price") if isinstance(line.get("price"), dict) else {}
        recurring = bool(price.get("recurring")) or line.get("type") == "subscription"
        if not recurring:
            continue
        saw = True
        try:
            total += int(line.get("amount") or 0)
        except (TypeError, ValueError):
            continue
    return total if saw else None


def _audit_slice(billing: dict) -> dict[str, Any]:
    return {key: billing.get(key) for key in _AUDIT_FIELDS}


def reduce_billing_event(doc: dict, event: dict, *, suspend_after: int | None = None) -> tuple[dict, str]:
    """Apply one verified Stripe event. Returns the billing dict and an outcome token.

    ``noted`` means the event was recorded and did not change access or MRR
    (a different subscription must not cancel or reactivate this agency).
    """
    if doc.get("tenant_id") == SHAMROCK_TENANT_ID:
        return _billing_of(doc), "ignored"
    billing = _billing_of(doc)
    if billing["status"] == "comped":
        return billing, "ignored"
    event_id = str(event.get("id") or "")
    if _mark_event(billing, event_id):
        return billing, "duplicate"
    kind = str(event.get("type") or "")
    obj = ((event.get("data") or {}).get("object") or {})
    if not isinstance(obj, dict):
        return billing, "ignored"
    threshold = suspend_after if suspend_after is not None else suspend_after_failures()
    if kind == "checkout.session.completed":
        if _identity_conflict(billing, customer=obj.get("customer"), subscription=obj.get("subscription")):
            return billing, "noted"
        billing["provider"] = "stripe_test"
        _remember_identity(billing, customer=obj.get("customer"), subscription=obj.get("subscription"))
        meta = obj.get("metadata") or {}
        plan_code = str(meta.get("plan_code") or billing["plan_code"] or "")
        if plan_code in _PLAN_BY_CODE:
            billing["plan_code"] = plan_code
        if obj.get("mode") == "subscription" and obj.get("status") == "complete":
            billing["status"] = "trialing" if meta.get("trial") == "1" else billing["status"] or "unbilled"
            if billing["status"] == "unbilled":
                billing["status"] = "trialing" if meta.get("trial") == "1" else "unbilled"
        return billing, "applied"
    if kind == "invoice.paid":
        if _identity_conflict(billing, customer=obj.get("customer"), subscription=obj.get("subscription")):
            return billing, "noted"
        _remember_invoice(billing, obj)
        billing["provider"] = "stripe_test"
        _remember_identity(billing, customer=obj.get("customer"), subscription=obj.get("subscription"))
        billing["failure_count"] = 0
        billing["status"] = "active"
        mrr = _subscription_mrr_cents(obj)
        if mrr is None and obj.get("subscription") and str(obj.get("billing_reason") or "") in _FULL_PERIOD_MRR:
            try:
                mrr = int(obj.get("amount_paid") or 0)
            except (TypeError, ValueError):
                mrr = None
        if mrr is not None:
            billing["mrr_cents"] = mrr
        billing["currency"] = str(obj.get("currency") or billing["currency"] or "usd")
        return billing, "applied"
    if kind == "invoice.payment_failed":
        if _identity_conflict(billing, customer=obj.get("customer"), subscription=obj.get("subscription")):
            return billing, "noted"
        _remember_invoice(billing, obj)
        _remember_identity(billing, customer=obj.get("customer"), subscription=obj.get("subscription"))
        billing["failure_count"] = int(billing["failure_count"]) + 1
        billing["status"] = "suspended" if billing["failure_count"] >= threshold else "past_due"
        return billing, "applied"
    if kind == "customer.subscription.deleted":
        deleted = str(obj.get("id") or "")
        if _identity_conflict(billing, customer=obj.get("customer"), subscription=deleted):
            return billing, "noted"
        stored = str(billing.get("subscription_id") or "")
        if stored and deleted and stored != deleted:
            return billing, "noted"
        billing["status"] = "canceled"
        billing["mrr_cents"] = 0
        billing["items"] = []
        if deleted:
            billing["subscription_id"] = deleted
        return billing, "applied"
    if kind == "customer.subscription.updated":
        if _identity_conflict(billing, customer=obj.get("customer"), subscription=obj.get("id")):
            return billing, "noted"
        stripe_status = str(obj.get("status") or "")
        _remember_identity(billing, customer=obj.get("customer"), subscription=obj.get("id"))
        if stripe_status == "trialing":
            billing["status"] = "trialing"
            billing["trial_end"] = str(obj.get("trial_end") or "")
        elif stripe_status == "active":
            billing["status"] = "active"
        elif stripe_status in {"past_due", "unpaid", "incomplete"}:
            billing["status"] = "past_due"
        elif stripe_status == "canceled":
            billing["status"] = "canceled"
            billing["mrr_cents"] = 0
            billing["items"] = []
        return billing, "applied"
    return billing, "ignored"


def _validated_price(plan_code: str, quantity: int) -> tuple[dict, str, int]:
    plan = _PLAN_BY_CODE.get(plan_code)
    if plan is None:
        raise BillingError("plan_invalid")
    price_id = (os.getenv(plan["env_price"]) or "").strip()
    if not price_id.startswith("price_"):
        raise BillingError("price_not_configured")
    _api_key()
    qty = 1
    if plan_code in {"county_addon", "state_addon"}:
        try:
            qty = int(quantity)
        except (TypeError, ValueError):
            raise BillingError("quantity_invalid")
        if qty < 1 or qty > 200:
            raise BillingError("quantity_invalid")
    return plan, price_id, qty


def checkout_form(
    *,
    tenant_id: str,
    plan_code: str,
    quantity: int,
    trial: bool,
    success_url: str,
    cancel_url: str,
    customer_id: str = "",
) -> dict[str, str]:
    if tenant_id == SHAMROCK_TENANT_ID:
        raise BillingError("internal_not_billed")
    plan, price_id, qty = _validated_price(plan_code, quantity)
    mode = "payment" if plan["kind"] == "one_time" else "subscription"
    form = {
        "mode": mode,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": str(qty),
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": tenant_id,
        "metadata[tenant_id]": tenant_id,
        "metadata[plan_code]": plan_code,
    }
    stored_customer = str(customer_id or "").strip()
    if stored_customer:
        form["customer"] = stored_customer
    if mode == "subscription":
        form["subscription_data[metadata][tenant_id]"] = tenant_id
        form["subscription_data[metadata][plan_code]"] = plan_code
    if trial:
        days = trial_days()
        if days <= 0:
            raise BillingError("trial_not_configured")
        if mode != "subscription":
            raise BillingError("trial_not_allowed")
        form["subscription_data[trial_period_days]"] = str(days)
        form["subscription_data[metadata][trial]"] = "1"
        form["metadata[trial]"] = "1"
    return form


def subscription_item_form(
    *,
    tenant_id: str,
    plan_code: str,
    quantity: int,
    subscription_id: str,
    subscription_item_id: str = "",
) -> dict[str, str]:
    """Add or update one item on the agency's existing subscription."""
    if tenant_id == SHAMROCK_TENANT_ID:
        raise BillingError("internal_not_billed")
    plan, price_id, qty = _validated_price(plan_code, quantity)
    if plan["kind"] == "one_time":
        raise BillingError("plan_invalid")
    sub = str(subscription_id or "").strip()
    if not sub:
        raise BillingError("subscription_required")
    form = {
        "price": price_id,
        "quantity": str(qty),
        "metadata[tenant_id]": tenant_id,
        "metadata[plan_code]": plan_code,
        "proration_behavior": "create_prorations",
    }
    item_id = str(subscription_item_id or "").strip()
    if item_id:
        form["subscription_item_id"] = item_id
    else:
        form["subscription"] = sub
    return form


def _post_stripe(url: str, form: dict[str, str], api_key: str) -> dict:
    """POST one Stripe test-mode form. Callers must have already rejected live keys."""
    if not str(api_key).startswith("sk_test_"):
        raise BillingError("stripe_test_key_required")
    data = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode())
    if not isinstance(body, dict):
        raise BillingError("checkout_rejected")
    return body


def post_checkout_session(form: dict[str, str], api_key: str) -> dict:
    """POST a Checkout Session. Callers must have already rejected live keys."""
    return _post_stripe("https://api.stripe.com/v1/checkout/sessions", form, api_key)


def post_subscription_item(form: dict[str, str], api_key: str) -> dict:
    """Add or update one subscription item. Does not open a second Checkout subscription."""
    item_id = str(form.get("subscription_item_id") or "").strip()
    body = {key: value for key, value in form.items() if key != "subscription_item_id"}
    if item_id:
        url = f"https://api.stripe.com/v1/subscription_items/{urllib.parse.quote(item_id, safe='')}"
    else:
        url = "https://api.stripe.com/v1/subscription_items"
    return _post_stripe(url, body, api_key)


def _accept_checkout_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme != "https" or parsed.netloc != "checkout.stripe.com":
        raise BillingError("checkout_url_rejected")
    return url


async def _tenants():
    from dashboard.extensions import get_collection

    return get_collection("tenants")


def _upsert_item(
    billing: dict,
    *,
    plan_code: str,
    price_id: str,
    quantity: int,
    subscription_item_id: str,
) -> None:
    items = [dict(row) for row in billing.get("items") or [] if isinstance(row, dict)]
    replaced = False
    for row in items:
        if row.get("plan_code") == plan_code or (
            price_id and row.get("price_id") == price_id
        ):
            row["plan_code"] = plan_code
            row["price_id"] = price_id
            row["quantity"] = quantity
            row["subscription_item_id"] = subscription_item_id
            replaced = True
            break
    if not replaced:
        items.append(
            {
                "plan_code": plan_code,
                "price_id": price_id,
                "quantity": quantity,
                "subscription_item_id": subscription_item_id,
            }
        )
    billing["items"] = items


async def _load_agency(tenant_id: str) -> dict | None:
    with bind_platform_job("agency_billing"):
        col = await _tenants()
        return await col.find_one({"tenant_id": tenant_id})


async def _save_billing(tenant_id: str, billing: dict) -> None:
    with bind_platform_job("agency_billing"):
        col = await _tenants()
        await col.update_one(
            {"tenant_id": tenant_id},
            {"$set": {"billing": billing, "updated_at": _now()}},
        )


async def _write_billing_audit(
    *,
    tenant_id: str,
    actor: str,
    reason: str,
    action: str,
    old_state: dict,
    new_state: dict,
) -> None:
    """Immutable billing transition. Written on the agency, not the platform directory."""
    from dashboard.extensions import get_collection

    event = {
        "event_id": str(uuid.uuid4()),
        "entity_type": "tenant_billing",
        "entity_id": tenant_id,
        "action": action,
        "actor": actor,
        "reason": reason,
        "old_state": old_state,
        "new_state": new_state,
        "timestamp": _now(),
    }
    with bind_job_tenant(tenant_id, job_name="agency_billing"):
        col = get_collection("audit_events")
        await col.insert_one(event)


async def start_checkout(
    tenant_id: str,
    plan_code: str,
    *,
    quantity: int = 1,
    trial: bool = False,
    success_url: str,
    cancel_url: str,
    poster: Callable[[dict[str, str], str], dict] | None = None,
    item_poster: Callable[[dict[str, str], str], dict] | None = None,
) -> dict[str, str]:
    if not multi_tenant_enabled():
        raise BillingDisabled()
    found = None
    if tenant_id != SHAMROCK_TENANT_ID:
        found = await _load_agency(tenant_id)
        if not found:
            raise BillingError("not_found")
    billing = _billing_of(found)
    plan = _PLAN_BY_CODE.get(plan_code)
    recurring = bool(plan and plan["kind"] != "one_time")
    subscription_id = str(billing.get("subscription_id") or "")
    if recurring and subscription_id and tenant_id != SHAMROCK_TENANT_ID:
        if billing.get("status") in _BLOCKED_ITEM_STATUS:
            raise BillingError("billing_not_current")
        existing = next(
            (row for row in billing["items"] if row.get("plan_code") == plan_code),
            None,
        )
        form = subscription_item_form(
            tenant_id=tenant_id,
            plan_code=plan_code,
            quantity=quantity,
            subscription_id=subscription_id,
            subscription_item_id=str((existing or {}).get("subscription_item_id") or ""),
        )
        key = _api_key()
        send_item = item_poster or post_subscription_item
        created = send_item(form, key)
        item_id = str(created.get("id") or "")
        if not item_id.startswith("si_"):
            raise BillingError("checkout_rejected")
        _upsert_item(
            billing,
            plan_code=plan_code,
            price_id=form["price"],
            quantity=int(form["quantity"]),
            subscription_item_id=item_id,
        )
        before = _audit_slice(_billing_of(found))
        await _save_billing(tenant_id, billing)
        await _write_billing_audit(
            tenant_id=tenant_id,
            actor="stripe_checkout",
            reason="subscription_item",
            action="billing_subscription_item",
            old_state=before,
            new_state={**_audit_slice(billing), "items": list(billing.get("items") or [])},
        )
        return {
            "mode": "subscription_item",
            "checkout_url": "",
            "session_id": "",
            "subscription_item_id": item_id,
            "plan_code": plan_code,
        }
    form = checkout_form(
        tenant_id=tenant_id,
        plan_code=plan_code,
        quantity=quantity,
        trial=trial,
        success_url=success_url,
        cancel_url=cancel_url,
        customer_id=str(billing.get("customer_id") or ""),
    )
    key = _api_key()
    send = poster or post_checkout_session
    created = send(form, key)
    url = _accept_checkout_url(str(created.get("url") or ""))
    session_id = str(created.get("id") or "")
    if not session_id.startswith("cs_test_"):
        raise BillingError("checkout_rejected")
    return {
        "mode": "checkout",
        "checkout_url": url,
        "session_id": session_id,
        "plan_code": plan_code,
    }


async def comp_agency(tenant_id: str, *, actor: str, plan_code: str = "monthly") -> dict:
    if not multi_tenant_enabled():
        raise BillingDisabled()
    if tenant_id == SHAMROCK_TENANT_ID or plan_code not in _PLAN_BY_CODE:
        raise BillingError("plan_invalid")
    found = await _load_agency(tenant_id)
    if not found:
        raise BillingError("not_found")
    before = _audit_slice(_billing_of(found))
    billing = _blank_billing()
    billing.update(
        {
            "provider": "comp",
            "plan_code": plan_code,
            "status": "comped",
            "mrr_cents": 0,
            "comped_by": actor,
        }
    )
    await _save_billing(tenant_id, billing)
    found["billing"] = billing
    await _write_billing_audit(
        tenant_id=tenant_id,
        actor=actor,
        reason="comp",
        action="billing_comped",
        old_state=before,
        new_state=_audit_slice(billing),
    )
    return public_billing_view(found)


async def billing_overview() -> dict[str, Any]:
    if not multi_tenant_enabled():
        raise BillingDisabled()
    with bind_platform_job("agency_billing"):
        col = await _tenants()
        cursor = col.find({})
        rows = []
        if hasattr(cursor, "__aiter__"):
            async for doc in cursor:
                rows.append(doc)
        else:
            rows = list(cursor)
    agencies = [public_billing_view(doc) for doc in rows if doc.get("tenant_id") != SHAMROCK_TENANT_ID]
    return {
        "mrr_cents": sum(int(row["mrr_cents"] or 0) for row in agencies),
        "currency": "usd",
        "agencies": agencies,
        "plans": catalog(),
    }


async def billing_for(tenant_id: str) -> dict:
    if not multi_tenant_enabled():
        raise BillingDisabled()
    with bind_platform_job("agency_billing"):
        col = await _tenants()
        found = await col.find_one({"tenant_id": tenant_id})
    if not found:
        raise BillingError("not_found")
    return public_billing_view(found)


def verify_stripe_signature(payload: bytes, header: str, *, secret: str, now: int) -> None:
    if not secret:
        raise BillingError("webhook_secret_required")
    parts: dict[str, list[str]] = {}
    for item in (header or "").split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts.setdefault(key.strip(), []).append(value.strip())
    timestamps = parts.get("t") or []
    signatures = parts.get("v1") or []
    if not timestamps or not signatures:
        raise BillingError("signature_invalid")
    try:
        stamped = int(timestamps[0])
    except ValueError:
        raise BillingError("signature_invalid")
    if abs(int(now) - stamped) > _SIGNATURE_TOLERANCE_SECONDS:
        raise BillingError("signature_expired")
    signed = str(stamped).encode() + b"." + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in signatures):
        raise BillingError("signature_invalid")


def event_tenant_id(event: dict) -> str:
    obj = ((event.get("data") or {}).get("object") or {})
    if not isinstance(obj, dict):
        return ""
    meta = obj.get("metadata") or {}
    if isinstance(meta, dict) and meta.get("tenant_id"):
        return str(meta["tenant_id"])
    if obj.get("client_reference_id"):
        return str(obj["client_reference_id"])
    nested = obj.get("subscription_details") or {}
    nested_meta = nested.get("metadata") if isinstance(nested, dict) else {}
    if isinstance(nested_meta, dict) and nested_meta.get("tenant_id"):
        return str(nested_meta["tenant_id"])
    return ""


async def apply_webhook_event(event: dict) -> dict[str, str]:
    if not multi_tenant_enabled():
        raise BillingDisabled()
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if key.startswith("sk_live_") or key.startswith("rk_live_"):
        raise BillingError("live_key_rejected")
    if event.get("livemode") is True and not livemode_explicitly_enabled():
        raise BillingError("livemode_rejected")
    tenant_id = event_tenant_id(event)
    if not tenant_id or tenant_id == SHAMROCK_TENANT_ID:
        return {"status": "ignored"}
    with bind_platform_job("agency_billing"):
        col = await _tenants()
        found = await col.find_one({"tenant_id": tenant_id})
        if not found:
            customer = str((((event.get("data") or {}).get("object") or {}) if isinstance(event.get("data"), dict) else {}).get("customer") or "")
            if customer:
                found = await col.find_one({"billing.customer_id": customer, "tenant_id": tenant_id})
        if not found:
            return {"status": "ignored"}
        before = _audit_slice(_billing_of(found))
        billing, outcome = reduce_billing_event(found, event)
        if outcome in {"applied", "noted"}:
            await col.update_one(
                {"tenant_id": tenant_id},
                {"$set": {"billing": billing, "updated_at": _now()}},
            )
    if outcome == "applied":
        kind = str(event.get("type") or "billing")
        event_id = str(event.get("id") or "")
        await _write_billing_audit(
            tenant_id=tenant_id,
            actor="stripe_webhook",
            reason=f"{kind}:{event_id}",
            action="billing_" + kind.replace(".", "_"),
            old_state=before,
            new_state=_audit_slice(billing),
        )
    return {"status": outcome, "billing_status": billing.get("status", "")}


async def suspension_block() -> str | None:
    """Return ``tenant_suspended`` when this request's agency cannot write.

    Flag off, Shamrock, and every status other than ``suspended`` return None
    without changing the caller's query.
    """
    if not multi_tenant_enabled():
        return None
    tenant_id = current_tenant_id()
    if not tenant_id or tenant_id == SHAMROCK_TENANT_ID:
        return None
    with bind_platform_job("billing_gate"):
        col = await _tenants()
        found = await col.find_one({"tenant_id": tenant_id})
    if _billing_of(found).get("status") == "suspended":
        return "tenant_suspended"
    return None


def sign_stripe_payload(payload: bytes, secret: str, timestamp: int) -> str:
    """Test helper. Production webhooks are signed by Stripe."""
    signed = str(timestamp).encode() + b"." + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"
