"""Unit tests — sub-agent RBAC path blocking + scope query builders."""
from __future__ import annotations

from dashboard.auth.agent_scope import (
    path_blocked_for_sub_agent,
    merge_scope,
)


def test_session_me_not_blocked():
    assert path_blocked_for_sub_agent("/api/session/me") is False


def test_sub_agents_management_blocked():
    assert path_blocked_for_sub_agent("/api/sub-agents/list") is True
    assert path_blocked_for_sub_agent("/api/sub-agents/add") is True


def test_restricted_prefixes_blocked():
    assert path_blocked_for_sub_agent("/api/alpr/status") is True
    assert path_blocked_for_sub_agent("/api/osint/search") is True
    assert path_blocked_for_sub_agent("/api/accounting/summary") is True
    assert path_blocked_for_sub_agent("/api/discharge-monitor/scan") is True


def test_bond_desk_apis_allowed():
    assert path_blocked_for_sub_agent("/api/active-bonds") is False
    assert path_blocked_for_sub_agent("/api/poa/list") is False
    assert path_blocked_for_sub_agent("/api/calendar/events") is False
    assert path_blocked_for_sub_agent("/api/intake/queue") is False


def test_merge_scope_and():
    base = {"status": "active"}
    scope = {"agent_license": "P1"}
    merged = merge_scope(base, scope)
    assert "$and" in merged
    assert base in merged["$and"]
    assert scope in merged["$and"]
    assert merge_scope({}, None) == {}
    assert merge_scope(None, scope) == scope


def test_sub_agent_service_importable():
    from dashboard.services import sub_agent_service, forfeiture_alert_service
    from dashboard.routers import sub_agents

    assert callable(sub_agent_service.is_whitelisted)
    assert callable(forfeiture_alert_service.detect_forfeiture)
    assert sub_agents.sub_agents_bp.prefix == "/api"
    det = forfeiture_alert_service.detect_forfeiture(
        "Notice of Bond Forfeiture", "Defendant John Doe case 26CF123 forfeiture estreature"
    )
    assert det["is_forfeiture"] is True
    assert det["confidence"] >= 40
