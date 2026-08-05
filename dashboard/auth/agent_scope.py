"""
ShamrockLeads — Sub-agent data scoping helpers
==============================================
God-Admin sees all records. Sub-agents only see bonds / POAs attributed
to their FL license number or agent name.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from starlette.requests import Request

from dashboard.auth.pin_middleware import get_session_from_request, session_is_god_admin


# API path prefixes blocked for sub_agent sessions (server-side hard gate)
SUB_AGENT_BLOCKED_API_PREFIXES = (
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
    "/api/sub-agents",  # management is God-Admin only (session/me is separate)
)


def is_sub_agent_session(request: Request) -> bool:
    sess = get_session_from_request(request)
    return bool(sess and sess.get("role") == "sub_agent")


def agent_identity(request: Request) -> Dict[str, str]:
    """Return agent_name + license_number from the session (empty for god_admin)."""
    sess = get_session_from_request(request) or {}
    return {
        "agent_name": str(sess.get("agent_name") or "").strip(),
        "license_number": str(sess.get("license_number") or "").strip().upper(),
        "role": str(sess.get("role") or ""),
        "email": str(sess.get("email") or ""),
    }


def path_blocked_for_sub_agent(path: str) -> bool:
    """True when a sub_agent must not call this API path."""
    if path == "/api/session/me" or path.startswith("/api/session/"):
        return False
    # Sub-agent whitelist management is God-Admin only
    if path.startswith("/api/sub-agents"):
        return True
    for prefix in SUB_AGENT_BLOCKED_API_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def bond_scope_query(request: Request) -> Optional[Dict[str, Any]]:
    """
    Mongo match clause for active_bonds.
    None = no extra filter (God-Admin).
    """
    if session_is_god_admin(request) or not is_sub_agent_session(request):
        return None
    ident = agent_identity(request)
    license_number = ident["license_number"]
    agent_name = ident["agent_name"]
    if not license_number and not agent_name:
        # Fail closed: empty identity must not see all bonds
        return {"_id": {"$exists": False}}
    ors = []
    if license_number:
        ors.extend(
            [
                {"agent_license": {"$regex": f"^{license_number}$", "$options": "i"}},
                {"writing_agent": {"$regex": f"^{license_number}$", "$options": "i"}},
                {"writing_agent_license": {"$regex": f"^{license_number}$", "$options": "i"}},
                {"license_number": {"$regex": f"^{license_number}$", "$options": "i"}},
            ]
        )
    if agent_name:
        ors.extend(
            [
                {"agent_name": {"$regex": f"^{agent_name}$", "$options": "i"}},
                {"writing_agent": {"$regex": f"^{agent_name}$", "$options": "i"}},
                {"writing_agent_name": {"$regex": f"^{agent_name}$", "$options": "i"}},
            ]
        )
    return {"$or": ors}


def poa_scope_query(request: Request) -> Optional[Dict[str, Any]]:
    """
    Mongo match clause for poa_inventory rows visible to a sub-agent.
    None = God-Admin (full inventory).
    """
    if session_is_god_admin(request) or not is_sub_agent_session(request):
        return None
    ident = agent_identity(request)
    license_number = ident["license_number"]
    agent_name = ident["agent_name"]
    if not license_number and not agent_name:
        return {"_id": {"$exists": False}}
    ors = []
    if license_number:
        ors.extend(
            [
                {"agent_license": {"$regex": f"^{license_number}$", "$options": "i"}},
                {"assigned_to_license": {"$regex": f"^{license_number}$", "$options": "i"}},
            ]
        )
    if agent_name:
        ors.extend(
            [
                {"assigned_to_agent": {"$regex": f"^{agent_name}$", "$options": "i"}},
                {"agent_name": {"$regex": f"^{agent_name}$", "$options": "i"}},
            ]
        )
    return {"$or": ors}


def merge_scope(base: Optional[Dict[str, Any]], scope: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """AND an existing match dict with an agent scope clause."""
    base = dict(base or {})
    if not scope:
        return base
    if not base:
        return dict(scope)
    return {"$and": [base, scope]}
