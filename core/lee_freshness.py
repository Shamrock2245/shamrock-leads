"""Lee (FL) freshness / heartbeat checks.

Lee is the home county and must never miss a lead. The public listing
(``/public-api/bookings``) is a short rolling window: on 2026-10-23 it held
20-38 rows covering roughly the last 14 hours, and it ignored the
``inCustody`` / ``startBooking`` / ``endBooking`` / ``limit`` parameters. A
booking that rolls off the listing before a successful run is lost, so the
scraper has to be watched from outside its own run loop.

This module is pure logic plus one small runner used by the dashboard cron
(``dashboard/cron.py`` ``lee_freshness``):

* ``booking_window`` / ``window_gap`` — overlap guard between consecutive
  runs, keyed only on the published 7-digit ``bookingNumber``.
* ``evaluate_freshness`` — findings from the persisted ``scraper_status`` doc:
  no successful run in N minutes, 0 rows while the site lists bookings,
  repeated errors, and a reported window gap.
* ``LeeFreshnessAlerter`` — alert de-duplication and recovery notices; posts
  through the existing ``SlackNotifier.notify_alert`` path (#alerts).

Nothing here logs or posts names, DOBs or other person fields: findings carry
counts, timestamps, error classes and booking-number ranges only.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

LEE_LABEL = "Lee (FL)"
LEE_LISTING_URL = "https://www.sheriffleefl.org/public-api/bookings"
LEE_PROBE_USER_AGENT = "ShamrockLeads/1.0 (+https://www.shamrockbailbonds.biz)"

# Published Lee booking numbers are 7 digits today (``id`` is a 14-digit
# internal row id and ``permId`` is a person id; neither is a booking key).
# 6-9 digits leaves room for the sequence to grow without admitting ``id``.
_BOOKING_NUMBER_RE = re.compile(r"^\d{6,9}$")


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip() or default)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def max_age_minutes() -> int:
    """N: alert when Lee has had no successful run for this many minutes.

    Default 75 = the 30-minute cadence x 2 missed cycles + ~15 minutes for a
    slow run (MAX_EXECUTION_S 330s plus outer retries/backoff). That is well
    inside the ~14-hour listing window, leaving hours to recover before rows
    roll off.
    """
    return _env_int("LEE_FRESHNESS_MAX_AGE_MIN", 75)


def max_consecutive_failures() -> int:
    return _env_int("LEE_FRESHNESS_MAX_FAILURES", 3)


def realert_minutes() -> int:
    return _env_int("LEE_FRESHNESS_REALERT_MIN", 60)


def published_booking_number(row: Any) -> str:
    """The published ``bookingNumber`` only (no ``id`` / ``permId`` fallback)."""
    if not isinstance(row, dict):
        return ""
    value = row.get("bookingNumber")
    if value is None:
        value = row.get("booking_number")
    text = str(value).strip() if value is not None else ""
    return text if _BOOKING_NUMBER_RE.match(text) else ""


def booking_window(rows: Iterable[Dict[str, Any]], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Summarise a fetched listing: row count and published booking-number range."""
    rows = list(rows or [])
    keys = sorted({int(bn) for bn in (published_booking_number(r) for r in rows) if bn})
    return {
        "rows": len(rows),
        "keyed_rows": len(keys),
        "min_bn": keys[0] if keys else None,
        "max_bn": keys[-1] if keys else None,
        "at": (now or datetime.now(timezone.utc)),
    }


def window_gap(previous: Optional[Dict[str, Any]], current: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return gap details when this run's window does not overlap the last one.

    Lee booking numbers are sequential but the listing skips unpublished
    numbers (they 404 on the detail endpoint), so gaps *inside* a window are
    normal. What matters is overlap between runs: if the oldest booking number
    listed now is more than one past the newest one seen last run, every
    number in between was never observed and may have rolled off unseen.
    """
    if not previous or not current:
        return None
    prev_max = previous.get("max_bn")
    cur_min = current.get("min_bn")
    if not isinstance(prev_max, int) or not isinstance(cur_min, int):
        return None
    if cur_min <= prev_max + 1:
        return None
    return {
        "prev_max_bn": prev_max,
        "cur_min_bn": cur_min,
        "unseen_numbers": cur_min - prev_max - 1,
    }


def _aware(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


@dataclass
class Finding:
    code: str
    level: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


def evaluate_freshness(
    doc: Optional[Dict[str, Any]],
    now: datetime,
    *,
    max_age_min: Optional[int] = None,
    max_failures: Optional[int] = None,
    site_rows: Optional[int] = None,
) -> List[Finding]:
    """Findings for the Lee ``scraper_status`` doc (empty list = healthy)."""
    max_age_min = max_age_min or max_age_minutes()
    max_failures = max_failures or max_consecutive_failures()
    findings: List[Finding] = []

    if not doc:
        return [Finding(
            "no_status",
            "critical",
            f"{LEE_LABEL} has no scraper_status document: the scraper has never reported.",
        )]

    last_success = _aware(doc.get("last_success_at"))
    last_run = _aware(doc.get("last_run"))
    reference = last_success
    if reference is None and str(doc.get("status") or "") in {"ok", "empty"}:
        # Docs written before last_success_at existed: last_run on an ok/empty
        # status is the last success.
        reference = last_run
    age_min = None if reference is None else (now - reference).total_seconds() / 60.0
    if age_min is None or age_min > max_age_min:
        findings.append(Finding(
            "stale",
            "critical",
            (
                f"{LEE_LABEL}: no successful run in "
                + (f"{age_min:.0f} min" if age_min is not None else "recorded history")
                + f" (threshold {max_age_min} min, cadence 30 min). "
                "Bookings roll off the public listing in hours."
            ),
            {
                "last_success_at": reference.isoformat() if reference else None,
                "last_run": last_run.isoformat() if last_run else None,
                "status": doc.get("status"),
                "error_class": doc.get("error_class") or doc.get("last_error_class"),
            },
        ))

    failures = int(doc.get("consecutive_failures") or 0)
    if failures >= max_failures:
        findings.append(Finding(
            "repeated_errors",
            "error",
            f"{LEE_LABEL}: {failures} consecutive failed runs (threshold {max_failures}).",
            {
                "error_class": doc.get("error_class") or doc.get("last_error_class"),
                "cooldown_active": bool(doc.get("cooldown_active")),
            },
        ))

    status = str(doc.get("status") or "")
    records = int(doc.get("records") or 0)
    if status in {"ok", "empty"} and records == 0 and site_rows:
        findings.append(Finding(
            "zero_rows_site_has_bookings",
            "critical",
            (
                f"{LEE_LABEL}: last run stored 0 rows but the public listing shows "
                f"{site_rows} bookings right now."
            ),
            {"site_rows": site_rows, "status": status},
        ))

    gap = doc.get("source_window_gap")
    if isinstance(gap, dict) and gap.get("unseen_numbers"):
        findings.append(Finding(
            "window_gap",
            "error",
            (
                f"{LEE_LABEL}: listing did not overlap the previous run; "
                f"{gap.get('unseen_numbers')} booking numbers between "
                f"{gap.get('prev_max_bn')} and {gap.get('cur_min_bn')} were never seen."
            ),
            {k: gap.get(k) for k in ("prev_max_bn", "cur_min_bn", "unseen_numbers")},
        ))
    return findings


def probe_listing_count(timeout: float = 20.0, get: Optional[Callable[..., Any]] = None) -> Optional[int]:
    """One plain GET of the public listing; returns the keyed row count or None.

    Honest User-Agent, certificate verification on, environment proxy settings
    ignored. Only called when the last run stored 0 rows, and skipped while
    the Lee rate-limit cooldown is active.
    """
    try:
        from scrapers.lee_rate_limit import is_cooled_down
        if is_cooled_down():
            return None
    except Exception:
        pass
    try:
        if get is None:
            import requests
            session = requests.Session()
            session.trust_env = False
            session.headers.update({
                "User-Agent": LEE_PROBE_USER_AGENT,
                "Accept": "application/json",
            })
            get = session.get
        resp = get(LEE_LISTING_URL, timeout=timeout)
        if getattr(resp, "status_code", None) != 200:
            return None
        data = resp.json()
    except Exception as exc:
        logger.info("[LeeFreshness] listing probe failed (%s)", type(exc).__name__)
        return None
    rows = data if isinstance(data, list) else []
    return sum(1 for r in rows if published_booking_number(r))


class LeeFreshnessAlerter:
    """De-duplicates findings and posts through ``SlackNotifier.notify_alert``."""

    def __init__(self, notifier: Any = None, realert_min: Optional[int] = None):
        self._notifier = notifier
        self._realert = timedelta(minutes=realert_min or realert_minutes())
        self._active: Dict[str, datetime] = {}

    def _get_notifier(self):
        if self._notifier is None:
            from writers.slack_notifier import SlackNotifier
            self._notifier = SlackNotifier()
        return self._notifier

    def process(self, findings: List[Finding], now: datetime) -> Dict[str, List[str]]:
        sent: List[str] = []
        recovered: List[str] = []
        current = {f.code for f in findings}
        for finding in findings:
            last = self._active.get(finding.code)
            if last is not None and now - last < self._realert:
                continue
            try:
                self._get_notifier().notify_alert(
                    title=f"Lee (FL) freshness: {finding.code}",
                    message=finding.message,
                    level=finding.level,
                    details=finding.details,
                    source="lee_freshness",
                )
            except Exception as exc:
                logger.warning("[LeeFreshness] Slack alert failed (%s)", type(exc).__name__)
                continue
            self._active[finding.code] = now
            sent.append(finding.code)
        for code in list(self._active):
            if code not in current:
                del self._active[code]
                recovered.append(code)
        if recovered:
            try:
                self._get_notifier().notify_alert(
                    title="Lee (FL) freshness recovered",
                    message=f"{LEE_LABEL}: cleared {', '.join(sorted(recovered))}.",
                    level="success",
                    source="lee_freshness",
                )
            except Exception as exc:
                logger.warning("[LeeFreshness] recovery alert failed (%s)", type(exc).__name__)
        return {"sent": sent, "recovered": recovered}


_ALERTER: Optional[LeeFreshnessAlerter] = None


def _alerter() -> LeeFreshnessAlerter:
    global _ALERTER
    if _ALERTER is None:
        _ALERTER = LeeFreshnessAlerter()
    return _ALERTER


LEE_STATUS_FILTER = {
    "$or": [
        {"county_label": LEE_LABEL},
        {"county": LEE_LABEL},
        {"county": "Lee", "state": "FL"},
        {"county": "Lee", "state": {"$exists": False}},
        {"county": "Lee", "state": None},
        {"county": "Lee", "state": ""},
    ]
}


async def run_lee_freshness_check(
    db: Any,
    *,
    now: Optional[datetime] = None,
    alerter: Optional[LeeFreshnessAlerter] = None,
    probe: Optional[Callable[[], Optional[int]]] = None,
) -> Dict[str, Any]:
    """Read Lee's scraper_status doc, evaluate, alert. Returns a PII-free summary."""
    import asyncio

    now = now or datetime.now(timezone.utc)
    doc = await db["scraper_status"].find_one(LEE_STATUS_FILTER, {"_id": 0})
    site_rows = None
    if doc and str(doc.get("status") or "") in {"ok", "empty"} and not int(doc.get("records") or 0):
        probe = probe or probe_listing_count
        site_rows = await asyncio.to_thread(probe)
    findings = evaluate_freshness(doc, now, site_rows=site_rows)
    result = (alerter or _alerter()).process(findings, now)
    return {
        "findings": [f.code for f in findings],
        "site_rows": site_rows,
        **result,
    }
