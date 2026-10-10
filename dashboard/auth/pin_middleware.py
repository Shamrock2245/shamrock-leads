"""
ShamrockLeads — FastAPI PIN Authentication Middleware

Stateless signed-cookie approach using itsdangerous.
Supports God-Admin (full access) and Sub-Agent (restricted) roles.

Roles:
  - god_admin: Full unrestricted access (PIN with no agent fields, or admin email)
  - admin / staff: Staff bond desk. May share forfeiture files to Recovery.
  - sub_agent: Restricted access — must be whitelisted in MongoDB `sub_agents` collection.
               Sees only their own bonds, revenue, and assigned POAs.
  - recovery: Fail-closed BailSafe recovery desk. Denied every route except the
              allowlist in ``dashboard.auth.recovery_scope``. Not a sub-agent.

Usage in main.py:
    from dashboard.auth.pin_middleware import PinAuthMiddleware, mount_login_routes
    app.add_middleware(PinAuthMiddleware)
    mount_login_routes(app)
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable
from urllib.parse import quote

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
from dashboard.tenancy.constants import SHAMROCK_TENANT_ID
from dashboard.auth.dev_mode import (  # noqa: F401  (re-exported)
    no_pin_passthrough_allowed,
    session_secret_fallback_allowed,
)

# ── Configuration ─────────────────────────────────────────────────────────────

# Env-only PIN — no hardcoded fallback (legacy dual-PIN removed for fail-closed auth)
DASHBOARD_PIN = (os.getenv("DASHBOARD_PIN") or "").strip()
COOKIE_NAME = "sl_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# Paths that bypass auth entirely.
# NOTE: "/" is NOT open by default — staff dashboard requires PIN.
# Indemnitor portal host (paperwork.*) is handled host-aware in dispatch().
OPEN_PATHS = frozenset({
    "/done",
    "/kiosk",  # neutral in-office tablet home (no data); kiosk idle-reset target
    "/paperwork",
    "/login",
    "/health",
    "/health/live",
    "/manifest.json",
    "/favicon.ico",
    "/favicon.png",
    "/apple-touch-icon.png",
    "/shamrock-logo.png",
    # Crawl hygiene: public disallow file. Must not 302 to /login.
    # Exact path only — do not open every *.txt under the dashboard.
    "/robots.txt",
})

# Hostnames that serve the public indemnitor PIN portal (not staff CRM)
PAPERWORK_HOST_MARKERS = (
    "paperwork.shamrockbailbonds.biz",
    "paperwork.",
)

# File extensions that are always public (static assets)
_STATIC_EXTENSIONS = (
    ".js", ".css", ".ico", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2",
    ".ttf", ".eot", ".webp", ".gif",
)

# Prefixes that bypass auth
OPEN_PREFIXES = (
    "/g/",
    "/c/",
    "/sign/",
    "/api/portal",
    "/done",
    "/paperwork",
    "/api/config/bluebubbles-url",
    "/traccar/setup/",
    "/track/",  # Trape skip-trace lure (captures IP, then redirects)
    # Shannon / Wix clipboard machine routes: pin skip, then route-level GAS_API_KEY / LEADS_INTERNAL_TOKEN
    "/api/agent-brain/memory/",
    "/api/imessage/shannon/",
    "/api/imessage/wix/",
    "/api/paperwork/shannon/",
    "/paperwork/shannon/",
    "/api/lee-county/",
)

# Management endpoints that live under /api/webhooks/ but are NOT inbound
# provider deliveries.  They must never inherit the POST webhook exemption
# (re-pointing BlueBubbles webhooks is an admin action) — they fall through to
# the machine-key / staff-session checks below like any other /api route.
WEBHOOK_MANAGEMENT_PATHS = frozenset({
    "/api/webhooks/bluebubbles/register",
})

# OAuth popup paths
OAUTH_PREFIXES = (
    "/api/social/oauth/google/",
    "/api/social/oauth/twitter/",
    "/api/social/oauth/linkedin/",
    "/api/social/oauth/meta/",
)

# Valid master PINs (env only — never ship a second hardcoded master)
VALID_PINS = frozenset(p for p in (DASHBOARD_PIN,) if p)


class SessionSecretMissing(RuntimeError):
    """SECRET_KEY is unset outside explicit development: no session can be
    signed or verified. The middleware answers 503 on protected routes."""


DEV_ONLY_SESSION_KEY = "shamrock-dev-only-session-key-v1-not-for-production"


def session_secret_configured() -> bool:
    """True when a session can be signed: SECRET_KEY set, or explicit development."""
    return bool(os.getenv("SECRET_KEY", "").strip()) or session_secret_fallback_allowed()


def _get_serializer() -> URLSafeTimedSerializer:
    """Build the cookie signer from SECRET_KEY.

    Fails closed: with no SECRET_KEY, only ENV/ENVIRONMENT exactly
    ``development`` (and REQUIRE_SECRET_KEY unset) may use the fixed
    development-only key. Everywhere else, including ENV unset, it raises
    :class:`SessionSecretMissing`. The key is never derived from the PIN.
    """
    secret = os.getenv("SECRET_KEY", "").strip()
    if not secret:
        if not session_secret_fallback_allowed():
            raise SessionSecretMissing(
                "SECRET_KEY must be set for dashboard session cookies "
                "(only ENV=development may run without it)"
            )
        secret = DEV_ONLY_SESSION_KEY
    return URLSafeTimedSerializer(secret)


def _sign_token(
    email: str | None = None,
    role: str | None = None,
    agent_name: str | None = None,
    license_number: str | None = None,
    is_admin: bool = False,
    recovery_id: str | None = None,
    tenant_id: str | None = None,
) -> str:
    """Create a signed session token with identity claims.

    ``tenant_id`` defaults to Shamrock. Login does not accept a tenant from
    the client. Old cookies without the field still load and resolve as Shamrock.
    """
    s = _get_serializer()
    payload: dict[str, Any] = {
        "auth": True,
        "t": int(time.time()),
        "tenant_id": tenant_id or SHAMROCK_TENANT_ID,
    }
    if is_admin and role != "recovery":
        payload["is_admin"] = True
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
    if recovery_id:
        payload["recovery_id"] = str(recovery_id).strip().upper()
    if payload.get("role") == "recovery":
        payload["is_admin"] = False
    return s.dumps(payload)


def _attach_session(request: Request, sess: dict[str, Any]) -> None:
    """Copy signed claims onto request.state. Recovery is never admin."""
    request.state.sl_session = sess
    request.state.sl_email = sess.get("email") or PRIMARY_SUPER_ADMIN
    request.state.sl_role = sess.get("role") or "god_admin"
    request.state.sl_agent_name = sess.get("agent_name") or ""
    request.state.sl_license_number = sess.get("license_number") or ""
    request.state.sl_recovery_id = sess.get("recovery_id") or ""
    request.state.sl_tenant_id = sess.get("tenant_id") or SHAMROCK_TENANT_ID
    request.state.sl_is_admin = (
        sess.get("role") in ("admin", "god_admin") or is_admin_email(sess.get("email"))
    )
    if sess.get("role") == "recovery":
        request.state.sl_is_admin = False


def _recovery_denied(path: str) -> Response:
    """Block a recovery session. APIs and assets get 403; pages go to /recovery."""
    asset = path.startswith("/static/") or any(path.endswith(ext) for ext in _STATIC_EXTENSIONS)
    if path.startswith("/api/") or asset or path == "/openapi.json":
        return JSONResponse(
            {
                "error": "Access denied — recovery role cannot use this endpoint",
                "role": "recovery",
                "code": "recovery_route_denied",
            },
            status_code=403,
        )
    return RedirectResponse("/recovery", status_code=302)


def _load_session(token: str | None) -> dict[str, Any] | None:
    """Return session payload dict or None if invalid/expired."""
    if not token:
        return None
    try:
        s = _get_serializer()
    except SessionSecretMissing:
        return None
    try:
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

def is_machine_auth_valid(request: Request) -> bool:
    """Verify machine request against GAS_API_KEY or LEADS_INTERNAL_TOKEN."""
    gas = (os.getenv("GAS_API_KEY") or "").strip()
    internal = (
        os.getenv("LEADS_INTERNAL_TOKEN")
        or os.getenv("INTERNAL_API_TOKEN")
        or ""
    ).strip()
    provided = (
        request.headers.get("X-API-Key")
        or request.headers.get("X-Api-Key")
        or request.headers.get("X-Internal-Token")
        or request.query_params.get("api_key")
        or ""
    ).strip()
    if not provided:
        return False
    import secrets
    if gas and secrets.compare_digest(provided, gas):
        return True
    if internal and secrets.compare_digest(provided, internal):
        return True
    return False


def _is_paperwork_host(request: Request) -> bool:
    """True when request is for the public indemnitor portal host."""
    host = (request.headers.get("host") or request.url.hostname or "").lower()
    # strip port if present
    host = host.split(":")[0]
    return any(m in host for m in PAPERWORK_HOST_MARKERS) or host == "paperwork.localhost"


class PinAuthMiddleware(BaseHTTPMiddleware):
    """Gate all routes behind staff PIN auth (except whitelisted paths).

    Two distinct access points:
      - leads.shamrockbailbonds.biz  → staff Auto-CRM (PIN / sub-agent login)
      - paperwork.shamrockbailbonds.biz → indemnitor portal (OTP PIN, public pages)
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Indemnitor host: public root portal pages (not staff CRM)
        if path == "/" and _is_paperwork_host(request):
            return await call_next(request)

        # Webhooks: incoming POST/PUT/PATCH webhooks (DocuSeal, Twilio, BlueBubbles, Stripe, Traccar)
        # stay public, but PIN-gate GET/HEAD/DELETE under /api/webhooks/ (e.g. /status or /history)
        # Management paths (e.g. BlueBubbles /register) are never exempt.
        # DELETE /api/webhooks/... already falls through to the staff check.
        if (
            path.startswith("/api/webhooks/") or path == "/api/traccar/webhook"
        ) and path.rstrip("/") not in WEBHOOK_MANAGEMENT_PATHS:
            if request.method in ("GET", "HEAD", "DELETE") and (
                path.endswith("/status") or path.endswith("/history")
            ):
                pass  # Fall through to staff session check below
            elif request.method in ("POST", "PUT", "PATCH"):
                return await call_next(request)

        # Device status check: allow if signed status token matches the device_id
        if path.startswith("/api/traccar/device-status/"):
            device_id = path.removeprefix("/api/traccar/device-status/").strip("/")
            token = request.query_params.get("token", "").strip()
            if device_id and token:
                from dashboard.routers.traccar_setup_api import verify_device_status_token
                if verify_device_status_token(device_id, token):
                    return await call_next(request)

        # Machine routes (Node-RED, Shannon, external crons, automation sweeps):
        # Allow requests with valid GAS_API_KEY or LEADS_INTERNAL_TOKEN
        if is_machine_auth_valid(request):
            return await call_next(request)

        # Recovery is fail-closed before static/open bypasses. A recovery
        # cookie must not download the staff CRM, its JS, or any non-allowlisted API.
        recovery_cookie = request.cookies.get(COOKIE_NAME)
        recovery_sess = _load_session(recovery_cookie) if recovery_cookie else None
        if recovery_sess and recovery_sess.get("role") == "recovery":
            from dashboard.auth.recovery_scope import path_allowed_for_recovery

            if not path_allowed_for_recovery(path, request.method):
                return _recovery_denied(path)
            _attach_session(request, recovery_sess)
            return await call_next(request)

        # Public agency signup. The handler 404s unless SAAS_MULTI_TENANT is on,
        # and it never sends mail or stores a raw secret. Staff /platform stays gated.
        if path in {"/signup", "/api/public/signup"}:
            return await call_next(request)

        if path in OPEN_PATHS or any(path.startswith(p) for p in OPEN_PREFIXES):
            return await call_next(request)

        if path.startswith("/static/") or any(path.endswith(ext) for ext in _STATIC_EXTENSIONS):
            return await call_next(request)

        if any(path.startswith(p) for p in OAUTH_PREFIXES):
            return await call_next(request)

        pin = (os.getenv("DASHBOARD_PIN") or DASHBOARD_PIN or "").strip()
        if not pin:
            # Fail closed: with no PIN, protected routes answer 503 unless the
            # process is explicitly ENV=development (local dev only). ENV unset,
            # production, staging or anything else is closed.
            if no_pin_passthrough_allowed():
                return await call_next(request)
            return JSONResponse({"error": "Dashboard PIN not configured"}, status_code=503)

        # No SECRET_KEY outside development: no session can be trusted or
        # issued, so protected routes fail closed (open paths, /health,
        # webhooks and machine keys were handled above).
        if not session_secret_configured():
            return JSONResponse({"error": "Session signing not configured"}, status_code=503)

        cookie = request.cookies.get(COOKIE_NAME)
        sess = _load_session(cookie) if cookie else None
        if sess:
            _attach_session(request, sess)
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
        if path.startswith("/api/") or path == "/openapi.json":
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        return RedirectResponse(login_redirect_location(path, request.url.query), status_code=302)


def login_redirect_location(path: str, query: str = "") -> str:
    """Preserve the original path+query on PIN login so bookmarklet ingest survives.

    Hash fragments never reach the server; the login page stashes those via postMessage.
    """
    raw_path = str(path or "/")
    if raw_path == "/login" or raw_path.startswith("/login"):
        return "/login"
    nxt = f"{raw_path}?{query}" if query else raw_path
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    return "/login?next=" + quote(nxt, safe="")


# ── Login Routes ──────────────────────────────────────────────────────────────

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Shamrock — Agent & Admin Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#0a0f1a 0%,#1a2332 50%,#0d1520 100%);
  font-family:'Inter',system-ui,sans-serif;color:#e0e0e0}
.card{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);
  border-radius:20px;padding:40px 36px;width:480px;max-width:calc(100vw - 24px);backdrop-filter:blur(20px);
  box-shadow:0 20px 60px rgba(0,0,0,0.5)}
.logo{font-size:28px;font-weight:700;text-align:center;margin-bottom:4px;
  background:linear-gradient(135deg,#00d26a,#00b85c);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent}
.sub{text-align:center;color:#8899aa;font-size:13px;margin-bottom:24px}
.tab-row{display:flex;gap:8px;background:rgba(255,255,255,0.05);padding:4px;border-radius:10px;margin-bottom:20px}
.tab-btn{flex:1;padding:8px 4px;border:none;border-radius:8px;background:none;color:#8899aa;font-size:12px;font-weight:600;cursor:pointer;transition:all .2s}
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
    <button class="tab-btn" id="tabRecoveryBtn" onclick="switchLoginMode('recovery')">🎯 Recovery</button>
  </div>
  <form id="f" method="POST" action="/login">
    <div id="subAgentFields" style="display:none">
      <label for="agent_name">Sub-Agent Full Name</label>
      <input type="text" name="agent_name" id="agent_name" placeholder="e.g. John Smith">
      <label for="license_number">FL License Number</label>
      <input type="text" name="license_number" id="license_number" placeholder="e.g. P123456">
    </div>
    <div id="recoveryFields" style="display:none">
      <label for="recovery_id">Recovery ID</label>
      <input type="text" name="recovery_id" id="recovery_id" placeholder="REC-1001" autocomplete="username">
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
  document.getElementById('tabRecoveryBtn').classList.toggle('active', m === 'recovery');
  document.getElementById('godAdminFields').style.display = m === 'god' ? 'block' : 'none';
  document.getElementById('subAgentFields').style.display = m === 'sub' ? 'block' : 'none';
  document.getElementById('recoveryFields').style.display = m === 'recovery' ? 'block' : 'none';
  document.getElementById('subBtnText').textContent = m === 'god'
    ? 'Unlock God-Admin Access'
    : (m === 'recovery' ? 'Enter Recovery' : 'Login as Sub-Agent');
  document.getElementById('loginHint').textContent = m === 'god'
    ? 'God-Admin grants full unrestricted control over the entire system.'
    : (m === 'recovery'
      ? 'Recovery sees only forfeiture files staff have shared. No bond writing, payments, or indemnitor contact data.'
      : 'Sub-Agent login requires whitelisted name and FL license number.');
}
(function(){
  function stashExtract(payload){
    try{
      var raw = typeof payload === 'string' ? payload : JSON.stringify(payload);
      if(raw && raw.length > 8) sessionStorage.setItem('sl_booking_extract', raw);
    }catch(e){}
  }
  try{
    var h=String(location.hash||'');
    var hm=h.match(/(?:^|#|&)booking-extract=([^&]*)/);
    if(hm&&hm[1]){
      stashExtract(decodeURIComponent(hm[1]));
      history.replaceState(null,'',location.pathname+location.search);
    }
  }catch(e){}
  window.addEventListener('message',function(e){
    var d=e.data;
    if(!d||d.type!=='sl-booking-extract'||!d.payload)return;
    var host=String(e.origin||'').replace(/^https?:\\/\\//,'');
    if(!/sheriffleefl\\.org$/i.test(host)&&e.origin!==location.origin)return;
    stashExtract(d.payload);
  });
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
      email: mode === 'god' ? (document.getElementById('email').value || '') : '',
      agent_name: mode === 'sub' ? document.getElementById('agent_name').value : '',
      license_number: mode === 'sub' ? document.getElementById('license_number').value : '',
      recovery_id: mode === 'recovery' ? document.getElementById('recovery_id').value : '',
    };
    const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},
      credentials:'same-origin',
      body:JSON.stringify(payload)});
    const j=await r.json().catch(()=>({}));
    if(r.ok){
      let dest = j.role === 'recovery' ? '/recovery' : next;
      try{
        if(j.role !== 'recovery' && sessionStorage.getItem('sl_booking_extract')){
          const u=new URL(dest, location.origin);
          u.searchParams.set('tab','defendants');
          u.searchParams.set('write','1');
          dest=u.pathname+u.search;
        }
      }catch(err){}
      window.location=dest;
    }
    else{
      document.getElementById('err').textContent=j.error||'Invalid credentials';
      document.getElementById('pin').value=''}
  });
})();
</script></body></html>"""


def mount_login_routes(app):
    """Register /login GET and POST routes on the FastAPI app."""

    @app.api_route("/login", methods=["GET", "HEAD"], include_in_schema=False)
    async def login_page():
        # HEAD must be registered. FastAPI GET-only routes 404 on HEAD, which
        # is what crawlers (Bingbot) receive as JSON instead of this page.
        return HTMLResponse(
            _LOGIN_HTML,
            headers={"X-Robots-Tag": "noindex, nofollow"},
        )

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
        recovery_id = str(data.get("recovery_id") or "").strip()

        if not session_secret_configured():
            return JSONResponse({"error": "Session signing not configured"}, status_code=503)

        if pin not in VALID_PINS:
            return JSONResponse({"error": "Invalid PIN"}, status_code=401)

        # ── Recovery login ────────────────────────────────────────────────
        if recovery_id:
            try:
                from dashboard.services.recovery_case_service import authenticate_recovery_agent
                agent = await authenticate_recovery_agent(recovery_id)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error("Recovery login lookup failed: %s", type(exc).__name__)
                return JSONResponse(
                    {"error": "System error checking Recovery access. Try again."},
                    status_code=500,
                )
            if not agent:
                return JSONResponse(
                    {"error": "Not authorized for Recovery. Ask staff to provision your recovery id."},
                    status_code=403,
                )
            token = _sign_token(
                email=agent["email"],
                role="recovery",
                agent_name=agent["display_name"],
                recovery_id=agent["recovery_id"],
                is_admin=False,
            )
            response = JSONResponse({
                "success": True,
                "email": agent["email"],
                "role": "recovery",
                "agent_name": agent["display_name"],
                "license_number": "",
                "recovery_id": agent["recovery_id"],
                "is_admin": False,
            })

        # ── Sub-Agent login ───────────────────────────────────────────────
        elif agent_name or license_number:
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
            is_admin = bool(agent_doc.get("is_admin", False))
            session_email = f"agent-{license_number.lower()}@shamrockbailbonds.biz"
            token = _sign_token(
                email=session_email,
                role=role,
                agent_name=canonical_name,
                license_number=license_number.upper(),
                is_admin=is_admin,
            )
            response = JSONResponse({
                "success": True,
                "email": session_email,
                "role": role,
                "agent_name": canonical_name,
                "license_number": license_number.upper(),
                "is_admin": is_admin,
            })

        # ── God-Admin login ───────────────────────────────────────────────
        else:
            role = "god_admin"
            session_email = email or PRIMARY_SUPER_ADMIN
            token = _sign_token(email=session_email, role=role, is_admin=True)
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
