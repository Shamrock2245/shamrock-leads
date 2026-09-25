"""
Scraper resilience primitives used by :class:`scrapers.base_scraper.BaseScraper`.

This is *not* a parallel framework: ``BaseScraper.run()`` remains the single
entry point. This module only holds the pure, unit-testable pieces that
``run()`` composes:

1. **Error classification** into a fixed set of classes::

       network      transient transport failure (timeouts, resets, DNS, HTTP 5xx)
       anti_bot     WAF / CAPTCHA / 401 / 403 / 429 / per-county rate-limit cooldown
       url_changed  HTTP 404 / 410 / "moved" — the source endpoint went away
       parse_drift  page/API loaded but its structure no longer matches the parser
       unknown      anything else

   ``parse_drift`` and ``unknown`` were added to the brief's base set
   (network / anti_bot / url_changed) because schema drift must alert loudly
   on its own and an honest catch-all is better than mislabeling.

2. **Transient retry** with exponential backoff (2s, 4s, 8s). Only ``network``
   failures are retried. A 429, an anti-bot block, or an *active per-county
   cooldown* (e.g. Lee's file-backed ``scrapers/lee_rate_limit.py``) is never
   retried — retrying into a throttle only extends it.

3. **Schema-drift assessment** of a scrape result (rows lost their
   source-issued booking identifier / names).

4. **Auto-disable state machine** — 5 consecutive counted failures trip
   ``auto_disabled``; a scheduled *canary* run is allowed every
   ``SCRAPER_AUTO_DISABLE_CANARY_MINUTES`` (default 360) and re-enables the
   scraper only when it returns records. Manual re-enable clears the flag
   (dashboard ``POST /api/scraper/enable`` or ``scripts/scraper_reenable.py``).

5. **Obscura (CDP stealth browser) routing policy** — Obscura may only be used
   for labels that are already ``verified_public`` *and* explicitly opted in
   via ``OBSCURA_ROUTE_COUNTIES``. It is always refused for ``fail_closed``
   scopes and for the hard-hold list (403 / Cloudflare-blocked sources).

Nothing in this module reads or logs secrets.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional, Sequence, Tuple, TypeVar

# ── Fixed error-class vocabulary ────────────────────────────────────────────
ERROR_NETWORK = "network"
ERROR_ANTI_BOT = "anti_bot"
ERROR_URL_CHANGED = "url_changed"
ERROR_PARSE_DRIFT = "parse_drift"
ERROR_UNKNOWN = "unknown"

ERROR_CLASSES: Tuple[str, ...] = (
    ERROR_NETWORK,
    ERROR_ANTI_BOT,
    ERROR_URL_CHANGED,
    ERROR_PARSE_DRIFT,
    ERROR_UNKNOWN,
)

# ── Tunables (env-overridable, safe defaults) ───────────────────────────────
RETRY_DELAYS_S: Tuple[float, ...] = (2.0, 4.0, 8.0)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def auto_disable_threshold() -> int:
    """Consecutive counted failures before a scraper is auto-disabled (default 5)."""
    return _env_int("SCRAPER_AUTO_DISABLE_THRESHOLD", 5)


def canary_interval() -> timedelta:
    """Minimum spacing between canary attempts for an auto-disabled scraper."""
    return timedelta(minutes=_env_int("SCRAPER_AUTO_DISABLE_CANARY_MINUTES", 360))


def auto_disable_enabled() -> bool:
    """Ops kill switch: ``SCRAPER_AUTO_DISABLE_ENABLED=false`` never skips runs."""
    return _env_bool("SCRAPER_AUTO_DISABLE_ENABLED", True)


def base_retry_enabled() -> bool:
    """Ops kill switch for the BaseScraper-level transient retry."""
    return _env_bool("SCRAPER_BASE_RETRY_ENABLED", True)


# SWFL core bond-desk counties must keep running (mirrors
# ``dashboard.extensions.KEY_FL_COUNTIES``; parity is enforced by tests).
# They still count failures and alert loudly at the threshold, but are never
# skipped by auto-disable.
AUTO_DISABLE_EXEMPT_LABELS = frozenset({
    "Lee (FL)",
    "Sarasota (FL)",
    "Collier (FL)",
    "Charlotte (FL)",
    "Manatee (FL)",
    "DeSoto (FL)",
    "Hendry (FL)",
})


# ── Typed signals scrapers may raise ────────────────────────────────────────
class ParseDriftError(RuntimeError):
    """Source responded, but its structure no longer matches the parser."""


class AntiBotBlocked(RuntimeError):
    """WAF / CAPTCHA / 403 / Cloudflare interstitial — never retried."""


class SourceUrlChanged(RuntimeError):
    """Source endpoint returned 404/410 or redirected away — never retried."""


class SourceCooldownActive(RuntimeError):
    """A per-county cooldown (e.g. Lee /32 429 window) is active.

    Never retried and never counted toward auto-disable: the cooldown module
    self-heals when its window elapses.
    """


class ObscuraRoutingRefused(PermissionError):
    """Obscura CDP stealth browser refused for this scope by policy."""


# ── Classification ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ErrorClassification:
    error_class: str
    retryable: bool = False
    cooldown: bool = False
    http_status: Optional[int] = None

    @property
    def counts_toward_disable(self) -> bool:
        """Active cooldowns are expected, self-healing waits — not failures."""
        return not self.cooldown


_STATUS_RE = re.compile(
    r"(?:\bHTTP(?:/\d(?:\.\d)?)?|\bstatus(?:[ _]code)?|\bresponse)\s*[:=#]?\s*\(?([1-5]\d\d)\b"
    r"|\b([1-5]\d\d)\s+(?:Client|Server)\s+Error\b",
    re.IGNORECASE,
)

_COOLDOWN_PATTERNS = (
    "rate-limit cooldown",
    "rate limit cooldown",
    "cooldown active",
    "cooling down",
    "rate-limit tripped",
)

_ANTI_BOT_PATTERNS = (
    "cloudflare",
    "captcha",
    "turnstile",
    "just a moment",
    "attention required",
    "access denied",
    "request rejected",
    "forbidden",
    "bot detection",
    "automated access",
    "pardon our interruption",
    "incapsula",
    "imperva",
    "akamai",
    "perimeterx",
    "datadome",
    "waf",
    "too many requests",
    "throttled",
    "rate limited",
    "http 429",
)

_PARSE_DRIFT_PATTERNS = (
    "schema drift",
    "parse drift",
    "selector not found",
    "element not found",
    "table not found",
    "no roster table",
    "missing column",
    "unexpected layout",
    "unexpected response shape",
    "unexpected schema",
    "expecting value: line 1 column 1",
)

_URL_CHANGED_PATTERNS = (
    "page not found",
    "http 404",
    "http 410",
    "moved permanently",
    "no longer available",
    "endpoint moved",
    "url changed",
)

_NETWORK_PATTERNS = (
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "connection aborted",
    "connection error",
    "max retries exceeded",
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname",
    "remote end closed",
    "remotedisconnected",
    "network is unreachable",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "econnreset",
    "broken pipe",
    "ssl: unexpected eof",
    "curl: (6)",
    "curl: (7)",
    "curl: (28)",
    "curl: (35)",
    "curl: (52)",
    "curl: (56)",
)

_NETWORK_TYPE_MARKERS = (
    "timeout",
    "connectionerror",
    "connecterror",
    "connecttimeout",
    "readtimeout",
    "protocolerror",
    "remotedisconnected",
    "chunkedencodingerror",
    "incompleteread",
    "newconnectionerror",
    "maxretryerror",
    "gaierror",
)


def _http_status(exc: BaseException) -> Optional[int]:
    for obj in (exc, getattr(exc, "response", None)):
        code = getattr(obj, "status_code", None) if obj is not None else None
        if code is None and obj is not None:
            code = getattr(obj, "status", None)
        if isinstance(code, int) and 100 <= code <= 599:
            return code
    match = _STATUS_RE.search(str(exc) or "")
    if match:
        return int(match.group(1) or match.group(2))
    return None


def _type_names(exc: BaseException) -> str:
    return " ".join(cls.__name__.lower() for cls in type(exc).__mro__)


def _has(text: str, patterns: Iterable[str]) -> bool:
    return any(p in text for p in patterns)


def classify_exception(exc: BaseException) -> ErrorClassification:
    """Map an exception to one of :data:`ERROR_CLASSES` (plus retry/cooldown flags)."""
    if isinstance(exc, SourceCooldownActive):
        return ErrorClassification(ERROR_ANTI_BOT, retryable=False, cooldown=True)
    if isinstance(exc, ParseDriftError):
        return ErrorClassification(ERROR_PARSE_DRIFT)
    if isinstance(exc, AntiBotBlocked):
        return ErrorClassification(ERROR_ANTI_BOT, http_status=_http_status(exc))
    if isinstance(exc, SourceUrlChanged):
        return ErrorClassification(ERROR_URL_CHANGED, http_status=_http_status(exc))

    text = (str(exc) or "").lower()
    status = _http_status(exc)

    if _has(text, _COOLDOWN_PATTERNS):
        return ErrorClassification(ERROR_ANTI_BOT, cooldown=True, http_status=status)
    if status in (401, 403, 429):
        return ErrorClassification(ERROR_ANTI_BOT, http_status=status)
    if status in (404, 410):
        return ErrorClassification(ERROR_URL_CHANGED, http_status=status)
    if _has(text, _ANTI_BOT_PATTERNS):
        return ErrorClassification(ERROR_ANTI_BOT, http_status=status)
    if status is not None and 500 <= status <= 599:
        return ErrorClassification(ERROR_NETWORK, retryable=True, http_status=status)

    names = _type_names(exc)
    if isinstance(exc, (TimeoutError, ConnectionError)) or _has(names, _NETWORK_TYPE_MARKERS):
        return ErrorClassification(ERROR_NETWORK, retryable=True, http_status=status)
    if _has(text, _PARSE_DRIFT_PATTERNS):
        return ErrorClassification(ERROR_PARSE_DRIFT, http_status=status)
    if _has(text, _URL_CHANGED_PATTERNS):
        return ErrorClassification(ERROR_URL_CHANGED, http_status=status)
    if _has(text, _NETWORK_PATTERNS):
        return ErrorClassification(ERROR_NETWORK, retryable=True, http_status=status)
    if isinstance(exc, (KeyError, IndexError)) or "jsondecodeerror" in names:
        return ErrorClassification(ERROR_PARSE_DRIFT, http_status=status)
    if isinstance(exc, AttributeError) and "'nonetype' object has no attribute" in text:
        return ErrorClassification(ERROR_PARSE_DRIFT, http_status=status)
    return ErrorClassification(ERROR_UNKNOWN, http_status=status)


# ── Transient retry ─────────────────────────────────────────────────────────
T = TypeVar("T")


def retry_transient(
    fn: Callable[[], T],
    *,
    delays: Sequence[float] = RETRY_DELAYS_S,
    classify: Callable[[BaseException], ErrorClassification] = classify_exception,
    sleep: Callable[[float], None] = time.sleep,
    cooldown_active: Optional[Callable[[], bool]] = None,
    on_retry: Optional[Callable[[int, float, BaseException, ErrorClassification], None]] = None,
) -> T:
    """Call ``fn``; on a *retryable* failure sleep ``delays[i]`` and try again.

    * Only classifications with ``retryable=True`` (``network``) are retried.
    * 429 / anti-bot / cooldown failures re-raise immediately.
    * ``cooldown_active()`` is checked before and after each backoff sleep so
      a retry never runs into an active per-county cooldown.
    * After ``len(delays)`` retries the last exception is re-raised.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classification decides
            verdict = classify(exc)
            if not verdict.retryable or attempt >= len(delays):
                raise
            if cooldown_active is not None and cooldown_active():
                raise
            delay = float(delays[attempt])
            if on_retry is not None:
                on_retry(attempt + 1, delay, exc, verdict)
            sleep(delay)
            if cooldown_active is not None and cooldown_active():
                raise
            attempt += 1


# ── Schema drift ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DriftFinding:
    severity: str  # "total" (run counts as failure) | "partial" (alert only)
    detail: str


DRIFT_MIN_ROWS = 10
DRIFT_RATIO = 0.5


def assess_schema_drift(raw_count: int, kept_records: Sequence[object]) -> Optional[DriftFinding]:
    """Detect parser/schema drift from a scrape result.

    * ``total``: the source returned rows but *none* carried a source-issued
      booking identifier — the key field moved or was renamed.
    * ``partial``: >=50% of >=10 rows lost the booking key, or >=50% of kept
      rows have no name.
    """
    kept = len(kept_records)
    dropped = max(0, raw_count - kept)
    if raw_count > 0 and kept == 0:
        return DriftFinding(
            "total",
            f"all {raw_count} parsed rows lacked a source-issued booking identifier",
        )
    if raw_count >= DRIFT_MIN_ROWS and dropped / raw_count >= DRIFT_RATIO:
        return DriftFinding(
            "partial",
            f"{dropped}/{raw_count} parsed rows lacked a source-issued booking identifier",
        )
    if kept >= DRIFT_MIN_ROWS:
        nameless = sum(1 for r in kept_records if not str(getattr(r, "Full_Name", "") or "").strip())
        if nameless / kept >= DRIFT_RATIO:
            return DriftFinding("partial", f"{nameless}/{kept} kept rows have an empty Full_Name")
    return None


class AlertThrottle:
    """In-process de-duplication so one broken county cannot flood Slack."""

    def __init__(self, window_s: float = 1800.0, clock: Callable[[], float] = time.monotonic):
        self._window = window_s
        self._clock = clock
        self._last: dict = {}
        self._lock = threading.Lock()

    def allow(self, key: Tuple[str, ...]) -> bool:
        now = self._clock()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self._window:
                return False
            self._last[key] = now
            return True


# ── Auto-disable state machine ──────────────────────────────────────────────
GATE_RUN = "run"
GATE_CANARY = "canary"
GATE_SKIP = "skip"


def _aware(value: object) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _str_or_none(value: object) -> Optional[str]:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class ResilienceState:
    consecutive_failures: int = 0
    auto_disabled: bool = False
    auto_disabled_at: Optional[datetime] = None
    auto_disabled_reason: Optional[str] = None
    last_canary_at: Optional[datetime] = None
    last_error_class: Optional[str] = None

    @classmethod
    def from_doc(cls, doc: Optional[dict]) -> "ResilienceState":
        if not isinstance(doc, dict):
            doc = {}
        raw_failures = doc.get("consecutive_failures")
        failures = raw_failures if isinstance(raw_failures, int) and not isinstance(raw_failures, bool) else 0
        failures = max(0, failures)
        return cls(
            consecutive_failures=failures,
            auto_disabled=doc.get("auto_disabled") is True,
            auto_disabled_at=_aware(doc.get("auto_disabled_at")),
            auto_disabled_reason=_str_or_none(doc.get("auto_disabled_reason")),
            last_canary_at=_aware(doc.get("last_canary_at")),
            last_error_class=_str_or_none(doc.get("last_error_class")),
        )

    def to_fields(self) -> dict:
        return {
            "consecutive_failures": self.consecutive_failures,
            "auto_disabled": self.auto_disabled,
            "auto_disabled_at": self.auto_disabled_at,
            "auto_disabled_reason": self.auto_disabled_reason,
            "last_canary_at": self.last_canary_at,
            "last_error_class": self.last_error_class,
        }


def gate_decision(
    state: ResilienceState,
    now: datetime,
    *,
    interval: Optional[timedelta] = None,
    force: bool = False,
) -> str:
    """``run`` normally; for an auto-disabled scraper ``canary`` or ``skip``."""
    if not state.auto_disabled:
        return GATE_RUN
    if force:
        return GATE_CANARY
    interval = interval if interval is not None else canary_interval()
    refs = [t for t in (state.auto_disabled_at, state.last_canary_at) if t is not None]
    if not refs or now - max(refs) >= interval:
        return GATE_CANARY
    return GATE_SKIP


def state_after_failure(
    state: ResilienceState,
    verdict: ErrorClassification,
    now: datetime,
    *,
    threshold: Optional[int] = None,
    exempt: bool = False,
    was_canary: bool = False,
    reason: str = "",
) -> Tuple[ResilienceState, bool]:
    """Return ``(new_state, tripped_now)``.

    Cooldown failures are not counted. ``exempt`` scopes (SWFL core) count and
    alert but are never flagged ``auto_disabled``.
    """
    if not verdict.counts_toward_disable:
        return replace(state, last_error_class=verdict.error_class), False
    threshold = threshold if threshold is not None else auto_disable_threshold()
    failures = state.consecutive_failures + 1
    tripped = (not state.auto_disabled) and (not exempt) and failures >= threshold
    new_state = replace(
        state,
        consecutive_failures=failures,
        last_error_class=verdict.error_class,
        auto_disabled=state.auto_disabled or tripped,
        auto_disabled_at=now if tripped else state.auto_disabled_at,
        auto_disabled_reason=(reason[:300] if tripped else state.auto_disabled_reason),
        last_canary_at=now if was_canary else state.last_canary_at,
    )
    return new_state, tripped


def state_after_success(
    state: ResilienceState,
    records: int,
    now: datetime,
    *,
    was_canary: bool = False,
) -> Tuple[ResilienceState, bool]:
    """Return ``(new_state, reenabled_now)``.

    A normal run that did not raise resets the streak (an honest empty roster
    is not a failure). An auto-disabled scraper is re-enabled only by a canary
    that actually returned records.
    """
    if state.auto_disabled:
        if was_canary and records > 0:
            return ResilienceState(last_canary_at=now), True
        return replace(state, last_canary_at=now if was_canary else state.last_canary_at), False
    return replace(state, consecutive_failures=0, last_error_class=None), False


# ── Obscura (CDP stealth browser) routing policy ────────────────────────────
OBSCURA_ROUTE_ENV = "OBSCURA_ROUTE_COUNTIES"

# Never route through Obscura regardless of opt-in: fail-closed holds and
# sources whose only observed ordinary-access answer is 403 / Cloudflare.
OBSCURA_HARD_DENY_LABELS = frozenset({
    "Hampton (SC)",
    "Marlboro (SC)",
    "Richland (SC)",
    "Sumter (SC)",
    "Sarasota (FL)",
    "St. Clair (AL)",  # verified_public but ordinary direct request returned 403
    # FL JailTracker wrappers (jailtracker_base.SOURCE_CONTRACT_VALIDATED=False)
    "Baker (FL)",
    "Calhoun (FL)",
    "Gulf (FL)",
    "Holmes (FL)",
    "Levy (FL)",
    "Wakulla (FL)",
    "Washington (FL)",
})


def obscura_opt_in_labels(env_value: Optional[str] = None) -> frozenset:
    raw = os.getenv(OBSCURA_ROUTE_ENV, "") if env_value is None else env_value
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def obscura_route_decision(
    label: str,
    *,
    source_contract_validated: bool,
    source_state: str,
    opt_in: Optional[frozenset] = None,
) -> Tuple[bool, str]:
    """Decide whether a scope may be *newly routed* through Obscura.

    Allowed only when the scope has a proven contract (``verified_public``),
    is not a hold / 403 source, and is explicitly listed in
    ``OBSCURA_ROUTE_COUNTIES``. Default: nothing is routed.
    """
    if not source_contract_validated or source_state == "fail_closed":
        return False, "fail_closed scope — Obscura never reopens a guarded source"
    if label in OBSCURA_HARD_DENY_LABELS:
        return False, "hold/403 scope — Obscura is not a WAF bypass"
    if source_state != "verified_public":
        return False, f"source state {source_state!r} is not verified_public"
    opt_in = obscura_opt_in_labels() if opt_in is None else opt_in
    if label not in opt_in:
        return False, f"not opted in via {OBSCURA_ROUTE_ENV}"
    return True, "verified_public and opted in"


def obscura_hard_refusal(label: str, *, source_contract_validated: bool, source_state: str) -> Optional[str]:
    """Reason Obscura must be refused outright (legacy callers included), else None."""
    if not source_contract_validated or source_state == "fail_closed":
        return "fail_closed scope"
    if label in OBSCURA_HARD_DENY_LABELS:
        return "hold/403 scope"
    return None

