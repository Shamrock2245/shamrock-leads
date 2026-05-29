"""
ShamrockLeads — FastAPI PIN Authentication Middleware

Replaces the Quart session-based PIN gate (dashboard/auth/pin_auth.py)
with a stateless signed-cookie approach using itsdangerous.

Usage in main.py:
    from dashboard.auth.pin_middleware import PinAuthMiddleware, mount_login_routes
    app.add_middleware(PinAuthMiddleware)
    mount_login_routes(app)
"""
from __future__ import annotations

import os
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse, RedirectResponse, HTMLResponse

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# ── Configuration ─────────────────────────────────────────────────────────────

DASHBOARD_PIN = os.getenv("DASHBOARD_PIN", "")
COOKIE_NAME = "sl_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# Paths that bypass auth entirely
OPEN_PATHS = frozenset({
    "/login", "/health", "/api/stats",
    "/docs", "/redoc", "/openapi.json",
})

# Prefixes that bypass auth
OPEN_PREFIXES = (
    "/api/webhooks/",
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
    """Build the cookie signer from the same secret derivation used by extensions.py."""
    secret = os.getenv("SECRET_KEY") or (
        "shamrock-" + (DASHBOARD_PIN or "leads-2245") + "-session-key-v1"
    )
    return URLSafeTimedSerializer(secret)


def _sign_token() -> str:
    """Create a signed session token."""
    s = _get_serializer()
    return s.dumps({"auth": True, "t": int(time.time())})


def _verify_token(token: str) -> bool:
    """Verify a signed session token (valid for 7 days)."""
    try:
        s = _get_serializer()
        s.loads(token, max_age=COOKIE_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


# ── Middleware ─────────────────────────────────────────────────────────────────

class PinAuthMiddleware(BaseHTTPMiddleware):
    """Gate all routes behind PIN auth (except whitelisted paths)."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # No PIN configured → open access
        if not DASHBOARD_PIN:
            return await call_next(request)

        path = request.url.path

        # Whitelisted paths pass through
        if path in OPEN_PATHS or any(path.startswith(p) for p in OPEN_PREFIXES):
            return await call_next(request)

        # OAuth popup flow — login redirects + callbacks bypass auth
        if any(path.startswith(p) for p in OAUTH_PREFIXES):
            return await call_next(request)

        # Check signed session cookie
        token = request.cookies.get(COOKIE_NAME)
        if token and _verify_token(token):
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
input{width:100%;padding:14px 16px;border:1px solid rgba(255,255,255,0.12);
  background:rgba(255,255,255,0.06);border-radius:12px;color:#fff;font-size:18px;
  letter-spacing:8px;text-align:center;outline:none;transition:border-color .3s}
input:focus{border-color:#00d26a}
button{width:100%;margin-top:16px;padding:14px;border:none;border-radius:12px;
  background:linear-gradient(135deg,#00d26a,#00b85c);color:#000;font-size:16px;
  font-weight:600;cursor:pointer;transition:transform .2s,box-shadow .2s}
button:hover{transform:translateY(-1px);box-shadow:0 8px 24px rgba(0,210,106,0.3)}
.err{color:#ff6b6b;text-align:center;margin-top:12px;font-size:13px;min-height:20px}
</style></head><body>
<div class="card">
  <div class="logo">☘️ Shamrock</div>
  <div class="sub">Staff Dashboard — Enter PIN</div>
  <form id="f" method="POST" action="/login">
    <input type="password" name="pin" id="pin" maxlength="6" placeholder="••••••" autofocus>
    <button type="submit">Unlock</button>
    <div class="err" id="err"></div>
  </form>
</div>
<script>
document.getElementById('f').addEventListener('submit',async e=>{
  e.preventDefault();
  const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({pin:document.getElementById('pin').value})});
  if(r.ok){window.location='/'}
  else{document.getElementById('err').textContent='Invalid PIN';
    document.getElementById('pin').value=''}
});
</script></body></html>"""


def mount_login_routes(app):
    """Register /login GET and POST routes on the FastAPI app."""
    from fastapi import FastAPI

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

        if not DASHBOARD_PIN or pin == DASHBOARD_PIN:
            token = _sign_token()
            response = JSONResponse({"success": True})
            response.set_cookie(
                key=COOKIE_NAME,
                value=token,
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                path="/",
            )
            return response

        return JSONResponse({"error": "Invalid PIN"}, status_code=401)
