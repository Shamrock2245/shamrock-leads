"""
Blog Auto-Publish Scheduler
============================
Runs as a background task in the Quart dashboard, checking for
due blog posts every 6 hours and publishing them automatically.

Also provides a REST API for manual trigger and status checks.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("blog.scheduler")

POSTS_DIR = Path(__file__).parent / "posts"


class BlogScheduler:
    """Background cron job for auto-publishing blog posts."""

    def __init__(self, db=None):
        """
        Args:
            db: MongoDB database instance (for logging publish events).
                Can be None — publishing still works, just no audit logging.
        """
        self.db = db
        self._publisher = None  # Lazy-loaded to avoid import-time env errors

    def _get_publisher(self):
        """Lazy-load the publisher (only when WIX_BLOG_API_KEY is set)."""
        if self._publisher is None:
            api_key = os.getenv("WIX_BLOG_API_KEY", "")
            if not api_key:
                logger.warning("⚠️ WIX_BLOG_API_KEY not set — blog auto-publish disabled")
                return None

            from blog.publisher import WixBlogPublisher
            self._publisher = WixBlogPublisher(api_key=api_key)

        return self._publisher

    def run(self) -> dict:
        """
        Check for due posts and publish them.
        This is the method called by the dashboard cron loop.
        Returns a summary dict.
        """
        publisher = self._get_publisher()
        if publisher is None:
            return {"status": "disabled", "reason": "WIX_BLOG_API_KEY not configured"}

        # Count pending posts
        pending = publisher.get_pending_posts()
        if not pending:
            logger.info("📰 Blog scheduler: no pending posts")
            return {"status": "ok", "pending": 0, "published": 0}

        logger.info(f"📰 Blog scheduler: {len(pending)} pending post(s), checking publish dates...")

        # Publish all due posts
        results = publisher.publish_all_due(dry_run=False)

        published = [r for r in results if r.get("success") and not r.get("skipped")]
        skipped = [r for r in results if r.get("skipped")]
        failed = [r for r in results if not r.get("success") and not r.get("skipped")]

        # Log to MongoDB if available
        if self.db and published:
            self._log_publishes(published)

        summary = {
            "status": "ok",
            "pending": len(pending),
            "published": len(published),
            "skipped": len(skipped),
            "failed": len(failed),
            "details": published,
        }

        if published:
            logger.info(f"✅ Blog scheduler: published {len(published)} post(s)")
            for p in published:
                logger.info(f"   📰 {p.get('title', 'unknown')}")

        return summary

    def get_status(self) -> dict:
        """Get current blog pipeline status."""
        publisher = self._get_publisher()
        if publisher is None:
            return {
                "enabled": False,
                "reason": "WIX_BLOG_API_KEY not configured",
                "pending": 0,
                "posts": [],
            }

        pending = publisher.get_pending_posts()
        # Check for published posts
        published_dir = POSTS_DIR / "published"
        published_count = len(list(published_dir.glob("*.md"))) if published_dir.exists() else 0

        return {
            "enabled": True,
            "pending": len(pending),
            "published_total": published_count,
            "posts": pending,
        }

    def _log_publishes(self, published: list[dict]):
        """Log publish events to MongoDB."""
        try:
            collection = self.db["blog_publish_log"]
            for p in published:
                collection.insert_one({
                    "post_id": p.get("post_id"),
                    "title": p.get("title"),
                    "url": p.get("url"),
                    "published_at": p.get("published_at", datetime.now(timezone.utc).isoformat()),
                    "agent": "BlogScheduler",
                    "created_at": datetime.now(timezone.utc),
                })
        except Exception as e:
            logger.warning(f"Failed to log publish events: {e}")
