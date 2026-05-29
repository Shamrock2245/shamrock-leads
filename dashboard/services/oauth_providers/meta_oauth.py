"""
OAuth Provider — Meta (Facebook + Instagram)
===============================================
Facebook Login → Page Access Token → Instagram Business Account.

One OAuth login covers both Facebook Pages and Instagram Business accounts.
Uses Graph API v21.0. Short-lived tokens are auto-exchanged for 60-day
long-lived tokens.
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

logger = logging.getLogger("dashboard.services.oauth_providers.meta")

_GRAPH_VERSION = "v21.0"
_AUTH_URL = f"https://www.facebook.com/{_GRAPH_VERSION}/dialog/oauth"
_TOKEN_URL = f"https://graph.facebook.com/{_GRAPH_VERSION}/oauth/access_token"
_GRAPH_BASE = f"https://graph.facebook.com/{_GRAPH_VERSION}"
_TIMEOUT = 15

_SCOPES = ",".join([
    "public_profile",
    "email",
    "pages_manage_posts",
    "pages_read_engagement",
    "instagram_basic",
    "instagram_content_publish",
    "business_management",
])


class MetaOAuthProvider(BaseOAuthProvider):
    """Meta (Facebook + Instagram) OAuth 2.0 provider."""

    platform = "meta"
    display_name = "Facebook & Instagram"

    def __init__(self):
        self._app_id = os.getenv("META_APP_ID", "")
        self._app_secret = os.getenv("META_APP_SECRET", "")
        if not self._app_id:
            logger.warning("META_APP_ID is not set")

    def get_auth_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": _SCOPES,
            "response_type": "code",
        }
        return f"{_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenResponse:
        """
        Exchange code → short-lived token → long-lived token (60 days).
        """
        # Step 1: Exchange code for short-lived token
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    _TOKEN_URL,
                    params={
                        "client_id": self._app_id,
                        "redirect_uri": redirect_uri,
                        "client_secret": self._app_secret,
                        "code": code,
                    },
                )
            data = resp.json()
            if resp.status_code != 200 or "access_token" not in data:
                error = data.get("error", {}).get("message", data.get("error", "unknown"))
                logger.error("Meta token exchange failed (%d): %s", resp.status_code, error)
                return TokenResponse(error=str(error))

            short_token = data["access_token"]

        except Exception as e:
            logger.exception("Meta code exchange error: %s", e)
            return TokenResponse(error=str(e))

        # Step 2: Exchange short-lived → long-lived token
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    _TOKEN_URL,
                    params={
                        "grant_type": "fb_exchange_token",
                        "client_id": self._app_id,
                        "client_secret": self._app_secret,
                        "fb_exchange_token": short_token,
                    },
                )
            data = resp.json()
            if resp.status_code != 200 or "access_token" not in data:
                # Fallback: use short-lived token
                logger.warning("Long-lived token exchange failed, using short-lived token")
                return TokenResponse(
                    access_token=short_token,
                    expires_in=3600,
                )

            return TokenResponse(
                access_token=data["access_token"],
                # Meta long-lived user tokens don't have a separate refresh_token
                # The token itself IS the refresh token (re-exchange before expiry)
                refresh_token=data["access_token"],
                expires_in=int(data.get("expires_in", 5184000)),  # ~60 days
                token_type=data.get("token_type", "bearer"),
            )

        except Exception as e:
            logger.exception("Meta long-lived exchange error: %s", e)
            # Return short-lived token as fallback
            return TokenResponse(access_token=short_token, expires_in=3600)

    async def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """
        For Meta, 'refresh' means exchanging the long-lived token for a new one.
        This only works if the current token hasn't expired yet.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    _TOKEN_URL,
                    params={
                        "grant_type": "fb_exchange_token",
                        "client_id": self._app_id,
                        "client_secret": self._app_secret,
                        "fb_exchange_token": refresh_token,
                    },
                )
            data = resp.json()
            if resp.status_code != 200 or "access_token" not in data:
                error = data.get("error", {}).get("message", "refresh_failed")
                logger.error("Meta token refresh failed: %s", error)
                return TokenResponse(error=str(error))

            new_token = data["access_token"]
            return TokenResponse(
                access_token=new_token,
                refresh_token=new_token,  # same token is used for next refresh
                expires_in=int(data.get("expires_in", 5184000)),
            )
        except Exception as e:
            logger.exception("Meta token refresh error: %s", e)
            return TokenResponse(error=str(e))

    async def get_profile(self, access_token: str) -> ProfileInfo:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{_GRAPH_BASE}/me",
                    params={
                        "fields": "id,name,email,picture.width(200)",
                        "access_token": access_token,
                    },
                )
            if resp.status_code != 200:
                logger.error("Meta profile fetch failed: %d", resp.status_code)
                return ProfileInfo()
            data = resp.json()
            picture_url = ""
            if "picture" in data:
                picture_url = data["picture"].get("data", {}).get("url", "")
            return ProfileInfo(
                account_id=data.get("id", ""),
                display_name=data.get("name", ""),
                email=data.get("email", ""),
                profile_picture=picture_url,
                raw=data,
            )
        except Exception as e:
            logger.exception("Meta profile fetch error: %s", e)
            return ProfileInfo()

    async def discover_accounts(self, access_token: str) -> list[PlatformAccount]:
        """
        Discover Facebook Pages and their linked Instagram Business accounts.
        """
        accounts: list[PlatformAccount] = []

        # ── Facebook Pages ────────────────────────────────────────────
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{_GRAPH_BASE}/me/accounts",
                    params={
                        "fields": "id,name,access_token,picture",
                        "access_token": access_token,
                    },
                )
            if resp.status_code == 200:
                data = resp.json()
                pages = data.get("data", [])
                logger.info("Discovered %d Facebook Page(s)", len(pages))

                for page in pages:
                    page_id = page.get("id", "")
                    page_token = page.get("access_token", "")
                    page_picture = ""
                    if "picture" in page:
                        page_picture = page["picture"].get("data", {}).get("url", "")

                    accounts.append(PlatformAccount(
                        account_id=page_id,
                        display_name=page.get("name", ""),
                        account_type="page",
                        access_token=page_token,
                        metadata={"picture": page_picture},
                    ))

                    # ── Instagram Business Account linked to this Page ──
                    try:
                        ig_resp = await client.get(
                            f"{_GRAPH_BASE}/{page_id}",
                            params={
                                "fields": "instagram_business_account{id,username,profile_picture_url}",
                                "access_token": page_token,
                            },
                        )
                        if ig_resp.status_code == 200:
                            ig_data = ig_resp.json()
                            ig_acct = ig_data.get("instagram_business_account", {})
                            if ig_acct and ig_acct.get("id"):
                                accounts.append(PlatformAccount(
                                    account_id=ig_acct["id"],
                                    display_name=f"@{ig_acct.get('username', '')}",
                                    account_type="ig_business",
                                    metadata={
                                        "page_id": page_id,
                                        "username": ig_acct.get("username", ""),
                                        "profile_picture": ig_acct.get("profile_picture_url", ""),
                                    },
                                ))
                                logger.info(
                                    "Found IG Business: @%s (linked to Page %s)",
                                    ig_acct.get("username"), page_id,
                                )
                    except Exception as e:
                        logger.info("IG discovery for page %s skipped: %s", page_id, e)
            else:
                logger.info("Facebook Pages API returned %d", resp.status_code)
        except Exception as e:
            logger.warning("Facebook Pages discovery error: %s", e)

        return accounts

    async def revoke(self, access_token: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.delete(
                    f"{_GRAPH_BASE}/me/permissions",
                    params={"access_token": access_token},
                )
            success = resp.status_code == 200
            if success:
                logger.info("Meta permissions revoked successfully")
            else:
                logger.warning("Meta revoke returned %d", resp.status_code)
            return success
        except Exception as e:
            logger.error("Meta revoke error: %s", e)
            return False

    def get_sub_platforms(self) -> list[str]:
        return ["facebook", "instagram"]
