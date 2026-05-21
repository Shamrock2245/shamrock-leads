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

    def is_configured(self) -> bool:
        cfg = settings.instagram
        return cfg.enabled and bool(cfg.business_account_id) and bool(settings.facebook.page_access_token)

    async def post(self, content: str, media_paths: Optional[list[str]] = None, link_url: str = "") -> PostResult:
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
