"""
OAuth Provider — Google
=========================
Google OAuth 2.0 for GBP (Google Business Profile) and YouTube.

Scopes:
  - openid, email, profile  (identity)
  - business.manage          (GBP locations)
  - youtube                  (YouTube channel)
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlencode

import httpx

from dashboard.services.oauth_providers.base import (
    BaseOAuthProvider,
    PlatformAccount,
    ProfileInfo,
    TokenResponse,
)

logger = logging.getLogger("dashboard.services.oauth_providers.google")

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_TIMEOUT = 15

_SCOPES = " ".join([
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/business.manage",
    "https://www.googleapis.com/auth/youtube",
])


class GoogleOAuthProvider(BaseOAuthProvider):
    """Google OAuth 2.0 — GBP + YouTube."""

    platform = "google"
    display_name = "Google"

    def __init__(self):
        self._client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
        self._client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
        if not self._client_id:
            logger.warning("GOOGLE_OAUTH_CLIENT_ID is not set")

    def get_auth_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _SCOPES,
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        return f"{_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenResponse:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(_TOKEN_URL, data=payload)
            data = resp.json()
            if resp.status_code != 200:
                error = data.get("error_description", data.get("error", "unknown"))
                logger.error("Google token exchange failed (%d): %s", resp.status_code, error)
                return TokenResponse(error=error)
            return TokenResponse(
                access_token=data.get("access_token", ""),
                refresh_token=data.get("refresh_token", ""),
                expires_in=int(data.get("expires_in", 0)),
                token_type=data.get("token_type", "Bearer"),
                scope=data.get("scope", ""),
                id_token=data.get("id_token", ""),
            )
        except Exception as e:
            logger.exception("Google token exchange error: %s", e)
            return TokenResponse(error=str(e))

    async def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(_TOKEN_URL, data=payload)
            data = resp.json()
            if resp.status_code != 200:
                error = data.get("error_description", data.get("error", "unknown"))
                logger.error("Google token refresh failed (%d): %s", resp.status_code, error)
                return TokenResponse(error=error)
            return TokenResponse(
                access_token=data.get("access_token", ""),
                refresh_token=refresh_token,  # Google doesn't always return new refresh_token
                expires_in=int(data.get("expires_in", 0)),
                token_type=data.get("token_type", "Bearer"),
                scope=data.get("scope", ""),
            )
        except Exception as e:
            logger.exception("Google token refresh error: %s", e)
            return TokenResponse(error=str(e))

    async def get_profile(self, access_token: str) -> ProfileInfo:
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(_USERINFO_URL, headers=headers)
            if resp.status_code != 200:
                logger.error("Google profile fetch failed: %d", resp.status_code)
                return ProfileInfo()
            data = resp.json()
            return ProfileInfo(
                account_id=data.get("email", data.get("id", "")),
                display_name=data.get("name", ""),
                email=data.get("email", ""),
                profile_picture=data.get("picture", ""),
                raw=data,
            )
        except Exception as e:
            logger.exception("Google profile fetch error: %s", e)
            return ProfileInfo()

    async def discover_accounts(self, access_token: str) -> list[PlatformAccount]:
        """Discover GBP locations and YouTube channels."""
        accounts: list[PlatformAccount] = []
        headers = {"Authorization": f"Bearer {access_token}"}

        # ── GBP Locations ────────────────────────────────────────────
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
                    headers=headers,
                )
            if resp.status_code == 200:
                data = resp.json()
                for acct in data.get("accounts", []):
                    accounts.append(PlatformAccount(
                        account_id=acct.get("name", ""),
                        display_name=acct.get("accountName", ""),
                        account_type="gbp_location",
                        metadata={"type": acct.get("type", ""), "role": acct.get("role", "")},
                    ))
                logger.info("Discovered %d GBP account(s)", len(data.get("accounts", [])))
            else:
                logger.info("GBP API returned %d (may not be enabled)", resp.status_code)
        except Exception as e:
            logger.info("GBP discovery skipped: %s", e)

        # ── YouTube Channels ─────────────────────────────────────────
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    headers=headers,
                    params={"part": "snippet", "mine": "true"},
                )
            if resp.status_code == 200:
                data = resp.json()
                for ch in data.get("items", []):
                    snippet = ch.get("snippet", {})
                    accounts.append(PlatformAccount(
                        account_id=ch.get("id", ""),
                        display_name=snippet.get("title", ""),
                        account_type="youtube_channel",
                        metadata={
                            "description": snippet.get("description", "")[:100],
                            "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                        },
                    ))
                logger.info("Discovered %d YouTube channel(s)", len(data.get("items", [])))
            else:
                logger.info("YouTube API returned %d", resp.status_code)
        except Exception as e:
            logger.info("YouTube discovery skipped: %s", e)

        return accounts

    async def revoke(self, access_token: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    _REVOKE_URL,
                    params={"token": access_token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            success = resp.status_code == 200
            if success:
                logger.info("Google token revoked successfully")
            else:
                logger.warning("Google revoke returned %d", resp.status_code)
            return success
        except Exception as e:
            logger.error("Google revoke error: %s", e)
            return False

    def get_sub_platforms(self) -> list[str]:
        return ["google", "gbp", "youtube"]
