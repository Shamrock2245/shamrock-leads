"""
Lee County Sheriff public-api rate-limit coordination.

Lee enforces a hard ceiling (observed 2026-08):
  ``[/32] Throttled N over (INTERVAL 12 HOUR | 480000)``

Once the VPS (or any single source IP) exceeds that, *every* consumer
(scraper, FirstAppearanceWatcher, URL ingest) must stop hammering the
origin or the window never recovers and the dashboard freezes on stale
bookings.

Shared process-local state:
  - record_429() → multi-hour cooldown
  - is_cooled_down() → callers should skip Lee public-api
  - record_success() → clear short failure streaks (not the long cooldown)
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# How long to pause ALL Lee public-api traffic after a 429.
# Default 3h — long enough for the 12h sliding window to start recovering
# without leaving Lee dark for a full half-day.
_COOLDOWN_S = float(os.getenv("LEE_RATE_LIMIT_COOLDOWN_S", str(3 * 3600)))

_lock = threading.Lock()
_cooled_until: float = 0.0
_last_429_at: float = 0.0
_last_429_detail: str = ""
_success_streak: int = 0


def is_cooled_down() -> bool:
    """True when Lee public-api must not be called."""
    with _lock:
        return time.time() < _cooled_until


def seconds_remaining() -> float:
    with _lock:
        return max(0.0, _cooled_until - time.time())


def cooldown_status() -> dict:
    with _lock:
        rem = max(0.0, _cooled_until - time.time())
        return {
            "cooled_down": rem > 0,
            "seconds_remaining": round(rem, 1),
            "last_429_at": _last_429_at or None,
            "last_429_detail": _last_429_detail or None,
            "cooldown_s": _COOLDOWN_S,
        }


def clear_cooldown() -> None:
    """Ops escape hatch (tests / manual recovery)."""
    global _cooled_until, _last_429_detail
    with _lock:
        _cooled_until = 0.0
        _last_429_detail = ""


def record_success() -> None:
    global _success_streak
    with _lock:
        _success_streak += 1


def record_429(detail: str = "", *, cooldown_s: Optional[float] = None) -> float:
    """
    Trip the shared cooldown. Returns seconds of cooldown applied.
    """
    global _cooled_until, _last_429_at, _last_429_detail, _success_streak
    wait = float(cooldown_s if cooldown_s is not None else _COOLDOWN_S)
    # Parse "Throttled N over (INTERVAL 12 HOUR | 480000)" if present
    parsed = _parse_throttle_body(detail)
    if parsed:
        detail = parsed
    now = time.time()
    with _lock:
        _last_429_at = now
        _last_429_detail = (detail or "HTTP 429")[:400]
        _success_streak = 0
        # Extend, never shorten, an active cooldown
        _cooled_until = max(_cooled_until, now + wait)
        remaining = _cooled_until - now
    logger.error(
        "[Lee rate-limit] ⛔ public-api 429 — cooling down %.0fs (%.1fh). detail=%s",
        remaining,
        remaining / 3600.0,
        (detail or "")[:200],
    )
    return remaining


def note_response(resp: Any) -> bool:
    """
    Inspect a response. If 429, trip cooldown and return True (caller should abort).
    If 200, record success. Returns True when the caller must stop Lee traffic.
    """
    if resp is None:
        return is_cooled_down()
    code = getattr(resp, "status_code", None)
    if code == 429:
        body = ""
        try:
            body = getattr(resp, "text", "") or ""
        except Exception:
            body = ""
        record_429(body)
        return True
    if code == 200:
        record_success()
    return is_cooled_down()


def _parse_throttle_body(text: str) -> str:
    if not text:
        return ""
    m = re.search(
        r"Throttled\s+(\d+)\s+over\s+\(INTERVAL\s+([^|]+)\|\s*(\d+)\)",
        text,
        re.I,
    )
    if m:
        return f"Throttled {m.group(1)} / {m.group(3).strip()} per {m.group(2).strip()}"
    if "Too Many Requests" in text or "Throttled" in text:
        # strip HTML tags lightly
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:300]
    return ""
