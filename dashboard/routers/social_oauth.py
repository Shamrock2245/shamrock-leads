"""
Dashboard Router — Social OAuth Flows
========================================
Handles OAuth 2.0 login/callback/disconnect for social media platforms.

Routes:
  GET  /api/social/oauth/{provider}/login     → Redirect to provider's auth page
  GET  /api/social/oauth/{provider}/callback   → Handle callback, store tokens
  POST /api/social/oauth/{provider}/disconnect → Revoke + delete connection
  GET  /api/social/oauth/status                → List all connected accounts
"""

from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

logger = logging.getLogger("dashboard.routers.social_oauth")

router = APIRouter(prefix="/api/social/oauth", tags=["social-oauth"])

# JWT secret for CSRF state tokens
_JWT_SECRET = os.getenv("SECRET_KEY", "shamrock-default-secret-change-me")

# Base URL for OAuth redirects
_REDIRECT_BASE = os.getenv(
    "SOCIAL_OAUTH_REDIRECT_BASE",
    "https://leads.shamrockbailbonds.biz",
)

VALID_PROVIDERS = {"google", "twitter", "linkedin", "meta"}


# ── State Token Helpers ───────────────────────────────────────────────────────

def _create_state(provider: str) -> str:
    """Create a signed JWT state token for CSRF protection."""
    payload = {
        "provider": provider,
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,  # 10 min expiry
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm="HS256")


def _verify_state(state: str) -> dict | None:
    """Verify and decode a state token. Returns payload or None."""
    try:
        return jwt.decode(state, _JWT_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid OAuth state token: %s", e)
        return None


def _get_redirect_uri(provider: str) -> str:
    """Build the callback redirect URI for a provider."""
    return f"{_REDIRECT_BASE}/api/social/oauth/{provider}/callback"


# ── Login ─────────────────────────────────────────────────────────────────────

@router.get("/{provider}/login")
async def oauth_login(provider: str):
    """Redirect user to the provider's OAuth authorization page."""
    if provider not in VALID_PROVIDERS:
        return JSONResponse(
            {"success": False, "error": f"Unknown provider: {provider}"},
            status_code=400,
        )

    try:
        from dashboard.services.oauth_providers import get_provider
        oauth = get_provider(provider)
    except ValueError as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    state = _create_state(provider)
    redirect_uri = _get_redirect_uri(provider)
    auth_url = oauth.get_auth_url(state=state, redirect_uri=redirect_uri)

    logger.info("🔐 OAuth login initiated for %s → %s", provider, redirect_uri)
    return RedirectResponse(auth_url)


# ── Callback ──────────────────────────────────────────────────────────────────

@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    error_description: str = Query(None),
):
    """
    Handle OAuth callback from provider.

    On success: stores tokens, discovers sub-accounts, returns HTML that
    posts a message to the opener window (the dashboard) and closes itself.
    """
    # Handle error from provider
    if error:
        logger.warning("OAuth error from %s: %s — %s", provider, error, error_description)
        return _callback_html(success=False, error=error_description or error)

    # Validate state
    if not state:
        return _callback_html(success=False, error="Missing state parameter")

    payload = _verify_state(state)
    if not payload:
        return _callback_html(success=False, error="Invalid or expired state token")

    if payload.get("provider") != provider:
        return _callback_html(success=False, error="State/provider mismatch")

    if not code:
        return _callback_html(success=False, error="Missing authorization code")

    # Exchange code for tokens
    try:
        from dashboard.services.oauth_providers import get_provider
        from dashboard.services.social_accounts_service import store_account
        from datetime import datetime, timezone, timedelta

        oauth = get_provider(provider)
        redirect_uri = _get_redirect_uri(provider)

        # Exchange — Twitter PKCE needs state to look up code_verifier
        if provider == "twitter":
            token_resp = await oauth.exchange_code(code, redirect_uri, state=state)
        else:
            token_resp = await oauth.exchange_code(code, redirect_uri)
        if not token_resp.success:
            return _callback_html(
                success=False,
                error=token_resp.error or "Token exchange failed",
            )

        # Get profile
        profile = await oauth.get_profile(token_resp.access_token)
        if not profile.account_id:
            return _callback_html(success=False, error="Could not fetch profile")

        # Discover sub-accounts (Pages, IG, GBP locations, etc.)
        sub_accounts = await oauth.discover_accounts(token_resp.access_token)
        metadata = {}
        for acct in sub_accounts:
            # Store page tokens and IDs in metadata
            if acct.account_type == "page":
                metadata["page_id"] = acct.account_id
                metadata["page_name"] = acct.display_name
                metadata["page_access_token"] = acct.access_token
            elif acct.account_type == "ig_business":
                metadata["ig_business_id"] = acct.account_id
                metadata["ig_username"] = acct.display_name
            elif acct.account_type == "gbp_location":
                metadata.setdefault("gbp_locations", []).append({
                    "id": acct.account_id,
                    "name": acct.display_name,
                })
            elif acct.account_type == "youtube_channel":
                metadata["youtube_channel_id"] = acct.account_id
                metadata["youtube_channel_name"] = acct.display_name
            elif acct.account_type == "organization":
                metadata["organization_urn"] = acct.account_id
                metadata["organization_name"] = acct.display_name

        # Calculate token expiry
        expires_at = None
        if token_resp.expires_in:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_resp.expires_in)

        # Store in MongoDB
        await store_account(
            platform=provider,
            account_id=profile.account_id,
            display_name=profile.display_name or profile.email or profile.account_id,
            access_token=token_resp.access_token,
            refresh_token=token_resp.refresh_token,
            token_expires_at=expires_at,
            scopes=token_resp.scope.split() if token_resp.scope else [],
            profile_picture=profile.profile_picture,
            sub_platforms=oauth.get_sub_platforms(),
            metadata=metadata,
        )

        display = profile.display_name or profile.email or profile.account_id
        logger.info(
            "✅ OAuth connection complete: %s → %s (%d sub-accounts)",
            provider, display, len(sub_accounts),
        )

        return _callback_html(
            success=True,
            provider=provider,
            display_name=display,
        )

    except Exception as e:
        logger.error("OAuth callback error for %s: %s", provider, e, exc_info=True)
        return _callback_html(success=False, error=str(e)[:200])


# ── Disconnect ────────────────────────────────────────────────────────────────

@router.post("/{provider}/disconnect")
async def oauth_disconnect(provider: str, request: Request):
    """Disconnect a social account."""
    if provider not in VALID_PROVIDERS:
        return JSONResponse(
            {"success": False, "error": f"Unknown provider: {provider}"},
            status_code=400,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}

    account_id = body.get("account_id", "")

    try:
        from dashboard.services.social_accounts_service import (
            get_active_token, disconnect,
        )
        from dashboard.services.oauth_providers import get_provider

        # Try to revoke the token first
        token_doc = await get_active_token(provider, account_id or None)
        if token_doc:
            try:
                oauth = get_provider(provider)
                await oauth.revoke(token_doc["access_token"])
            except Exception as e:
                logger.warning("Token revocation failed (continuing): %s", e)

            await disconnect(provider, token_doc["account_id"])

        return JSONResponse({"success": True, "provider": provider})

    except Exception as e:
        logger.error("Disconnect error for %s: %s", provider, e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def oauth_status():
    """List all connected social accounts (tokens redacted)."""
    try:
        from dashboard.services.social_accounts_service import list_connected
        accounts = await list_connected()

        # Serialize datetimes
        for acct in accounts:
            for key in ("connected_at", "last_refreshed", "token_expires_at"):
                val = acct.get(key)
                if hasattr(val, "isoformat"):
                    acct[key] = val.isoformat()
                elif val is None:
                    acct[key] = None

        return JSONResponse({"success": True, "accounts": accounts})

    except Exception as e:
        logger.error("Status fetch error: %s", e)
        return JSONResponse({"success": False, "accounts": [], "error": str(e)})


# ── Callback HTML ─────────────────────────────────────────────────────────────

def _callback_html(
    success: bool,
    provider: str = "",
    display_name: str = "",
    error: str = "",
) -> HTMLResponse:
    """
    Return an HTML page that posts a message to the opener window and closes.

    This is displayed in the OAuth popup window. It sends the result back to
    the dashboard via window.opener.postMessage, then self-closes.
    """
    result = {
        "type": "shamrock_oauth_callback",
        "success": success,
        "provider": provider,
        "display_name": display_name,
        "error": error,
    }

    import json
    result_json = json.dumps(result)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>{'Connected!' if success else 'Connection Failed'}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            display: flex; align-items: center; justify-content: center;
            min-height: 100vh; margin: 0;
            background: #0f1724; color: #e2e8f0;
        }}
        .card {{
            text-align: center; padding: 40px;
            background: #1a2332; border: 1px solid #2a3a4e;
            border-radius: 16px; max-width: 400px;
            box-shadow: 0 20px 60px rgba(0,0,0,.4);
        }}
        .icon {{ font-size: 48px; margin-bottom: 16px; }}
        .title {{ font-size: 18px; font-weight: 700; margin-bottom: 8px; }}
        .sub {{ font-size: 13px; color: #94a3b8; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">{'✅' if success else '❌'}</div>
        <div class="title">
            {'Connected to ' + (provider or '').title() + '!' if success else 'Connection Failed'}
        </div>
        <div class="sub">
            {f'Logged in as {display_name}' if success and display_name else ''}
            {error if error else ''}
            <br><br>This window will close automatically…
        </div>
    </div>
    <script>
        if (window.opener) {{
            window.opener.postMessage({result_json}, '*');
        }}
        setTimeout(() => window.close(), 2000);
    </script>
</body>
</html>"""
    return HTMLResponse(html)
