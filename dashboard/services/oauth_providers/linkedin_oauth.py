"""
OAuth Provider — LinkedIn
===========================
LinkedIn OAuth 2.0 for Company Page posting.

Uses OpenID Connect for profile + Community Management API for posting.
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

logger = logging.getLogger("dashboard.services.oauth_providers.linkedin")

_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
_TIMEOUT = 15

_SCOPES = "openid profile email w_member_social r_organization_social w_organization_social"


class LinkedInOAuthProvider(BaseOAuthProvider):
    """LinkedIn OAuth 2.0 — Company Page posting."""

    platform = "linkedin"
    display_name = "LinkedIn"

    def __init__(self):
        self._client_id = os.getenv("LINKEDIN_OAUTH_CLIENT_ID", "")
        self._client_secret = os.getenv("LINKEDIN_OAUTH_CLIENT_SECRET", "")
        if not self._client_id:
            logger.warning("LINKEDIN_OAUTH_CLIENT_ID is not set")

    def get_auth_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": _SCOPES,
        }
        return f"{_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenResponse:
        # LinkedIn requires form-encoded POST (not JSON)
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    _TOKEN_URL,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            data = resp.json()
            if resp.status_code != 200:
                error = data.get("error_description", data.get("error", "unknown"))
                logger.error("LinkedIn token exchange failed (%d): %s", resp.status_code, error)
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
            logger.exception("LinkedIn token exchange error: %s", e)
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
                resp = await client.post(
                    _TOKEN_URL,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            data = resp.json()
            if resp.status_code != 200:
                error = data.get("error_description", data.get("error", "unknown"))
                logger.error("LinkedIn token refresh failed (%d): %s", resp.status_code, error)
                return TokenResponse(error=error)
            return TokenResponse(
                access_token=data.get("access_token", ""),
                refresh_token=data.get("refresh_token", refresh_token),
                expires_in=int(data.get("expires_in", 0)),
                scope=data.get("scope", ""),
            )
        except Exception as e:
            logger.exception("LinkedIn token refresh error: %s", e)
            return TokenResponse(error=str(e))

    async def get_profile(self, access_token: str) -> ProfileInfo:
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(_USERINFO_URL, headers=headers)
            if resp.status_code != 200:
                logger.error("LinkedIn profile fetch failed: %d", resp.status_code)
                return ProfileInfo()
            data = resp.json()
            return ProfileInfo(
                account_id=data.get("sub", ""),
                display_name=data.get("name", ""),
                email=data.get("email", ""),
                profile_picture=data.get("picture", ""),
                raw=data,
            )
        except Exception as e:
            logger.exception("LinkedIn profile fetch error: %s", e)
            return ProfileInfo()

    async def discover_accounts(self, access_token: str) -> list[PlatformAccount]:
        """Discover LinkedIn organizations the user administers."""
        accounts: list[PlatformAccount] = []
        headers = {
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": "202401",
            "X-Restli-Protocol-Version": "2.0.0",
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    "https://api.linkedin.com/rest/organizationAcls",
                    headers=headers,
                    params={
                        "q": "roleAssignee",
                        "role": "ADMINISTRATOR",
                    },
                )
            if resp.status_code == 200:
                data = resp.json()
                for elem in data.get("elements", []):
                    org_urn = elem.get("organization", "")
                    org_detail = elem.get("organization~", {})
                    accounts.append(PlatformAccount(
                        account_id=org_urn,
                        display_name=org_detail.get("localizedName", org_urn),
                        account_type="organization",
                        metadata={"vanity_name": org_detail.get("vanityName", "")},
                    ))
                logger.info("Discovered %d LinkedIn organization(s)", len(accounts))
            else:
                logger.info("LinkedIn org discovery returned %d", resp.status_code)
        except Exception as e:
            logger.info("LinkedIn org discovery skipped: %s", e)

        return accounts

    async def revoke(self, access_token: str) -> bool:
        # LinkedIn doesn't have a standard revoke endpoint
        logger.info("LinkedIn token revoke (no-op — LinkedIn has no revoke endpoint)")
        return True
