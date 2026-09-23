"""
Lee County Sheriff public-api rate-limit coordination.

Lee enforces a hard ceiling (observed 2026-08):
  ``[/32] Throttled N over (INTERVAL 12 HOUR | 480000)``

Once the VPS (or any single source IP) exceeds that, *every* consumer
(scraper, FirstAppearanceWatcher, URL ingest) must stop hammering the
origin or the window never recovers and the dashboard freezes on stale
bookings.

Shared state (process memory + optional durable file):
  - record_429() → multi-hour cooldown (persisted so PM2/process restarts
    do not immediately re-burn the /32 quota)
  - is_cooled_down() → callers should skip Lee public-api
  - record_success() → clear short failure streaks (not the long cooldown)
  - cooldown auto-clears when cooled_until elapses (no stuck state)

Self-healing in practice: after a 429, Health shows status=error with a
clear cooldown message; scheduled runs keep failing closed until the
window ends, then the next run proceeds automatically with no manual reset.
"""
from __future__ import annotations

import json
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

# Durable path so scraper + FA watcher + URL ingest share one cooldown across
# process restarts. Empty string disables persistence (unit tests may override).
_STATE_PATH = (os.getenv("LEE_RATE_LIMIT_STATE_PATH") or "").strip()
if not _STATE_PATH and os.getenv("LEE_RATE_LIMIT_PERSIST", "true").strip().lower() in {
    "1", "true", "yes", "on",
}:
    _STATE_PATH = os.path.join(
        os.getenv("TMPDIR") or os.getenv("TEMP") or "/tmp",
        "shamrock_lee_public_api_cooldown.json",
    )

_lock = threading.Lock()
_cooled_until: float = 0.0
_last_429_at: float = 0.0
_last_429_detail: str = ""
_success_streak: int = 0
_loaded_from_disk: bool = False
_last_recovery_logged_until: float = 0.0


def _state_path() -> str:
    return _STATE_PATH


def _load_state_unlocked() -> None:
    """Hydrate in-memory cooldown from disk (idempotent)."""
    global _cooled_until, _last_429_at, _last_429_detail, _loaded_from_disk
    global _last_recovery_logged_until
    if _loaded_from_disk:
        return
    _loaded_from_disk = True
    path = _STATE_PATH
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return
    except Exception as exc:
        logger.warning("[Lee rate-limit] could not read state %s: %s", path, exc)
        return
    try:
        cooled = float(data.get("cooled_until") or 0.0)
        last_at = float(data.get("last_429_at") or 0.0)
        detail = str(data.get("last_429_detail") or "")[:400]
    except (TypeError, ValueError):
        return
    now = time.time()
    if cooled > now:
        _cooled_until = max(_cooled_until, cooled)
        _last_429_at = max(_last_429_at, last_at)
        if detail:
            _last_429_detail = detail
        logger.info(
            "[Lee rate-limit] restored cooldown from disk (%.0fs / %.1fh remaining)",
            _cooled_until - now,
            (_cooled_until - now) / 3600.0,
        )
    elif cooled > 0:
        # Expired on disk — self-heal: drop stale file so next run is clean.
        _cooled_until = 0.0
        _last_recovery_logged_until = cooled
        _unlink_state_unlocked()
        logger.info(
            "[Lee rate-limit] cooldown window ended — auto-cleared durable state"
        )


def _save_state_unlocked() -> None:
    path = _STATE_PATH
    if not path:
        return
    if _cooled_until <= time.time():
        _unlink_state_unlocked()
        return
    payload = {
        "cooled_until": _cooled_until,
        "last_429_at": _last_429_at,
        "last_429_detail": _last_429_detail,
        "cooldown_s": _COOLDOWN_S,
    }
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("[Lee rate-limit] could not persist state %s: %s", path, exc)


def _unlink_state_unlocked() -> None:
    path = _STATE_PATH
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.debug("[Lee rate-limit] unlink %s failed: %s", path, exc)


def _sync_and_maybe_recover_unlocked() -> None:
    """Load disk state; clear expired cooldown (self-heal)."""
    global _cooled_until, _last_recovery_logged_until
    _load_state_unlocked()
    now = time.time()
    if _cooled_until and _cooled_until <= now:
        expired_until = _cooled_until
        _cooled_until = 0.0
        _unlink_state_unlocked()
        if expired_until != _last_recovery_logged_until:
            _last_recovery_logged_until = expired_until
            logger.info(
                "[Lee rate-limit] cooldown expired — public-api traffic allowed again"
            )


def is_cooled_down() -> bool:
    """True when Lee public-api must not be called."""
    with _lock:
        _sync_and_maybe_recover_unlocked()
        return time.time() < _cooled_until


def seconds_remaining() -> float:
    with _lock:
        _sync_and_maybe_recover_unlocked()
        return max(0.0, _cooled_until - time.time())


def cooldown_status() -> dict:
    with _lock:
        _sync_and_maybe_recover_unlocked()
        rem = max(0.0, _cooled_until - time.time())
        return {
            "cooled_down": rem > 0,
            "seconds_remaining": round(rem, 1),
            "last_429_at": _last_429_at or None,
            "last_429_detail": _last_429_detail or None,
            "cooldown_s": _COOLDOWN_S,
            "state_path": _STATE_PATH or None,
        }


def clear_cooldown() -> None:
    """Ops escape hatch (tests / manual recovery)."""
    global _cooled_until, _last_429_detail, _loaded_from_disk
    with _lock:
        _cooled_until = 0.0
        _last_429_detail = ""
        _unlink_state_unlocked()
        # Allow re-load on next check (tests may write a file after clear).
        _loaded_from_disk = True


def reset_for_tests(*, state_path: Optional[str] = None) -> None:
    """Test helper: clear memory + optionally point persistence at a temp file."""
    global _STATE_PATH, _cooled_until, _last_429_at, _last_429_detail
    global _success_streak, _loaded_from_disk, _last_recovery_logged_until
    with _lock:
        if _STATE_PATH:
            try:
                os.unlink(_STATE_PATH)
            except OSError:
                pass
        if state_path is not None:
            _STATE_PATH = state_path
        _cooled_until = 0.0
        _last_429_at = 0.0
        _last_429_detail = ""
        _success_streak = 0
        _loaded_from_disk = False
        _last_recovery_logged_until = 0.0
        if _STATE_PATH:
            try:
                os.unlink(_STATE_PATH)
            except OSError:
                pass


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
        _load_state_unlocked()
        _last_429_at = now
        _last_429_detail = (detail or "HTTP 429")[:400]
        _success_streak = 0
        # Extend, never shorten, an active cooldown
        _cooled_until = max(_cooled_until, now + wait)
        remaining = _cooled_until - now
        _save_state_unlocked()
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
