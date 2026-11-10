from __future__ import annotations

"""
ShamrockLeads — Client Portal API Blueprint
Public-facing endpoints for defendant/indemnitor self-service.

Routes:
  GET  /c/<token>                    — Portal page (HTML)
  GET  /api/portal/<token>/status    — Case status (JSON)
  GET  /api/portal/<token>/payments  — Payment history (JSON)
  POST /api/portal/<token>/checkin   — Submit check-in (GPS + selfie)
  GET  /api/portal/<token>/payment-link — the bond's own verified invoice link
                                       (never a static link; null → call us)
  POST /api/portal/<token>/invoice-link — indemnitor self-pay: create or
                                       return the bond's locked invoice link

Staff-only (staff session or machine key, checked in each route because
pin_middleware leaves the /api/portal prefix open for the token routes):
  POST /api/portal/generate          — Generate a new portal token
  GET  /api/portal/tokens/<booking>  — List active tokens for a bond
  POST /api/portal/revoke            — Revoke a token
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

_PUBLIC_URL = os.getenv("DASHBOARD_PUBLIC_URL", "https://leads.shamrockbailbonds.biz")

from dashboard.extensions import get_collection
from dashboard.services.client_portal_service import (
    generate_portal_token,
    validate_token,
    revoke_portal_token,
    get_portal_case_status,
    submit_portal_checkin,
    get_portal_tokens_for_bond,
)

logger = logging.getLogger(__name__)

# No router prefix — routes own full paths:
#   /c/{token}  and  /api/portal/...
portal_bp = APIRouter(tags=["client_portal"])
DASHBOARD_DIR = os.path.dirname(os.path.dirname(__file__))


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC ROUTES — No auth required (token-gated)
# ══════════════════════════════════════════════════════════════════════════════

@portal_bp.get("/c/{token}", response_class=HTMLResponse)
async def portal_page(request: Request, token: str):
    """Serve the client portal HTML page and record device footprint."""
    token_data = await validate_token(token)
    if not token_data:
        return HTMLResponse(
            await _error_page("This link has expired or is no longer valid."),
            status_code=404,
        )

    portal_path = os.path.join(DASHBOARD_DIR, "portal.html")
    if not os.path.isfile(portal_path):
        logger.error("portal.html not found at %s", portal_path)
        return HTMLResponse(
            await _error_page("Portal temporarily unavailable."),
            status_code=500,
        )

    # ── Record client device footprint for lawful skip tracing / risk mitigation ──
    try:
        booking_number = token_data.get("booking_number")
        if booking_number:
            client_ip = (
                request.headers.get("CF-Connecting-IP")
                or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or request.client.host
                if request.client
                else "unknown"
            )
            user_agent = request.headers.get("User-Agent", "Unknown")
            now_iso = datetime.now(timezone.utc).isoformat()
            footprint_entry = {
                "ip": client_ip,
                "user_agent": user_agent,
                "accessed_at": now_iso,
                "role": token_data.get("role", "unknown"),
                "token_id": token_data.get("token_id", ""),
            }
            active_bonds = get_collection("active_bonds")
            await active_bonds.update_one(
                {"booking_number": booking_number},
                {
                    "$push": {"device_footprints": {"$each": [footprint_entry], "$slice": -50}},
                    "$set": {
                        "last_seen_device": {
                            "ip": client_ip,
                            "user_agent": user_agent,
                            "timestamp": now_iso,
                        }
                    },
                },
            )
    except Exception as fp_err:
        logger.warning("[portal] Failed to record device footprint: %s", fp_err)

    with open(portal_path, "r", encoding="utf-8") as f:
        html = f.read()

    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@portal_bp.get("/api/portal/{token}/status")
async def portal_status(token: str):
    """Return case status data scoped to the token's role."""
    token_data = await validate_token(token)
    if not token_data:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)

    status = await get_portal_case_status(
        token_data["booking_number"],
        token_data["role"],
    )

    if "error" in status:
        code = 409 if status.get("error") == "bond_ambiguous" else 404
        return JSONResponse(status, status_code=code)
    status["role"] = token_data["role"]
    return status


@portal_bp.post("/api/portal/{token}/invoice-link")
async def portal_invoice_link(request: Request, token: str):
    """Indemnitor self-pay: the bond's own locked invoice link (§2.2).

    The amount always comes from the bond's confirmed premium / down
    payment; anything in the body is ignored. Errors never carry a URL.
    """
    from dashboard.services.portal_invoice_service import (
        rate_limited,
        request_invoice_link,
    )

    token_data = await validate_token(token)
    if not token_data:
        return JSONResponse({"ok": False, "error": "token_invalid_or_expired"}, status_code=401)
    if rate_limited(token):
        return JSONResponse(
            {"ok": False, "error": "rate_limited"},
            status_code=429,
            headers={"Retry-After": "60"},
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    status, payload = await request_invoice_link(token_data, body)
    return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})


@portal_bp.post("/api/portal/{token}/checkin")
async def portal_checkin(request: Request, token: str):
    """Submit a defendant check-in (explicit consent + GPS + optional selfie)."""
    token_data = await validate_token(token)
    if not token_data:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)

    if token_data["role"] != "defendant":
        return JSONResponse(
            {"error": "Check-in is only available for defendants"},
            status_code=403,
        )

    data = await request.json() or {}
    consent = bool(data.get("consent") is True or data.get("consent") == "true")

    result = await submit_portal_checkin(
        booking_number=token_data["booking_number"],
        lat=data.get("lat"),
        lng=data.get("lng"),
        accuracy=data.get("accuracy"),
        selfie_data=data.get("selfie"),
        notes=data.get("notes", ""),
        consent=consent,
        consent_version=data.get("consent_version", ""),
    )

    if result.get("success"):
        return JSONResponse(result, status_code=201)
    return JSONResponse(result, status_code=400)


@portal_bp.get("/api/portal/{token}/payment-link")
async def portal_payment_link(token: str):
    """Return the bond's own verified invoice link, or null with the call-us number.

    Never a static / per-source link: those carry no amount, so the portal
    shows "Call (239) 332-2245" instead.
    """
    token_data = await validate_token(token)
    if not token_data:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)

    from dashboard.services.portal_invoice_service import portal_payment_view

    view = await portal_payment_view(token_data["booking_number"])
    return {
        "payment_link": view.get("payment_link"),
        "amount_due": view.get("amount_due"),
        "call_phone": view.get("call_phone"),
        "booking_number": token_data["booking_number"],
        "defendant_name": token_data.get("defendant_name", ""),
    }


# ══════════════════════════════════════════════════════════════════════════════
# STAFF ROUTES — Session-authed (behind PIN gate)
# ══════════════════════════════════════════════════════════════════════════════

def _staff_actor(request: Request) -> str | None:
    """Staff session user (or ``machine`` for a valid machine key), else None.

    Never the request body. Recovery sessions are refused.
    """
    from dashboard.auth.pin_middleware import (
        get_session_from_request,
        is_machine_auth_valid,
    )
    from dashboard.auth.recovery_scope import STAFF_ROLES

    try:
        sess = get_session_from_request(request)
    except Exception:
        sess = None
    if sess and sess.get("auth"):
        role = str(sess.get("role") or "")
        if role != "recovery" and role in (set(STAFF_ROLES) | {"sub_agent"}):
            actor = str(sess.get("email") or sess.get("agent_name") or "").strip()
            if actor:
                return actor
    try:
        if is_machine_auth_valid(request):
            return "machine"
    except Exception:
        pass
    return None


def _staff_required() -> JSONResponse:
    return JSONResponse({"error": "staff_auth_required"}, status_code=401)


@portal_bp.post("/api/portal/generate")
async def generate_token_endpoint(request: Request):
    """Generate a new portal magic link for a bond case.

    ``created_by`` is the session user (never the request body).
    """
    created_by = _staff_actor(request)
    if not created_by:
        return _staff_required()
    data = await request.json() or {}
    booking_number = str(data.get("booking_number") or "").strip()
    role = str(data.get("role") or "indemnitor").strip().lower()

    if not booking_number:
        return JSONResponse({"error": "booking_number is required"}, status_code=400)
    if role not in ("defendant", "indemnitor"):
        return JSONResponse(
            {"error": "role must be 'defendant' or 'indemnitor'"},
            status_code=400,
        )

    result = await generate_portal_token(
        booking_number=booking_number,
        role=role,
        created_by=created_by,
    )

    if result.get("success"):
        return JSONResponse(result, status_code=201)
    return JSONResponse(result, status_code=404)


@portal_bp.get("/api/portal/tokens/{booking_number}")
async def list_tokens(request: Request, booking_number: str):
    """List all active portal tokens for a bond (staff view)."""
    if not _staff_actor(request):
        return _staff_required()
    tokens = await get_portal_tokens_for_bond(booking_number)
    return {"tokens": tokens, "count": len(tokens)}


@portal_bp.post("/api/portal/revoke")
async def revoke_token_endpoint(request: Request):
    """Revoke a portal token."""
    if not _staff_actor(request):
        return _staff_required()
    data = await request.json() or {}
    token = data.get("token", "").strip()
    if not token:
        return JSONResponse({"error": "token is required"}, status_code=400)

    result = await revoke_portal_token(token)
    if result.get("success"):
        return result
    return JSONResponse(result, status_code=404)


@portal_bp.post("/api/portal/send-link")
async def send_portal_link(request: Request):
    """Generate a portal link and send it via iMessage/SMS (staff-gated)."""
    actor = _staff_actor(request)
    if not actor:
        return _staff_required()
    data = await request.json() or {}
    booking_number = data.get("booking_number", "").strip()
    role = data.get("role", "indemnitor").strip().lower()
    phone = data.get("phone", "").strip()

    if not booking_number or not phone:
        return JSONResponse(
            {"error": "booking_number and phone are required"},
            status_code=400,
        )

    # Defendant check-in path: use enrollment service (transparent copy, no covert geo line)
    if role == "defendant" and data.get("purpose") == "checkin":
        from dashboard.services.checkin_enrollment_service import send_checkin_link
        result = await send_checkin_link(
            booking_number,
            phone=phone,
            actor=actor,
        )
        status = 200 if result.get("success") else 400
        return JSONResponse(result, status_code=status)

    token_result = await generate_portal_token(
        booking_number=booking_number,
        role=role,
        created_by=f"staff_send:{actor}",
    )

    if not token_result.get("success"):
        return JSONResponse(token_result, status_code=404)
    portal_url = token_result["url"]
    defendant_name = token_result.get("defendant_name", "defendant")

    if role == "indemnitor":
        msg = (
            f"Hi! Here's your Shamrock Bail Bonds portal link for "
            f"{defendant_name}'s bond. View status, make payments, and "
            f"manage everything in one place:\n\n{portal_url}\n\n"
            f"☘️ Shamrock Bail Bonds (239) 332-2245"
        )
    else:
        msg = (
            f"Hi {defendant_name}! Here's your Shamrock Bail Bonds portal. "
            f"Check in, view court dates, and stay compliant:\n\n{portal_url}\n\n"
            f"☘️ Shamrock Bail Bonds (239) 332-2245"
        )

    send_result = {"success": False, "sent": False, "queued": False, "error": "No messaging channel available"}
    try:
        from dashboard.services.bb_client import (
            send_message_universal,
            normalize_bb_send_result,
            bb_send_accepted,
        )
        send_result = normalize_bb_send_result(await send_message_universal(phone, msg))
        accepted = bb_send_accepted(send_result)
    except Exception as e:
        logger.warning("Portal link send via BB failed: %s", e)
        send_result = {"success": False, "sent": False, "queued": False, "error": str(e)}
        accepted = False

    return {
        "token_generated": True,
        "url": portal_url,
        "message_sent": accepted,
        "channel": send_result.get("channel", "unknown"),
        "queued": bool(send_result.get("queued")),
        "booking_number": booking_number,
        "role": role,
    }


@portal_bp.get("/api/portal/stats")
async def portal_stats():
    """Return aggregate portal token stats for the dashboard KPI panel."""
    col = get_collection("portal_tokens")
    now = datetime.now(timezone.utc)
    total_active = await col.count_documents(
        {"expires_at": {"$gt": now}, "active": True}
    )
    checkins_col = get_collection("bond_checkins")
    checkins_since = now - timedelta(days=7)
    checkins_7d = await checkins_col.count_documents({
        "checkin_at": {"$gte": checkins_since},
        "status": "completed",
    })
    total_all_time = await col.count_documents({})
    return {
        "active_tokens": total_active,
        "checkins_7d": checkins_7d,
        "total_all_time": total_all_time,
    }


@portal_bp.get("/api/portal/monitoring-conditions")
async def portal_monitoring_conditions():
    """Staff: return standard check-in condition language (Track C)."""
    from dashboard.services.checkin_enrollment_service import get_condition_language
    return get_condition_language()


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _error_page(message: str) -> str:
    """Return a branded error page for invalid/expired tokens."""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Shamrock Bail Bonds</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:#f8faf9;font-family:'DM Sans',system-ui,sans-serif;color:#1a2e1a;padding:24px}}
.card{{background:#fff;border-radius:24px;padding:48px 32px;max-width:400px;width:100%;
  text-align:center;box-shadow:0 4px 24px rgba(0,0,0,0.06)}}
.logo{{font-size:48px;margin-bottom:16px}}
h1{{font-size:20px;font-weight:700;margin-bottom:8px;color:#1a2e1a}}
p{{color:#5a6b5a;font-size:15px;line-height:1.5;margin-bottom:24px}}
a{{display:inline-block;padding:14px 32px;background:#00844a;color:#fff;
  text-decoration:none;border-radius:14px;font-weight:600;font-size:15px;
  transition:all .2s}}
a:hover{{background:#006b3c;transform:translateY(-1px)}}
</style>
</head>
<body>
<div class="card">
  <div class="logo">☘️</div>
  <h1>Link Expired</h1>
  <p>{message}</p>
  <a href="https://www.shamrockbailbonds.biz">Visit Shamrock Bail Bonds</a>
</div>
</body>
</html>"""
    return html
