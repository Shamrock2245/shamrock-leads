"""
Shamrock Social Engine — Media Pipeline
==========================================
Central media orchestration that auto-generates images for every social post.

Budget Strategy ($25/month Grok API cap):
  - Blog reposts: Pillow branded cards (FREE) — templated, data-driven
  - Arrest intel: Pillow stat cards (FREE) — data-driven
  - Grok news posts: Grok Imagine (PAID ~$0.07/img) — needs creative imagery
  - Gmail harvested posts: Grok Imagine (PAID) — needs creative imagery
  - Budget gate: tracks monthly spend, falls back to Pillow at $25

Platform Sizing:
  - Twitter/X:   1200×675  (landscape)
  - Instagram:   1080×1080 (square)
  - Facebook:    1200×630  (landscape, OG-optimized)

One Grok image → resized/cropped via Pillow for each platform = 1 API call per content piece.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from social.models import SocialPost, SourceType, Platform, MediaAsset
from social.config import settings

logger = logging.getLogger("social.media_pipeline")

# ── Cost Constants ──────────────────────────────────────────────────────────────
GROK_IMAGE_COST_PER_CALL = 0.07  # USD per grok-2-image generation
MONTHLY_BUDGET_USD = 25.00
BUDGET_COLLECTION = "social_budget"

# ── Platform → Image Template Mapping ──────────────────────────────────────────
PLATFORM_TEMPLATES = {
    Platform.TWITTER:   "landscape",   # 1200×675
    Platform.FACEBOOK:  "landscape",   # 1200×675 (OG preview)
    Platform.INSTAGRAM: "square",      # 1080×1080
    Platform.LINKEDIN:  "landscape",   # 1200×675
}


class MediaPipeline:
    """
    Auto-generates platform-appropriate media for social posts.

    Decision tree:
      1. Post already has media? → Skip (respect manual attachments)
      2. Blog source? → Pillow branded card (free)
      3. Arrest intel? → Pillow stat card (free)
      4. Grok content / manual? → Try Grok Imagine (paid), fallback to Pillow
      5. Budget exceeded? → Always Pillow
      6. Instagram post with no image after all attempts? → Force Pillow card
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._image_gen = None
        self._grok_client = None

    def _get_image_gen(self):
        """Lazy-load Pillow image generator."""
        if self._image_gen is None:
            from social.image_gen import ImageGenerator
            self._image_gen = ImageGenerator()
        return self._image_gen

    def _get_grok(self):
        """Lazy-load Grok client."""
        if self._grok_client is None:
            from social.grok_client import GrokClient
            self._grok_client = GrokClient()
        return self._grok_client

    # ── Public API ─────────────────────────────────────────────────────────

    async def generate_media_for_post(self, post: SocialPost) -> SocialPost:
        """
        Auto-generate and attach media to a post if it doesn't have any.
        Mutates and returns the post with media attached.
        """
        # Already has media — respect it
        if post.media:
            logger.debug("Post %s already has %d media asset(s) — skipping", post.post_id[:8], len(post.media))
            return post

        template = PLATFORM_TEMPLATES.get(post.platform, "landscape")
        asset = None

        # ── Route by source type ──
        if post.source_type == SourceType.BLOG:
            asset = self._generate_blog_card(post, template)

        elif post.source_type == SourceType.ARREST_INTEL:
            asset = self._generate_stat_card(post, template)

        else:
            # Manual / Grok news / Gmail harvest → try AI image
            asset = await self._generate_ai_image(post, template)

        # ── Fallback: if Instagram and still no image, force a Pillow card ──
        if not asset and post.platform == Platform.INSTAGRAM:
            logger.info("📸 Instagram requires image — generating Pillow fallback")
            asset = self._generate_blog_card(post, "square")

        if asset:
            post.media = [asset]
            logger.info(
                "🖼️  Media attached to %s post: %s (%s)",
                post.platform.value, asset.alt_text[:40], "AI" if asset.generated else "template",
            )

        return post

    async def generate_media_batch(self, posts: list[SocialPost]) -> list[SocialPost]:
        """Generate media for a batch of posts (e.g., multi-platform variants)."""
        results = []
        for post in posts:
            enriched = await self.generate_media_for_post(post)
            results.append(enriched)
        return results

    # ── Pillow Generators (FREE) ───────────────────────────────────────────

    def _generate_blog_card(self, post: SocialPost, template: str) -> Optional[MediaAsset]:
        """Generate a branded blog card using Pillow (free)."""
        gen = self._get_image_gen()
        title = post.source_title or post.content[:60]
        subtitle = post.content[:120] if post.source_title else ""

        return gen.generate_blog_card(
            title=title,
            subtitle=subtitle,
            template=template,
        )

    def _generate_stat_card(self, post: SocialPost, template: str) -> Optional[MediaAsset]:
        """Generate a stat card using Pillow (free)."""
        gen = self._get_image_gen()

        # Extract a number from the content if possible
        import re
        numbers = re.findall(r"\d[\d,]*", post.content)
        stat_value = numbers[0] if numbers else "📊"
        stat_label = "arrests this week" if "arrest" in post.content.lower() else "SWFL Update"
        headline = post.source_title or "Weekly Intelligence"

        return gen.generate_stat_card(
            headline=headline,
            stat_value=stat_value,
            stat_label=stat_label,
            template=template,
        )

    # ── Grok Imagine (PAID — budget-gated) ─────────────────────────────────

    async def _generate_ai_image(self, post: SocialPost, template: str) -> Optional[MediaAsset]:
        """Generate an AI image via Grok Imagine, respecting monthly budget."""
        grok = self._get_grok()

        if not grok.is_configured:
            logger.debug("Grok not configured — falling back to Pillow")
            return self._generate_blog_card(post, template)

        # Check budget
        remaining = await self._get_remaining_budget()
        if remaining < GROK_IMAGE_COST_PER_CALL:
            logger.warning(
                "💰 Grok image budget exhausted ($%.2f remaining) — using Pillow fallback",
                remaining,
            )
            return self._generate_blog_card(post, template)

        # Generate image
        try:
            # Build a content-aware prompt
            prompt = self._build_image_prompt(post)

            asset = await grok.generate_image(
                prompt=prompt,
                filename=f"social_{post.post_id[:8]}_{template}.png",
            )

            if asset:
                # Track spend
                await self._record_spend(GROK_IMAGE_COST_PER_CALL, post.post_id)
                asset.generated = True
                return asset

        except Exception as e:
            logger.error("❌ Grok image generation failed: %s — falling back to Pillow", e)

        # Fallback to Pillow
        return self._generate_blog_card(post, template)

    def _build_image_prompt(self, post: SocialPost) -> str:
        """Analyze post content and create a descriptive image prompt."""
        content_snippet = post.content[:200].strip()

        # Base prompt with brand guidelines
        prompt = (
            f"A clean, modern social media graphic for Shamrock Bail Bonds, "
            f"a premium Florida bail bond agency. "
            f"Shamrock green (#1B6B3A) and gold (#D4AF37) color scheme on dark background. "
            f"Minimalist, professional design. No clip art. "
        )

        # Content-aware additions
        lower_content = post.content.lower()

        if any(w in lower_content for w in ["arrest", "booking", "jail", "custody"]):
            prompt += (
                "Theme: know-your-rights, legal education. "
                "Visual: abstract scales of justice or courthouse silhouette. "
            )
        elif any(w in lower_content for w in ["court", "hearing", "judge", "trial"]):
            prompt += (
                "Theme: court preparation. "
                "Visual: abstract gavel or courtroom columns. "
            )
        elif any(w in lower_content for w in ["family", "loved one", "help", "support"]):
            prompt += (
                "Theme: family support in difficult times. "
                "Visual: warm, supportive abstract imagery, subtle human connection. "
            )
        elif any(w in lower_content for w in ["payment", "plan", "affordable", "cost"]):
            prompt += (
                "Theme: affordable bail bonds. "
                "Visual: abstract financial imagery, shield or checkmark. "
            )
        elif any(w in lower_content for w in ["24/7", "available", "call", "contact"]):
            prompt += (
                "Theme: always available. "
                "Visual: clock or communication imagery, phone icon. "
            )
        else:
            prompt += (
                "Theme: professional bail bonds service. "
                "Visual: abstract legal/liberty imagery, Florida scenery elements. "
            )

        prompt += (
            f"The post is about: {content_snippet}... "
            f"Subtle shamrock/clover motif in corner. "
            f"Phone: 239-332-BAIL."
        )

        return prompt

    # ── Budget Tracking ────────────────────────────────────────────────────

    async def _get_remaining_budget(self) -> float:
        """Check remaining Grok API budget for the current month."""
        month_key = datetime.now(timezone.utc).strftime("%Y-%m")

        doc = await self.db[BUDGET_COLLECTION].find_one({"month": month_key})
        if not doc:
            return MONTHLY_BUDGET_USD

        spent = doc.get("total_spent", 0.0)
        return max(0.0, MONTHLY_BUDGET_USD - spent)

    async def _record_spend(self, amount: float, post_id: str = ""):
        """Record a Grok API spend in the budget tracker."""
        month_key = datetime.now(timezone.utc).strftime("%Y-%m")

        await self.db[BUDGET_COLLECTION].update_one(
            {"month": month_key},
            {
                "$inc": {"total_spent": amount, "image_count": 1},
                "$push": {
                    "history": {
                        "amount": amount,
                        "post_id": post_id,
                        "timestamp": datetime.now(timezone.utc),
                        "type": "grok_image",
                    }
                },
                "$setOnInsert": {"month": month_key, "budget": MONTHLY_BUDGET_USD},
            },
            upsert=True,
        )

    async def get_budget_status(self) -> dict:
        """Public API: get current month's budget status."""
        month_key = datetime.now(timezone.utc).strftime("%Y-%m")
        doc = await self.db[BUDGET_COLLECTION].find_one({"month": month_key})

        if not doc:
            return {
                "month": month_key,
                "budget": MONTHLY_BUDGET_USD,
                "spent": 0.0,
                "remaining": MONTHLY_BUDGET_USD,
                "image_count": 0,
                "percent_used": 0.0,
            }

        spent = doc.get("total_spent", 0.0)
        return {
            "month": month_key,
            "budget": MONTHLY_BUDGET_USD,
            "spent": round(spent, 2),
            "remaining": round(max(0, MONTHLY_BUDGET_USD - spent), 2),
            "image_count": doc.get("image_count", 0),
            "percent_used": round((spent / MONTHLY_BUDGET_USD) * 100, 1),
        }
