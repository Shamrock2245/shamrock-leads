"""
Shamrock Social Engine — Postiz Public API v1 Client
=====================================================
Clean Python wrapper for the Postiz Public API v1.
Used for getting connected integrations, scheduling/publishing posts,
uploading media, and pulling post history/status.

Auth Header: Authorization: <POSTIZ_API_KEY> (no Bearer prefix)
Base URL: https://social.shamrockbailbonds.biz/api/public/v1/
"""
from __future__ import annotations
import logging
import os
from typing import Optional
from datetime import datetime, timezone
import httpx
from social.config import settings

logger = logging.getLogger("social.postiz_client")

# Maps internal platform names to Postiz's provider identifiers

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


class PostizPublicClient:
    """
    HTTP Client for Postiz Public API v1.
    """
    def __init__(self, base_url: str = "", api_key: str = ""):
        # Default to the public API v1 endpoint
        self.base_url = (
            base_url
            or settings.postiz_base_url
            or "https://social.shamrockbailbonds.biz/api/public/v1"
        ).rstrip("/")
        # API key from config/settings or env
        self.api_key = api_key or settings.postiz_api_key or os.getenv("POSTIZ_API_KEY", "")
        self._client: Optional[httpx.AsyncClient] = None
        self._integrations_cache: dict = {}
        self._cache_ts: Optional[datetime] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Initialize or return the async HTTP client with correct auth headers."""
        if self._client is None or self._client.is_closed:
            # Note: No "Bearer" prefix for Postiz Public API v1 Auth Header!
            headers = {
                "Authorization": self.api_key,
                "Content-Type": "application/json",
            }
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                headers=headers,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Integrations ────────────────────────────────────────────────────────
    async def list_integrations(self, force_refresh: bool = False) -> list[dict]:
        """
        GET /public/v1/integrations
        Returns list of connected channels. Caches results for 1 minute.
        """
        now = datetime.now(timezone.utc)
        if (
            not force_refresh
            and self._cache_ts
            and (now - self._cache_ts).seconds < 60
            and self._integrations_cache
        ):
            return self._integrations_cache.get("integrations", [])

        client = self._get_client()
        try:
            resp = await client.get("/integrations")
            resp.raise_for_status()
            data = resp.json()
            # Handle potential variations in list vs dict responses
            integrations = data if isinstance(data, list) else data.get("integrations", data.get("data", []))
            self._integrations_cache = {"integrations": integrations}
            self._cache_ts = now
            return integrations
        except Exception as e:
            logger.error("❌ Postiz Public list_integrations failed: %s", e)
            return []

    async def get_all_connected_platforms(self) -> dict[str, dict]:
        """
        Get status of all connected platforms mapped to internal names.
        Returns: {platform_name: {id, name, picture, connected: True, provider}}
        
        The Postiz Public API returns `identifier` (e.g. "x", "facebook", "instagram").
        We map these to our internal names (e.g. "x" -> "twitter", "gmb" -> "gbp").
        """
        integrations = await self.list_integrations()
        result = {}
        # Build reverse map: postiz identifier -> our internal name
        provider_to_platform = {v: k for k, v in PLATFORM_TO_POSTIZ_PROVIDER.items()}
        # Also add direct mappings for identifiers that match (e.g. "facebook" -> "facebook")
        provider_to_platform["gmb"] = "gbp"  # Postiz uses "gmb", we use "gbp"
        for integration in integrations:
            # Postiz Public API v1 returns "identifier" not "providerIdentifier"
            identifier = (
                integration.get("identifier", "")
                or integration.get("providerIdentifier", "")
                or integration.get("provider", "")
            ).lower()
            platform_name = provider_to_platform.get(identifier, identifier)
            result[platform_name] = {
                "id": integration.get("id", ""),
                "name": integration.get("name", ""),
                "picture": integration.get("picture", ""),
                "connected": True,
                "provider": identifier,
            }
        return result

    async def get_integration_for_platform(self, platform: str) -> Optional[str]:
        """Find integration ID for a given platform name."""
        connected = await self.get_all_connected_platforms()
        return connected.get(platform, {}).get("id")

    # ── Posting ───────────────────────────────────────────────────────────
    async def create_post(
        self,
        post_type: str,  # "schedule" | "now" | "draft"
        platform_posts: list[dict],  # [{"integration": {"id": "..."}, "value": [{"content": "...", "image": []}], "settings": {"__type": "..."}}]
        schedule_date: Optional[datetime] = None,
    ) -> dict:
        """
        POST /public/v1/posts
        Creates or schedules a multi-platform post.
        """
        client = self._get_client()
        payload = {
            "type": post_type,
            "posts": platform_posts,
        }
        if post_type == "schedule" and schedule_date:
            payload["date"] = schedule_date.isoformat()

        try:
            resp = await client.post("/posts", json=payload)
            resp.raise_for_status()
            data = resp.json()
            logger.info("🚀 Postiz Public post created/scheduled successfully: %s", data)
            return data
        except httpx.HTTPStatusError as e:
            error_body = e.response.text if e.response else str(e)
            logger.error("❌ Postiz Public post failed (%d): %s", e.response.status_code, error_body)
            return {"error": error_body, "status_code": e.response.status_code}
        except Exception as e:
            logger.error("❌ Postiz Public post error: %s", e)
            return {"error": str(e)}

    # ── Media Upload ─────────────────────────────────────────────────────
    async def upload_media(self, file_path: str) -> Optional[str]:
        """
        POST /public/v1/upload (multipart form-data)
        Uploads a media file and returns the media ID.
        """
        client = self._get_client()
        try:
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f)}
                # Headers for multipart need to omit Content-Type so httpx sets boundary automatically
                headers = {"Authorization": self.api_key}
                resp = await client.post(
                    "/upload",
                    files=files,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                media_id = data.get("id", "")
                logger.info("📎 Media uploaded to Postiz Public: %s → %s", file_path, media_id)
                return media_id
        except Exception as e:
            logger.error("❌ Media upload to Postiz Public failed: %s", e)
            return None

    async def upload_media_from_url(self, url: str) -> Optional[str]:
        """
        POST /public/v1/upload-from-url
        Uploads a media file from a URL and returns the media ID.
        """
        client = self._get_client()
        try:
            resp = await client.post("/upload-from-url", json={"url": url})
            resp.raise_for_status()
            data = resp.json()
            media_id = data.get("id", "")
            logger.info("📎 Media URL uploaded to Postiz Public: %s → %s", url, media_id)
            return media_id
        except Exception as e:
            logger.error("❌ Media URL upload to Postiz Public failed: %s", e)
            return None

    # ── Fetch Posts ───────────────────────────────────────────────────────
    async def get_posts(self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> list[dict]:
        """
        GET /public/v1/posts
        Retrieves post history and status.
        """
        client = self._get_client()
        params = {}
        if start_date:
            params["startDate"] = start_date.isoformat()
        if end_date:
            params["endDate"] = end_date.isoformat()

        try:
            resp = await client.get("/posts", params=params)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("posts", data.get("data", []))
        except Exception as e:
            logger.error("❌ Postiz Public get_posts failed: %s", e)
            return []

    # ── Health Check ──────────────────────────────────────────────────────
    async def health_check(self) -> dict:
        """Check if Postiz Public API is reachable and authorized."""
        try:
            # We can use list_integrations as a lightweight check
            integrations = await self.list_integrations(force_refresh=True)
            return {
                "status": "healthy",
                "connected": True,
                "integrations_count": len(integrations),
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "connected": False,
                "error": str(e),
            }


# Singleton helper
_public_client: Optional[PostizPublicClient] = None


def get_public_postiz_client() -> PostizPublicClient:
    global _public_client
    if _public_client is None:
        _public_client = PostizPublicClient()
    return _public_client
