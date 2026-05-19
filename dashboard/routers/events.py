"""
ShamrockLeads — Server-Sent Events (SSE) Router
=================================================
Provides a real-time event stream to the dashboard frontend.

Endpoints:
  GET /api/events/stream  — Persistent SSE connection; receives all domain events

Published by other routers via:
    from dashboard.routers.events import publish_event
    await publish_event("event_type", {...})

Uses sse-starlette for proper ASGI-native SSE (no Quart streaming Response).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

events_bp = APIRouter(prefix="/api", tags=["events"])

# ── In-process fan-out: set of live subscriber queues ────────────────────────
# Each connected SSE client gets its own asyncio.Queue.
# publish_event() drops a message into every live queue.
# For multi-worker production, swap this with Redis pub/sub.
_subscribers: set[asyncio.Queue] = set()


async def publish_event(event_type: str, data: dict) -> None:
    """Broadcast a typed event to all connected SSE clients.

    Call from any router:
        from dashboard.routers.events import publish_event
        await publish_event("document_signed", {"packet_id": "..."})
    """
    msg = json.dumps({"type": event_type, **data})
    dead: set[asyncio.Queue] = set()
    for q in _subscribers:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.add(q)
        except Exception:
            dead.add(q)
    _subscribers.difference_update(dead)


# Alias used by older call-sites that referenced emit_event
emit_event = publish_event


async def _event_generator(queue: asyncio.Queue) -> AsyncGenerator[dict, None]:
    """Yield SSE-formatted messages from the subscriber queue."""
    # Send an initial heartbeat so the client knows the connection is live
    yield {"event": "connected", "data": json.dumps({"status": "ok"})}
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield {"event": "message", "data": msg}
            except asyncio.TimeoutError:
                # Send periodic heartbeat to keep proxies/nginx from closing idle connections
                yield {"event": "ping", "data": "{}"}
    except asyncio.CancelledError:
        pass
    finally:
        _subscribers.discard(queue)


@events_bp.get("/events/stream")
async def event_stream() -> EventSourceResponse:
    """Open a persistent Server-Sent Events stream.

    The dashboard frontend connects here on load and receives real-time
    domain events (document_signed, intake_promoted, bond_exonerated, etc.)

    Nginx config: set proxy_buffering off; proxy_read_timeout 3600s;
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(queue)
    logger.debug("[SSE] New subscriber connected — total: %d", len(_subscribers))
    return EventSourceResponse(
        _event_generator(queue),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",        # Disable Nginx response buffering
        },
    )


@events_bp.get("/events/health")
async def events_health() -> dict:
    """Health check: returns number of active SSE subscribers."""
    return {"success": True, "subscribers": len(_subscribers)}
