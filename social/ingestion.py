"""
Shamrock Social Engine — Content Ingestion
=============================================
Pulls content from blog posts, arrest intelligence, and manual input,
then generates platform-specific social post variants via the repurposer.

Sources:
  1. Blog posts: Scan blog/posts/*.md for published posts
  2. Arrest intelligence: County-level stats from MongoDB (NO PII)
  3. Manual: Raw text via API

Dedup: Checks social_queue for existing source_id + platform + variant
before generating new variants.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from motor.motor_asyncio import AsyncIOMotorDatabase

from social.config import settings, get_enabled_platforms
from social.models import (
    SocialPost,
    PostStatus,
    Platform,
    SourceType,
    ContentTone,
    ContentVariant,
    IngestionResult,
)
from social.queue_manager import QueueManager
from social.repurposer import ContentRepurposer
from social.media_pipeline import MediaPipeline

logger = logging.getLogger("social.ingestion")


class ContentIngester:
    """Scans content sources and generates social post variants."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.queue = QueueManager(db)
        self.repurposer = ContentRepurposer()
        self.media = MediaPipeline(db)

    # ── Public API ─────────────────────────────────────────────────────────

    async def ingest_blogs(self) -> IngestionResult:
        """
        Scan blog/posts/ directory for markdown files.
        For each blog post, generate social variants for every enabled platform.
        Skip posts already ingested (dedup by slug + platform + variant).
        """
        result = IngestionResult(source_type=SourceType.BLOG)
        blog_dir = Path(settings.blog_posts_dir)

        if not blog_dir.exists():
            logger.warning("📁 Blog posts directory not found: %s", blog_dir)
            return result

        # Also check published/ subdirectory
        search_dirs = [blog_dir]
        published_dir = blog_dir / "published"
        if published_dir.exists():
            search_dirs.append(published_dir)

        md_files = []
        for d in search_dirs:
            md_files.extend(sorted(d.glob("*.md")))

        if not md_files:
            logger.info("📰 No blog posts found")
            return result

        enabled_platforms = get_enabled_platforms()
        if not enabled_platforms:
            logger.warning("⚠️  No social platforms enabled — skipping ingestion")
            return result

        for md_file in md_files:
            result.sources_scanned += 1
            try:
                frontmatter, body = self._parse_markdown(md_file)
                if not frontmatter or not body:
                    continue

                slug = frontmatter.get("slug", md_file.stem)
                title = frontmatter.get("title", md_file.stem.replace("_", " ").title())
                excerpt = frontmatter.get("excerpt", "")
                categories = frontmatter.get("categories", [])
                tags = frontmatter.get("tags", [])
                blog_url = f"https://www.shamrockbailbonds.biz/post/{slug}"

                # Generate variants for each enabled platform
                for platform_name in enabled_platforms:
                    platform = Platform(platform_name)
                    variants = await self.repurposer.repurpose_blog(
                        title=title,
                        body=body,
                        excerpt=excerpt,
                        categories=categories,
                        tags=tags,
                        platform=platform,
                        blog_url=blog_url,
                    )

                    for variant in variants:
                        variant.source_id = slug
                        variant.source_title = title
                        # Auto-generate media for this variant
                        try:
                            variant = await self.media.generate_media_for_post(variant)
                        except Exception as e:
                            logger.warning("⚠️  Media gen failed for %s/%s: %s", slug, variant.platform.value, e)
                        enqueued = await self.queue.enqueue(variant)
                        if enqueued:
                            result.posts_generated += 1
                        else:
                            result.posts_skipped += 1

                result.details.append({
                    "file": md_file.name,
                    "title": title,
                    "slug": slug,
                    "platforms": enabled_platforms,
                })

            except Exception as e:
                logger.error("❌ Failed to ingest %s: %s", md_file.name, e)
                result.errors += 1

        logger.info(
            "📰 Blog ingestion complete: scanned=%d generated=%d skipped=%d errors=%d",
            result.sources_scanned, result.posts_generated,
            result.posts_skipped, result.errors,
        )
        return result

    async def ingest_arrest_intel(self) -> IngestionResult:
        """
        Generate educational social content from aggregate arrest statistics.
        NO PII — only county-level stats (e.g., "X arrests in Lee County this week").
        """
        result = IngestionResult(source_type=SourceType.ARREST_INTEL)
        enabled_platforms = get_enabled_platforms()
        if not enabled_platforms:
            return result

        try:
            # Aggregate arrest stats by county from last 7 days
            from datetime import timedelta
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

            pipeline = [
                {"$match": {"scraped_at": {"$gte": week_ago}}},
                {"$group": {
                    "_id": "$county",
                    "total_arrests": {"$sum": 1},
                    "avg_bond": {"$avg": {"$toDouble": {"$ifNull": ["$bond_amount", 0]}}},
                    "hot_leads": {
                        "$sum": {"$cond": [{"$gte": [{"$toInt": {"$ifNull": ["$lead_score", 0]}}, 80]}, 1, 0]}
                    },
                }},
                {"$sort": {"total_arrests": -1}},
                {"$limit": 5},
            ]

            stats = []
            async for doc in self.db["arrests"].aggregate(pipeline):
                county = doc["_id"]
                if county:
                    stats.append({
                        "county": county,
                        "total": doc["total_arrests"],
                        "avg_bond": round(doc.get("avg_bond", 0), 2),
                    })

            if not stats:
                logger.info("📊 No recent arrest stats for social content")
                return result

            result.sources_scanned = len(stats)

            for platform_name in enabled_platforms:
                platform = Platform(platform_name)
                variants = await self.repurposer.repurpose_arrest_stats(
                    stats=stats,
                    platform=platform,
                )
                for variant in variants:
                    # Use a time-bucketed source_id for weekly dedup
                    week_key = datetime.now(timezone.utc).strftime("%Y-W%W")
                    variant.source_id = f"arrest_intel_{week_key}"
                    variant.source_title = f"Weekly Arrest Intelligence ({week_key})"
                    # Auto-generate stat card media
                    try:
                        variant = await self.media.generate_media_for_post(variant)
                    except Exception as e:
                        logger.warning("⚠️  Media gen failed for arrest intel/%s: %s", platform_name, e)
                    enqueued = await self.queue.enqueue(variant)
                    if enqueued:
                        result.posts_generated += 1
                    else:
                        result.posts_skipped += 1

        except Exception as e:
            logger.error("❌ Arrest intel ingestion error: %s", e)
            result.errors += 1

        return result

    async def ingest_manual(
        self,
        content: str,
        platform: Platform,
        title: str = "Manual Post",
        hashtags: Optional[list[str]] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> Optional[SocialPost]:
        """Create a manual social post (bypasses LLM repurposing)."""
        post = SocialPost(
            source_type=SourceType.MANUAL,
            source_id=f"manual_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            source_title=title,
            platform=platform,
            content=content,
            hashtags=hashtags or [],
            variant=ContentVariant.SINGLE,
            tone=ContentTone.CASUAL,
            status=PostStatus.PENDING,
            scheduled_for=scheduled_for,
            compliance_disclaimer=settings.compliance_disclaimer,
        )
        return await self.queue.enqueue(post)

    async def run_full_ingestion(self) -> dict:
        """Run all ingestion pipelines and return combined results."""
        blog_result = await self.ingest_blogs()
        intel_result = await self.ingest_arrest_intel()

        # Also expire stale pending posts
        expired = await self.queue.expire_stale_posts(
            max_age_days=settings.auto_expire_days
        )

        return {
            "blog": blog_result.model_dump(),
            "arrest_intel": intel_result.model_dump(),
            "expired_posts": expired,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Markdown Parsing ──────────────────────────────────────────────────

    def _parse_markdown(self, path: Path) -> tuple[Optional[dict], Optional[str]]:
        """Parse a markdown file with YAML frontmatter."""
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("Failed to read %s: %s", path, e)
            return None, None

        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
        if not fm_match:
            logger.warning("No frontmatter in %s", path.name)
            return {}, content

        try:
            frontmatter = yaml.safe_load(fm_match.group(1))
        except yaml.YAMLError as e:
            logger.error("Invalid YAML in %s: %s", path.name, e)
            return None, None

        body = fm_match.group(2).strip()
        return frontmatter, body
