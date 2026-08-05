"""
ShamrockLeads — Sub-Agent Management & Session API
===================================================
God-Admin-only endpoints for whitelist management.
Session identity endpoint for all users.

Endpoints:
  GET  /api/session/me              — Current user identity + permissions
  GET  /api/sub-agents/list         — List all whitelisted agents (God-Admin only)
  POST /api/sub-agents/add          — Whitelist a new agent (God-Admin only)
  POST /api/sub-agents/remove       — Revoke an agent (God-Admin only)
  POST /api/sub-agents/update       — Update agent details (God-Admin only)
"""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.auth.pin_middleware import session_is_god_admin, get_session_from_request
from dashboard.services.sub_agent_service import (
    list_sub_agents,
    add_sub_agent,
    remove_sub_agent,
    get_agent_by_license,
)

logger = logging.getLogger(__name__)

sub_agents_bp = APIRouter(prefix="/api", tags=["sub_agents"])

# ── Restricted tabs for sub-agents (must match index.html data-tab values) ─
SUB_AGENT_BLOCKED_TABS = [
    "tabAnalytics",
    "tabAccounting",
    "tabFTA",
    "tabAutomations",
    "tabPaperwork",
    "tabReports",
    "tabPortal",
    "tabMultiState",
    "tabBondIntel",
    "tabHealth",
    "tabAdminHygiene",
    "tabIntelligence",
    "tabOSINT",
    "tabALPR",
    "tabEnrichment",
    "tabAlphaIntel",
    "tabLegalNLP",
    "tabSocial",
    "tabStaff",
]

# API prefixes blocked for sub-agents (enforced in pin_middleware via agent_scope)
SUB_AGENT_BLOCKED_API_PREFIXES = [
    "/api/accounting",
    "/api/scraper",
    "/api/osint",
    "/api/alpr",
    "/api/data-retention",
    "/api/social",
    "/api/admin-hygiene",
    "/api/analytics",
    "/api/multi-state",
    "/api/intelligence",
    "/api/enrichment",
    "/api/legal-nlp",
    "/api/reports",
    "/api/fta",
    "/api/discharge-monitor",
    "/api/sub-agents",
]


@sub_agents_bp.get("/session/me")
async def session_me(request: Request):
    """Return the current user's session identity, role, and permissions."""
    sess = get_session_from_request(request)
    if not sess:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    role = sess.get("role", "god_admin")
    is_god = role in ("god_admin", "admin")

    return {
        "success": True,
        "email": sess.get("email", ""),
        "role": role,
        "agent_name": sess.get("agent_name", ""),
        "license_number": sess.get("license_number", ""),
        "is_god_admin": is_god,
        "is_sub_agent": role == "sub_agent",
        "blocked_tabs": [] if is_god else SUB_AGENT_BLOCKED_TABS,
        "blocked_api_prefixes": [] if is_god else SUB_AGENT_BLOCKED_API_PREFIXES,
    }


@sub_agents_bp.get("/sub-agents/list")
async def api_list_sub_agents(request: Request):
    """List all whitelisted sub-agents. God-Admin only."""
    if not session_is_god_admin(request):
        return JSONResponse({"error": "God-Admin access required"}, status_code=403)
    agents = await list_sub_agents()
    return {"success": True, "agents": agents, "total": len(agents)}


@sub_agents_bp.post("/sub-agents/add")
async def api_add_sub_agent(request: Request):
    """Whitelist a new sub-agent. God-Admin only."""
    if not session_is_god_admin(request):
        return JSONResponse({"error": "God-Admin access required"}, status_code=403)

    data = await request.json()
    agent_name = str(data.get("agent_name", "")).strip()
    license_number = str(data.get("license_number", "")).strip()
    phone = str(data.get("phone", "")).strip()
    notes = str(data.get("notes", "")).strip()

    if not agent_name or not license_number:
        return JSONResponse({"error": "agent_name and license_number are required"}, status_code=400)

    sess = get_session_from_request(request)
    whitelisted_by = sess.get("email", "admin@shamrockbailbonds.biz") if sess else "admin@shamrockbailbonds.biz"

    result = await add_sub_agent(
        agent_name=agent_name,
        license_number=license_number,
        phone=phone,
        whitelisted_by=whitelisted_by,
        notes=notes,
    )
    status_code = 200 if result.get("success") else 409
    return JSONResponse(result, status_code=status_code)


@sub_agents_bp.post("/sub-agents/remove")
async def api_remove_sub_agent(request: Request):
    """Revoke a sub-agent. God-Admin only."""
    if not session_is_god_admin(request):
        return JSONResponse({"error": "God-Admin access required"}, status_code=403)

    data = await request.json()
    license_number = str(data.get("license_number", "")).strip()
    if not license_number:
        return JSONResponse({"error": "license_number is required"}, status_code=400)

    sess = get_session_from_request(request)
    revoked_by = sess.get("email", "admin@shamrockbailbonds.biz") if sess else "admin@shamrockbailbonds.biz"

    result = await remove_sub_agent(license_number, revoked_by=revoked_by)
    status_code = 200 if result.get("success") else 404
    return JSONResponse(result, status_code=status_code)


@sub_agents_bp.post("/sub-agents/update")
async def api_update_sub_agent(request: Request):
    """Update a sub-agent's details. God-Admin only."""
    if not session_is_god_admin(request):
        return JSONResponse({"error": "God-Admin access required"}, status_code=403)

    data = await request.json()
    license_number = str(data.get("license_number", "")).strip()
    if not license_number:
        return JSONResponse({"error": "license_number is required"}, status_code=400)

    from dashboard.extensions import get_collection
    sub_agents_col = get_collection("sub_agents")

    update_fields = {}
    for field in ("agent_name", "phone", "notes"):
        if field in data:
            update_fields[field] = str(data[field]).strip()

    if not update_fields:
        return JSONResponse({"error": "No fields to update"}, status_code=400)

    result = await sub_agents_col.update_one(
        {"license_number": {"$regex": f"^{license_number}$", "$options": "i"}},
        {"$set": update_fields},
    )
    if result.modified_count == 0:
        return JSONResponse({"error": f"Agent {license_number} not found"}, status_code=404)

    return {"success": True, "license_number": license_number, "updated": list(update_fields.keys())}
