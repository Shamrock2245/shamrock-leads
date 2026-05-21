"""
Shamrock Social Engine — Queue Manager
========================================
MongoDB CRUD operations for the social_queue collection.
Handles lifecycle transitions: pending → approved → posted / failed / rejected / expired.

Dedup key: source_id + platform + variant — prevents double-posting.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from social.models import (
    SocialPost,
    PostStatus,
    Platform,
    SourceType,
    QueueStats,
)

logger = logging.getLogger("social.queue_manager")

COLLECTION = "social_queue"


class QueueManager:
    """Manages the social_queue MongoDB collection."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.col = db[COLLECTION]

    # ── Indexes ───────────────────────────────────────────────────────────────

    async def ensure_indexes(self):
        """Create MongoDB indexes for the social_queue collection."""
        await self.col.create_index(
            [("post_id", 1)],
            unique=True, name="idx_post_id_unique", background=True,
        )
        await self.col.create_index(
            [("source_id", 1), ("platform", 1), ("variant", 1)],
            unique=True, name="idx_dedup_source_platform_variant", background=True,
        )
        await self.col.create_index(
            [("status", 1), ("scheduled_for", 1)],
            name="idx_status_scheduled", background=True,
        )
        await self.col.create_index(
            [("status", 1), ("created_at", -1)],
            name="idx_status_created", background=True,
        )
        await self.col.create_index(
            [("platform", 1), ("status", 1)],
            name="idx_platform_status", background=True,
        )
        # TTL: auto-delete rejected/expired posts after 90 days
        await self.col.create_index(
            [("updated_at", 1)],
            name="idx_ttl_90d", background=True,
            expireAfterSeconds=7776000,
            partialFilterExpression={
                "status": {"$in": [PostStatus.REJECTED.value, PostStatus.EXPIRED.value]}
            },
        )
        logger.info("☘️  social_queue indexes ensured")

    # ── Create ────────────────────────────────────────────────────────────────

    async def enqueue(self, post: SocialPost) -> Optional[SocialPost]:
        """
        Add a post to the queue. Returns None if a duplicate exists
        (same source_id + platform + variant).
        """
        try:
            await self.col.insert_one(post.to_mongo())
            logger.info(
                "📝 Queued: [%s] %s → %s (%s)",
                post.platform.value, post.source_title[:40],
                post.variant.value, post.tone.value,
            )
            return post
        except Exception as e:
            if "duplicate key" in str(e).lower() or "E11000" in str(e):
                logger.debug(
                    "⏭️  Duplicate skipped: %s / %s / %s",
                    post.source_id, post.platform.value, post.variant.value,
                )
                return None
            logger.error("❌ Queue insert error: %s", e)
            raise

    async def enqueue_batch(self, posts: list[SocialPost]) -> dict:
        """Enqueue multiple posts, skipping duplicates."""
        inserted = 0
        skipped = 0
        errors = 0
        for post in posts:
            try:
                result = await self.enqueue(post)
                if result:
                    inserted += 1
                else:
                    skipped += 1
            except Exception:
                errors += 1
        return {"inserted": inserted, "skipped": skipped, "errors": errors}

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_post(self, post_id: str) -> Optional[SocialPost]:
        """Get a single post by post_id."""
        doc = await self.col.find_one({"post_id": post_id})
        return SocialPost.from_mongo(doc) if doc else None

    async def list_posts(
        self,
        status: Optional[PostStatus] = None,
        platform: Optional[Platform] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[SocialPost]:
        """List posts with optional filters."""
        query: dict = {}
        if status:
            query["status"] = status.value
        if platform:
            query["platform"] = platform.value

        cursor = (
            self.col.find(query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        return [SocialPost.from_mongo(doc) async for doc in cursor]

    async def get_due_posts(self) -> list[SocialPost]:
        """Get all approved posts whose scheduled_for is now or in the past."""
        now = datetime.now(timezone.utc)
        query = {
            "status": PostStatus.APPROVED.value,
            "$or": [
                {"scheduled_for": {"$lte": now}},
                {"scheduled_for": None},
            ],
        }
        cursor = self.col.find(query).sort("scheduled_for", 1)
        return [SocialPost.from_mongo(doc) async for doc in cursor]

    async def get_stats(self) -> QueueStats:
        """Aggregate queue statistics."""
        pipeline = [
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
        status_counts = {}
        async for doc in self.col.aggregate(pipeline):
            status_counts[doc["_id"]] = doc["count"]

        platform_pipeline = [
            {"$group": {"_id": "$platform", "count": {"$sum": 1}}},
        ]
        platform_counts = {}
        async for doc in self.col.aggregate(platform_pipeline):
            platform_counts[doc["_id"]] = doc["count"]

        total = sum(status_counts.values())
        return QueueStats(
            total=total,
            pending=status_counts.get(PostStatus.PENDING.value, 0),
            approved=status_counts.get(PostStatus.APPROVED.value, 0),
            posted=status_counts.get(PostStatus.POSTED.value, 0),
            failed=status_counts.get(PostStatus.FAILED.value, 0),
            rejected=status_counts.get(PostStatus.REJECTED.value, 0),
            expired=status_counts.get(PostStatus.EXPIRED.value, 0),
            by_platform=platform_counts,
        )

    # ── Update: Status Transitions ────────────────────────────────────────────

    async def approve(
        self,
        post_id: str,
        approved_by: str = "dashboard",
        scheduled_for: Optional[datetime] = None,
    ) -> Optional[SocialPost]:
        """Approve a pending post for posting."""
        now = datetime.now(timezone.utc)
        result = await self.col.find_one_and_update(
            {"post_id": post_id, "status": PostStatus.PENDING.value},
            {"$set": {
                "status": PostStatus.APPROVED.value,
                "approved_by": approved_by,
                "approved_at": now,
                "scheduled_for": scheduled_for or now,
                "updated_at": now,
            }},
            return_document=True,
        )
        if result:
            logger.info("✅ Approved: %s by %s", post_id[:8], approved_by)
            return SocialPost.from_mongo(result)
        return None

    async def reject(
        self,
        post_id: str,
        reason: str = "",
        rejected_by: str = "dashboard",
    ) -> Optional[SocialPost]:
        """Reject a pending post."""
        now = datetime.now(timezone.utc)
        result = await self.col.find_one_and_update(
            {"post_id": post_id, "status": PostStatus.PENDING.value},
            {"$set": {
                "status": PostStatus.REJECTED.value,
                "rejected_reason": reason,
                "approved_by": rejected_by,
                "updated_at": now,
            }},
            return_document=True,
        )
        if result:
            logger.info("❌ Rejected: %s — %s", post_id[:8], reason or "no reason")
            return SocialPost.from_mongo(result)
        return None

    async def mark_posted(
        self,
        post_id: str,
        platform_post_id: str,
        platform_url: str = "",
    ) -> Optional[SocialPost]:
        """Mark a post as successfully posted."""
        now = datetime.now(timezone.utc)
        result = await self.col.find_one_and_update(
            {"post_id": post_id, "status": PostStatus.APPROVED.value},
            {"$set": {
                "status": PostStatus.POSTED.value,
                "posted_at": now,
                "platform_post_id": platform_post_id,
                "platform_url": platform_url,
                "updated_at": now,
            }},
            return_document=True,
        )
        if result:
            logger.info("🚀 Posted: %s → %s", post_id[:8], platform_url or platform_post_id)
            return SocialPost.from_mongo(result)
        return None

    async def mark_failed(
        self,
        post_id: str,
        error: str,
        retry: bool = True,
    ) -> Optional[SocialPost]:
        """Mark a post as failed. If retry=True and retries remain, keep as approved."""
        now = datetime.now(timezone.utc)
        post = await self.get_post(post_id)
        if not post:
            return None

        if retry and post.retry_count < 3:
            # Keep as approved for retry
            result = await self.col.find_one_and_update(
                {"post_id": post_id},
                {"$set": {
                    "error": error,
                    "retry_count": post.retry_count + 1,
                    "last_retry_at": now,
                    "updated_at": now,
                }},
                return_document=True,
            )
            logger.warning(
                "⚠️  Post %s failed (attempt %d/3): %s — will retry",
                post_id[:8], post.retry_count + 1, error[:80],
            )
        else:
            # Max retries exhausted → permanent failure
            result = await self.col.find_one_and_update(
                {"post_id": post_id},
                {"$set": {
                    "status": PostStatus.FAILED.value,
                    "error": error,
                    "retry_count": post.retry_count + 1,
                    "last_retry_at": now,
                    "updated_at": now,
                }},
                return_document=True,
            )
            logger.error("💀 Post %s permanently failed: %s", post_id[:8], error[:80])

        return SocialPost.from_mongo(result) if result else None

    async def update_content(
        self,
        post_id: str,
        content: Optional[str] = None,
        hashtags: Optional[list[str]] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> Optional[SocialPost]:
        """Edit a pending post's content before approval."""
        updates: dict = {"updated_at": datetime.now(timezone.utc)}
        if content is not None:
            updates["content"] = content
        if hashtags is not None:
            updates["hashtags"] = hashtags
        if scheduled_for is not None:
            updates["scheduled_for"] = scheduled_for

        result = await self.col.find_one_and_update(
            {"post_id": post_id, "status": {"$in": [PostStatus.PENDING.value, PostStatus.APPROVED.value]}},
            {"$set": updates},
            return_document=True,
        )
        return SocialPost.from_mongo(result) if result else None

    async def update_engagement(
        self,
        post_id: str,
        engagement: dict,
    ) -> None:
        """Update engagement metrics for a posted item."""
        engagement["last_updated"] = datetime.now(timezone.utc)
        await self.col.update_one(
            {"post_id": post_id, "status": PostStatus.POSTED.value},
            {"$set": {"engagement": engagement, "updated_at": datetime.now(timezone.utc)}},
        )

    # ── Maintenance ───────────────────────────────────────────────────────────

    async def expire_stale_posts(self, max_age_days: int = 7) -> int:
        """Auto-expire pending posts older than max_age_days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        result = await self.col.update_many(
            {
                "status": PostStatus.PENDING.value,
                "created_at": {"$lt": cutoff},
            },
            {"$set": {
                "status": PostStatus.EXPIRED.value,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        if result.modified_count:
            logger.info("🕰️  Expired %d stale posts", result.modified_count)
        return result.modified_count
