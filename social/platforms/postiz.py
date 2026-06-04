"""
Shamrock Social Engine — Postiz Unified Platform Adapter
==========================================================
Routes ALL social media posting through the self-hosted Postiz instance.
Uses the public API v1 wrapper to interact with Postiz.
"""
from __future__ import annotations
import logging
from typing import Optional
from social.models import PostResult, Platform
from social.platforms.base import PlatformAdapter
from social.postiz_client import get_public_postiz_client


logger = logging.getLogger("social.platforms.postiz")


class PostizAdapter(PlatformAdapter):
    """
    Unified platform adapter that routes ALL posts through Postiz.
    Delegates completely to PostizPublicClient.
    """
    def __init__(self, target_platform: str = "twitter"):
        self._target_platform = target_platform
        self._client = get_public_postiz_client()
        self.platform = Platform(target_platform) if target_platform in [p.value for p in Platform] else Platform.TWITTER

    async def post(
        self,
        content: str,
        media_paths: Optional[list[str]] = None,
        link_url: str = "",
    ) -> PostResult:
        """Post content via Postiz to the target platform."""
        integration_id = await self._client.get_integration_for_platform(self._target_platform)
        if not integration_id:
            return PostResult(
                success=False,
                error=f"Platform '{self._target_platform}' not connected in Postiz. "
                      f"Connect it at {self._client.base_url}",
            )

        # Upload media if provided
        media_ids = []
        if media_paths:
            for path in media_paths:
                mid = await self._client.upload_media(path)
                if mid:
                    media_ids.append(mid)

        # Append link to content if provided
        post_content = content
        if link_url and link_url not in content:
            post_content = f"{content}\n\n{link_url}"

        # Postiz Public API expects:
        # {
        #   "type": "now",
        #   "posts": [{"integration": {"id": "integration_id"}, "value": [{"content": "...", "image": [{"id": "media_id"}]}]}]
        # }
        platform_posts = [
            {
                "integration": {"id": integration_id},
                "value": [
                    {
                        "content": post_content,
                        "image": [{"id": mid} for mid in media_ids] if media_ids else [],
                    }
                ],
                "settings": {"__type": self._target_platform}
            }
        ]

        result = await self._client.create_post(
            post_type="now",
            platform_posts=platform_posts,
        )

        if "error" in result:
            return PostResult(
                success=False,
                error=result["error"],
                rate_limited="rate" in str(result.get("error", "")).lower(),
            )

        # Extract post ID from Postiz response
        post_id = ""
        if isinstance(result, list) and result:
            post_id = result[0].get("id", "")
        elif isinstance(result, dict):
            post_id = result.get("id", result.get("postId", ""))

        return PostResult(
            success=True,
            platform_post_id=post_id,
            platform_url="",
        )

    async def post_thread(
        self,
        parts: list[str],
        media_paths: Optional[list[str]] = None,
    ) -> PostResult:
        """Post a thread/series via Postiz."""
        integration_id = await self._client.get_integration_for_platform(self._target_platform)
        if not integration_id:
            return PostResult(
                success=False,
                error=f"Platform '{self._target_platform}' not connected in Postiz.",
            )

        # Upload media for first post
        media_ids = []
        if media_paths:
            for path in media_paths:
                mid = await self._client.upload_media(path)
                if mid:
                    media_ids.append(mid)

        # For threads, combine into single post with thread markers or handle per-platform
        combined = "\n\n---\n\n".join(parts)
        platform_posts = [
            {
                "integration": {"id": integration_id},
                "value": [
                    {
                        "content": combined,
                        "image": [{"id": mid} for mid in media_ids] if media_ids else [],
                    }
                ],
                "settings": {"__type": self._target_platform}
            }
        ]

        result = await self._client.create_post(
            post_type="now",
            platform_posts=platform_posts,
        )

        if "error" in result:
            return PostResult(success=False, error=result["error"])

        post_id = ""
        if isinstance(result, list) and result:
            post_id = result[0].get("id", "")
        elif isinstance(result, dict):
            post_id = result.get("id", result.get("postId", ""))

        return PostResult(success=True, platform_post_id=post_id)

    async def get_engagement(self, platform_post_id: str) -> dict:
        """Pull engagement metrics via Postiz analytics."""
        # For now, return empty or mock if analytics isn't fully supported in Public API v1
        return {}

    def validate_content(self, content: str) -> tuple[bool, str]:
        """Validate content against platform character limits."""
        limits = {
            "twitter": 280,
            "linkedin": 3000,
            "facebook": 63206,
            "instagram": 2200,
            "threads": 500,
            "tiktok": 2200,
            "bluesky": 300,
            "mastodon": 500,
        }
        limit = limits.get(self._target_platform, 5000)
        if len(content) > limit:
            return False, f"Content ({len(content)} chars) exceeds {self._target_platform} limit ({limit})"
        return True, ""

    async def delete_post(self, platform_post_id: str) -> bool:
        """Delete a post via Postiz."""
        # Note: Public API v1 delete endpoint isn't fully standardized, so return False or stub
        return False

    def is_configured(self) -> bool:
        """Check if Postiz API key is configured."""
        return bool(self._client.api_key)


# Singleton/helper mapping to maintain compatibility with existing codebase imports
def get_postiz_client():
    return get_public_postiz_client()
