"""
ShamrockLeads — FastAPI PIN Authentication Middleware

Replaces the Quart session-based PIN gate (dashboard/auth/pin_auth.py)
with a stateless signed-cookie approach using itsdangerous.

Usage in main.py:
    from dashboard.auth.pin_middleware import PinAuthMiddleware, mount_login_routes
    app.add_middleware(PinAuthMiddleware)
    mount_login_routes(app)

Session payload includes email + role so admin@shamrockbailbonds.biz is
recognized as super-admin across privileged endpoints.
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

DASHBOARD_PIN = os.getenv("DASHBOARD_PIN", "")
COOKIE_NAME = "sl_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# Paths that bypass auth entirely
OPEN_PATHS = frozenset({
    "/login", "/health", "/health/live", "/api/stats",
    "/docs", "/redoc", "/openapi.json",
    "/manifest.json", "/favicon.ico",
})

# File extensions that are always public (static assets — JS, CSS, fonts, images)
# These must load before the session cookie exists so the login page renders.
_STATIC_EXTENSIONS = (
    ".js", ".css", ".ico", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2",
    ".ttf", ".eot", ".webp", ".gif", ".json",
)

# Prefixes that bypass auth
OPEN_PREFIXES = (
    "/api/webhooks/",
    "/api/automation/",  # Node-RED / machine sweeps (GAS_API_KEY enforced in router)
    "/g/",
    "/c/",
    "/api/portal/",
    "/api/config/bluebubbles-url",
)

# OAuth popup paths — login redirects + provider callbacks (no cookie in popup)
OAUTH_PREFIXES = (
    "/api/social/oauth/google/",
    "/api/social/oauth/twitter/",
    "/api/social/oauth/linkedin/",
    "/api/social/oauth/meta/",
)


def _get_serializer() -> URLSafeTimedSerializer:
    """Build the cookie signer from SECRET_KEY (required in production)."""
    secret = os.getenv("SECRET_KEY", "").strip()
    if not secret:
        # Dev-only fallback — never use a predictable PIN-derived secret in prod
        if os.getenv("ENV", os.getenv("ENVIRONMENT", "")).lower() in (
            "production",
            "prod",
        ) or os.getenv("REQUIRE_SECRET_KEY", "").lower() in ("1", "true", "yes"):
            raise RuntimeError(
                "SECRET_KEY must be set for dashboard session cookies in production"
            )
        secret = "shamrock-dev-only-session-key-v1-not-for-production"
    return URLSafeTimedSerializer(secret)


def _sign_token(email: str | None = None, role: str | None = None) -> str:
    """Create a signed session token with optional identity claims."""
    s = _get_serializer()
    payload: dict[str, Any] = {"auth": True, "t": int(time.time())}
    if email:
        payload["email"] = normalize_email(email)
        payload["role"] = role or resolve_role_for_email(email)
    else:
        # PIN-only unlock still grants full CRM access; default identity is super admin
        # so staff tools that check email/role treat the operator as admin.
        payload["email"] = PRIMARY_SUPER_ADMIN
        payload["role"] = "admin"
    return s.dumps(payload)


def _verify_token(token: str) -> bool:
    """Verify a signed session token (valid for 7 days)."""
    return _load_session(token) is not None


def _load_session(token: str | None) -> dict[str, Any] | None:
    """Return session payload or None if invalid/expired."""
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
    """True when the current session is super-admin or ADMIN_EMAILS allowlist."""
    sess = get_session_from_request(request)
    if not sess:
        return False
    if sess.get("role") == "admin":
        return True
    return is_admin_email(sess.get("email"))


# ── Middleware ─────────────────────────────────────────────────────────────────

class PinAuthMiddleware(BaseHTTPMiddleware):
    """Gate all routes behind PIN auth (except whitelisted paths)."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # No PIN configured → open access in dev only
        if not DASHBOARD_PIN:
            env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").lower()
            if env in ("production", "prod") or os.getenv("REQUIRE_DASHBOARD_PIN", "").lower() in (
                "1",
                "true",
                "yes",
            ):
                return JSONResponse(
                    {"error": "Dashboard PIN not configured"},
                    status_code=503,
                )
            return await call_next(request)

        path = request.url.path

        # Whitelisted paths pass through
        if path in OPEN_PATHS or any(path.startswith(p) for p in OPEN_PREFIXES):
            return await call_next(request)

        # Static assets (JS, CSS, fonts, images) are always public.
        # They must be served before the session cookie exists so the
        # login page itself can render correctly.
        if any(path.endswith(ext) for ext in _STATIC_EXTENSIONS):
            return await call_next(request)

        # OAuth popup flow — login redirects + callbacks bypass auth
        if any(path.startswith(p) for p in OAUTH_PREFIXES):
            return await call_next(request)

        # Check signed session cookie — attach identity to request.state
        token = request.cookies.get(COOKIE_NAME)
        sess = _load_session(token) if token else None
        if sess:
            request.state.sl_session = sess
            request.state.sl_email = sess.get("email") or PRIMARY_SUPER_ADMIN
            request.state.sl_role = sess.get("role") or "admin"
            request.state.sl_is_admin = (
                sess.get("role") == "admin" or is_admin_email(sess.get("email"))
            )
            return await call_next(request)

        # Not authenticated
        if path.startswith("/api/"):
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        return RedirectResponse("/login", status_code=302)


# ── Login Routes ──────────────────────────────────────────────────────────────

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shamrock — Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#0a0f1a 0%,#1a2332 50%,#0d1520 100%);
  font-family:'Inter',system-ui,sans-serif;color:#e0e0e0}
.card{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);
  border-radius:20px;padding:48px 40px;width:380px;backdrop-filter:blur(20px);
  box-shadow:0 20px 60px rgba(0,0,0,0.4)}
.logo{font-size:28px;font-weight:700;text-align:center;margin-bottom:8px;
  background:linear-gradient(135deg,#00d26a,#00b85c);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent}
.sub{text-align:center;color:#8899aa;font-size:14px;margin-bottom:32px}
label{display:block;font-size:12px;color:#8899aa;margin-bottom:6px;margin-top:12px}
input{width:100%;padding:14px 16px;border:1px solid rgba(255,255,255,0.12);
  background:rgba(255,255,255,0.06);border-radius:12px;color:#fff;font-size:16px;
  outline:none;transition:border-color .3s}
input#pin{font-size:18px;letter-spacing:8px;text-align:center}
input:focus{border-color:#00d26a}
button{width:100%;margin-top:16px;padding:14px;border:none;border-radius:12px;
  background:linear-gradient(135deg,#00d26a,#00b85c);color:#000;font-size:16px;
  font-weight:600;cursor:pointer;transition:transform .2s,box-shadow .2s}
button:hover{transform:translateY(-1px);box-shadow:0 8px 24px rgba(0,210,106,0.3)}
.err{color:#ff6b6b;text-align:center;margin-top:12px;font-size:13px;min-height:20px}
.hint{text-align:center;color:#667788;font-size:11px;margin-top:16px;line-height:1.4}
</style></head><body>
<div class="card">
  <div class="logo">☘️ Shamrock</div>
  <div class="sub">Staff Dashboard — Super CRM</div>
  <form id="f" method="POST" action="/login">
    <label for="email">Staff email (optional — admin@ = full admin)</label>
    <input type="email" name="email" id="email" placeholder="admin@shamrockbailbonds.biz" autocomplete="username">
    <label for="pin">PIN</label>
    <input type="password" name="pin" id="pin" maxlength="12" placeholder="••••••" autofocus autocomplete="current-password">
    <button type="submit">Unlock</button>
    <div class="err" id="err"></div>
    <div class="hint">Logged in as admin@shamrockbailbonds.biz grants admin across the ecosystem.</div>
  </form>
</div>
<script>
(function(){
  const q=new URLSearchParams(location.search);
  if(q.get('reason')==='session_expired'){
    document.getElementById('err').textContent='Session expired — enter your PIN to continue.';
  }
  const nextRaw=q.get('next')||'/';
  // Only allow relative same-origin paths (no open redirects)
  const next=(nextRaw.startsWith('/')&&!nextRaw.startsWith('//'))?nextRaw:'/';
  document.getElementById('f').addEventListener('submit',async e=>{
    e.preventDefault();
    const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},
      credentials:'same-origin',
      body:JSON.stringify({
        pin:document.getElementById('pin').value,
        email:document.getElementById('email').value||''
      })});
    if(r.ok){window.location=next}
    else{const j=await r.json().catch(()=>({}));
      document.getElementById('err').textContent=j.error||'Invalid PIN';
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
        pin = data.get("pin", "")
        email = normalize_email(data.get("email") or "")

        if not DASHBOARD_PIN or pin == DASHBOARD_PIN:
            # PIN unlocks the CRM. Email (when provided) sets identity/role claims.
            # Super-admin email always stores role=admin.
            if email and not is_admin_email(email) and os.getenv(
                "STRICT_ADMIN_EMAIL_LOGIN", ""
            ).lower() in ("1", "true", "yes"):
                # Optional hard mode: only ADMIN_EMAILS may log in (off by default)
                return JSONResponse(
                    {"error": "Email not authorized for dashboard access"},
                    status_code=403,
                )

            role = resolve_role_for_email(email) if email else "admin"
            session_email = email or PRIMARY_SUPER_ADMIN
            token = _sign_token(email=session_email, role=role)
            response = JSONResponse(
                {
                    "success": True,
                    "email": session_email,
                    "role": role,
                    "is_admin": role == "admin" or is_admin_email(session_email),
                }
            )
            # secure=True is required on HTTPS — without it the browser
            # silently drops the cookie and every subsequent API call returns 401,
            # causing the dashboard to render as a black screen.
            response.set_cookie(
                key=COOKIE_NAME,
                value=token,
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                secure=True,
                samesite="lax",
                path="/",
            )
            return response

        return JSONResponse({"error": "Invalid PIN"}, status_code=401)
