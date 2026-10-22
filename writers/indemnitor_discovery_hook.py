"""Queue Lee (FL) indemnitor discovery after a scraper write.

The scraper calls :func:`run_post_write_hook`. That wrapper starts the
enqueue on a daemon thread and joins it for at most ``HOOK_JOIN_SECONDS``.
A raise or a hang inside the hook cannot fail or stall ``write_records``.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

# Fast path usually finishes inside this window. A hung hook is abandoned.
HOOK_JOIN_SECONDS = 0.25


def _is_lee_florida(county: Any, state: Any) -> bool:
    from dashboard.services.place_identity import is_lee_florida

    return is_lee_florida(county, state)


def enqueue_lee_indemnitor_discovery(db: Any, records: Sequence[Any]) -> int:
    """Mark new Lee (FL) bookings queued. May raise or block. Callers must guard it.

    Logs a count only. Booking numbers and names are not logged.
    """
    if db is None or not records:
        return 0
    arrests = db["arrests"]
    now = datetime.now(timezone.utc)
    queued = 0
    for record in records:
        county = (getattr(record, "County", "") or "").strip()
        state = (getattr(record, "State", "") or "FL").strip().upper() or "FL"
        booking = (getattr(record, "Booking_Number", "") or "").strip()
        if not booking or not county or not _is_lee_florida(county, state):
            continue
        arrests.update_one(
            {"state": state, "county": county, "booking_number": booking},
            {"$set": {
                "indemnitor_discovery.status": "queued",
                "indemnitor_discovery.queued_at": now,
                "indemnitor_discovery.updated_at": now,
            }},
        )
        queued += 1
    if queued:
        logger.info("indemnitor discovery queued count=%s", queued)
    return queued


def run_post_write_hook(hook: Callable[..., Any], *args: Any, join_seconds: float | None = None) -> None:
    """Run ``hook`` without letting it fail or stall the caller.

    Exceptions stay on the daemon thread. ``join`` returns when the hook
    finishes or when ``join_seconds`` elapses, whichever comes first.
    """
    limit = HOOK_JOIN_SECONDS if join_seconds is None else join_seconds

    def _target() -> None:
        try:
            hook(*args)
        except Exception as exc:
            logger.warning(
                "indemnitor discovery hook failed error_type=%s",
                type(exc).__name__,
            )

    thread = threading.Thread(
        target=_target,
        name="indemnitor-discovery-hook",
        daemon=True,
    )
    thread.start()
    thread.join(limit)
