"""Lead subscription console. Inert unless SAAS_MULTI_TENANT is on."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard.auth.super_admin import is_super_admin_email
from dashboard.services.lead_subscriptions import (
    LeadSubscriptionError,
    LeadSubscriptionsDisabled,
    catalog,
    matrix,
    replace_subscriptions,
)
from dashboard.tenancy.flag import multi_tenant_enabled

router = APIRouter(tags=["platform-leads"])
_PAGE = Path(__file__).resolve().parents[1] / "platform_leads.html"


def _disabled():
    return JSONResponse({"error": "not_found"}, status_code=404)


def _admin(request: Request) -> bool:
    role = getattr(request.state, "sl_role", "") or ""
    email = getattr(request.state, "sl_email", "") or ""
    return role in {"god_admin", "admin"} and is_super_admin_email(email)


def _actor(request: Request) -> str:
    return getattr(request.state, "sl_email", "") or ""


@router.get("/platform/leads")
async def leads_page(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))


@router.get("/api/platform/lead-subscriptions")
async def api_matrix(request: Request, state: str = ""):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    try:
        body = await matrix()
        body.update(await catalog(state))
        return body
    except LeadSubscriptionsDisabled:
        return _disabled()


@router.put("/api/platform/tenants/{tenant_id}/lead-subscriptions")
async def api_replace(tenant_id: str, request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    payload = await request.json()
    body = payload if isinstance(payload, dict) else {}
    try:
        return await replace_subscriptions(
            tenant_id,
            body,
            actor=_actor(request),
            reason=str(body.get("reason") or ""),
        )
    except LeadSubscriptionsDisabled:
        return _disabled()
    except LeadSubscriptionError as exc:
        status = 404 if exc.code == "not_found" else 400
        return JSONResponse({"error": exc.code}, status_code=status)
