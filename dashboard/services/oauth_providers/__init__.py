"""
OAuth Provider Registry
========================
Maps platform names to their OAuth provider implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dashboard.services.oauth_providers.base import BaseOAuthProvider

_PROVIDERS: dict[str, type] = {}


def get_provider(platform: str) -> "BaseOAuthProvider":
    """Get an instantiated OAuth provider by platform name."""
    if not _PROVIDERS:
        _register_all()
    cls = _PROVIDERS.get(platform)
    if not cls:
        raise ValueError(f"Unknown OAuth provider: {platform}")
    return cls()


def _register_all():
    """Lazy-import and register all providers."""
    from dashboard.services.oauth_providers.google_oauth import GoogleOAuthProvider
    from dashboard.services.oauth_providers.twitter_oauth import TwitterOAuthProvider
    from dashboard.services.oauth_providers.linkedin_oauth import LinkedInOAuthProvider
    from dashboard.services.oauth_providers.meta_oauth import MetaOAuthProvider

    _PROVIDERS["google"] = GoogleOAuthProvider
    _PROVIDERS["twitter"] = TwitterOAuthProvider
    _PROVIDERS["linkedin"] = LinkedInOAuthProvider
    _PROVIDERS["meta"] = MetaOAuthProvider
