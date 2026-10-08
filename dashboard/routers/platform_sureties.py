"""Super-admin surety checklist. Inert unless SAAS_MULTI_TENANT is on."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard.auth.super_admin import is_super_admin_email
from dashboard.services.surety_entitlements import (
    SuretyEntitlementError,
    SuretyEntitlementsDisabled,
    list_access,
    set_access,
)
from dashboard.tenancy.flag import multi_tenant_enabled

router = APIRouter(tags=["platform-sureties"])
_PAGE = Path(__file__).resolve().parents[1] / "platform_sureties.html"


def _disabled():
    return JSONResponse({"error": "not_found"}, status_code=404)


def _admin(request: Request) -> bool:
    role = getattr(request.state, "sl_role", "") or ""
    email = getattr(request.state, "sl_email", "") or ""
    return role in {"god_admin", "admin"} and is_super_admin_email(email)


def _actor(request: Request) -> str:
    return getattr(request.state, "sl_email", "") or "platform"


@router.get("/platform/sureties")
async def sureties_page(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))


@router.get("/api/platform/sureties")
async def api_list(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    try:
        return await list_access()
    except SuretyEntitlementsDisabled:
        return _disabled()


@router.put("/api/platform/tenants/{tenant_id}/sureties")
async def api_set(tenant_id: str, request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    payload = await request.json()
    payload = payload if isinstance(payload, dict) else {}
    try:
        return await set_access(tenant_id, payload, actor=_actor(request), reason=str(payload.get("reason") or ""))
    except SuretyEntitlementsDisabled:
        return _disabled()
    except SuretyEntitlementError as exc:
        status = 404 if exc.code == "not_found" else 400
        return JSONResponse({"error": exc.code}, status_code=status)
