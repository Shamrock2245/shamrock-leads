"""
Shamrock Social Engine — Postiz Unified Platform Adapter
==========================================================
Routes ALL social media posting through the self-hosted Postiz instance.

Why Postiz instead of direct platform APIs:
  - Handles OAuth refresh, rate limiting, and API changes for 30+ platforms
  - Single API to post to Twitter, LinkedIn, Facebook, Instagram, Threads, etc.
  - Built-in analytics, media upload, scheduling
  - $0/month (self-hosted) vs $49-299/month (Ayrshare, Buffer, etc.)

Architecture:
  Our Queue (MongoDB) → Approved Post → PostizAdapter → Postiz API → 30+ Platforms

Postiz REST API Reference:
  POST /api/posts          → Create/schedule a post
  GET  /api/posts          → List posts
  GET  /api/integrations   → List connected channels (integration IDs)
  POST /api/media          → Upload media
  GET  /api/analytics      → Get analytics
"""

from __future__ import annotations

import logging
import os
from typing import Optional
from datetime import datetime, timezone

import httpx

from social.models import PostResult, Platform
from social.platforms.base import PlatformAdapter

logger = logging.getLogger("social.platforms.postiz")


# ── Platform → Postiz Provider Mapping ────────────────────────────────────────
# Maps our internal platform names to Postiz's provider identifiers
PLATFORM_TO_POSTIZ_PROVIDER = {
    "twitter": "x",
    "linkedin": "linkedin",
    "facebook": "facebook",
    "instagram": "instagram",
    "threads": "threads",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "reddit": "reddit",
    "telegram": "telegram",
    "bluesky": "bluesky",
    "mastodon": "mastodon",
    "pinterest": "pinterest",
    "gbp": "google-business",
}


class PostizClient:
    """
    Low-level async HTTP client for the Postiz REST API.
    All platform adapters delegate to this client.
    """

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
    ):
        self.base_url = (
            base_url
            or os.getenv("POSTIZ_URL", "http://postiz:5000")
        ).rstrip("/")
        self.api_key = api_key or os.getenv("POSTIZ_API_KEY", "")
        self._client: Optional[httpx.AsyncClient] = None
        self._integrations_cache: dict = {}
        self._cache_ts: Optional[datetime] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Integration (Channel) Management ──────────────────────────────────

    async def list_integrations(self, force_refresh: bool = False) -> list[dict]:
        """
        List all connected social media channels.
        Caches results for 5 minutes.

        Returns list of:
            {id, name, provider, picture, ...}
        """
        now = datetime.now(timezone.utc)
        if (
            not force_refresh
            and self._cache_ts
            and (now - self._cache_ts).seconds < 300
            and self._integrations_cache
        ):
            return self._integrations_cache.get("integrations", [])

        client = self._get_client()
        try:
            resp = await client.get("/api/integrations")
            resp.raise_for_status()
            data = resp.json()
            integrations = data if isinstance(data, list) else data.get("integrations", data.get("data", []))
            self._integrations_cache = {"integrations": integrations}
            self._cache_ts = now
            return integrations
        except Exception as e:
            logger.error("❌ Postiz list_integrations failed: %s", e)
            return []

    async def get_integration_for_platform(self, platform: str) -> Optional[str]:
        """
        Find the Postiz integration ID for a given platform.

        Args:
            platform: Our internal platform name (twitter, linkedin, etc.)

        Returns:
            The integration ID string, or None if not connected
        """
        postiz_provider = PLATFORM_TO_POSTIZ_PROVIDER.get(platform, platform)
        integrations = await self.list_integrations()

        for integration in integrations:
            provider = integration.get("providerIdentifier", "") or integration.get("provider", "")
            if provider.lower() == postiz_provider.lower():
                return integration.get("id", "")

        return None

    async def get_all_connected_platforms(self) -> dict[str, dict]:
        """
        Get status of all connected platforms.

        Returns:
            {platform_name: {id, name, picture, connected: True/False}}
        """
        integrations = await self.list_integrations()
        result = {}

        # Build reverse map
        provider_to_platform = {v: k for k, v in PLATFORM_TO_POSTIZ_PROVIDER.items()}

        for integration in integrations:
            provider = (
                integration.get("providerIdentifier", "")
                or integration.get("provider", "")
            ).lower()
            platform_name = provider_to_platform.get(provider, provider)
            result[platform_name] = {
                "id": integration.get("id", ""),
                "name": integration.get("name", ""),
                "picture": integration.get("picture", ""),
                "connected": True,
                "provider": provider,
            }

        return result

    # ── Posting ───────────────────────────────────────────────────────────

    async def create_post(
        self,
        content: str,
        integration_ids: list[str],
        media_ids: Optional[list[str]] = None,
        schedule_date: Optional[datetime] = None,
        settings_override: Optional[dict] = None,
    ) -> dict:
        """
        Create a post via Postiz API.

        Args:
            content: Post text
            integration_ids: List of Postiz integration IDs to post to
            media_ids: Optional list of uploaded media IDs
            schedule_date: Optional future datetime for scheduling
            settings_override: Platform-specific settings

        Returns:
            Postiz response dict with post ID
        """
        client = self._get_client()

        payload = {
            "posts": [
                {
                    "content": content,
                    "integration": {
                        "id": iid,
                    },
                    **({"media": media_ids} if media_ids else {}),
                    **(settings_override or {}),
                }
                for iid in integration_ids
            ],
            "type": "now" if not schedule_date else "schedule",
        }

        if schedule_date:
            payload["date"] = schedule_date.isoformat()

        try:
            resp = await client.post("/api/posts", json=payload)
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "🚀 Postiz post created: %d platform(s), content: %.40s…",
                len(integration_ids), content,
            )
            return data
        except httpx.HTTPStatusError as e:
            error_body = e.response.text if e.response else str(e)
            logger.error("❌ Postiz post failed (%d): %s", e.response.status_code, error_body)
            return {"error": error_body, "status_code": e.response.status_code}
        except Exception as e:
            logger.error("❌ Postiz post error: %s", e)
            return {"error": str(e)}

    # ── Media Upload ─────────────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """
        Upload a media file to Postiz.

        Returns the media ID for use in create_post.
        """
        client = self._get_client()

        try:
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f)}
                # Remove Content-Type for multipart
                headers = {"Authorization": f"Bearer {self.api_key}"}
                resp = await client.post(
                    "/api/media",
                    files=files,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                media_id = data.get("id", "")
                logger.info("📎 Media uploaded: %s → %s", file_path, media_id)
                return media_id
        except Exception as e:
            logger.error("❌ Media upload failed: %s", e)
            return None

    # ── Analytics ─────────────────────────────────────────────────────────

    async def get_post_analytics(self, post_id: str) -> dict:
        """Get engagement analytics for a specific post."""
        client = self._get_client()
        try:
            resp = await client.get(f"/api/posts/{post_id}/analytics")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("❌ Analytics fetch failed for %s: %s", post_id, e)
            return {}

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> dict:
        """Check if the Postiz instance is healthy and reachable."""
        client = self._get_client()
        try:
            resp = await client.get("/api/auth/me")
            if resp.status_code == 200:
                user = resp.json()
                return {
                    "status": "healthy",
                    "connected": True,
                    "user": user.get("name", user.get("email", "unknown")),
                }
            return {"status": "unhealthy", "connected": False, "error": f"HTTP {resp.status_code}"}
        except httpx.ConnectError:
            return {"status": "unreachable", "connected": False, "error": "Cannot reach Postiz"}
        except Exception as e:
            return {"status": "error", "connected": False, "error": str(e)}

    # ── Delete ────────────────────────────────────────────────────────────

    async def delete_post(self, post_id: str) -> bool:
        """Delete a post from Postiz."""
        client = self._get_client()
        try:
            resp = await client.delete(f"/api/posts/{post_id}")
            return resp.status_code < 300
        except Exception as e:
            logger.error("❌ Postiz delete failed: %s", e)
            return False


class PostizAdapter(PlatformAdapter):
    """
    Unified platform adapter that routes ALL posts through Postiz.

    Instead of maintaining individual Twitter/LinkedIn/Facebook/Instagram
    adapters with their own OAuth flows and API versioning nightmares,
    this adapter sends everything through the self-hosted Postiz API.
    """

    def __init__(self, target_platform: str = "twitter"):
        self._target_platform = target_platform
        self._client = PostizClient()
        self.platform = Platform(target_platform) if target_platform in [p.value for p in Platform] else Platform.TWITTER

    async def post(
        self,
        content: str,
        media_paths: Optional[list[str]] = None,
        link_url: str = "",
    ) -> PostResult:
        """Post content via Postiz to the target platform."""
        integration_id = await self._client.get_integration_for_platform(
            self._target_platform
        )
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

        result = await self._client.create_post(
            content=post_content,
            integration_ids=[integration_id],
            media_ids=media_ids if media_ids else None,
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
            platform_url="",  # Postiz doesn't always return the platform URL immediately
        )

    async def post_thread(
        self,
        parts: list[str],
        media_paths: Optional[list[str]] = None,
    ) -> PostResult:
        """
        Post a thread/series via Postiz.
        For Twitter threads, Postiz handles the chaining automatically.
        """
        integration_id = await self._client.get_integration_for_platform(
            self._target_platform
        )
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

        # For threads, combine into single post with thread markers
        # Postiz handles Twitter threads natively
        combined = "\n\n---\n\n".join(parts)
        result = await self._client.create_post(
            content=combined,
            integration_ids=[integration_id],
            media_ids=media_ids if media_ids else None,
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
        return await self._client.get_post_analytics(platform_post_id)

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
        return await self._client.delete_post(platform_post_id)

    def is_configured(self) -> bool:
        """Check if Postiz API key is configured."""
        return bool(self._client.api_key)


# ── Singleton for shared use ──────────────────────────────────────────────────
_postiz_client: Optional[PostizClient] = None


def get_postiz_client() -> PostizClient:
    """Get or create the global PostizClient instance."""
    global _postiz_client
    if _postiz_client is None:
        _postiz_client = PostizClient()
    return _postiz_client
