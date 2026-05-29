"""
Shamrock Social Engine — Instagram Adapter (Stub)
===================================================
Instagram posting via Graph API (Content Publishing).
Full implementation in Phase 3.
"""

from __future__ import annotations

import logging
from typing import Optional

from social.config import settings
from social.models import PostResult, Platform
from social.platforms.base import PlatformAdapter

logger = logging.getLogger("social.platforms.instagram")


class InstagramAdapter(PlatformAdapter):
    """Instagram Business Account posting adapter — Phase 3 implementation."""

    platform = Platform.INSTAGRAM

    def __init__(self):
        self._oauth_token = None
        self._oauth_loaded = False

    async def _load_oauth_token(self):
        """Try to load a live OAuth token from MongoDB (Meta provider)."""
        if self._oauth_loaded:
            return
        self._oauth_loaded = True
        try:
            from social.oauth_bridge import get_live_token
            self._oauth_token = await get_live_token("meta")
        except Exception:
            self._oauth_token = None

    def _get_ig_account_id(self) -> str:
        """Get IG Business Account ID — from OAuth metadata or static config."""
        if self._oauth_token and self._oauth_token.get("metadata"):
            ig_id = self._oauth_token["metadata"].get("ig_business_id", "")
            if ig_id:
                return ig_id
        return settings.instagram.business_account_id

    def is_configured(self) -> bool:
        cfg = settings.instagram
        has_static = cfg.enabled and bool(cfg.business_account_id) and bool(settings.facebook.page_access_token)
        has_oauth = bool(self._oauth_token and self._get_ig_account_id())
        return has_static or has_oauth

    async def post(self, content: str, media_paths: Optional[list[str]] = None, link_url: str = "") -> PostResult:
        await self._load_oauth_token()
        return PostResult(platform=self.platform, error="Instagram adapter not yet implemented (Phase 3)")

    async def post_thread(self, parts: list[str], media_paths: Optional[list[str]] = None) -> PostResult:
        return PostResult(platform=self.platform, error="Instagram adapter not yet implemented (Phase 3)")

    async def get_engagement(self, platform_post_id: str) -> dict:
        return {}

    def validate_content(self, content: str) -> tuple[bool, str]:
        if not content.strip():
            return False, "Content is empty"
        if len(content) > settings.instagram.max_caption_length:
            return False, f"Caption exceeds {settings.instagram.max_caption_length} chars"
        return True, ""

    async def delete_post(self, platform_post_id: str) -> bool:
        return False
