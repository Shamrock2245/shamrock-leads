"""
ShamrockLeads — Staff-Gated Match & BondCase Chain API
======================================================
Provides the authoritative staff-gated endpoint to ensure the canonical
paperwork chain without requiring direct-Mongo shell backfills:
    ArrestLead → Defendant → Indemnitor → validated Match → BondCase → Packet

Endpoints:
  POST /api/staff/chain/ensure-match-bondcase
  POST /api/staff/ensure-match-bondcase
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.auth.pin_middleware import (
    DASHBOARD_PIN,
    get_session_from_request,
    is_machine_auth_valid,
    session_is_admin,
    session_is_god_admin,
)
from dashboard.services.staff_chain_service import ensure_match_bondcase

logger = logging.getLogger(__name__)

staff_chain_bp = APIRouter(prefix="/api/staff", tags=["staff_chain"])


def _check_staff_auth(request: Request, body: Dict[str, Any]) -> tuple[bool, str]:
    """Verify God-Admin, admin, or staff. Fail closed.

    A signed cookie with auth=True is not enough. Sub-agent sessions and any
    other non-staff role are refused. Machine tokens and the dashboard PIN
    remain valid for staff tooling.
    """
    # 1. God-Admin / admin / staff cookie. Sub-agents cannot mint a BondCase.
    sess = get_session_from_request(request)
    if sess and sess.get("auth"):
        role = str(sess.get("role") or "").strip().lower()
        if role != "sub_agent" and (
            session_is_god_admin(request)
            or session_is_admin(request)
            or role in ("staff", "admin", "god_admin")
        ):
            actor = sess.get("email") or sess.get("agent_name") or "staff_session"
            return True, actor

    # 2. Machine auth (GAS_API_KEY, LEADS_INTERNAL_TOKEN)
    if is_machine_auth_valid(request):
        actor = request.headers.get("X-Staff-Email", "").strip() or "machine_key"
        return True, actor

    # 3. Header-based token checks (X-Admin-Token, X-PIN, Bearer)
    admin_token = (
        request.headers.get("X-Admin-Token")
        or request.headers.get("X-PIN")
        or request.headers.get("X-Staff-PIN")
        or ""
    ).strip()

    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer_val = auth_header[7:].strip()
        if bearer_val:
            admin_token = bearer_val

    body_pin = str(body.get("pin") or body.get("authorization_pin") or "").strip()

    active_pin = (os.getenv("DASHBOARD_PIN") or DASHBOARD_PIN or "").strip()
    if active_pin:
        if admin_token and secrets.compare_digest(admin_token, active_pin):
            actor = request.headers.get("X-Staff-Email", "").strip() or "god_admin"
            return True, actor
        if body_pin and secrets.compare_digest(body_pin, active_pin):
            actor = request.headers.get("X-Staff-Email", "").strip() or "god_admin"
            return True, actor

    return False, ""


@staff_chain_bp.post("/chain/ensure-match-bondcase")
@staff_chain_bp.post("/ensure-match-bondcase")
async def api_ensure_match_bondcase(request: Request):
    """
    Staff-gated endpoint to ensure the complete validated chain:
    ArrestLead → Defendant → Indemnitor → validated Match → BondCase → Packet.

    Idempotent. Fails closed on missing CRM facts.
    """
    try:
        body: Dict[str, Any] = (await request.json()) or {}
    except Exception:
        body = {}

    is_auth, actor = _check_staff_auth(request, body)
    if not is_auth:
        return JSONResponse(
            {
                "success": False,
                "error": "unauthorized",
                "message": "God-Admin or staff PIN / session required",
            },
            status_code=401,
        )

    booking_number = str(
        body.get("booking_number") or body.get("booking") or ""
    ).strip()
    if not booking_number:
        return JSONResponse(
            {
                "success": False,
                "error": "booking_number_required",
                "message": "booking_number is required",
            },
            status_code=400,
        )

    res = await ensure_match_bondcase(
        booking_number=booking_number,
        surety_id=body.get("surety_id"),
        case_number=body.get("case_number"),
        poa_numbers=body.get("poa_numbers"),
        poa_number=body.get("poa_number"),
        indemnitor_name=body.get("indemnitor_name") or (body.get("indemnitor") or {}).get("name"),
        indemnitor_email=body.get("indemnitor_email") or (body.get("indemnitor") or {}).get("email"),
        indemnitor_phone=body.get("indemnitor_phone") or (body.get("indemnitor") or {}).get("phone"),
        bond_amount=body.get("bond_amount"),
        premium=body.get("premium"),
        packet_id=body.get("packet_id"),
        charge_details=body.get("charge_details"),
        actor_email=actor or "admin@shamrockbailbonds.biz",
        source="staff_ensure_match_bondcase",
    )

    status_code = res.get("status_code", 200 if res.get("success") else 400)
    return JSONResponse(res, status_code=status_code)
