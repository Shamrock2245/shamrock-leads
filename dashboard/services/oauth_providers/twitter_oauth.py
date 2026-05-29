"""
OAuth Provider — Twitter / X
===============================
OAuth 2.0 with PKCE (Proof Key for Code Exchange) for Twitter API v2.

Twitter requires PKCE for public *and* confidential clients.  We generate a
random ``code_verifier`` per authorization attempt, derive a S256
``code_challenge``, and stash the verifier in a module-level dict keyed by
the opaque ``state`` parameter so ``exchange_code`` can retrieve it later.

Ref: https://developer.twitter.com/en/docs/authentication/oauth-2-0/authorization-code
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from typing import Optional
from urllib.parse import urlencode

import httpx

from dashboard.services.oauth_providers.base import (
    BaseOAuthProvider,
    ProfileInfo,
    TokenResponse,
)

logger = logging.getLogger("dashboard.services.oauth_providers.twitter")

# ---------------------------------------------------------------------------
# PKCE verifier store  (state → code_verifier)
# ---------------------------------------------------------------------------
# In a multi-process / multi-instance deployment, swap this for a shared
# cache (Redis, DB, etc.).  For a single-process FastAPI app this is fine.
_pkce_verifiers: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Twitter API endpoints
# ---------------------------------------------------------------------------
_AUTH_URL = "https://twitter.com/i/oauth2/authorize"
_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
_USER_ME_URL = "https://api.twitter.com/2/users/me"
_REVOKE_URL = "https://api.twitter.com/2/oauth2/revoke"

_SCOPES = "tweet.read tweet.write users.read offline.access"
_TIMEOUT = 15  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_code_verifier(length: int = 64) -> str:
    """Return a URL-safe random string between 43 and 128 chars (RFC 7636)."""
    length = max(43, min(length, 128))
    return secrets.token_urlsafe(length)[:length]


def _compute_code_challenge(verifier: str) -> str:
    """SHA-256 hash of the verifier, base64url-encoded (no padding)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    """Build an HTTP Basic Authorization header value."""
    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class TwitterOAuthProvider(BaseOAuthProvider):
    """Twitter / X  —  OAuth 2.0 + PKCE provider."""

    platform: str = "twitter"
    display_name: str = "X / Twitter"

    def __init__(self) -> None:
        self._client_id: str = os.getenv("TWITTER_OAUTH_CLIENT_ID", "")
        self._client_secret: str = os.getenv("TWITTER_OAUTH_CLIENT_SECRET", "")

        if not self._client_id:
            logger.warning("TWITTER_OAUTH_CLIENT_ID is not set")
        if not self._client_secret:
            logger.warning("TWITTER_OAUTH_CLIENT_SECRET is not set")

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def get_auth_url(self, state: str, redirect_uri: str) -> str:
        """Build the Twitter authorize URL with PKCE challenge."""
        code_verifier = _generate_code_verifier()
        code_challenge = _compute_code_challenge(code_verifier)

        # Stash the verifier so exchange_code can retrieve it via state.
        _pkce_verifiers[state] = code_verifier

        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": _SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        url = f"{_AUTH_URL}?{urlencode(params)}"
        logger.debug("Twitter auth URL generated for state=%s", state)
        return url

    # ------------------------------------------------------------------
    # Token exchange
    # ------------------------------------------------------------------

    async def exchange_code(self, code: str, redirect_uri: str, state: str = "") -> TokenResponse:
        """Exchange the authorization code + PKCE verifier for tokens.

        ``state`` must be passed so we can look up the stored code_verifier.
        """
        code_verifier = _pkce_verifiers.pop(state, "")
        if not code_verifier:
            logger.error("No PKCE code_verifier found for state=%s", state)
            return TokenResponse(error="missing_pkce_verifier")

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": _basic_auth_header(self._client_id, self._client_secret),
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(_TOKEN_URL, data=payload, headers=headers)

            data = resp.json()

            if resp.status_code != 200:
                error_msg = data.get("error_description") or data.get("error", "unknown_error")
                logger.error("Twitter token exchange failed (%s): %s", resp.status_code, error_msg)
                return TokenResponse(error=error_msg)

            return TokenResponse(
                access_token=data.get("access_token", ""),
                refresh_token=data.get("refresh_token", ""),
                expires_in=int(data.get("expires_in", 0)),
                token_type=data.get("token_type", "Bearer"),
                scope=data.get("scope", ""),
            )

        except httpx.HTTPError as exc:
            logger.exception("HTTP error during Twitter token exchange: %s", exc)
            return TokenResponse(error=str(exc))
        except Exception as exc:
            logger.exception("Unexpected error during Twitter token exchange: %s", exc)
            return TokenResponse(error=str(exc))

    # ------------------------------------------------------------------
    # Token refresh
    # ------------------------------------------------------------------

    async def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """Refresh an expired access token using the refresh_token grant."""
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": _basic_auth_header(self._client_id, self._client_secret),
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(_TOKEN_URL, data=payload, headers=headers)

            data = resp.json()

            if resp.status_code != 200:
                error_msg = data.get("error_description") or data.get("error", "unknown_error")
                logger.error("Twitter token refresh failed (%s): %s", resp.status_code, error_msg)
                return TokenResponse(error=error_msg)

            return TokenResponse(
                access_token=data.get("access_token", ""),
                refresh_token=data.get("refresh_token", refresh_token),
                expires_in=int(data.get("expires_in", 0)),
                token_type=data.get("token_type", "Bearer"),
                scope=data.get("scope", ""),
            )

        except httpx.HTTPError as exc:
            logger.exception("HTTP error during Twitter token refresh: %s", exc)
            return TokenResponse(error=str(exc))
        except Exception as exc:
            logger.exception("Unexpected error during Twitter token refresh: %s", exc)
            return TokenResponse(error=str(exc))

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    async def get_profile(self, access_token: str) -> ProfileInfo:
        """Fetch the authenticated user's Twitter profile via /2/users/me."""
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"user.fields": "profile_image_url,name,username"}

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(_USER_ME_URL, headers=headers, params=params)

            data = resp.json()

            if resp.status_code != 200:
                error_msg = data.get("detail", data.get("title", "profile_fetch_failed"))
                logger.error("Twitter profile fetch failed (%s): %s", resp.status_code, error_msg)
                return ProfileInfo(raw=data)

            user = data.get("data", {})
            return ProfileInfo(
                account_id=user.get("id", ""),
                display_name=f"@{user.get('username', '')}" if user.get("username") else user.get("name", ""),
                profile_picture=user.get("profile_image_url", ""),
                raw=data,
            )

        except httpx.HTTPError as exc:
            logger.exception("HTTP error fetching Twitter profile: %s", exc)
            return ProfileInfo()
        except Exception as exc:
            logger.exception("Unexpected error fetching Twitter profile: %s", exc)
            return ProfileInfo()

    # ------------------------------------------------------------------
    # Revoke
    # ------------------------------------------------------------------

    async def revoke(self, access_token: str) -> bool:
        """Revoke an access token via Twitter's revocation endpoint."""
        payload = {
            "token": access_token,
            "token_type_hint": "access_token",
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": _basic_auth_header(self._client_id, self._client_secret),
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(_REVOKE_URL, data=payload, headers=headers)

            if resp.status_code == 200:
                logger.info("Twitter access token revoked successfully")
                return True

            data = resp.json() if resp.content else {}
            logger.error(
                "Twitter token revocation failed (%s): %s",
                resp.status_code,
                data.get("error_description", data.get("error", "unknown")),
            )
            return False

        except httpx.HTTPError as exc:
            logger.exception("HTTP error during Twitter token revocation: %s", exc)
            return False
        except Exception as exc:
            logger.exception("Unexpected error during Twitter token revocation: %s", exc)
            return False
