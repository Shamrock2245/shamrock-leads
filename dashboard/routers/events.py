# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

from fastapi import APIRouter
from starlette.responses import Response
from fastapi.responses import JSONResponse
import asyncio
import json

events_bp = APIRouter(prefix="/api", tags=["events"])
# Global event queue (use Redis pub/sub in production)
event_queues = set()

async def publish_event(event_type: str, data: dict):
    """Call this from other blueprints when something happens."""
    # BUG FIX: `event_queues -= dead` is an augmented assignment which makes Python
    # treat `event_queues` as a local variable, causing UnboundLocalError.
    # Fix: declare it global so the in-place set-difference mutates the module-level set.
    global event_queues
    msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    dead = set()
    for q in event_queues:
        try:
            await q.put(msg)
        except Exception:
            dead.add(q)
    event_queues.difference_update(dead)

@events_bp.get("/events/stream")
async def event_stream():
    q = asyncio.Queue()
    event_queues.add(q)
    try:
        async def generate():
            while True:
                msg = await q.get()
                yield msg
        return Response(generate(), content_type='text/event-stream',
                       headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
    finally:
        event_queues.discard(q)
