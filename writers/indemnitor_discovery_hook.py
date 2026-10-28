"""Queue Lee (FL) indemnitor discovery as part of the arrest write.

The queue marker rides inside the arrest upsert itself (``$setOnInsert``),
so it is durable the moment the bulk write returns. There is no thread and
no second round trip, so a one-shot run that exits right after the write
cannot lose the enqueue, and the write is never delayed by it.

:func:`discovery_insert_marker` is fail-open: any error is logged by type
and the arrest is written without a marker. The dashboard cron still picks
up recent Lee (FL) rows that have no marker.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger(__name__)

# Top-level arrest field the marker lives under. The scraped ``$set`` never
# carries it; if it ever does, the marker is skipped (Mongo rejects a path in
# both $set and $setOnInsert).
MARKER_FIELD = "indemnitor_discovery"


def _is_lee_florida(county: Any, state: Any) -> bool:
    from dashboard.services.place_identity import is_lee_florida

    return is_lee_florida(county, state)


def queued_marker(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    return {"status": "queued", "queued_at": now, "updated_at": now}


def discovery_insert_marker(
    state: Any,
    county: Any,
    booking: Any,
    set_doc: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """``$setOnInsert`` fields that queue discovery for a new Lee (FL) booking.

    Returns ``{}`` for any other county, a blank key, a ``$set`` that already
    carries the field, or any error. Never raises. Logs the error type only.
    """
    try:
        if not str(booking or "").strip() or not str(county or "").strip():
            return {}
        if set_doc and any(
            key == MARKER_FIELD or str(key).startswith(MARKER_FIELD + ".") for key in set_doc
        ):
            return {}
        if not _is_lee_florida(county, state or "FL"):
            return {}
        return {MARKER_FIELD: queued_marker(now)}
    except Exception as exc:  # isolation: the arrest write must go ahead
        logger.warning(
            "indemnitor discovery marker skipped error_type=%s",
            type(exc).__name__,
        )
        return {}


def enqueue_lee_indemnitor_discovery(db: Any, records: Sequence[Any]) -> int:
    """Backfill helper: mark existing Lee (FL) bookings queued.

    Not called on the scraper write path (the marker is in the upsert). May
    raise; callers must guard it. Logs a count only.
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
