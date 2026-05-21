"""
Shamrock Social Engine — Data Models
======================================
Pydantic models for the social content pipeline.
All models map to MongoDB documents in the ShamrockBailDB database.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class PostStatus(str, Enum):
    """Lifecycle status of a queued social post."""
    PENDING = "pending"          # Generated, awaiting human approval
    APPROVED = "approved"        # Human-approved, scheduled for posting
    POSTED = "posted"            # Successfully posted to platform
    FAILED = "failed"            # Posting failed (retries exhausted)
    REJECTED = "rejected"        # Human-rejected
    EXPIRED = "expired"          # Auto-expired (not approved within N days)


class Platform(str, Enum):
    TWITTER = "twitter"
    LINKEDIN = "linkedin"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"


class SourceType(str, Enum):
    BLOG = "blog"                # From blog/posts/ markdown files
    ARREST_INTEL = "arrest_intel" # County arrest statistics (no PII)
    MANUAL = "manual"            # Manually created via API


class ContentTone(str, Enum):
    EDUCATIONAL = "educational"  # Authoritative, informative
    URGENT = "urgent"            # Empathetic, action-oriented
    PROFESSIONAL = "professional" # LinkedIn-style thought leadership
    CASUAL = "casual"            # Facebook/Instagram conversational


class ContentVariant(str, Enum):
    SINGLE = "single"            # Single post
    THREAD = "thread"            # Multi-part thread (Twitter)
    CAROUSEL = "carousel"        # Multi-image carousel (Instagram)
    ARTICLE = "article"          # Long-form article share (LinkedIn)


# ── Models ────────────────────────────────────────────────────────────────────

class MediaAsset(BaseModel):
    """An image or video attached to a social post."""
    url: str = ""                # Local path or CDN URL
    alt_text: str = ""
    media_type: str = "image"    # "image" | "video"
    width: int = 0
    height: int = 0
    generated: bool = False      # True if created by image_gen.py


class PostEngagement(BaseModel):
    """Engagement metrics pulled from the platform."""
    impressions: int = 0
    likes: int = 0
    shares: int = 0              # Retweets, reposts, reshares
    comments: int = 0
    clicks: int = 0
    saves: int = 0               # Instagram saves
    engagement_rate: float = 0.0 # (likes+shares+comments+clicks) / impressions
    last_updated: Optional[datetime] = None


class SocialPost(BaseModel):
    """A single queued social media post."""
    post_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_type: SourceType = SourceType.BLOG
    source_id: str = ""          # Blog post slug, arrest ObjectId, or "manual"
    source_title: str = ""       # Original blog title for reference

    # Platform + content
    platform: Platform = Platform.TWITTER
    content: str = ""            # Platform-specific formatted content
    thread_parts: list[str] = Field(default_factory=list)  # For Twitter threads
    hashtags: list[str] = Field(default_factory=list)
    media: list[MediaAsset] = Field(default_factory=list)
    link_url: str = ""           # URL to include (blog post URL, shamrockbailbonds.biz)
    cta: str = ""                # Call-to-action text

    # Content metadata
    variant: ContentVariant = ContentVariant.SINGLE
    tone: ContentTone = ContentTone.EDUCATIONAL
    tone_confidence: float = 0.0 # LLM confidence in tone match (0-1)
    compliance_disclaimer: str = ""

    # Lifecycle
    status: PostStatus = PostStatus.PENDING
    scheduled_for: Optional[datetime] = None
    posted_at: Optional[datetime] = None
    platform_post_id: Optional[str] = None  # ID from the platform after posting
    platform_url: Optional[str] = None      # Direct URL to the posted content

    # Engagement (backfilled by analytics cron)
    engagement: PostEngagement = Field(default_factory=PostEngagement)

    # Error handling
    error: Optional[str] = None
    retry_count: int = 0
    last_retry_at: Optional[datetime] = None

    # Approval
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejected_reason: Optional[str] = None

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_mongo(self) -> dict:
        """Convert to MongoDB document."""
        d = self.model_dump()
        d["updated_at"] = datetime.now(timezone.utc)
        return d

    @classmethod
    def from_mongo(cls, doc: dict) -> SocialPost:
        """Create from MongoDB document."""
        if "_id" in doc:
            doc.pop("_id")
        return cls(**doc)


class PostResult(BaseModel):
    """Result of attempting to post to a platform."""
    success: bool = False
    platform: Platform = Platform.TWITTER
    platform_post_id: Optional[str] = None
    platform_url: Optional[str] = None
    error: Optional[str] = None
    rate_limited: bool = False
    retry_after: Optional[int] = None  # Seconds to wait before retry


class IngestionResult(BaseModel):
    """Summary of a content ingestion run."""
    source_type: SourceType = SourceType.BLOG
    sources_scanned: int = 0
    posts_generated: int = 0
    posts_skipped: int = 0       # Already ingested (dedup)
    errors: int = 0
    details: list[dict] = Field(default_factory=list)


class QueueStats(BaseModel):
    """Aggregate statistics for the social queue."""
    total: int = 0
    pending: int = 0
    approved: int = 0
    posted: int = 0
    failed: int = 0
    rejected: int = 0
    expired: int = 0
    by_platform: dict[str, int] = Field(default_factory=dict)
