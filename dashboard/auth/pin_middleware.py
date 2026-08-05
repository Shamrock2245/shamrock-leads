"""
ShamrockLeads — FastAPI PIN Authentication Middleware

Stateless signed-cookie approach using itsdangerous.
Supports God-Admin (full access) and Sub-Agent (restricted) roles.

Roles:
  - god_admin: Full unrestricted access (PIN 224545 with no agent fields, or admin email)
  - sub_agent: Restricted access — must be whitelisted in MongoDB `sub_agents` collection.
               Sees only their own bonds, revenue, and assigned POAs.

Usage in main.py:
    from dashboard.auth.pin_middleware import PinAuthMiddleware, mount_login_routes
    app.add_middleware(PinAuthMiddleware)
    mount_login_routes(app)
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse, RedirectResponse, HTMLResponse

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from dashboard.auth.super_admin import (
    PRIMARY_SUPER_ADMIN,
    is_admin_email,
    normalize_email,
    resolve_role_for_email,
)

# ── Configuration ─────────────────────────────────────────────────────────────

DASHBOARD_PIN = os.getenv("DASHBOARD_PIN", "224545")
COOKIE_NAME = "sl_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# Paths that bypass auth entirely
OPEN_PATHS = frozenset({
    "/login", "/health", "/health/live", "/api/stats",
    "/docs", "/redoc", "/openapi.json",
    "/manifest.json", "/favicon.ico", "/favicon.png", "/apple-touch-icon.png", "/shamrock-logo.png",
})

# File extensions that are always public (static assets)
_STATIC_EXTENSIONS = (
    ".js", ".css", ".ico", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2",
    ".ttf", ".eot", ".webp", ".gif", ".json",
)

# Prefixes that bypass auth
OPEN_PREFIXES = (
    "/api/webhooks/",
    "/api/automation/",
    "/g/",
    "/c/",
    "/api/portal/",
    "/api/config/bluebubbles-url",
    "/traccar/setup/",
    "/api/traccar/device-status/",
)

# OAuth popup paths
OAUTH_PREFIXES = (
    "/api/social/oauth/google/",
    "/api/social/oauth/twitter/",
    "/api/social/oauth/linkedin/",
    "/api/social/oauth/meta/",
)

# Valid master PINs
VALID_PINS = frozenset({DASHBOARD_PIN, "224545"})


def _get_serializer() -> URLSafeTimedSerializer:
    """Build the cookie signer from SECRET_KEY (required in production)."""
    secret = os.getenv("SECRET_KEY", "").strip()
    if not secret:
        if os.getenv("ENV", os.getenv("ENVIRONMENT", "")).lower() in (
            "production", "prod",
        ) or os.getenv("REQUIRE_SECRET_KEY", "").lower() in ("1", "true", "yes"):
            raise RuntimeError(
                "SECRET_KEY must be set for dashboard session cookies in production"
            )
        secret = "shamrock-dev-only-session-key-v1-not-for-production"
    return URLSafeTimedSerializer(secret)


def _sign_token(
    email: str | None = None,
    role: str | None = None,
    agent_name: str | None = None,
    license_number: str | None = None,
) -> str:
    """Create a signed session token with identity claims."""
    s = _get_serializer()
    payload: dict[str, Any] = {"auth": True, "t": int(time.time())}
    if email:
        payload["email"] = normalize_email(email)
        payload["role"] = role or resolve_role_for_email(email)
    else:
        payload["email"] = PRIMARY_SUPER_ADMIN
        payload["role"] = role or "god_admin"
    if agent_name:
        payload["agent_name"] = str(agent_name)
    if license_number:
        payload["license_number"] = str(license_number)
    return s.dumps(payload)


def _load_session(token: str | None) -> dict[str, Any] | None:
    """Return session payload dict or None if invalid/expired."""
    if not token:
        return None
    try:
        s = _get_serializer()
        data = s.loads(token, max_age=COOKIE_MAX_AGE)
        if not isinstance(data, dict) or not data.get("auth"):
            return None
        return data
    except (BadSignature, SignatureExpired):
        return None


def get_session_from_request(request: Request) -> dict[str, Any] | None:
    """Public helper for routers that need email/role from the session cookie."""
    return _load_session(request.cookies.get(COOKIE_NAME))


def session_is_admin(request: Request) -> bool:
    """True when the current session is god_admin or admin."""
    sess = get_session_from_request(request)
    if not sess:
        return False
    if sess.get("role") in ("admin", "god_admin"):
        return True
    return is_admin_email(sess.get("email"))


def session_is_god_admin(request: Request) -> bool:
    """True only for God-Admin level access (never sub_agent)."""
    sess = get_session_from_request(request)
    if not sess:
        return False
    if sess.get("role") == "sub_agent":
        return False
    # god_admin (current) + legacy admin role + admin email allowlist
    return (
        sess.get("role") in ("god_admin", "admin")
        or is_admin_email(sess.get("email"))
    )


# ── Middleware ─────────────────────────────────────────────────────────────────

class PinAuthMiddleware(BaseHTTPMiddleware):
    """Gate all routes behind PIN auth (except whitelisted paths)."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not DASHBOARD_PIN:
            env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").lower()
            if env in ("production", "prod") or os.getenv("REQUIRE_DASHBOARD_PIN", "").lower() in (
                "1", "true", "yes",
            ):
                return JSONResponse({"error": "Dashboard PIN not configured"}, status_code=503)
            return await call_next(request)

        path = request.url.path

        if path in OPEN_PATHS or any(path.startswith(p) for p in OPEN_PREFIXES):
            return await call_next(request)

        if path.startswith("/static/") or any(path.endswith(ext) for ext in _STATIC_EXTENSIONS):
            return await call_next(request)

        if any(path.startswith(p) for p in OAUTH_PREFIXES):
            return await call_next(request)

        cookie = request.cookies.get(COOKIE_NAME)
        sess = _load_session(cookie) if cookie else None
        if sess:
            request.state.sl_session = sess
            request.state.sl_email = sess.get("email") or PRIMARY_SUPER_ADMIN
            request.state.sl_role = sess.get("role") or "god_admin"
            request.state.sl_agent_name = sess.get("agent_name") or ""
            request.state.sl_license_number = sess.get("license_number") or ""
            request.state.sl_is_admin = (
                sess.get("role") in ("admin", "god_admin") or is_admin_email(sess.get("email"))
            )
            # Sub-agent hard gate: block restricted API prefixes server-side
            if (
                sess.get("role") == "sub_agent"
                and path.startswith("/api/")
            ):
                from dashboard.auth.agent_scope import path_blocked_for_sub_agent

                if path_blocked_for_sub_agent(path):
                    return JSONResponse(
                        {
                            "error": "Access denied — sub-agent role cannot use this endpoint",
                            "role": "sub_agent",
                            "path": path,
                        },
                        status_code=403,
                    )
            return await call_next(request)

        # Not authenticated
        if path.startswith("/api/"):
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        return RedirectResponse("/login", status_code=302)


# ── Login Routes ──────────────────────────────────────────────────────────────

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shamrock — Agent & Admin Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#0a0f1a 0%,#1a2332 50%,#0d1520 100%);
  font-family:'Inter',system-ui,sans-serif;color:#e0e0e0}
.card{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);
  border-radius:20px;padding:40px 36px;width:420px;backdrop-filter:blur(20px);
  box-shadow:0 20px 60px rgba(0,0,0,0.5)}
.logo{font-size:28px;font-weight:700;text-align:center;margin-bottom:4px;
  background:linear-gradient(135deg,#00d26a,#00b85c);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent}
.sub{text-align:center;color:#8899aa;font-size:13px;margin-bottom:24px}
.tab-row{display:flex;gap:8px;background:rgba(255,255,255,0.05);padding:4px;border-radius:10px;margin-bottom:20px}
.tab-btn{flex:1;padding:8px;border:none;border-radius:8px;background:none;color:#8899aa;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s}
.tab-btn.active{background:#00d26a;color:#000}
label{display:block;font-size:12px;color:#8899aa;margin-bottom:6px;margin-top:12px}
input{width:100%;padding:12px 14px;border:1px solid rgba(255,255,255,0.12);
  background:rgba(255,255,255,0.06);border-radius:10px;color:#fff;font-size:15px;
  outline:none;transition:border-color .3s}
input#pin{font-size:18px;letter-spacing:6px;text-align:center}
input:focus{border-color:#00d26a}
button.submit-btn{width:100%;margin-top:20px;padding:14px;border:none;border-radius:10px;
  background:linear-gradient(135deg,#00d26a,#00b85c);color:#000;font-size:16px;
  font-weight:700;cursor:pointer;transition:transform .2s,box-shadow .2s}
button.submit-btn:hover{transform:translateY(-1px);box-shadow:0 8px 24px rgba(0,210,106,0.3)}
.err{color:#ff6b6b;text-align:center;margin-top:12px;font-size:13px;min-height:20px}
.hint{text-align:center;color:#667788;font-size:11px;margin-top:16px;line-height:1.4}
</style></head><body>
<div class="card">
  <div class="logo">☘️ Shamrock</div>
  <div class="sub">Bail Bond Auto-CRM — Agent & Admin Portal</div>
  <div class="tab-row">
    <button class="tab-btn active" id="tabGodBtn" onclick="switchLoginMode('god')">👑 God Admin</button>
    <button class="tab-btn" id="tabSubBtn" onclick="switchLoginMode('sub')">🏷️ Sub-Agent</button>
  </div>
  <form id="f" method="POST" action="/login">
    <div id="subAgentFields" style="display:none">
      <label for="agent_name">Sub-Agent Full Name</label>
      <input type="text" name="agent_name" id="agent_name" placeholder="e.g. John Smith">
      <label for="license_number">FL License Number</label>
      <input type="text" name="license_number" id="license_number" placeholder="e.g. P123456">
    </div>
    <div id="godAdminFields">
      <label for="email">Owner / Admin Email (optional)</label>
      <input type="email" name="email" id="email" placeholder="admin@shamrockbailbonds.biz" autocomplete="username">
    </div>
    <label for="pin">Agency Master PIN</label>
    <input type="password" name="pin" id="pin" maxlength="12" placeholder="••••••" autofocus autocomplete="current-password">
    <button type="submit" class="submit-btn" id="subBtnText">Unlock God-Admin Access</button>
    <div class="err" id="err"></div>
    <div class="hint" id="loginHint">God-Admin grants full unrestricted control over the entire system.</div>
  </form>
</div>
<script>
let mode = 'god';
function switchLoginMode(m) {
  mode = m;
  document.getElementById('tabGodBtn').classList.toggle('active', m === 'god');
  document.getElementById('tabSubBtn').classList.toggle('active', m === 'sub');
  document.getElementById('godAdminFields').style.display = m === 'god' ? 'block' : 'none';
  document.getElementById('subAgentFields').style.display = m === 'sub' ? 'block' : 'none';
  document.getElementById('subBtnText').textContent = m === 'god' ? 'Unlock God-Admin Access' : 'Login as Sub-Agent';
  document.getElementById('loginHint').textContent = m === 'god'
    ? 'God-Admin grants full unrestricted control over the entire system.'
    : 'Sub-Agent login requires whitelisted name and FL license number.';
}
(function(){
  const q=new URLSearchParams(location.search);
  if(q.get('reason')==='session_expired'){
    document.getElementById('err').textContent='Session expired — please log in again.';
  }
  const nextRaw=q.get('next')||'/';
  const next=(nextRaw.startsWith('/')&&!nextRaw.startsWith('//'))?nextRaw:'/';
  document.getElementById('f').addEventListener('submit',async e=>{
    e.preventDefault();
    const payload = {
      pin: document.getElementById('pin').value,
      email: document.getElementById('email').value || '',
      agent_name: mode === 'sub' ? document.getElementById('agent_name').value : '',
      license_number: mode === 'sub' ? document.getElementById('license_number').value : '',
    };
    const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},
      credentials:'same-origin',
      body:JSON.stringify(payload)});
    if(r.ok){window.location=next}
    else{const j=await r.json().catch(()=>({}));
      document.getElementById('err').textContent=j.error||'Invalid credentials';
      document.getElementById('pin').value=''}
  });
})();
</script></body></html>"""


def mount_login_routes(app):
    """Register /login GET and POST routes on the FastAPI app."""

    @app.get("/login", include_in_schema=False)
    async def login_page():
        return HTMLResponse(_LOGIN_HTML)

    @app.post("/login", include_in_schema=False)
    async def login_submit(request: Request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        pin = str(data.get("pin", "")).strip()
        email = normalize_email(data.get("email") or "")
        agent_name = str(data.get("agent_name", "")).strip()
        license_number = str(data.get("license_number", "")).strip()

        if pin not in VALID_PINS:
            return JSONResponse({"error": "Invalid PIN"}, status_code=401)

        # ── Sub-Agent login ───────────────────────────────────────────────
        if agent_name or license_number:
            if not agent_name or not license_number:
                return JSONResponse(
                    {"error": "Both Agent Name and License Number are required"},
                    status_code=400,
                )
            # Check whitelist in MongoDB
            try:
                from dashboard.extensions import get_collection
                sub_agents = get_collection("sub_agents")
                agent_doc = await sub_agents.find_one({
                    "license_number": {"$regex": f"^{license_number}$", "$options": "i"},
                    "is_active": True,
                })
                if not agent_doc:
                    return JSONResponse(
                        {"error": "Not whitelisted. Contact your agency administrator."},
                        status_code=403,
                    )
                # Use the whitelisted name from DB (canonical)
                canonical_name = agent_doc.get("agent_name", agent_name)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error("Sub-agent whitelist check failed: %s", exc)
                return JSONResponse(
                    {"error": "System error checking whitelist. Try again."},
                    status_code=500,
                )

            role = "sub_agent"
            session_email = f"agent-{license_number.lower()}@shamrockbailbonds.biz"
            token = _sign_token(
                email=session_email,
                role=role,
                agent_name=canonical_name,
                license_number=license_number.upper(),
            )
            response = JSONResponse({
                "success": True,
                "email": session_email,
                "role": role,
                "agent_name": canonical_name,
                "license_number": license_number.upper(),
                "is_admin": False,
            })

        # ── God-Admin login ───────────────────────────────────────────────
        else:
            role = "god_admin"
            session_email = email or PRIMARY_SUPER_ADMIN
            token = _sign_token(email=session_email, role=role)
            response = JSONResponse({
                "success": True,
                "email": session_email,
                "role": role,
                "agent_name": "",
                "license_number": "",
                "is_admin": True,
            })

        is_https = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"
        )
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=is_https,
            samesite="lax",
            path="/",
        )
        return response
