"""
Staff Accounts Receivable API.

PIN middleware already gates /api/*. These handlers also require a staff
session (god-admin or sub-agent). Sub-agents only see bonds in their scope.
Balance is computed. It is not accepted from the client.
"""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from dashboard.auth.agent_scope import bond_scope_query, merge_scope
from dashboard.auth.pin_middleware import get_session_from_request
from dashboard.services import ar_service

ar_bp = APIRouter(prefix="/api/ar", tags=["accounts_receivable"])


def _actor(request: Request) -> str:
    sess = get_session_from_request(request) or {}
    name = str(sess.get("agent_name") or sess.get("email") or "").strip()
    if name:
        return name
    header = str(request.headers.get("x-actor") or "").strip()
    return header or "staff"


def require_staff(request: Request) -> str:
    sess = get_session_from_request(request)
    if sess and sess.get("auth"):
        return _actor(request)
    pin = (os.getenv("DASHBOARD_PIN") or "").strip()
    token = (request.headers.get("x-admin-token") or "").strip()
    if pin and token and secrets.compare_digest(token, pin):
        return _actor(request)
    # No PIN configured: fail closed (401) unless explicit ENV=development.
    from dashboard.auth.dev_mode import no_pin_passthrough_allowed

    if not pin and no_pin_passthrough_allowed():
        return _actor(request)
    return ""


def _unauthorized():
    return JSONResponse({"ok": False, "error": "Authentication required"}, status_code=401)


@ar_bp.get("/bonds")
async def list_ar_bonds(
    request: Request,
    filter: str = Query(default="all"),
    sort: str = Query(default="overdue"),
    q: str = Query(default=""),
):
    actor = require_staff(request)
    if not actor:
        return _unauthorized()
    match = merge_scope({}, bond_scope_query(request))
    data = await ar_service.list_bonds(match, filt=filter, sort=sort, query=q)
    return data


@ar_bp.get("/bonds/{booking_number}")
async def get_ar_bond(request: Request, booking_number: str):
    if not require_staff(request):
        return _unauthorized()
    from dashboard.extensions import get_collection
    match = merge_scope({"booking_number": booking_number}, bond_scope_query(request))
    bond = await get_collection("active_bonds").find_one(match)
    if not bond:
        return JSONResponse({"ok": False, "error": "bond_not_found"}, status_code=404)
    row = await ar_service.bond_detail(booking_number)
    return {"bond": row}


@ar_bp.post("/write-capture")
async def capture_from_write_bond(request: Request):
    """Called by the Write Bond screen. One entry. No second premium form."""
    actor = require_staff(request)
    if not actor:
        return _unauthorized()
    payload = await request.json()
    result = await ar_service.apply_write_bond_money(payload or {}, actor)
    status = 200 if result.get("ok") else int(result.get("status") or 400)
    return JSONResponse(result, status_code=status)


@ar_bp.post("/bonds/{booking_number}/payments")
async def log_payment(request: Request, booking_number: str):
    actor = require_staff(request)
    if not actor:
        return _unauthorized()
    payload = await request.json()
    result = await ar_service.log_later_payment(booking_number, payload or {}, actor)
    status = 200 if result.get("ok") else int(result.get("status") or 400)
    return JSONResponse(result, status_code=status)


@ar_bp.post("/bonds/{booking_number}/reminders/draft")
async def draft_reminder(request: Request, booking_number: str):
    actor = require_staff(request)
    if not actor:
        return _unauthorized()
    payload = await request.json()
    result = await ar_service.draft_reminder(booking_number, payload or {}, actor)
    status = 200 if result.get("ok") else int(result.get("status") or 400)
    return JSONResponse(result, status_code=status)


@ar_bp.post("/bonds/{booking_number}/reminders/send")
async def send_reminder(request: Request, booking_number: str):
    """Staff approval. Drafts never send themselves."""
    actor = require_staff(request)
    if not actor:
        return _unauthorized()
    payload = await request.json()
    result = await ar_service.send_reminder(booking_number, payload or {}, actor)
    status = 200 if result.get("ok") else int(result.get("status") or 400)
    return JSONResponse(result, status_code=status)
