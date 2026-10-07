"""
BailSafe recovery role — fail-closed route allowlist.

A ``recovery`` session is denied every route unless the method and path are
listed here. Login, liveness, and ``GET /api/session/me`` are the only
non-recovery surfaces. Staff share, unshare, agent provisioning, and the
forfeiture picker are not on this list.

This is not ``bounty_hunter_service`` and not ``intake_recovery_service``.
"""
from __future__ import annotations

import re

from starlette.requests import Request

from dashboard.auth.pin_middleware import get_session_from_request

STAFF_ROLES = frozenset({"god_admin", "admin", "staff"})

# Exact (METHOD, path) pairs. Trailing slashes are stripped before lookup.
_EXACT_ALLOWED = frozenset({
    ("GET", "/recovery"),
    ("HEAD", "/recovery"),
    ("GET", "/login"),
    ("HEAD", "/login"),
    ("POST", "/login"),
    ("GET", "/health"),
    ("HEAD", "/health"),
    ("GET", "/health/live"),
    ("HEAD", "/health/live"),
    ("GET", "/favicon.ico"),
    ("HEAD", "/favicon.ico"),
    ("GET", "/favicon.png"),
    ("GET", "/apple-touch-icon.png"),
    ("GET", "/shamrock-logo.png"),
    ("GET", "/manifest.json"),
    ("GET", "/robots.txt"),
    ("HEAD", "/robots.txt"),
    ("GET", "/api/session/me"),
    ("HEAD", "/api/session/me"),
    ("POST", "/api/recovery/logout"),
    ("GET", "/api/recovery/cases"),
    ("HEAD", "/api/recovery/cases"),
})

_ID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

# Recovery-facing case routes only. Staff share/revoke/agents/candidates
# are intentionally absent so a recovery cookie cannot reach them.
_ALLOWED_PATTERNS = (
    ("GET", re.compile(rf"^/api/recovery/cases/{_ID}$")),
    ("HEAD", re.compile(rf"^/api/recovery/cases/{_ID}$")),
    ("POST", re.compile(rf"^/api/recovery/cases/{_ID}/notes$")),
    ("POST", re.compile(rf"^/api/recovery/cases/{_ID}/disposition$")),
    ("POST", re.compile(rf"^/api/recovery/cases/{_ID}/documents$")),
    ("GET", re.compile(rf"^/api/recovery/cases/{_ID}/documents/{_ID}$")),
    ("HEAD", re.compile(rf"^/api/recovery/cases/{_ID}/documents/{_ID}$")),
)


def normalize_recovery_path(path: str) -> str:
    raw = str(path or "").split("?", 1)[0].split("#", 1)[0]
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw or ".." in raw:
        return ""
    if len(raw) > 1:
        raw = raw.rstrip("/")
    return raw


def path_allowed_for_recovery(path: str, method: str = "GET") -> bool:
    """True only for the recovery allowlist. Everything else is denied."""
    method_name = (method or "GET").upper()
    if method_name not in ("GET", "HEAD", "POST"):
        return False
    clean = normalize_recovery_path(path)
    if not clean:
        return False
    if (method_name, clean) in _EXACT_ALLOWED:
        return True
    for allowed_method, pattern in _ALLOWED_PATTERNS:
        if method_name == allowed_method and pattern.match(clean):
            return True
    return False


def session_role(request: Request) -> str:
    sess = get_session_from_request(request) or {}
    return str(sess.get("role") or "")


def session_is_recovery(request: Request) -> bool:
    return session_role(request) == "recovery"


def session_is_staff(request: Request) -> bool:
    """God-admin, admin, and staff. Recovery and sub-agent are not staff."""
    role = session_role(request)
    if role in ("recovery", "sub_agent"):
        return False
    return role in STAFF_ROLES


def recovery_id_from_session(request: Request) -> str:
    sess = get_session_from_request(request) or {}
    return str(sess.get("recovery_id") or "").strip().upper()
