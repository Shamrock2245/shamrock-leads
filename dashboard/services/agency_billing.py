"""Agency SaaS billing. Stripe test mode only. No live charges.

Premium collection (SwipeSimple) is a different ledger and is not touched here.
Prices are read from environment price ids. This module does not invent amounts.
MRR is the sum of ``mrr_cents`` values stored from ``invoice.paid`` webhooks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from dashboard.tenancy.constants import SHAMROCK_TENANT_ID
from dashboard.tenancy.context import bind_platform_job, current_tenant_id
from dashboard.tenancy.flag import multi_tenant_enabled

PLAN_CATALOG = (
    {"code": "setup", "kind": "one_time", "label": "Setup fee", "env_price": "STRIPE_PRICE_SETUP"},
    {"code": "monthly", "kind": "recurring", "label": "Monthly plan", "env_price": "STRIPE_PRICE_MONTHLY"},
    {"code": "county_addon", "kind": "recurring", "label": "Per-county lead add-on", "env_price": "STRIPE_PRICE_COUNTY"},
    {"code": "state_addon", "kind": "recurring", "label": "Per-state lead add-on", "env_price": "STRIPE_PRICE_STATE"},
    {"code": "usage", "kind": "metered", "label": "Usage", "env_price": "STRIPE_PRICE_USAGE"},
)
_PLAN_BY_CODE = {row["code"]: row for row in PLAN_CATALOG}
_MRR_REASONS = frozenset({"subscription_cycle", "subscription_create", "subscription_update"})
_ACTIVE_MRR = frozenset({"active"})
_SIGNATURE_TOLERANCE_SECONDS = 300


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
        "comped_by": "",
    }


def _billing_of(doc: dict | None) -> dict[str, Any]:
    current = dict(_blank_billing())
    stored = (doc or {}).get("billing") or {}
    if isinstance(stored, dict):
        current.update({key: stored.get(key, current[key]) for key in current})
    current["invoices"] = list(current.get("invoices") or [])
    current["event_ids"] = list(current.get("event_ids") or [])
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


def reduce_billing_event(doc: dict, event: dict, *, suspend_after: int | None = None) -> tuple[dict, str]:
    """Apply one verified Stripe event. Returns the billing dict and an outcome token."""
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
        billing["provider"] = "stripe_test"
        billing["customer_id"] = str(obj.get("customer") or billing["customer_id"] or "")
        billing["subscription_id"] = str(obj.get("subscription") or billing["subscription_id"] or "")
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
        _remember_invoice(billing, obj)
        billing["provider"] = "stripe_test"
        billing["customer_id"] = str(obj.get("customer") or billing["customer_id"] or "")
        billing["subscription_id"] = str(obj.get("subscription") or billing["subscription_id"] or "")
        billing["failure_count"] = 0
        billing["status"] = "active"
        if obj.get("subscription") and str(obj.get("billing_reason") or "") in _MRR_REASONS:
            try:
                billing["mrr_cents"] = int(obj.get("amount_paid") or 0)
            except (TypeError, ValueError):
                billing["mrr_cents"] = billing["mrr_cents"]
        billing["currency"] = str(obj.get("currency") or billing["currency"] or "usd")
        return billing, "applied"
    if kind == "invoice.payment_failed":
        _remember_invoice(billing, obj)
        billing["failure_count"] = int(billing["failure_count"]) + 1
        billing["status"] = "suspended" if billing["failure_count"] >= threshold else "past_due"
        return billing, "applied"
    if kind == "customer.subscription.deleted":
        billing["status"] = "canceled"
        billing["mrr_cents"] = 0
        billing["subscription_id"] = str(obj.get("id") or billing["subscription_id"] or "")
        return billing, "applied"
    if kind == "customer.subscription.updated":
        stripe_status = str(obj.get("status") or "")
        billing["subscription_id"] = str(obj.get("id") or billing["subscription_id"] or "")
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
        return billing, "applied"
    return billing, "ignored"


def checkout_form(
    *,
    tenant_id: str,
    plan_code: str,
    quantity: int,
    trial: bool,
    success_url: str,
    cancel_url: str,
) -> dict[str, str]:
    if tenant_id == SHAMROCK_TENANT_ID:
        raise BillingError("internal_not_billed")
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
    if trial:
        days = trial_days()
        if days <= 0:
            raise BillingError("trial_not_configured")
        if mode != "subscription":
            raise BillingError("trial_not_allowed")
        form["subscription_data[trial_period_days]"] = str(days)
        form["metadata[trial]"] = "1"
    return form


def post_checkout_session(form: dict[str, str], api_key: str) -> dict:
    """POST a Checkout Session. Callers must have already rejected live keys."""
    if not str(api_key).startswith("sk_test_"):
        raise BillingError("stripe_test_key_required")
    data = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(
        "https://api.stripe.com/v1/checkout/sessions",
        data=data,
        headers={"Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode())
    if not isinstance(body, dict):
        raise BillingError("checkout_rejected")
    return body


def _accept_checkout_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme != "https" or parsed.netloc != "checkout.stripe.com":
        raise BillingError("checkout_url_rejected")
    return url


async def _tenants():
    from dashboard.extensions import get_collection

    return get_collection("tenants")


async def start_checkout(
    tenant_id: str,
    plan_code: str,
    *,
    quantity: int = 1,
    trial: bool = False,
    success_url: str,
    cancel_url: str,
    poster: Callable[[dict[str, str], str], dict] | None = None,
) -> dict[str, str]:
    if not multi_tenant_enabled():
        raise BillingDisabled()
    form = checkout_form(
        tenant_id=tenant_id,
        plan_code=plan_code,
        quantity=quantity,
        trial=trial,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    key = _api_key()
    send = poster or post_checkout_session
    created = send(form, key)
    url = _accept_checkout_url(str(created.get("url") or ""))
    session_id = str(created.get("id") or "")
    if not session_id.startswith("cs_test_"):
        raise BillingError("checkout_rejected")
    return {"checkout_url": url, "session_id": session_id, "plan_code": plan_code}


async def comp_agency(tenant_id: str, *, actor: str, plan_code: str = "monthly") -> dict:
    if not multi_tenant_enabled():
        raise BillingDisabled()
    if tenant_id == SHAMROCK_TENANT_ID or plan_code not in _PLAN_BY_CODE:
        raise BillingError("plan_invalid")
    with bind_platform_job("agency_billing"):
        col = await _tenants()
        found = await col.find_one({"tenant_id": tenant_id})
        if not found:
            raise BillingError("not_found")
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
        await col.update_one({"tenant_id": tenant_id}, {"$set": {"billing": billing, "updated_at": _now()}})
        found["billing"] = billing
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
        billing, outcome = reduce_billing_event(found, event)
        if outcome == "applied":
            await col.update_one(
                {"tenant_id": tenant_id},
                {"$set": {"billing": billing, "updated_at": _now()}},
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
