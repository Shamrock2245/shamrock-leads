"""Platform billing console and the Stripe test-mode webhook.

Flag off: every route returns 404. The webhook verifies a signature and never
accepts a live secret key. Checkout is created only with an injected or
test-mode Stripe call.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard.auth.super_admin import is_super_admin_email
from dashboard.services.agency_billing import (
    BillingDisabled,
    BillingError,
    apply_webhook_event,
    billing_for,
    billing_overview,
    comp_agency,
    start_checkout,
    verify_stripe_signature,
)
from dashboard.tenancy.flag import multi_tenant_enabled

router = APIRouter(tags=["platform-billing"])
_PAGE = Path(__file__).resolve().parents[1] / "platform_billing.html"


def _disabled():
    return JSONResponse({"error": "not_found"}, status_code=404)


def _admin(request: Request) -> bool:
    role = getattr(request.state, "sl_role", "") or ""
    email = getattr(request.state, "sl_email", "") or ""
    return role in {"god_admin", "admin"} and is_super_admin_email(email)


def _actor(request: Request) -> str:
    return getattr(request.state, "sl_email", "") or "platform"


@router.get("/platform/billing")
async def billing_page(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))


@router.get("/api/platform/billing")
async def api_overview(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    try:
        return await billing_overview()
    except BillingDisabled:
        return _disabled()


@router.get("/api/platform/tenants/{tenant_id}/billing")
async def api_tenant_billing(tenant_id: str, request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    try:
        return await billing_for(tenant_id)
    except BillingDisabled:
        return _disabled()
    except BillingError as exc:
        status = 404 if exc.code == "not_found" else 400
        return JSONResponse({"error": exc.code}, status_code=status)


@router.post("/api/platform/tenants/{tenant_id}/billing/checkout")
async def api_checkout(tenant_id: str, request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    payload = await request.json()
    payload = payload if isinstance(payload, dict) else {}
    base = str(request.base_url)
    try:
        session = await start_checkout(
            tenant_id,
            str(payload.get("plan_code") or ""),
            quantity=int(payload.get("quantity") or 1),
            trial=bool(payload.get("trial")),
            success_url=f"{base}platform/billing?checkout=return",
            cancel_url=f"{base}platform/billing?checkout=cancel",
        )
    except BillingDisabled:
        return _disabled()
    except BillingError as exc:
        return JSONResponse({"error": exc.code}, status_code=400)
    except (TypeError, ValueError):
        return JSONResponse({"error": "quantity_invalid"}, status_code=400)
    return JSONResponse(session, status_code=201)


@router.post("/api/platform/tenants/{tenant_id}/billing/comp")
async def api_comp(tenant_id: str, request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    payload = await request.json()
    plan_code = "monthly"
    if isinstance(payload, dict) and payload.get("plan_code"):
        plan_code = str(payload.get("plan_code"))
    try:
        view = await comp_agency(tenant_id, actor=_actor(request), plan_code=plan_code)
    except BillingDisabled:
        return _disabled()
    except BillingError as exc:
        status = 404 if exc.code == "not_found" else 400
        return JSONResponse({"error": exc.code}, status_code=status)
    return view


@router.post("/api/webhooks/stripe-billing")
async def stripe_billing_webhook(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    payload = await request.body()
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    try:
        verify_stripe_signature(
            payload,
            request.headers.get("stripe-signature", ""),
            secret=secret,
            now=int(time.time()),
        )
        event = json.loads(payload.decode() or "{}")
        if not isinstance(event, dict):
            raise BillingError("signature_invalid")
        result = await apply_webhook_event(event)
    except BillingDisabled:
        return _disabled()
    except BillingError as exc:
        return JSONResponse({"error": exc.code}, status_code=400)
    except ValueError:
        return JSONResponse({"error": "signature_invalid"}, status_code=400)
    return result
