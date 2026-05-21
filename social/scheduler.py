"""
Shamrock Social Engine — Background Scheduler
================================================
APScheduler-based background jobs for:
  1. Content ingestion scan (every 6 hours) — blog posts → social variants
  2. Posting cadence check (every 30 minutes) — post approved items due now
  3. Analytics pull (every 24 hours) — pull engagement metrics from platforms
  4. Stale post expiration (daily) — expire unapproved posts after 7 days
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from social.config import settings, get_enabled_platforms
from social.models import PostStatus, ContentVariant
from social.queue_manager import QueueManager
from social.ingestion import ContentIngester

logger = logging.getLogger("social.scheduler")


class SocialScheduler:
    """Manages all background scheduling for the social engine."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.queue = QueueManager(db)
        self.ingester = ContentIngester(db)
        self._running = False

    async def start(self):
        """Start all background loops."""
        self._running = True
        logger.info("☘️  Social scheduler starting...")
        await self.queue.ensure_indexes()

        # Launch all loops as concurrent tasks
        tasks = [
            asyncio.create_task(self._ingestion_loop(), name="social_ingestion"),
            asyncio.create_task(self._posting_loop(), name="social_posting"),
            asyncio.create_task(self._analytics_loop(), name="social_analytics"),
            asyncio.create_task(self._expiration_loop(), name="social_expiration"),
            asyncio.create_task(self._gmail_scan_loop(), name="social_gmail_scan"),
            asyncio.create_task(self._grok_news_loop(), name="social_grok_news"),
        ]
        logger.info("☘️  Social scheduler: %d background tasks launched", len(tasks))
        return tasks

    async def stop(self):
        """Signal all loops to stop."""
        self._running = False
        logger.info("☘️  Social scheduler stopping...")

    # ── Ingestion Loop ────────────────────────────────────────────────────

    async def _ingestion_loop(self):
        """Periodically scan for new content to repurpose."""
        await asyncio.sleep(30)  # Initial delay
        interval = settings.ingestion_interval_hours * 3600

        while self._running:
            try:
                result = await self.ingester.run_full_ingestion()
                blog = result.get("blog", {})
                intel = result.get("arrest_intel", {})

                total_generated = blog.get("posts_generated", 0) + intel.get("posts_generated", 0)
                if total_generated > 0:
                    logger.info(
                        "📰 Ingestion cycle: %d posts generated (blog=%d, intel=%d)",
                        total_generated,
                        blog.get("posts_generated", 0),
                        intel.get("posts_generated", 0),
                    )

                    # Send Slack notification for new content
                    await self._notify_slack_new_content(total_generated)

            except Exception as e:
                logger.error("❌ Ingestion loop error: %s", e)

            await asyncio.sleep(interval)

    # ── Posting Loop ──────────────────────────────────────────────────────

    async def _posting_loop(self):
        """Check for approved posts due for publishing."""
        await asyncio.sleep(60)  # Initial delay
        interval = settings.posting_check_interval_minutes * 60

        while self._running:
            try:
                due_posts = await self.queue.get_due_posts()
                if due_posts:
                    logger.info("📤 %d approved post(s) due for publishing", len(due_posts))
                    for post in due_posts:
                        await self._publish_post(post)
            except Exception as e:
                logger.error("❌ Posting loop error: %s", e)

            await asyncio.sleep(interval)

    async def _publish_post(self, post):
        """Post a single approved item to its platform."""
        from social.platforms.twitter import TwitterAdapter
        from social.platforms.linkedin import LinkedInAdapter
        from social.platforms.facebook import FacebookAdapter
        from social.platforms.instagram import InstagramAdapter

        adapters = {
            "twitter": TwitterAdapter(),
            "linkedin": LinkedInAdapter(),
            "facebook": FacebookAdapter(),
            "instagram": InstagramAdapter(),
        }

        adapter = adapters.get(post.platform.value)
        if not adapter:
            await self.queue.mark_failed(post.post_id, f"Unknown platform: {post.platform.value}", retry=False)
            return

        if not adapter.is_configured():
            await self.queue.mark_failed(
                post.post_id,
                f"{post.platform.value} not configured — check env vars",
                retry=False,
            )
            return

        # Use thread posting for threads
        if post.variant == ContentVariant.THREAD and post.thread_parts:
            media = [m.url for m in post.media] if post.media else None
            result = await adapter.post_thread(post.thread_parts, media)
        else:
            media = [m.url for m in post.media] if post.media else None
            result = await adapter.post(post.content, media, post.link_url)

        if result.success:
            await self.queue.mark_posted(
                post.post_id,
                platform_post_id=result.platform_post_id or "",
                platform_url=result.platform_url or "",
            )
            await self._notify_slack_posted(post, result)
        else:
            should_retry = not result.rate_limited or post.retry_count < settings.max_post_retries
            await self.queue.mark_failed(
                post.post_id,
                result.error or "Unknown error",
                retry=should_retry,
            )

    # ── Analytics Loop ────────────────────────────────────────────────────

    async def _analytics_loop(self):
        """Pull engagement metrics for recently posted items."""
        await asyncio.sleep(300)  # Wait 5 min before first run
        interval = settings.analytics_pull_interval_hours * 3600

        while self._running:
            try:
                from social.platforms.twitter import TwitterAdapter
                from social.platforms.linkedin import LinkedInAdapter

                adapters = {
                    "twitter": TwitterAdapter(),
                    "linkedin": LinkedInAdapter(),
                }

                # Get posts from last 7 days
                posted = await self.queue.list_posts(status=PostStatus.POSTED, limit=100)
                updated = 0

                for post in posted:
                    if not post.platform_post_id:
                        continue
                    adapter = adapters.get(post.platform.value)
                    if not adapter or not adapter.is_configured():
                        continue

                    try:
                        engagement = await adapter.get_engagement(post.platform_post_id)
                        if engagement:
                            await self.queue.update_engagement(post.post_id, engagement)
                            updated += 1
                    except Exception:
                        pass

                if updated:
                    logger.info("📊 Analytics: updated engagement for %d posts", updated)

            except Exception as e:
                logger.error("❌ Analytics loop error: %s", e)

            await asyncio.sleep(interval)

    # ── Expiration Loop ───────────────────────────────────────────────────

    async def _expiration_loop(self):
        """Expire stale pending posts."""
        await asyncio.sleep(600)  # Wait 10 min
        interval = 86400  # Daily

        while self._running:
            try:
                expired = await self.queue.expire_stale_posts(
                    max_age_days=settings.auto_expire_days
                )
                if expired:
                    logger.info("🕰️  Expired %d stale posts", expired)
            except Exception as e:
                logger.error("❌ Expiration loop error: %s", e)

            await asyncio.sleep(interval)

    # ── Slack Notifications ───────────────────────────────────────────────

    async def _notify_slack_new_content(self, count: int):
        """Notify Slack about new content ready for review."""
        webhook = settings.slack_webhook_social
        if not webhook:
            return

        try:
            import httpx
            payload = {
                "text": (
                    f"📝 *Social Engine:* {count} new post(s) ready for review\n"
                    f"Open the dashboard → Social tab to approve/reject."
                ),
            }
            async with httpx.AsyncClient() as client:
                await client.post(webhook, json=payload, timeout=10)
        except Exception as e:
            logger.warning("Slack notification failed: %s", e)

    async def _notify_slack_posted(self, post, result):
        """Notify Slack about a successful post."""
        webhook = settings.slack_webhook_social
        if not webhook:
            return

        try:
            import httpx
            payload = {
                "text": (
                    f"🚀 *Posted to {post.platform.value}:*\n"
                    f"> {post.content[:150]}{'...' if len(post.content) > 150 else ''}\n"
                    f"🔗 {result.platform_url or 'Link pending'}"
                ),
            }
            async with httpx.AsyncClient() as client:
                await client.post(webhook, json=payload, timeout=10)
        except Exception as e:
            logger.warning("Slack notification failed: %s", e)

    # ── Gmail Grok Scanner Loop ────────────────────────────────────────────

    async def _gmail_scan_loop(self):
        """
        Periodically scan admin@shamrockbailbonds.biz for Grok-authored
        social media posts and ingest them into the queue.
        """
        await asyncio.sleep(120)  # Wait 2 min before first run
        interval = settings.gmail_scan_interval_hours * 3600

        while self._running:
            try:
                from social.gmail_scanner import GmailGrokScanner

                scanner = GmailGrokScanner(self.db)

                if not scanner.is_configured:
                    logger.debug("[GmailScan] Gmail OAuth not configured — skipping")
                    await asyncio.sleep(interval)
                    continue

                result = await scanner.scan_and_ingest(
                    max_results=20,
                    humanize=settings.humanizer_enabled,
                )

                ingested = result.get("ingested", 0)
                if ingested > 0:
                    logger.info(
                        "📧 Gmail scan: %d Grok post(s) harvested from email",
                        ingested,
                    )
                    await self._notify_slack_grok_harvest(ingested, result.get("details", []))

            except Exception as e:
                logger.error("❌ Gmail scan loop error: %s", e)

            await asyncio.sleep(interval)

    # ── Grok News Generation Loop ──────────────────────────────────────────

    async def _grok_news_loop(self):
        """
        Periodically ask Grok to generate a timely, news-aware social post.
        Grok has live web access, so posts reference current events.
        """
        await asyncio.sleep(300)  # Wait 5 min before first run
        interval = settings.grok_news_interval_hours * 3600

        while self._running:
            try:
                if not settings.grok_enabled:
                    logger.debug("[GrokNews] Grok not enabled — skipping")
                    await asyncio.sleep(interval)
                    continue

                from social.grok_client import GrokClient
                from social.humanizer import ContentHumanizer
                from social.models import Platform

                grok = GrokClient()
                humanizer = ContentHumanizer()

                if not grok.is_configured:
                    logger.debug("[GrokNews] XAI_API_KEY not set — skipping")
                    await asyncio.sleep(interval)
                    continue

                # Generate a news-hook post for Twitter
                post = await grok.generate_news_hook_post(platform=Platform.TWITTER)

                if post:
                    # Humanize before queuing
                    if settings.humanizer_enabled:
                        post.content = await humanizer.humanize(
                            post.content,
                            platform="twitter",
                            max_length=280,
                        )

                    result = await self.queue.enqueue(post)
                    if result:
                        logger.info(
                            "🤖 Grok news post generated: %s",
                            post.content[:80],
                        )
                        await self._notify_slack_grok_news(post.content)

            except Exception as e:
                logger.error("❌ Grok news loop error: %s", e)

            await asyncio.sleep(interval)

    # ── Grok-Specific Slack Notifications ──────────────────────────────────

    async def _notify_slack_grok_harvest(self, count: int, details: list):
        """Notify Slack about Grok posts harvested from Gmail."""
        webhook = settings.slack_webhook_social
        if not webhook:
            return

        try:
            import httpx
            previews = "\n".join(
                f"> {d.get('content_preview', '')[:80]}" for d in details[:3]
            )
            payload = {
                "text": (
                    f"📧 *Social Engine — Grok Email Harvest*\n"
                    f"{count} new post(s) found in admin@shamrockbailbonds.biz\n"
                    f"{previews}\n"
                    f"Open dashboard → Social tab to review."
                ),
            }
            async with httpx.AsyncClient() as client:
                await client.post(webhook, json=payload, timeout=10)
        except Exception as e:
            logger.warning("Slack grok harvest notification failed: %s", e)

    async def _notify_slack_grok_news(self, content: str):
        """Notify Slack about a new Grok-generated news post."""
        webhook = settings.slack_webhook_social
        if not webhook:
            return

        try:
            import httpx
            payload = {
                "text": (
                    f"🤖 *Grok News Post Generated:*\n"
                    f"> {content[:200]}\n"
                    f"Pending approval in Social queue."
                ),
            }
            async with httpx.AsyncClient() as client:
                await client.post(webhook, json=payload, timeout=10)
        except Exception as e:
            logger.warning("Slack grok news notification failed: %s", e)
