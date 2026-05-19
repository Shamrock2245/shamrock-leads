"""
dashboard/api/events.py — Compatibility shim
=============================================
This module is a legacy compatibility shim.

All real SSE infrastructure has been moved to:
    dashboard/routers/events.py

This file re-exports the public API so that any remaining
imports of the form:
    from dashboard.api.events import publish_event
    from dashboard.api.events import emit_event
    from dashboard.api.events import events_bp
continue to work without modification.

DO NOT add new code here. Update dashboard/routers/events.py instead.
"""
from dashboard.routers.events import (  # noqa: F401 — re-export shim
    events_bp,
    publish_event,
    emit_event,
    event_stream,
    events_health,
    _subscribers,
)
