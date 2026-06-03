"""
Shamrock Social Engine — FastAPI Application
==============================================
Lightweight microservice for social media content repurposing and posting.

Runs as a separate Docker container (shamrock-social) on port 5060 (internal).
Proxied through the main dashboard API for authentication.

Endpoints:
  GET   /health                          — Health check
  POST  /api/social/ingest               — Trigger content ingestion
  GET   /api/social/queue                — List queued posts
  GET   /api/social/queue/stats          — Queue statistics
  GET   /api/social/post/{post_id}       — Get single post details
  POST  /api/social/approve/{post_id}    — Approve a pending post
  POST  /api/social/approve/batch        — Batch approve posts
  POST  /api/social/reject/{post_id}     — Reject a pending post
  POST  /api/social/edit/{post_id}       — Edit content before approval
  POST  /api/social/post/{post_id}       — Force-post an approved item now
  POST  /api/social/post/batch           — Post all approved items due now
  POST  /api/social/manual               — Create a manual post
  GET   /api/social/analytics            — Engagement stats
  GET   /api/social/platforms            — Enabled platforms status
"""

from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

# Load .env from project root
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_project_root, ".env"))

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient

from social.config import settings, get_enabled_platforms
from social.models import PostStatus, Platform
from social.queue_manager import QueueManager
from social.ingestion import ContentIngester
from social.scheduler import SocialScheduler

logger = logging.getLogger("social")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# ── MongoDB + Scheduler singletons ─────────────────────────────────────────────
_motor_client: Optional[AsyncIOMotorClient] = None
_scheduler: Optional[SocialScheduler] = None
_scheduler_tasks = []


def get_db():
    """Get the MongoDB database instance."""
    global _motor_client
    if _motor_client is None:
        _motor_client = AsyncIOMotorClient(settings.mongodb_uri)
    return _motor_client[settings.mongodb_db_name]


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect to MongoDB, start scheduler. Shutdown: cleanup."""
    global _scheduler, _scheduler_tasks

    logger.info("☘️  Social Engine starting...")
    db = get_db()

    # Start background scheduler
    _scheduler = SocialScheduler(db)
    _scheduler_tasks = await _scheduler.start()

    platforms = get_enabled_platforms()
    logger.info(
        "☘️  Social Engine ready — %d platform(s) enabled: %s — %d background tasks",
        len(platforms), ", ".join(platforms) or "none",
        len(_scheduler_tasks),
    )

    yield

    # Shutdown
    if _scheduler:
        await _scheduler.stop()
    for t in _scheduler_tasks:
        t.cancel()
    if _motor_client:
        _motor_client.close()
    logger.info("☘️  Social Engine stopped")


# ── App Instance ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Shamrock Social Engine",
    description="Social media content repurposing & multi-platform posting",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
)


# ── Health Check ───────────────────────────────────────────────────────────────

@app.get("/health", tags=["infra"])
async def health():
    """Health check — verifies MongoDB connectivity."""
    try:
        db = get_db()
        count = await db["social_queue"].estimated_document_count()
        return {
            "status": "ok",
            "engine": "social",
            "queue_size": count,
            "platforms_enabled": get_enabled_platforms(),
        }
    except Exception as e:
        return JSONResponse(
            {"status": "degraded", "engine": "social", "error": str(e)},
            status_code=503,
        )


# ── Platforms ──────────────────────────────────────────────────────────────────

@app.get("/api/social/platforms", tags=["config"])
async def list_platforms():
    """List all platforms and their connection status via Postiz."""
    from social.platforms.postiz import get_postiz_client

    client = get_postiz_client()
    all_platforms = [
        "twitter", "linkedin", "facebook", "instagram",
        "threads", "tiktok", "youtube", "reddit",
        "telegram", "bluesky", "mastodon", "pinterest", "gbp",
    ]

    try:
        connected = await client.get_all_connected_platforms()
        platforms = {}
        for p in all_platforms:
            if p in connected:
                platforms[p] = {
                    "enabled": True,
                    "name": connected[p].get("name", p),
                    "picture": connected[p].get("picture", ""),
                    "provider": connected[p].get("provider", p),
                }
            else:
                platforms[p] = {"enabled": False, "name": p}

        return {
            "platforms": platforms,
            "postiz_connected": client.api_key != "",
            "enabled": list(connected.keys()),
        }
    except Exception as e:
        # Fallback: Postiz not reachable
        return {
            "platforms": {p: {"enabled": False, "name": p} for p in all_platforms},
            "postiz_connected": False,
            "error": str(e),
            "enabled": [],
        }


# ── Ingestion ──────────────────────────────────────────────────────────────────

@app.post("/api/social/ingest", tags=["ingestion"])
async def trigger_ingestion(request: Request):
    """Trigger content ingestion pipeline."""
    db = get_db()
    ingester = ContentIngester(db)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    source_type = body.get("source_type", "all")

    if source_type == "blog":
        result = await ingester.ingest_blogs()
        return {"success": True, "result": result.model_dump()}
    elif source_type == "arrest_intel":
        result = await ingester.ingest_arrest_intel()
        return {"success": True, "result": result.model_dump()}
    else:
        result = await ingester.run_full_ingestion()
        return {"success": True, "result": result}


# ── Queue ──────────────────────────────────────────────────────────────────────

@app.get("/api/social/queue", tags=["queue"])
async def list_queue(
    status: Optional[str] = Query(None, description="Filter by status"),
    platform: Optional[str] = Query(None, description="Filter by platform"),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
):
    """List queued social posts."""
    db = get_db()
    queue = QueueManager(db)

    status_filter = PostStatus(status) if status else None
    platform_filter = Platform(platform) if platform else None

    posts = await queue.list_posts(
        status=status_filter,
        platform=platform_filter,
        limit=limit,
        skip=skip,
    )

    return {
        "success": True,
        "count": len(posts),
        "posts": [p.model_dump() for p in posts],
    }


@app.get("/api/social/queue/stats", tags=["queue"])
async def queue_stats():
    """Get aggregate queue statistics."""
    db = get_db()
    queue = QueueManager(db)
    stats = await queue.get_stats()
    return {"success": True, "stats": stats.model_dump()}


@app.get("/api/social/post/{post_id}", tags=["queue"])
async def get_post(post_id: str):
    """Get a single post by ID."""
    db = get_db()
    queue = QueueManager(db)
    post = await queue.get_post(post_id)
    if not post:
        return JSONResponse({"success": False, "error": "Post not found"}, status_code=404)
    return {"success": True, "post": post.model_dump()}


# ── Approval Flow ──────────────────────────────────────────────────────────────

@app.post("/api/social/approve/{post_id}", tags=["approval"])
async def approve_post(post_id: str, request: Request):
    """Approve a pending post for publication."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    db = get_db()
    queue = QueueManager(db)

    scheduled_str = body.get("scheduled_for")
    scheduled_for = None
    if scheduled_str:
        try:
            scheduled_for = datetime.fromisoformat(scheduled_str)
        except ValueError:
            pass

    post = await queue.approve(
        post_id,
        approved_by=body.get("approved_by", "dashboard"),
        scheduled_for=scheduled_for,
    )

    if not post:
        return JSONResponse(
            {"success": False, "error": "Post not found or not in pending status"},
            status_code=404,
        )

    return {"success": True, "post": post.model_dump()}


@app.post("/api/social/approve/batch", tags=["approval"])
async def batch_approve(request: Request):
    """Approve all pending posts (or filtered by platform)."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    db = get_db()
    queue = QueueManager(db)

    platform_filter = None
    if body.get("platform"):
        platform_filter = Platform(body["platform"])

    pending = await queue.list_posts(status=PostStatus.PENDING, platform=platform_filter, limit=200)

    approved = 0
    for post in pending:
        result = await queue.approve(post.post_id, approved_by=body.get("approved_by", "dashboard"))
        if result:
            approved += 1

    return {"success": True, "approved": approved, "total_pending": len(pending)}


@app.post("/api/social/reject/{post_id}", tags=["approval"])
async def reject_post(post_id: str, request: Request):
    """Reject a pending post."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    db = get_db()
    queue = QueueManager(db)
    post = await queue.reject(
        post_id,
        reason=body.get("reason", ""),
        rejected_by=body.get("rejected_by", "dashboard"),
    )

    if not post:
        return JSONResponse(
            {"success": False, "error": "Post not found or not in pending status"},
            status_code=404,
        )

    return {"success": True, "post": post.model_dump()}


@app.post("/api/social/edit/{post_id}", tags=["approval"])
async def edit_post(post_id: str, request: Request):
    """Edit a pending/approved post's content."""
    body = await request.json()
    db = get_db()
    queue = QueueManager(db)

    scheduled_str = body.get("scheduled_for")
    scheduled_for = None
    if scheduled_str:
        try:
            scheduled_for = datetime.fromisoformat(scheduled_str)
        except ValueError:
            pass

    post = await queue.update_content(
        post_id,
        content=body.get("content"),
        hashtags=body.get("hashtags"),
        scheduled_for=scheduled_for,
    )

    if not post:
        return JSONResponse(
            {"success": False, "error": "Post not found or already posted/failed"},
            status_code=404,
        )

    return {"success": True, "post": post.model_dump()}


# ── Posting ────────────────────────────────────────────────────────────────────

@app.post("/api/social/publish/{post_id}", tags=["posting"])
async def force_publish(post_id: str):
    """Force-publish an approved post immediately."""
    db = get_db()
    queue = QueueManager(db)
    post = await queue.get_post(post_id)

    if not post:
        return JSONResponse({"success": False, "error": "Post not found"}, status_code=404)
    if post.status != PostStatus.APPROVED:
        return JSONResponse({"success": False, "error": f"Post status is {post.status.value}, not approved"}, status_code=400)

    if _scheduler:
        await _scheduler._publish_post(post)
        # Re-fetch to get updated status
        updated = await queue.get_post(post_id)
        return {"success": True, "post": updated.model_dump() if updated else {}}

    return JSONResponse({"success": False, "error": "Scheduler not running"}, status_code=500)


@app.post("/api/social/publish/batch", tags=["posting"])
async def batch_publish():
    """Publish all approved posts that are due now."""
    db = get_db()
    queue = QueueManager(db)
    due = await queue.get_due_posts()

    published = 0
    failed = 0
    for post in due:
        if _scheduler:
            await _scheduler._publish_post(post)
            updated = await queue.get_post(post.post_id)
            if updated and updated.status == PostStatus.POSTED:
                published += 1
            else:
                failed += 1

    return {
        "success": True,
        "due": len(due),
        "published": published,
        "failed": failed,
    }


# ── Manual Post ────────────────────────────────────────────────────────────────

@app.post("/api/social/manual", tags=["ingestion"])
async def create_manual(request: Request):
    """Create a manual social post (bypasses LLM repurposing)."""
    body = await request.json()

    content = body.get("content", "")
    if not content:
        return JSONResponse({"success": False, "error": "Content is required"}, status_code=400)

    platform_str = body.get("platform", "twitter")
    try:
        platform = Platform(platform_str)
    except ValueError:
        return JSONResponse({"success": False, "error": f"Invalid platform: {platform_str}"}, status_code=400)

    db = get_db()
    ingester = ContentIngester(db)

    scheduled_str = body.get("scheduled_for")
    scheduled_for = None
    if scheduled_str:
        try:
            scheduled_for = datetime.fromisoformat(scheduled_str)
        except ValueError:
            pass

    post = await ingester.ingest_manual(
        content=content,
        platform=platform,
        title=body.get("title", "Manual Post"),
        hashtags=body.get("hashtags"),
        scheduled_for=scheduled_for,
    )

    if post:
        return {"success": True, "post": post.model_dump()}
    return JSONResponse({"success": False, "error": "Duplicate content"}, status_code=409)


# ── Analytics ──────────────────────────────────────────────────────────────────

@app.get("/api/social/analytics", tags=["analytics"])
async def analytics(
    platform: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
):
    """Engagement analytics for posted content."""
    from datetime import timedelta

    db = get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = {
        "status": PostStatus.POSTED.value,
        "posted_at": {"$gte": cutoff},
    }
    if platform:
        query["platform"] = platform

    cursor = db["social_queue"].find(query).sort("posted_at", -1).limit(100)

    posts = []
    total_impressions = 0
    total_likes = 0
    total_shares = 0
    total_comments = 0

    async for doc in cursor:
        eng = doc.get("engagement", {})
        total_impressions += eng.get("impressions", 0)
        total_likes += eng.get("likes", 0)
        total_shares += eng.get("shares", 0)
        total_comments += eng.get("comments", 0)

        posts.append({
            "post_id": doc.get("post_id"),
            "platform": doc.get("platform"),
            "content": doc.get("content", "")[:100],
            "posted_at": doc.get("posted_at"),
            "engagement": eng,
            "tone": doc.get("tone"),
            "variant": doc.get("variant"),
        })

    return {
        "success": True,
        "period_days": days,
        "total_posts": len(posts),
        "totals": {
            "impressions": total_impressions,
            "likes": total_likes,
            "shares": total_shares,
            "comments": total_comments,
        },
        "posts": posts,
    }


# ── Grok / xAI ─────────────────────────────────────────────────────────────────

@app.post("/api/social/grok/generate", tags=["grok"])
async def grok_generate(request: Request):
    """Generate a social post using Grok (with optional live news hook)."""
    body = await request.json()

    topic = body.get("topic", "")
    if not topic:
        return JSONResponse({"success": False, "error": "topic is required"}, status_code=400)

    platform_str = body.get("platform", "twitter")
    try:
        platform = Platform(platform_str)
    except ValueError:
        return JSONResponse({"success": False, "error": f"Invalid platform: {platform_str}"}, status_code=400)

    from social.grok_client import GrokClient
    from social.humanizer import ContentHumanizer
    from social.models import ContentTone

    grok = GrokClient()
    if not grok.is_configured:
        return JSONResponse({"success": False, "error": "XAI_API_KEY not configured"}, status_code=503)

    tone_str = body.get("tone", "casual")
    try:
        tone = ContentTone(tone_str)
    except ValueError:
        tone = ContentTone.CASUAL

    post = await grok.generate_post(
        topic=topic,
        platform=platform,
        tone=tone,
        include_news=body.get("include_news", True),
        max_length=body.get("max_length"),
    )

    if not post:
        return JSONResponse({"success": False, "error": "Grok generation failed"}, status_code=500)

    # Humanize
    if settings.humanizer_enabled:
        humanizer = ContentHumanizer()
        post.content = await humanizer.humanize(
            post.content,
            platform=platform.value,
            max_length=280 if platform == Platform.TWITTER else None,
        )

    # Enqueue
    db = get_db()
    queue = QueueManager(db)
    result = await queue.enqueue(post)

    if result:
        return {"success": True, "post": post.model_dump()}
    return JSONResponse({"success": False, "error": "Duplicate or enqueue failed"}, status_code=409)


@app.post("/api/social/grok/news", tags=["grok"])
async def grok_news():
    """Ask Grok what's happening NOW and generate a timely post."""
    from social.grok_client import GrokClient
    from social.humanizer import ContentHumanizer

    grok = GrokClient()
    if not grok.is_configured:
        return JSONResponse({"success": False, "error": "XAI_API_KEY not configured"}, status_code=503)

    post = await grok.generate_news_hook_post(platform=Platform.TWITTER)
    if not post:
        return JSONResponse({"success": False, "error": "Grok news generation failed"}, status_code=500)

    if settings.humanizer_enabled:
        humanizer = ContentHumanizer()
        post.content = await humanizer.humanize(post.content, platform="twitter", max_length=280)

    db = get_db()
    queue = QueueManager(db)
    await queue.enqueue(post)

    return {"success": True, "post": post.model_dump()}


@app.post("/api/social/grok/image", tags=["grok"])
async def grok_image(request: Request):
    """Generate a branded social media image using Grok Imagine."""
    body = await request.json()
    headline = body.get("headline", "")
    if not headline:
        return JSONResponse({"success": False, "error": "headline is required"}, status_code=400)

    platform_str = body.get("platform", "twitter")
    try:
        platform = Platform(platform_str)
    except ValueError:
        platform = Platform.TWITTER

    from social.grok_client import GrokClient
    grok = GrokClient()
    if not grok.is_configured:
        return JSONResponse({"success": False, "error": "XAI_API_KEY not configured"}, status_code=503)

    asset = await grok.generate_social_card(headline=headline, platform=platform)
    if not asset:
        return JSONResponse({"success": False, "error": "Image generation failed"}, status_code=500)

    return {"success": True, "asset": asset.model_dump()}


# ── Gmail Scanner (Grok Post Harvesting) ───────────────────────────────────────

@app.post("/api/social/gmail/scan", tags=["gmail"])
async def gmail_scan(request: Request):
    """Scan Gmail for new Grok-authored posts and ingest them."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    from social.gmail_scanner import GmailGrokScanner
    db = get_db()
    scanner = GmailGrokScanner(db)

    if not scanner.is_configured:
        return JSONResponse({"success": False, "error": "Gmail OAuth not configured"}, status_code=503)

    result = await scanner.scan_and_ingest(
        max_results=body.get("max_results", 50),
        humanize=body.get("humanize", settings.humanizer_enabled),
    )
    return {"success": True, **result}


@app.post("/api/social/gmail/backlog", tags=["gmail"])
async def gmail_backlog(request: Request):
    """One-time backlog harvest — pull ALL historical Grok emails."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    from social.gmail_scanner import GmailGrokScanner
    db = get_db()
    scanner = GmailGrokScanner(db)

    if not scanner.is_configured:
        return JSONResponse({"success": False, "error": "Gmail OAuth not configured"}, status_code=503)

    result = await scanner.scan_backlog(
        max_results=body.get("max_results", 200),
        humanize=body.get("humanize", settings.humanizer_enabled),
    )
    return {"success": True, **result}


# ── Humanizer ──────────────────────────────────────────────────────────────────

@app.post("/api/social/humanize", tags=["humanizer"])
async def humanize_text(request: Request):
    """Run text through the 29-pattern humanizer."""
    body = await request.json()
    text = body.get("text", "")
    if not text:
        return JSONResponse({"success": False, "error": "text is required"}, status_code=400)

    from social.humanizer import ContentHumanizer
    humanizer = ContentHumanizer()

    # Score before
    score_before = humanizer.score_ai_likelihood(text)

    # Humanize
    result = await humanizer.humanize(
        text,
        platform=body.get("platform", "twitter"),
        max_length=body.get("max_length"),
    )

    # Score after
    score_after = humanizer.score_ai_likelihood(result)

    return {
        "success": True,
        "original": text,
        "humanized": result,
        "score_before": score_before,
        "score_after": score_after,
    }


@app.post("/api/social/humanize/score", tags=["humanizer"])
async def score_text(request: Request):
    """Score how AI-generated text sounds (0-100, lower = more human)."""
    body = await request.json()
    text = body.get("text", "")
    if not text:
        return JSONResponse({"success": False, "error": "text is required"}, status_code=400)

    from social.humanizer import ContentHumanizer
    humanizer = ContentHumanizer()
    score = humanizer.score_ai_likelihood(text)

    return {"success": True, **score}


# ── Budget Tracking ────────────────────────────────────────────────────────────

@app.get("/api/social/budget", tags=["budget"])
async def budget_status():
    """Get current month's Grok image generation budget status."""
    from social.media_pipeline import MediaPipeline

    db = get_db()
    pipeline = MediaPipeline(db)
    status = await pipeline.get_budget_status()

    return {"success": True, **status}
