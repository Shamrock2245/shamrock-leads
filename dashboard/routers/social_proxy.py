"""
Dashboard Router — Social Engine Proxy
========================================
Proxies requests from the authenticated dashboard to the Social Engine
microservice (shamrock-social:5060 on shamrock-net).

This lets the frontend call /api/social/* through the dashboard's
PIN-authenticated session without needing separate auth for the social service.

Uses httpx async client with connection pooling for low-latency forwarding.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger("dashboard.routers.social_proxy")

router = APIRouter(prefix="/api/social", tags=["social"])

# Social engine URL — Docker DNS resolves hostname within shamrock-net
SOCIAL_ENGINE_URL = os.getenv(
    "SOCIAL_ENGINE_URL",
    "http://shamrock-social:5060",
)

# Connection pool (reused across requests)
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=SOCIAL_ENGINE_URL,
            timeout=30.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


async def _proxy(method: str, path: str, request: Request = None, params: dict = None) -> JSONResponse:
    """Forward a request to the social engine."""
    client = _get_client()
    url = f"/api/social{path}"

    try:
        body = None
        if request and method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.json()
            except Exception:
                body = {}

        response = await client.request(
            method=method,
            url=url,
            json=body if body else None,
            params=params,
        )
        return JSONResponse(response.json(), status_code=response.status_code)

    except httpx.ConnectError:
        return JSONResponse(
            {"success": False, "error": "Social engine not reachable (is shamrock-social running?)"},
            status_code=503,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {"success": False, "error": "Social engine timed out"},
            status_code=504,
        )
    except Exception as e:
        logger.error("Social proxy error: %s", e)
        return JSONResponse(
            {"success": False, "error": f"Proxy error: {str(e)}"},
            status_code=502,
        )


# ── Health ─────────────────────────────────────────────────────────────────────

@router.get("/health")
async def social_health():
    """Check if the social engine is reachable."""
    return await _proxy("GET", "/../health")


# ── Postiz Integration ────────────────────────────────────────────────────────

@router.get("/postiz/health")
async def postiz_health():
    """Check if Postiz is reachable and authenticated."""
    try:
        from social.postiz_client import get_public_postiz_client
        client = get_public_postiz_client()
        health = await client.health_check()
        return JSONResponse(health)
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


@router.get("/postiz/integrations")
async def postiz_integrations():
    """List all connected Postiz social channels."""
    try:
        from social.postiz_client import get_public_postiz_client
        client = get_public_postiz_client()
        connected = await client.get_all_connected_platforms()
        integrations = await client.list_integrations()
        return JSONResponse({
            "success": True,
            "platforms": connected,
            "integrations": integrations,
            "count": len(integrations),
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/postiz/posts")
async def postiz_posts(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Get posts history and status from Postiz."""
    try:
        from social.postiz_client import get_public_postiz_client
        from datetime import datetime
        client = get_public_postiz_client()
        s_date = datetime.fromisoformat(start_date) if start_date else None
        e_date = datetime.fromisoformat(end_date) if end_date else None
        posts = await client.get_posts(start_date=s_date, end_date=e_date)
        return JSONResponse({"success": True, "posts": posts})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/postiz/post")
async def postiz_direct_post(request: Request):
    """Post directly via Postiz API (bypasses queue)."""
    try:
        from social.platforms.postiz import PostizAdapter
        body = await request.json()
        content = body.get("content", "")
        platform = body.get("platform", "twitter")
        if not content:
            return JSONResponse({"success": False, "error": "No content provided"}, status_code=400)

        adapter = PostizAdapter(target_platform=platform)
        result = await adapter.post(content)
        return JSONResponse({
            "success": result.success,
            "platform_post_id": result.platform_post_id,
            "platform_url": result.platform_url,
            "error": result.error,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/queue")
async def list_queue(
    status: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
):
    return await _proxy("GET", "/queue", params={
        k: v for k, v in {"status": status, "platform": platform, "limit": limit, "skip": skip}.items()
        if v is not None
    })


@router.get("/queue/stats")
async def queue_stats():
    return await _proxy("GET", "/queue/stats")


@router.get("/post/{post_id}")
async def get_post(post_id: str):
    return await _proxy("GET", f"/post/{post_id}")


# ── Approval Flow ──────────────────────────────────────────────────────────────

@router.post("/approve/batch")
async def batch_approve(request: Request):
    return await _proxy("POST", "/approve/batch", request)


@router.post("/approve/{post_id}")
async def approve(post_id: str, request: Request):
    return await _proxy("POST", f"/approve/{post_id}", request)


@router.post("/reject/{post_id}")
async def reject(post_id: str, request: Request):
    return await _proxy("POST", f"/reject/{post_id}", request)


@router.post("/edit/{post_id}")
async def edit(post_id: str, request: Request):
    return await _proxy("POST", f"/edit/{post_id}", request)


# ── Publishing ─────────────────────────────────────────────────────────────────

@router.post("/publish/batch")
async def batch_publish():
    return await _proxy("POST", "/publish/batch")


@router.post("/publish/{post_id}")
async def publish(post_id: str):
    return await _proxy("POST", f"/publish/{post_id}")


# ── Content Creation ───────────────────────────────────────────────────────────

@router.post("/manual")
async def manual_post(request: Request):
    return await _proxy("POST", "/manual", request)


@router.post("/ingest")
async def ingest(request: Request):
    return await _proxy("POST", "/ingest", request)


# ── Grok / xAI ────────────────────────────────────────────────────────────────

@router.post("/grok/generate")
async def grok_generate(request: Request):
    return await _proxy("POST", "/grok/generate", request)


@router.post("/grok/news")
async def grok_news(request: Request):
    return await _proxy("POST", "/grok/news", request)


@router.post("/grok/image")
async def grok_image(request: Request):
    return await _proxy("POST", "/grok/image", request)


# ── Gmail Scanner ──────────────────────────────────────────────────────────────

@router.post("/gmail/scan")
async def gmail_scan(request: Request):
    return await _proxy("POST", "/gmail/scan", request)


@router.post("/gmail/backlog")
async def gmail_backlog(request: Request):
    return await _proxy("POST", "/gmail/backlog", request)


# ── Humanizer ──────────────────────────────────────────────────────────────────

@router.post("/humanize")
async def humanize(request: Request):
    return await _proxy("POST", "/humanize", request)


@router.post("/humanize/score")
async def humanize_score(request: Request):
    return await _proxy("POST", "/humanize/score", request)


# ── Analytics ──────────────────────────────────────────────────────────────────

@router.get("/analytics")
async def analytics(
    platform: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
):
    return await _proxy("GET", "/analytics", params={
        k: v for k, v in {"platform": platform, "days": days}.items()
        if v is not None
    })


# ── Platforms ──────────────────────────────────────────────────────────────────

@router.get("/platforms")
async def platforms():
    return await _proxy("GET", "/platforms")
