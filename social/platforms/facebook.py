"""
Shamrock Social Engine — Facebook Pages Adapter (Stub)
=======================================================
Facebook Pages posting via Graph API v19.
Full implementation in Phase 3.
"""

from __future__ import annotations

import logging
from typing import Optional

from social.config import settings
from social.models import PostResult, Platform
from social.platforms.base import PlatformAdapter

logger = logging.getLogger("social.platforms.facebook")


class FacebookAdapter(PlatformAdapter):
    """Facebook Pages posting adapter — Phase 3 implementation."""

    platform = Platform.FACEBOOK

    def __init__(self):
        self._oauth_token = None
        self._oauth_loaded = False

    async def _load_oauth_token(self):
        """Try to load a live OAuth token from MongoDB."""
        if self._oauth_loaded:
            return
        self._oauth_loaded = True
        try:
            from social.oauth_bridge import get_live_token
            self._oauth_token = await get_live_token("meta")
        except Exception:
            self._oauth_token = None

    def _get_page_token(self) -> str:
        """Get page access token — from OAuth metadata or static config."""
        if self._oauth_token and self._oauth_token.get("metadata"):
            pt = self._oauth_token["metadata"].get("page_access_token", "")
            if pt:
                return pt
        return settings.facebook.page_access_token

    def _get_page_id(self) -> str:
        """Get page ID — from OAuth metadata or static config."""
        if self._oauth_token and self._oauth_token.get("metadata"):
            pid = self._oauth_token["metadata"].get("page_id", "")
            if pid:
                return pid
        return settings.facebook.page_id

    def is_configured(self) -> bool:
        cfg = settings.facebook
        has_static = cfg.enabled and bool(cfg.page_id) and bool(cfg.page_access_token)
        has_oauth = bool(self._oauth_token and self._get_page_token())
        return has_static or has_oauth

    async def post(self, content: str, media_paths: Optional[list[str]] = None, link_url: str = "") -> PostResult:
        await self._load_oauth_token()
        return PostResult(platform=self.platform, error="Facebook adapter not yet implemented (Phase 3)")

    async def post_thread(self, parts: list[str], media_paths: Optional[list[str]] = None) -> PostResult:
        return PostResult(platform=self.platform, error="Facebook adapter not yet implemented (Phase 3)")

    async def get_engagement(self, platform_post_id: str) -> dict:
        return {}

    def validate_content(self, content: str) -> tuple[bool, str]:
        if not content.strip():
            return False, "Content is empty"
        if len(content) > settings.facebook.max_post_length:
            return False, f"Content exceeds {settings.facebook.max_post_length} chars"
        return True, ""

    async def delete_post(self, platform_post_id: str) -> bool:
        return False
