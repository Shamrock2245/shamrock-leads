"""
Shamrock Social Engine — Platform Adapter Base
================================================
Abstract base class for all social media platform adapters.
Each platform (Twitter, LinkedIn, Facebook, Instagram) implements this interface.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from social.models import PostResult, Platform

logger = logging.getLogger("social.platforms")


class PlatformAdapter(ABC):
    """Abstract base class for social media platform posting adapters."""

    platform: Platform = Platform.TWITTER

    @abstractmethod
    async def post(
        self,
        content: str,
        media_paths: Optional[list[str]] = None,
        link_url: str = "",
    ) -> PostResult:
        """
        Post content to the platform.

        Args:
            content: The formatted post text (already platform-optimized)
            media_paths: Optional list of local file paths for images/videos
            link_url: Optional URL to include

        Returns:
            PostResult with success/failure and platform post ID
        """
        ...

    @abstractmethod
    async def post_thread(
        self,
        parts: list[str],
        media_paths: Optional[list[str]] = None,
    ) -> PostResult:
        """
        Post a multi-part thread (Twitter) or carousel (Instagram).

        Args:
            parts: List of individual post texts
            media_paths: Optional media for the first post

        Returns:
            PostResult with the ID of the first post in the thread
        """
        ...

    @abstractmethod
    async def get_engagement(self, platform_post_id: str) -> dict:
        """
        Pull engagement metrics for a posted item.

        Returns dict with: impressions, likes, shares, comments, clicks
        """
        ...

    @abstractmethod
    def validate_content(self, content: str) -> tuple[bool, str]:
        """
        Validate content against platform rules (character limits, etc.)

        Returns:
            (is_valid, error_message)
        """
        ...

    @abstractmethod
    async def delete_post(self, platform_post_id: str) -> bool:
        """Delete a posted item from the platform."""
        ...

    def is_configured(self) -> bool:
        """Check if the platform has valid credentials configured."""
        return False
