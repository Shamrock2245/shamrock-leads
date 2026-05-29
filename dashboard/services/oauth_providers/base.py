"""
OAuth Provider — Base Class
=============================
Abstract interface every OAuth provider must implement.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger("dashboard.services.oauth_providers")


@dataclass
class TokenResponse:
    """Result of an OAuth token exchange or refresh."""
    access_token: str = ""
    refresh_token: str = ""
    expires_in: int = 0  # seconds
    token_type: str = "Bearer"
    scope: str = ""
    id_token: str = ""
    error: str = ""

    @property
    def success(self) -> bool:
        return bool(self.access_token) and not self.error


@dataclass
class ProfileInfo:
    """User profile fetched after OAuth."""
    account_id: str = ""        # unique ID on the platform
    display_name: str = ""      # human-readable name or email
    email: str = ""
    profile_picture: str = ""
    raw: dict = field(default_factory=dict)  # full API response


@dataclass
class PlatformAccount:
    """A sub-account discovered during OAuth (e.g. a Facebook Page, GBP location)."""
    account_id: str = ""
    display_name: str = ""
    account_type: str = ""      # "page", "ig_business", "gbp_location", "channel"
    access_token: str = ""      # page-specific token if applicable
    metadata: dict = field(default_factory=dict)


class BaseOAuthProvider(ABC):
    """Abstract OAuth 2.0 provider."""

    platform: str = ""
    display_name: str = ""

    @abstractmethod
    def get_auth_url(self, state: str, redirect_uri: str) -> str:
        """Build the authorization URL to redirect the user to."""
        ...

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> TokenResponse:
        """Exchange an authorization code for tokens."""
        ...

    @abstractmethod
    async def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """Refresh an expired access token."""
        ...

    @abstractmethod
    async def get_profile(self, access_token: str) -> ProfileInfo:
        """Fetch the authenticated user's profile info."""
        ...

    async def discover_accounts(self, access_token: str) -> list[PlatformAccount]:
        """
        Discover sub-accounts (Pages, IG accounts, GBP locations, etc.)

        Override in providers that have multi-account hierarchies.
        Returns empty list by default.
        """
        return []

    async def revoke(self, access_token: str) -> bool:
        """Revoke an access token. Returns True on success."""
        return True

    def get_sub_platforms(self) -> list[str]:
        """Return platform keys this provider covers (e.g. Meta → facebook, instagram)."""
        return [self.platform]
