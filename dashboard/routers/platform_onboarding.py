"""Platform onboarding console and public signup.

Both are inert unless SAAS_MULTI_TENANT is on. Signup never sends mail.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard.auth.super_admin import is_super_admin_email
from dashboard.services.agency_onboarding import (
    OnboardingDisabled,
    OnboardingError,
    create_agency,
    decide_agency,
    list_agencies,
)
from dashboard.tenancy.flag import multi_tenant_enabled

router = APIRouter(tags=["platform-onboarding"])
_PAGE = Path(__file__).resolve().parents[1] / "platform_onboarding.html"


def _disabled():
    return JSONResponse({"error": "not_found"}, status_code=404)


def _page(mode: str) -> HTMLResponse:
    html = _PAGE.read_text(encoding="utf-8")
    html = html.replace("<body>", f'<body data-mode="{mode}">', 1)
    return HTMLResponse(html)


def _admin(request: Request) -> bool:
    role = getattr(request.state, "sl_role", "") or ""
    email = getattr(request.state, "sl_email", "") or ""
    return role in {"god_admin", "admin"} and is_super_admin_email(email)


def _actor(request: Request) -> str:
    return getattr(request.state, "sl_email", "") or "platform"


@router.get("/platform")
async def platform_home(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    return _page("admin")


@router.get("/signup")
async def signup_page():
    if not multi_tenant_enabled():
        return _disabled()
    return _page("signup")


@router.get("/api/platform/tenants")
async def api_list_tenants(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    try:
        agencies = await list_agencies()
    except OnboardingDisabled:
        return _disabled()
    return {"agencies": agencies}


@router.post("/api/platform/tenants")
async def api_create_tenant(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    payload = await request.json()
    approve_now = bool(payload.get("approve_now")) if isinstance(payload, dict) else False
    try:
        agency = await create_agency(
            payload, source="super_admin", actor=_actor(request), approve_now=approve_now
        )
    except OnboardingDisabled:
        return _disabled()
    except OnboardingError as exc:
        return JSONResponse({"error": exc.code}, status_code=400)
    return JSONResponse(agency, status_code=201)


@router.post("/api/public/signup")
async def api_public_signup(request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    payload = await request.json()
    try:
        agency = await create_agency(payload, source="self_serve", actor="self_serve", approve_now=False)
    except OnboardingDisabled:
        return _disabled()
    except OnboardingError as exc:
        return JSONResponse({"error": exc.code}, status_code=400)
    return JSONResponse(agency, status_code=201)


@router.post("/api/platform/tenants/{tenant_id}/approve")
async def api_approve(tenant_id: str, request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    try:
        agency = await decide_agency(tenant_id, approve=True, actor=_actor(request))
    except OnboardingError as exc:
        status = 404 if exc.code == "not_found" else 400
        return JSONResponse({"error": exc.code}, status_code=status)
    return agency


@router.post("/api/platform/tenants/{tenant_id}/reject")
async def api_reject(tenant_id: str, request: Request):
    if not multi_tenant_enabled():
        return _disabled()
    if not _admin(request):
        return JSONResponse({"error": "platform_admin_required"}, status_code=403)
    payload = await request.json()
    reason = payload.get("reason") if isinstance(payload, dict) else ""
    try:
        agency = await decide_agency(tenant_id, approve=False, actor=_actor(request), reason=reason)
    except OnboardingError as exc:
        status = 404 if exc.code == "not_found" else 400
        return JSONResponse({"error": exc.code}, status_code=status)
    return agency
