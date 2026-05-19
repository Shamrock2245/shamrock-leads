# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""
ShamrockLeads — Client Portal API Blueprint
Public-facing endpoints for defendant/indemnitor self-service.

Routes:
  GET  /c/<token>                    — Portal page (HTML)
  GET  /api/portal/<token>/status    — Case status (JSON)
  GET  /api/portal/<token>/payments  — Payment history (JSON)
  POST /api/portal/<token>/checkin   — Submit check-in (GPS + selfie)
  GET  /api/portal/<token>/payment-link — SwipeSimple link

Staff-only (session-authed):
  POST /api/portal/generate          — Generate a new portal token
  GET  /api/portal/tokens/<booking>  — List active tokens for a bond
  POST /api/portal/revoke            — Revoke a token
"""
from __future__ import annotations

import os
import secrets
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from starlette.responses import Response
from fastapi.responses import JSONResponse

_PUBLIC_URL = os.getenv("DASHBOARD_PUBLIC_URL", "https://shamrockbailbonds.biz")

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

portal_bp = APIRouter(prefix="/api", tags=["client_portal"])
DASHBOARD_DIR = os.path.dirname(os.path.dirname(__file__))


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC ROUTES — No auth required (token-gated)
# ══════════════════════════════════════════════════════════════════════════════

@portal_bp.get("/c/<token>")
async def portal_page(token: str):
    """Serve the client portal HTML page."""
    # Quick token validation — just check if it exists
    token_data = await validate_token(token)
    if not token_data:
        return JSONResponse(await _error_page("This link has expired or is no longer valid."), status_code=404)

    # Serve the portal HTML — all data loading happens via JS fetch
    portal_path = os.path.join(DASHBOARD_DIR, "portal.html")
    if not os.path.isfile(portal_path):
        logger.error("portal.html not found at %s", portal_path)
        return JSONResponse(await _error_page("Portal temporarily unavailable."), status_code=500)

    with open(portal_path, "r") as f:
        html = f.read()

    resp = await JSONResponse(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@portal_bp.get("/api/portal/<token>/status")
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
        return JSONResponse(status, status_code=404)
    status["role"] = token_data["role"]
    return status


@portal_bp.post("/api/portal/<token>/checkin")
async def portal_checkin(request: Request, token: str):
    """Submit a defendant check-in (GPS + optional selfie)."""
    token_data = await validate_token(token)
    if not token_data:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)

    if token_data["role"] != "defendant":
        return JSONResponse({"error": "Check-in is only available for defendants"}, status_code=403)

    data = await request.json() or {}

    result = await submit_portal_checkin(
        booking_number=token_data["booking_number"],
        lat=data.get("lat"),
        lng=data.get("lng"),
        accuracy=data.get("accuracy"),
        selfie_data=data.get("selfie"),
        notes=data.get("notes", ""),
    )

    return result, 201 if result.get("success") else 400


@portal_bp.get("/api/portal/<token>/payment-link")
async def portal_payment_link(token: str):
    """Return the SwipeSimple payment link for this bond."""
    token_data = await validate_token(token)
    if not token_data:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)

    swipesimple_link = os.getenv(
        "SWIPESIMPLE_PAYMENT_LINK",
        "https://swipesimple.com/links/lnk_b6bf996f4c57bb340a150e297e769abd",
    )

    return {
        "payment_link": swipesimple_link,
        "booking_number": token_data["booking_number"],
        "defendant_name": token_data["defendant_name"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# STAFF ROUTES — Session-authed (behind PIN gate)
# ══════════════════════════════════════════════════════════════════════════════

@portal_bp.post("/api/portal/generate")
async def generate_token_endpoint(request: Request):
    """Generate a new portal magic link for a bond case."""
    data = await request.json() or {}
    booking_number = data.get("booking_number", "").strip()
    role = data.get("role", "indemnitor").strip().lower()
    created_by = data.get("created_by", "staff")

    if not booking_number:
        return JSONResponse({"error": "booking_number is required"}, status_code=400)
    if role not in ("defendant", "indemnitor"):
        return JSONResponse({"error": "role must be 'defendant' or 'indemnitor'"}, status_code=400)

    result = await generate_portal_token(
        booking_number=booking_number,
        role=role,
        created_by=created_by,
    )

    if result.get("success"):
        return JSONResponse(result, status_code=201)
    return JSONResponse(result, status_code=404)
@portal_bp.get("/api/portal/tokens/<booking_number>")
async def list_tokens(booking_number: str):
    """List all active portal tokens for a bond (staff view)."""
    tokens = await get_portal_tokens_for_bond(booking_number)
    return {"tokens": tokens, "count": len(tokens)}


@portal_bp.post("/api/portal/revoke")
async def revoke_token_endpoint(request: Request):
    """Revoke a portal token."""
    data = await request.json() or {}
    token = data.get("token", "").strip()
    if not token:
        return JSONResponse({"error": "token is required"}, status_code=400)

    result = await revoke_portal_token(token)
    return result, 200 if result.get("success") else 404


@portal_bp.post("/api/portal/send-link")
async def send_portal_link(request: Request):
    """Generate a portal link and send it via iMessage/SMS."""
    data = await request.json() or {}
    booking_number = data.get("booking_number", "").strip()
    role = data.get("role", "indemnitor").strip().lower()
    phone = data.get("phone", "").strip()
    channel = data.get("channel", "imessage")  # imessage, sms, or both

    if not booking_number or not phone:
        return JSONResponse({"error": "booking_number and phone are required"}, status_code=400)

    # Generate token
    token_result = await generate_portal_token(
        booking_number=booking_number,
        role=role,
        created_by="staff_send",
    )

    if not token_result.get("success"):
        return JSONResponse(token_result, status_code=404)
    portal_url = token_result["url"]
    defendant_name = token_result.get("defendant_name", "defendant")

    # Generate a geolocator link (mandatory project requirement for all outbound texts)
    geo_token = secrets.token_urlsafe(12)
    geo_url = f"{_PUBLIC_URL.rstrip('/')}/g/{geo_token}"
    try:
        geo_col = get_collection("geo_pings")
        await geo_col.insert_one({
            "token": geo_token,
            "booking_number": booking_number,
            "phone": phone,
            "recipient": f"portal_link_{role}",
            "status": "pending",
            "ping_count": 0,
            "pings": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        geo_url = ""  # non-fatal

    geo_line = f"\nConfirm your location: {geo_url}" if geo_url else ""

    if role == "indemnitor":
        msg = (
            f"Hi! Here's your Shamrock Bail Bonds portal link for "
            f"{defendant_name}'s bond. View status, make payments, and "
            f"manage everything in one place:\n\n{portal_url}{geo_line}\n\n"
            f"☘️ Shamrock Bail Bonds (239) 332-2245"
        )
    else:
        msg = (
            f"Hi {defendant_name}! Here's your Shamrock Bail Bonds portal. "
            f"Check in, view court dates, and stay compliant:\n\n{portal_url}{geo_line}\n\n"
            f"☘️ Shamrock Bail Bonds (239) 332-2245"
        )

    # Send via iMessage (BlueBubbles)
    send_result = {"success": False, "error": "No messaging channel available"}
    try:
        from dashboard.services.bb_client import send_message_universal
        send_result = await send_message_universal(phone, msg)
    except Exception as e:
        logger.warning("Portal link send via iMessage failed: %s", e)
        # Fallback to Twilio SMS
        try:
            from dashboard.services.twilio_service import TwilioService
            twilio = TwilioService()
            sms_result = twilio.send_sms(phone, msg)
            send_result = {"success": True, "channel": "sms", "sid": sms_result}
        except Exception as sms_err:
            logger.warning("Portal link send via SMS also failed: %s", sms_err)
            send_result = {"success": False, "error": str(sms_err)}

    return {
        "token_generated": True,
        "url": portal_url,
        "message_sent": send_result.get("success", False),
        "channel": send_result.get("channel", "unknown"),
        "booking_number": booking_number,
        "role": role,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/portal/stats  — aggregate portal token stats for dashboard KPI
# ─────────────────────────────────────────────────────────────────────────────
@portal_bp.get("/api/portal/stats")
async def portal_stats():
    """Return aggregate portal token stats for the dashboard KPI panel."""
    col = get_collection("portal_tokens")
    now = datetime.now(timezone.utc)
    total_active = await col.count_documents({"expires_at": {"$gt": now}, "revoked": {"$ne": True}})
    total_checkins = await col.count_documents({"last_checkin": {"$exists": True}})
    total_all_time = await col.count_documents({})
    return {
        "active_tokens": total_active,
        "total_checkins": total_checkins,
        "total_all_time": total_all_time,
    }


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
