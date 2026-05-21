"""
Shamrock Social Engine — LinkedIn Platform Adapter
====================================================
Posts to LinkedIn Company Page via REST API v2.

Supports:
  - Text posts with commentary
  - Article shares (with link preview)
  - Image posts (upload + create)
  - Engagement metrics (likes, comments, shares)
  - Content validation (3000 char limit)

Requires:
  - LinkedIn Developer App with "Community Management" product
  - Organization Admin access
  - Long-lived access token (or ROPC refresh flow)
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from social.config import settings
from social.models import PostResult, Platform
from social.platforms.base import PlatformAdapter

logger = logging.getLogger("social.platforms.linkedin")

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
LINKEDIN_REST_BASE = "https://api.linkedin.com/rest"


class LinkedInAdapter(PlatformAdapter):
    """LinkedIn Company Page posting adapter via REST API v2."""

    platform = Platform.LINKEDIN

    def __init__(self):
        self._headers = None

    def _get_headers(self) -> dict:
        """Build auth headers."""
        if self._headers is None:
            cfg = settings.linkedin
            self._headers = {
                "Authorization": f"Bearer {cfg.access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
                "LinkedIn-Version": "202401",
            }
        return self._headers

    def is_configured(self) -> bool:
        cfg = settings.linkedin
        return cfg.enabled and bool(cfg.access_token) and bool(cfg.organization_urn)

    # ── Post ──────────────────────────────────────────────────────────────

    async def post(
        self,
        content: str,
        media_paths: Optional[list[str]] = None,
        link_url: str = "",
    ) -> PostResult:
        """Post to LinkedIn Company Page."""
        if not self.is_configured():
            return PostResult(platform=self.platform, error="LinkedIn not configured")

        valid, err = self.validate_content(content)
        if not valid:
            return PostResult(platform=self.platform, error=err)

        cfg = settings.linkedin

        try:
            # Build UGC post payload (v2 API)
            payload = {
                "author": cfg.organization_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {
                            "text": content,
                        },
                        "shareMediaCategory": "NONE",
                    },
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
                },
            }

            # Add link if provided
            if link_url:
                payload["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "ARTICLE"
                payload["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [{
                    "status": "READY",
                    "originalUrl": link_url,
                }]

            # Upload image if provided
            if media_paths and not link_url:
                image_urn = await self._upload_image(media_paths[0])
                if image_urn:
                    payload["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "IMAGE"
                    payload["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [{
                        "status": "READY",
                        "media": image_urn,
                    }]

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{LINKEDIN_API_BASE}/ugcPosts",
                    headers=self._get_headers(),
                    json=payload,
                )

                if resp.status_code in (200, 201):
                    post_id = resp.headers.get("x-restli-id", "")
                    # Build public URL (approximate — LinkedIn doesn't return it directly)
                    urn_id = post_id.split(":")[-1] if post_id else ""
                    post_url = f"https://www.linkedin.com/feed/update/{post_id}" if post_id else ""

                    logger.info("💼 LinkedIn post created: %s", post_url or post_id)
                    return PostResult(
                        success=True,
                        platform=self.platform,
                        platform_post_id=post_id,
                        platform_url=post_url,
                    )
                else:
                    error = resp.text[:500]
                    rate_limited = resp.status_code == 429
                    logger.error("❌ LinkedIn post failed (%d): %s", resp.status_code, error)
                    return PostResult(
                        platform=self.platform,
                        error=error,
                        rate_limited=rate_limited,
                        retry_after=int(resp.headers.get("Retry-After", 60)) if rate_limited else None,
                    )

        except Exception as e:
            logger.error("❌ LinkedIn post error: %s", e)
            return PostResult(platform=self.platform, error=str(e)[:500])

    async def post_thread(
        self,
        parts: list[str],
        media_paths: Optional[list[str]] = None,
    ) -> PostResult:
        """LinkedIn doesn't support threads — post as a single long post."""
        combined = "\n\n".join(parts)
        return await self.post(combined, media_paths)

    # ── Engagement ────────────────────────────────────────────────────────

    async def get_engagement(self, platform_post_id: str) -> dict:
        """Pull engagement metrics for a LinkedIn post."""
        if not self.is_configured():
            return {}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Get social actions (likes, comments)
                resp = await client.get(
                    f"{LINKEDIN_API_BASE}/socialActions/{platform_post_id}",
                    headers=self._get_headers(),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "likes": data.get("likesSummary", {}).get("totalLikes", 0),
                        "comments": data.get("commentsSummary", {}).get("totalFirstLevelComments", 0),
                        "shares": 0,  # Requires separate endpoint
                        "impressions": 0,  # Requires analytics API
                        "clicks": 0,
                    }
        except Exception as e:
            logger.warning("Failed to get LinkedIn engagement: %s", e)

        return {}

    # ── Validation ────────────────────────────────────────────────────────

    def validate_content(self, content: str) -> tuple[bool, str]:
        """Validate LinkedIn post content."""
        if not content.strip():
            return False, "Content is empty"
        if len(content) > settings.linkedin.max_post_length:
            return False, f"Content exceeds {settings.linkedin.max_post_length} chars ({len(content)})"
        return True, ""

    # ── Delete ────────────────────────────────────────────────────────────

    async def delete_post(self, platform_post_id: str) -> bool:
        """Delete a LinkedIn post."""
        if not self.is_configured():
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.delete(
                    f"{LINKEDIN_API_BASE}/ugcPosts/{platform_post_id}",
                    headers=self._get_headers(),
                )
                if resp.status_code in (200, 204):
                    logger.info("🗑️  LinkedIn post deleted: %s", platform_post_id)
                    return True
                logger.warning("LinkedIn delete failed (%d)", resp.status_code)
                return False
        except Exception as e:
            logger.error("LinkedIn delete error: %s", e)
            return False

    # ── Image Upload ──────────────────────────────────────────────────────

    async def _upload_image(self, image_path: str) -> Optional[str]:
        """Upload an image to LinkedIn and return the media URN."""
        cfg = settings.linkedin

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # Step 1: Register upload
                register_payload = {
                    "registerUploadRequest": {
                        "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                        "owner": cfg.organization_urn,
                        "serviceRelationships": [{
                            "relationshipType": "OWNER",
                            "identifier": "urn:li:userGeneratedContent",
                        }],
                    },
                }

                reg_resp = await client.post(
                    f"{LINKEDIN_API_BASE}/assets?action=registerUpload",
                    headers=self._get_headers(),
                    json=register_payload,
                )

                if reg_resp.status_code != 200:
                    logger.error("LinkedIn image register failed: %s", reg_resp.text[:200])
                    return None

                reg_data = reg_resp.json()
                upload_url = reg_data["value"]["uploadMechanism"][
                    "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
                ]["uploadUrl"]
                asset_urn = reg_data["value"]["asset"]

                # Step 2: Upload binary
                with open(image_path, "rb") as f:
                    upload_resp = await client.put(
                        upload_url,
                        content=f.read(),
                        headers={
                            "Authorization": f"Bearer {cfg.access_token}",
                            "Content-Type": "image/png",
                        },
                    )

                if upload_resp.status_code in (200, 201):
                    logger.info("📸 LinkedIn image uploaded: %s", asset_urn)
                    return asset_urn
                else:
                    logger.error("LinkedIn image upload failed: %d", upload_resp.status_code)
                    return None

        except Exception as e:
            logger.error("LinkedIn image upload error: %s", e)
            return None
