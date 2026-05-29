"""
Shamrock Social Engine — Twitter/X Platform Adapter
=====================================================
Posts to Twitter/X using tweepy v2 API.

Supports:
  - Single tweets (280 char max)
  - Thread posting (numbered, reply chain)
  - Media upload (images)
  - Engagement metrics pull
  - Content validation

Rate limits (Essential tier):
  - Tweet creation: 200 per 15 min
  - Media upload: 615 per 15 min
"""

from __future__ import annotations

import logging
from typing import Optional

from social.config import settings
from social.models import PostResult, Platform
from social.platforms.base import PlatformAdapter

logger = logging.getLogger("social.platforms.twitter")


class TwitterAdapter(PlatformAdapter):
    """Twitter/X posting adapter via tweepy v2 API."""

    platform = Platform.TWITTER

    def __init__(self):
        self._client = None
        self._api = None  # v1.1 API (needed for media upload)
        self._oauth_token = None
        self._oauth_loaded = False

    async def _load_oauth_token(self):
        """Try to load a live OAuth token from MongoDB."""
        if self._oauth_loaded:
            return
        self._oauth_loaded = True
        try:
            from social.oauth_bridge import get_live_token
            self._oauth_token = await get_live_token("twitter")
        except Exception:
            self._oauth_token = None

    def _get_client(self):
        """Lazy-load tweepy Client (v2 API). Uses OAuth token if available."""
        if self._client is None:
            cfg = settings.twitter
            # Prefer OAuth token from MongoDB, fall back to static config
            bearer = cfg.bearer_token
            if self._oauth_token and self._oauth_token.get("access_token"):
                bearer = self._oauth_token["access_token"]
            if not bearer:
                return None
            if not cfg.enabled and not self._oauth_token:
                return None
            try:
                import tweepy
                self._client = tweepy.Client(
                    bearer_token=bearer,
                    consumer_key=cfg.api_key or None,
                    consumer_secret=cfg.api_secret or None,
                    access_token=cfg.access_token or None,
                    access_token_secret=cfg.access_secret or None,
                    wait_on_rate_limit=True,
                )
            except ImportError:
                logger.error("tweepy not installed — run: pip install tweepy")
                return None
        return self._client

    def _get_api_v1(self):
        """Lazy-load tweepy API (v1.1 — needed for media upload)."""
        if self._api is None:
            cfg = settings.twitter
            try:
                import tweepy
                auth = tweepy.OAuth1UserHandler(
                    cfg.api_key, cfg.api_secret,
                    cfg.access_token, cfg.access_secret,
                )
                self._api = tweepy.API(auth)
            except ImportError:
                return None
        return self._api

    def is_configured(self) -> bool:
        cfg = settings.twitter
        has_static = cfg.enabled and bool(cfg.bearer_token) and bool(cfg.api_key)
        has_oauth = bool(self._oauth_token and self._oauth_token.get("access_token"))
        return has_static or has_oauth

    # ── Post ──────────────────────────────────────────────────────────────

    async def post(
        self,
        content: str,
        media_paths: Optional[list[str]] = None,
        link_url: str = "",
    ) -> PostResult:
        """Post a single tweet."""
        await self._load_oauth_token()
        client = self._get_client()
        if not client:
            return PostResult(
                platform=self.platform,
                error="Twitter client not configured",
            )

        valid, err = self.validate_content(content)
        if not valid:
            return PostResult(platform=self.platform, error=err)

        try:
            import asyncio

            # Upload media if present
            media_ids = []
            if media_paths:
                media_ids = await self._upload_media(media_paths)

            kwargs = {"text": content}
            if media_ids:
                kwargs["media_ids"] = media_ids

            response = await asyncio.to_thread(
                client.create_tweet, **kwargs
            )

            tweet_id = str(response.data["id"])
            tweet_url = f"https://x.com/i/status/{tweet_id}"

            logger.info("🐦 Tweet posted: %s", tweet_url)
            return PostResult(
                success=True,
                platform=self.platform,
                platform_post_id=tweet_id,
                platform_url=tweet_url,
            )

        except Exception as e:
            error_str = str(e)
            rate_limited = "429" in error_str or "rate limit" in error_str.lower()
            logger.error("❌ Twitter post failed: %s", error_str[:200])
            return PostResult(
                platform=self.platform,
                error=error_str[:500],
                rate_limited=rate_limited,
                retry_after=900 if rate_limited else None,
            )

    async def post_thread(
        self,
        parts: list[str],
        media_paths: Optional[list[str]] = None,
    ) -> PostResult:
        """Post a Twitter thread (reply chain)."""
        client = self._get_client()
        if not client:
            return PostResult(platform=self.platform, error="Twitter client not configured")

        if not parts:
            return PostResult(platform=self.platform, error="Empty thread")

        try:
            import asyncio

            # Upload media for first tweet
            media_ids = []
            if media_paths:
                media_ids = await self._upload_media(media_paths)

            # Post first tweet
            first_kwargs = {"text": parts[0]}
            if media_ids:
                first_kwargs["media_ids"] = media_ids

            first_response = await asyncio.to_thread(
                client.create_tweet, **first_kwargs
            )
            first_id = str(first_response.data["id"])

            # Post replies
            reply_to = first_id
            for part in parts[1:]:
                resp = await asyncio.to_thread(
                    client.create_tweet,
                    text=part,
                    in_reply_to_tweet_id=reply_to,
                )
                reply_to = str(resp.data["id"])

            tweet_url = f"https://x.com/i/status/{first_id}"
            logger.info("🧵 Thread posted (%d tweets): %s", len(parts), tweet_url)

            return PostResult(
                success=True,
                platform=self.platform,
                platform_post_id=first_id,
                platform_url=tweet_url,
            )

        except Exception as e:
            error_str = str(e)
            rate_limited = "429" in error_str or "rate limit" in error_str.lower()
            logger.error("❌ Twitter thread failed: %s", error_str[:200])
            return PostResult(
                platform=self.platform,
                error=error_str[:500],
                rate_limited=rate_limited,
            )

    # ── Engagement ────────────────────────────────────────────────────────

    async def get_engagement(self, platform_post_id: str) -> dict:
        """Pull engagement metrics for a tweet."""
        client = self._get_client()
        if not client:
            return {}

        try:
            import asyncio
            response = await asyncio.to_thread(
                client.get_tweet,
                platform_post_id,
                tweet_fields=["public_metrics"],
            )
            metrics = response.data.get("public_metrics", {}) if response.data else {}
            return {
                "impressions": metrics.get("impression_count", 0),
                "likes": metrics.get("like_count", 0),
                "shares": metrics.get("retweet_count", 0),
                "comments": metrics.get("reply_count", 0),
                "clicks": 0,  # Not available in basic tier
            }
        except Exception as e:
            logger.warning("Failed to get tweet engagement: %s", e)
            return {}

    # ── Validation ────────────────────────────────────────────────────────

    def validate_content(self, content: str) -> tuple[bool, str]:
        """Validate tweet content."""
        if not content.strip():
            return False, "Content is empty"
        if len(content) > settings.twitter.max_tweet_length:
            return False, f"Content exceeds {settings.twitter.max_tweet_length} chars ({len(content)})"
        return True, ""

    # ── Delete ────────────────────────────────────────────────────────────

    async def delete_post(self, platform_post_id: str) -> bool:
        """Delete a tweet."""
        client = self._get_client()
        if not client:
            return False
        try:
            import asyncio
            await asyncio.to_thread(client.delete_tweet, platform_post_id)
            logger.info("🗑️  Tweet deleted: %s", platform_post_id)
            return True
        except Exception as e:
            logger.error("Failed to delete tweet: %s", e)
            return False

    # ── Media Upload ──────────────────────────────────────────────────────

    async def _upload_media(self, media_paths: list[str]) -> list[str]:
        """Upload images to Twitter (v1.1 API required)."""
        api = self._get_api_v1()
        if not api:
            logger.warning("v1.1 API not configured — skipping media upload")
            return []

        media_ids = []
        import asyncio
        for path in media_paths[:4]:  # Twitter max 4 images
            try:
                media = await asyncio.to_thread(api.media_upload, filename=path)
                media_ids.append(str(media.media_id))
                logger.info("📸 Media uploaded: %s", path)
            except Exception as e:
                logger.warning("Media upload failed for %s: %s", path, e)

        return media_ids
