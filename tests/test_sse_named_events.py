"""Regression tests — SSE named-event dispatch (dashboard/routers/events.py).

Guards against the July 2026 bug where every domain event was emitted as the
generic SSE ``event: message``, so frontend named listeners
(``es.addEventListener('new_arrest', ...)`` etc.) never fired and the entire
real-time layer (toasts, activity feed, badges) was silently dead.
"""
import asyncio
import json

import pytest

from dashboard.routers import events as events_mod


@pytest.mark.asyncio
async def test_publish_event_enqueues_named_tuple():
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    events_mod._subscribers.add(queue)
    try:
        await events_mod.publish_event("new_arrest", {"county": "Lee", "state": "FL"})
        item = queue.get_nowait()
        assert isinstance(item, tuple) and len(item) == 2
        event_name, payload = item
        assert event_name == "new_arrest"
        data = json.loads(payload)
        assert data["type"] == "new_arrest"
        assert data["county"] == "Lee"
    finally:
        events_mod._subscribers.discard(queue)


@pytest.mark.asyncio
async def test_generator_yields_named_sse_event():
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    queue.put_nowait(("document_signed", json.dumps({"type": "document_signed", "packet_id": "P1"})))

    gen = events_mod._event_generator(queue)
    first = await gen.asend(None)
    assert first["event"] == "connected"

    second = await gen.asend(None)
    assert second["event"] == "document_signed", (
        "SSE must use the domain event name so frontend addEventListener fires"
    )
    assert json.loads(second["data"])["packet_id"] == "P1"
    await gen.aclose()


@pytest.mark.asyncio
async def test_generator_tolerates_legacy_string_items():
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    queue.put_nowait(json.dumps({"type": "payment_received", "amount": 100}))

    gen = events_mod._event_generator(queue)
    await gen.asend(None)  # connected
    evt = await gen.asend(None)
    assert evt["event"] == "payment_received"
    await gen.aclose()


@pytest.mark.asyncio
async def test_emit_event_alias_still_works():
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    events_mod._subscribers.add(queue)
    try:
        await events_mod.emit_event("bond_renewed", {"booking_number": "B1"})
        event_name, payload = queue.get_nowait()
        assert event_name == "bond_renewed"
    finally:
        events_mod._subscribers.discard(queue)
