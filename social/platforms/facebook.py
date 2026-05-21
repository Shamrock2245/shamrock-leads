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

    def is_configured(self) -> bool:
        cfg = settings.facebook
        return cfg.enabled and bool(cfg.page_id) and bool(cfg.page_access_token)

    async def post(self, content: str, media_paths: Optional[list[str]] = None, link_url: str = "") -> PostResult:
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
