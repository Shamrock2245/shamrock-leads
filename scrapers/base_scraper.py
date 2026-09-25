"""
Base Scraper — Abstract interface all county scrapers implement.

Every county scraper inherits from BaseScraper and implements:
- scrape() → List[ArrestRecord]
- county property

The scheduler calls run() which handles:
1. Scraping
2. Lead scoring (auto-score every record)
3. Writing to MongoDB/Sheets
4. Slack notifications (hot leads + summaries)
"""

import logging
import os
import re
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime, timezone

from core.models import ArrestRecord
from scoring.lead_scorer import LeadScorer
from scrapers.scraper_resilience import (
    AUTO_DISABLE_EXEMPT_LABELS,
    ERROR_PARSE_DRIFT,
    GATE_CANARY,
    GATE_SKIP,
    RETRY_DELAYS_S,
    AlertThrottle,
    ObscuraRoutingRefused,
    ParseDriftError,
    ResilienceState,
    assess_schema_drift,
    auto_disable_enabled,
    auto_disable_threshold,
    base_retry_enabled,
    classify_exception,
    gate_decision,
    obscura_hard_refusal,
    obscura_route_decision,
    retry_transient,
    state_after_failure,
    state_after_success,
)

# Hybrid scorer: ML + rule-based blending + FTA risk overlay
try:
    from scoring.hybrid_scorer import hybrid_score as _hybrid_score
    _hybrid_available = True
except ImportError:
    _hybrid_available = False

# Dashboard import is optional and must never break scraper unit tests or CLI
# one-shots when FastAPI/Starlette versions disagree with local deps.
try:
    from dashboard.server import update_scraper_status
    _dashboard_available = True
except Exception:
    update_scraper_status = None  # type: ignore[assignment]
    _dashboard_available = False

try:
    from scrapers.poison_pill import PoisonPillDetector
    _poison_pill = PoisonPillDetector()
except ImportError:
    _poison_pill = None

from writers.slack_notifier import SlackNotifier
try:
    from scrapers.poison_pill import PoisonPillDetector, get_scraper_headers  # noqa: F401 — re-exported for subclasses
    _pill_detector = PoisonPillDetector()
except ImportError:
    _pill_detector = None
    def get_scraper_headers(**kwargs):  # noqa: E302
        return {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Self-hosted error tracking (MongoDB + Slack)
try:
    from dashboard.services.error_tracker import ErrorTracker
    _error_tracker = ErrorTracker()
except Exception:
    _error_tracker = None

logger = logging.getLogger(__name__)

# Shared instances (initialized once)
_scorer = LeadScorer()
_slack = SlackNotifier()
# Drift alerts: first occurrence is immediate; repeats for the same county and
# severity are suppressed for 30 minutes so a broken parser cannot flood Slack.
_drift_alert_throttle = AlertThrottle(window_s=1800.0)


def _registry_source_state(label: str) -> str:
    """Return the deployed Health source state for ``County (ST)`` (never raises)."""
    try:
        from dashboard.extensions import scraper_source_state

        return scraper_source_state(label)
    except Exception:
        return "unverified"


class BaseScraper(ABC):
    """
    Abstract base class for all county scrapers.

    Subclasses must implement:
    - county (property): Return the county name (e.g., "Lee")
    - scrape(): Fetch and parse arrest records from the county source

    The run() method wraps scrape() with error handling, timing,
    lead scoring, writer integration, and Slack alerts.
    """

    # Deterministic prefixes found in historical county wrappers when a source
    # did not expose a true booking identifier. They are never acceptable as the
    # immutable County + Booking_Number deduplication key.
    _SYNTHETIC_BOOKING_RE = re.compile(
        r"^(?:AIK|CHS|DAR|MAR|NEW|MECK|CAT|RAN|DUR)_[0-9a-f]{10,12}$"
        r"|^SC_[0-9a-f]{10}$"
        r"|^SC_[A-Za-z]{2,16}_[0-9]{1,6}$"
        r"|^ONS_[A-Za-z0-9]{1,14}$",
        re.IGNORECASE,
    )

    # Source-contract state defaults to enabled. County modules set this to False
    # only when public-source recon does not prove a compliant broad listing.
    # ``run`` then returns before any network, score, writer, broadcast, or alert
    # behavior can occur.
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = ""

    # ── Self-healing (scrapers/scraper_resilience.py) ──
    # run() retries scrape() on *transient network* failures with exponential
    # backoff 2s → 4s → 8s. 429 / anti-bot / active per-county cooldowns are
    # never retried. Subclasses that already implement a cooldown-aware retry
    # loop against a quota-limited origin (Lee) set BASE_RETRY_ENABLED = False
    # so attempts are not multiplied.
    BASE_RETRY_ENABLED = True
    TRANSIENT_RETRY_DELAYS = RETRY_DELAYS_S
    _retry_sleep = staticmethod(time.sleep)

    # Disk thresholds (percentage used)
    DISK_WARN_THRESHOLD = 75
    DISK_PRUNE_THRESHOLD = 80
    DISK_BLOCK_THRESHOLD = 95

    def __init__(self):
        self.last_run: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.total_runs: int = 0
        self.total_records_scraped: int = 0

        # Namespaced per-scraper logger — subclasses and base classes can safely
        # call self.logger.* (previously interopweb/smartcop bases crashed with
        # AttributeError because no logger attribute existed).
        try:
            self.logger = logging.getLogger(f"scrapers.{self.county.lower().replace(' ', '_')}")
        except Exception:
            self.logger = logger
        
        # Initialize Autonomous Proxy Engine (APE)
        try:
            from scrapers.proxy_engine import get_ape
            self.ape = get_ape()
            logger.info(f"✅ APE initialized for {self.county} scraper")
        except ImportError:
            self.ape = None
            logger.warning(f"APE not available for {self.county} scraper")

    @classmethod
    def _get_browser_options(cls):
        """
        Create ChromiumOptions with correct browser path for Docker/VPS.

        DrissionPage does NOT auto-detect the CHROME_PATH env var —
        you MUST call set_browser_path() explicitly. Without this,
        every DrissionPage scraper silently fails inside Docker.

        Stealth features (updated 2026-05-27):
        - Chrome 126 user agent (current stable) — Chrome 120 was trivially
          detectable by Cloudflare as bot traffic from datacenter IPs
        - navigator.webdriver patched via --disable-blink-features
        - Realistic language, platform, and WebGL preferences
        - Randomized viewport to prevent fingerprint correlation

        Usage in any county scraper:
            co = self._get_browser_options()
            page = ChromiumPage(addr_or_opts=co)
        """
        import random
        from DrissionPage import ChromiumOptions
        co = ChromiumOptions()
        co.auto_port()
        co.headless(True)
        co.set_argument("--headless=new")
        co.set_argument("--ignore-certificate-errors")
        co.set_argument("--ignore-ssl-errors")

        # Lean Chromium (shared flags) — process-per-site off, low-end mode
        from scrapers.chromium_flags import apply_drission_memory_flags
        apply_drission_memory_flags(co)

        # ── Stealth: Anti-bot evasion ──
        # Disable automation detection flags
        co.set_argument("--disable-blink-features=AutomationControlled")
        # Randomized viewport to avoid fingerprint correlation across runs
        w = random.randint(1280, 1920)
        h = random.randint(800, 1080)
        co.set_argument(f"--window-size={w},{h}")
        # Realistic language/locale preferences
        co.set_argument("--lang=en-US")
        co.set_argument("--accept-lang=en-US,en;q=0.9")

        # Chrome 126 user agent (current stable as of 2026-05)
        # CRITICAL: Chrome 120 was from Dec 2023 — Cloudflare auto-blocks
        # ancient browser versions from datacenter IPs
        co.set_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )

        # Suppress "Chrome is being controlled by automated software" infobar
        co.set_argument("--disable-infobars")

        # Critical: set browser path for Docker where chromium lives at /usr/bin/chromium
        chrome_path = os.getenv("CHROME_PATH")
        if chrome_path:
            co.set_browser_path(chrome_path)
        return co

    @classmethod
    def _inject_stealth_js(cls, page):
        """
        Inject stealth JavaScript patches AFTER page creation but BEFORE
        navigating to target sites. Patches navigator.webdriver, plugins,
        and other fingerprint vectors that Cloudflare checks.

        Call this once after creating the ChromiumPage:
            page = ChromiumPage(addr_or_opts=co)
            cls._inject_stealth_js(page)
        """
        try:
            page.run_js("""
                // Patch navigator.webdriver (Cloudflare checks this)
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                // Patch navigator.plugins to look like a real browser
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                // Patch navigator.languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
                // Patch chrome.runtime to exist (headless Chrome lacks this)
                window.chrome = window.chrome || {};
                window.chrome.runtime = window.chrome.runtime || {};
                // Patch permissions API
                if (navigator.permissions) {
                    const originalQuery = navigator.permissions.query;
                    navigator.permissions.query = (parameters) =>
                        parameters.name === 'notifications'
                            ? Promise.resolve({ state: Notification.permission })
                            : originalQuery(parameters);
                }
            """)
        except Exception:
            pass  # Non-critical — some pages may not support JS injection

    # ── Obscura CDP Connection ────────────────────────────────────────────
    # Shared stealth browser for Cloudflare-protected counties.
    # Connects to the Obscura container via Chrome DevTools Protocol.
    # Usage:
    #   pw, browser = await self._get_obscura_browser()
    #   page = await browser.new_page()
    #   await page.goto("https://target.com")
    #   # ... scrape ...
    #   await browser.close()
    #   await pw.stop()

    OBSCURA_CDP_URL = os.getenv("OBSCURA_CDP_URL", "ws://obscura:9222")

    # Obscura routing policy (see scrapers/scraper_resilience.py):
    #   * always refused for fail_closed scopes and the hold/403 list — Obscura
    #     egresses through the office residential SOCKS proxy and must never be
    #     used to reopen a guarded or WAF-blocked source;
    #   * new routing only for verified_public labels explicitly listed in
    #     OBSCURA_ROUTE_COUNTIES (default: none);
    #   * pre-existing legacy callers on non-verified scopes keep working but
    #     log a warning so the decision stays visible.
    def _obscura_guard(self) -> None:
        label = self.county_label
        validated = bool(getattr(self, "SOURCE_CONTRACT_VALIDATED", True))
        source_state = _registry_source_state(label)
        refusal = obscura_hard_refusal(
            label, source_contract_validated=validated, source_state=source_state
        )
        if refusal:
            raise ObscuraRoutingRefused(
                f"Obscura refused for {label}: {refusal} (no-bypass rule)"
            )
        allowed, reason = obscura_route_decision(
            label, source_contract_validated=validated, source_state=source_state
        )
        if not allowed:
            logger.warning(
                "Obscura legacy use for %s outside routing policy (%s)", label, reason
            )

    def obscura_route_enabled(self) -> bool:
        """True only for verified_public scopes opted in via OBSCURA_ROUTE_COUNTIES."""
        allowed, _reason = obscura_route_decision(
            self.county_label,
            source_contract_validated=bool(getattr(self, "SOURCE_CONTRACT_VALIDATED", True)),
            source_state=_registry_source_state(self.county_label),
        )
        return allowed

    async def _get_obscura_browser(self):
        """
        Connect to the Obscura CDP server via Playwright.

        Returns:
            tuple: (playwright_instance, browser) — caller must close both.

        Raises:
            ObscuraRoutingRefused: scope is fail_closed or on the hold/403 list.
            ConnectionError: If Obscura container is unreachable.
        """
        self._obscura_guard()
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "playwright not installed. Run: pip install playwright"
            )

        pw = await async_playwright().__aenter__()
        try:
            browser = await pw.chromium.connect_over_cdp(self.OBSCURA_CDP_URL)
            logger.info("✅ Connected to Obscura CDP for %s", self.county_label)
            return pw, browser
        except Exception as e:
            await pw.__aexit__(None, None, None)
            raise ConnectionError(
                f"❌ Cannot connect to Obscura CDP: {type(e).__name__}"
            ) from e

    def _get_obscura_browser_sync(self):
        """
        Synchronous wrapper for _get_obscura_browser().
        For use in scrapers that don't use async/await.

        Returns:
            tuple: (playwright_instance, browser) — caller must close both.
        """
        self._obscura_guard()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "playwright not installed. Run: pip install playwright"
            )

        pw = sync_playwright().start()
        try:
            browser = pw.chromium.connect_over_cdp(self.OBSCURA_CDP_URL)
            logger.info("✅ Connected to Obscura CDP for %s", self.county_label)
            return pw, browser
        except Exception as e:
            pw.stop()
            raise ConnectionError(
                f"❌ Cannot connect to Obscura CDP: {type(e).__name__}"
            ) from e

    @classmethod
    def _check_disk_space(cls) -> dict:
        """
        Check disk space and auto-prune Docker if needed.

        Returns:
            dict with 'ok' (bool), 'percent_used', 'free_gb', 'action_taken'

        Self-healing behavior:
        - >75%: Log warning
        - >80%: Auto-prune Docker (dangling images, build cache, old browser profiles)
        - >95%: Block writes, raise error
        """
        try:
            usage = shutil.disk_usage('/')
            percent = (usage.used / usage.total) * 100
            free_gb = usage.free / (1024 ** 3)
            result = {
                'ok': True,
                'percent_used': round(percent, 1),
                'free_gb': round(free_gb, 2),
                'action_taken': None,
            }

            if percent >= cls.DISK_BLOCK_THRESHOLD:
                # Critical — attempt emergency prune then check again
                logger.critical(
                    f"🚨 DISK CRITICAL: {percent:.0f}% used ({free_gb:.1f}GB free) — "
                    f"attempting emergency prune"
                )
                cls._auto_prune_docker(aggressive=True)
                # Re-check after prune
                usage2 = shutil.disk_usage('/')
                percent2 = (usage2.used / usage2.total) * 100
                free_gb2 = usage2.free / (1024 ** 3)
                if percent2 >= cls.DISK_BLOCK_THRESHOLD:
                    result['ok'] = False
                    result['percent_used'] = round(percent2, 1)
                    result['free_gb'] = round(free_gb2, 2)
                    result['action_taken'] = 'emergency_prune_failed'
                    return result
                result['percent_used'] = round(percent2, 1)
                result['free_gb'] = round(free_gb2, 2)
                result['action_taken'] = 'emergency_prune_success'

            elif percent >= cls.DISK_PRUNE_THRESHOLD:
                logger.warning(
                    f"⚠️ DISK HIGH: {percent:.0f}% used ({free_gb:.1f}GB free) — "
                    f"auto-pruning Docker"
                )
                cls._auto_prune_docker(aggressive=False)
                # Re-check
                usage2 = shutil.disk_usage('/')
                result['percent_used'] = round((usage2.used / usage2.total) * 100, 1)
                result['free_gb'] = round(usage2.free / (1024 ** 3), 2)
                result['action_taken'] = 'auto_prune'

            elif percent >= cls.DISK_WARN_THRESHOLD:
                logger.info(
                    f"📊 Disk: {percent:.0f}% used ({free_gb:.1f}GB free)"
                )

            return result
        except Exception as e:
            logger.debug(f"Disk check failed: {e}")
            return {'ok': True, 'percent_used': 0, 'free_gb': 0, 'action_taken': None}

    @classmethod
    def _auto_prune_docker(cls, aggressive: bool = False):
        """
        Auto-prune Docker resources to free disk space.
        Called automatically when disk usage exceeds thresholds.
        """
        try:
            cmds = [
                ['docker', 'image', 'prune', '-f'],  # Dangling images
                ['docker', 'builder', 'prune', '-f', '--filter', 'until=72h'],  # Old build cache
            ]
            if aggressive:
                cmds = [
                    ['docker', 'builder', 'prune', '--all', '-f'],  # ALL build cache
                    ['docker', 'image', 'prune', '-a', '-f', '--filter', 'until=24h'],  # All unused images
                    ['docker', 'volume', 'prune', '-f'],  # Unused volumes
                ]

            for cmd in cmds:
                try:
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=60
                    )
                    if 'reclaimed' in result.stdout.lower():
                        logger.info(f"🧹 Prune: {result.stdout.strip().split(chr(10))[-1]}")
                except subprocess.TimeoutExpired:
                    pass
                except FileNotFoundError:
                    break  # Docker CLI not available (not running on Docker host)

            # Clean stale browser profiles
            drission_tmp = '/tmp/DrissionPage'
            if os.path.isdir(drission_tmp):
                import time
                now = time.time()
                cleaned = 0
                for item in os.listdir(drission_tmp):
                    item_path = os.path.join(drission_tmp, item)
                    try:
                        if os.stat(item_path).st_mtime < now - 3600:  # >1 hour old
                            shutil.rmtree(item_path, ignore_errors=True)
                            cleaned += 1
                    except OSError:
                        pass
                if cleaned:
                    logger.info(f"🧹 Cleaned {cleaned} stale DrissionPage profiles")

        except Exception as e:
            logger.debug(f"Auto-prune failed: {e}")

    @property
    @abstractmethod
    def county(self) -> str:
        """Return the county name (e.g., 'Lee', 'Charlotte')."""
        ...

    @abstractmethod
    def scrape(self) -> List[ArrestRecord]:
        """
        Fetch and parse arrest records.

        Returns:
            List of ArrestRecord instances. May be empty if no new records
            or if the source is temporarily unavailable.

        Raises:
            Any exception — handled by run().
        """
        ...

    def _check_for_poison_pill(self, html: str, url: str = "") -> bool:
        """
        Check if an HTTP response body is a WAF/CAPTCHA/block page.

        Returns True if the content is poisoned (not real data).
        Logs the detection details and notifies Slack on persistent blocks.

        Usage in county scrapers:
            html = page.html
            if self._check_for_poison_pill(html, url=roster_url):
                return []  # Abort — WAF intercepted the request
        """
        if not _poison_pill:
            return False
        result = _poison_pill.check_html(html, url=url)
        if result.is_poisoned:
            logger.warning(
                f"🛡️ {self.county}: Poison pill detected — "
                f"{result.detection_type} ({result.vendor}) "
                f"confidence={result.confidence:.0%}: {result.detail}"
            )
            if not result.should_retry:
                try:
                    _slack.notify_scraper_error(
                        self.county,
                        f"WAF/Anti-bot block: {result.detection_type} "
                        f"({result.vendor}) — {result.detail}"
                    )
                except Exception:
                    pass
            return True
        return False

    def _check_response_for_poison_pill(
        self, status_code: int, headers: dict, body: str, url: str = ""
    ) -> bool:
        """
        Check a full HTTP response (status + headers + body) for WAF/block indicators.

        Usage with requests library:
            resp = requests.get(url)
            if self._check_response_for_poison_pill(
                resp.status_code, dict(resp.headers), resp.text, url
            ):
                return []
        """
        if not _poison_pill:
            return False
        result = _poison_pill.check_response(status_code, headers, body, url)
        if result.is_poisoned:
            logger.warning(
                f"🛡️ {self.county}: Poison pill detected — "
                f"{result.detection_type} ({result.vendor}) "
                f"confidence={result.confidence:.0%}: {result.detail}"
            )
            if not result.should_retry:
                try:
                    _slack.notify_scraper_error(
                        self.county,
                        f"WAF/Anti-bot block: {result.detection_type} "
                        f"({result.vendor}) — {result.detail}"
                    )
                except Exception:
                    pass
            return True
        return False

    # ── Autonomous Proxy Engine (APE) Helpers ────────────────────────────────────
    
    def get_proxy(
        self,
        prefer_residential: bool = True,
        *,
        residential_only: bool = False,
    ) -> Optional[str]:
        """
        Get proxy from APE with automatic failover.

        Args:
            prefer_residential: Prefer residential proxies (Warren/S5W2C)
            residential_only: Exclude Stormsia free lists (WAF/CF counties)

        Returns:
            Proxy URL or None if all sources fail
        """
        if not self.ape:
            return None
        return self.ape.get_next_proxy(
            prefer_residential=prefer_residential,
            residential_only=residential_only,
        )

    def get_vendor_headers(self, vendor: str | None = None) -> dict[str, str]:
        """Get tailored HTTP headers for a given JMS vendor or scraper's vendor."""
        from scrapers.jms_headers import get_vendor_headers as gvh
        v_name = vendor or getattr(self, "jms_vendor", None) or getattr(self, "vendor", None) or ""
        return gvh(v_name)

    def get_sticky_proxy(
        self, session_id: str, *, residential_only: bool = False
    ) -> Optional[str]:
        """
        Get proxy with sticky session (same IP for multi-step flows).

        Args:
            session_id: Session identifier
            residential_only: Exclude Stormsia free lists

        Returns:
            Proxy URL with sticky routing
        """
        if not self.ape:
            return None
        return self.ape.get_sticky_proxy(
            session_id, residential_only=residential_only
        )
    
    def record_proxy_success(self, proxy: str, response_time_ms: float = 0.0):
        """
        Record successful proxy use in APE metrics.
        
        Args:
            proxy: Proxy URL
            response_time_ms: Response time in milliseconds
        """
        if self.ape and proxy:
            self.ape.record_success(proxy, response_time_ms=response_time_ms)
    
    def record_proxy_failure(self, proxy: str):
        """
        Record failed proxy use in APE metrics.
        
        Args:
            proxy: Proxy URL
        """
        if self.ape and proxy:
            self.ape.record_failure(proxy)
    
    @classmethod
    def _has_source_booking_identifier(cls, value: object) -> bool:
        """Return whether a booking key is present and not a known local fallback.

        Scraper modules must pass through identifiers supplied by their public
        source. A name-, date-, or hash-derived value is not a booking number and
        can merge different arrests; those rows are rejected before scoring,
        writing, notifications, or dashboard metrics.
        """
        booking = str(value or "").strip()
        return bool(booking) and not bool(cls._SYNTHETIC_BOOKING_RE.fullmatch(booking))

    @classmethod
    def _filter_records_without_source_booking(cls, records: List[ArrestRecord]) -> List[ArrestRecord]:
        """Fail closed on missing or known synthetic booking identifiers."""
        return [
            record for record in records
            if cls._has_source_booking_identifier(getattr(record, "Booking_Number", ""))
        ]

    @property
    def state(self) -> str:
        """Two-letter state code. Override for non-FL scrapers (GA, SC, etc)."""
        return "FL"

    @property
    def scraper_id(self) -> str:
        """Unique identifier for this scraper.

        FL keeps the legacy form ``scraper_<county>`` for dashboard compatibility.
        Other states use ``scraper_<st>_<county>`` so multi-state names (Lee, Sumter,
        etc.) do not collide.
        """
        # Strip periods so St. Johns → st_johns (not st._johns)
        county_slug = (
            self.county.lower()
            .replace(".", "")
            .replace(" ", "_")
            .replace("-", "_")
            .strip("_")
        )
        st = (self.state or "FL").upper()
        if st == "FL":
            return f"scraper_{county_slug}"
        return f"scraper_{st.lower()}_{county_slug}"

    @property
    def county_label(self) -> str:
        """Registry label ``County (ST)`` used by Health and SCRAPER_SOURCE_STATES."""
        return f"{self.county} ({(getattr(self, 'state', None) or 'FL').upper()})"

    def in_source_cooldown(self) -> bool:
        """True while a per-county cooldown is active (never retry into it).

        Default: no cooldown. Counties with a shared throttle (Lee's file-backed
        /32 window in ``scrapers/lee_rate_limit.py``) override this.
        """
        return False

    def _scrape_with_retry(self) -> List[ArrestRecord]:
        """scrape() wrapped in the BaseScraper transient-retry policy."""
        if not (getattr(self, "BASE_RETRY_ENABLED", True) and base_retry_enabled()):
            return self.scrape()

        def _log_retry(attempt, delay, exc, verdict):
            logger.warning(
                "🔁 %s: transient %s failure (retry %d/%d in %.0fs): %s",
                self.county_label,
                verdict.error_class,
                attempt,
                len(self.TRANSIENT_RETRY_DELAYS),
                delay,
                str(exc)[:200],
            )

        return retry_transient(
            self.scrape,
            delays=self.TRANSIENT_RETRY_DELAYS,
            sleep=self._retry_sleep,
            cooldown_active=self.in_source_cooldown,
            on_retry=_log_retry,
        )

    @staticmethod
    def _status_writer(writers: Optional[list]):
        for writer in (writers or []):
            if hasattr(writer, "upsert_scraper_status"):
                return writer
        return None

    def _load_resilience_state(self, status_writer) -> ResilienceState:
        """Persisted consecutive-failure / auto-disable state (Mongo scraper_status)."""
        getter = getattr(status_writer, "get_scraper_resilience", None) if status_writer else None
        if callable(getter):
            try:
                doc = getter(
                    county=self.county,
                    state=getattr(self, "state", None) or "FL",
                )
                state = ResilienceState.from_doc(doc)
                self._resilience_state = state
                return state
            except Exception as exc:
                logger.warning(
                    "⚠️ %s: could not load resilience state (%s) — using in-memory",
                    self.county_label,
                    type(exc).__name__,
                )
        return getattr(self, "_resilience_state", None) or ResilienceState()

    def _persist_status(self, status_writer, *, extra_fields: Optional[dict] = None, **kwargs) -> None:
        if status_writer is None:
            return
        try:
            status_writer.upsert_scraper_status(
                county=self.county,
                state=getattr(self, "state", None) or "FL",
                scraper_id=getattr(self, "scraper_id", None),
                extra_fields=extra_fields,
                **kwargs,
            )
        except Exception as exc:
            logger.warning(f"⚠️ {self.county}: scraper_status upsert failed: {exc}")

    def _alert_parse_drift(self, detail: str, severity: str) -> None:
        """Fail loudly: schema drift goes to #scraper-errors immediately."""
        logger.error("🧬 %s: parse/schema drift (%s): %s", self.county_label, severity, detail[:300])
        if not _drift_alert_throttle.allow((self.county_label, severity)):
            return
        try:
            _slack.notify_parse_drift(self.county_label, detail, severity=severity)
        except Exception:
            pass

    def run(self, writers: list = None, *, force_canary: bool = False) -> dict:
        """
        Execute the full scrape → score → write → alert pipeline.

        Args:
            writers: List of writer instances (MongoWriter, SheetsWriter, etc.)
            force_canary: Operator-requested run (dashboard Run Now / CLI). An
                auto-disabled scraper runs as a canary instead of being skipped.

        Self-healing contract (scrapers/scraper_resilience.py):
            * transient network failures retry 2s → 4s → 8s (never into a 429,
              anti-bot block, or active per-county cooldown);
            * every failure is classified network / anti_bot / url_changed /
              parse_drift / unknown and persisted to scraper_status;
            * schema drift alerts #scraper-errors immediately;
            * 5 consecutive counted failures → status ``auto_disabled``;
              re-enabled by a successful canary (records > 0) or manually.

        Returns:
            Combined statistics dict.
        """
        start = datetime.now(timezone.utc)
        self.total_runs += 1

        logger.info(f"{'═' * 50}")
        logger.info(f"🚦 Starting {self.county} County scraper (run #{self.total_runs})")
        logger.info(f"{'═' * 50}")

        # ── Preflight: source-contract guard ──
        # A job that lacks a proven public broad-listing contract must never make
        # a source request or reach the score/write/alert stages. This guard is
        # intentionally before disk, scrape, and all downstream activity.
        if not getattr(self, "SOURCE_CONTRACT_VALIDATED", True):
            reason = getattr(self, "SOURCE_CONTRACT_REASON", "") or (
                "No validated public source contract is available."
            )
            logger.warning(
                "%s %s fail closed before source fetch: %s",
                self.county,
                self.state,
                reason,
            )
            self.last_run = datetime.now(timezone.utc)
            self.last_error = reason
            if _dashboard_available:
                try:
                    update_scraper_status(
                        county=self.county,
                        records=0,
                        hot=0,
                        warm=0,
                        cold=0,
                        disqualified=0,
                        duration=0,
                        status="fail_closed",
                    )
                except Exception:
                    pass
            return {
                "county": self.county,
                "records_scraped": 0,
                "elapsed_seconds": 0,
                "source_contract_state": "fail_closed",
                "error": reason,
            }

        # ── Preflight: auto-disable gate ──
        status_writer = self._status_writer(writers)
        res_state = self._load_resilience_state(status_writer)
        gate = gate_decision(res_state, start, force=force_canary) if auto_disable_enabled() else "run"
        if gate == GATE_SKIP:
            reason = (
                f"auto-disabled after {res_state.consecutive_failures} consecutive failures "
                f"({res_state.last_error_class or 'unknown'}); waiting for canary window or manual re-enable"
            )
            logger.warning("⛔ %s: %s", self.county_label, reason)
            self.last_error = reason
            return {
                "county": self.county,
                "records_scraped": 0,
                "elapsed_seconds": 0,
                "status": "auto_disabled",
                "auto_disabled": True,
                "consecutive_failures": res_state.consecutive_failures,
                "error_class": res_state.last_error_class,
                "error": reason,
            }
        # With the kill switch off every run proceeds; a successful run of a
        # previously auto-disabled scraper is then treated like a canary.
        was_canary = gate == GATE_CANARY or (res_state.auto_disabled and not auto_disable_enabled())
        if was_canary:
            logger.info("🐤 %s: auto-disabled — running canary attempt", self.county_label)

        # ── Step 0: Disk space guard ──
        disk = self._check_disk_space()
        if not disk['ok']:
            msg = (
                f"Disk full: {disk['percent_used']}% used, {disk['free_gb']}GB free. "
                f"Auto-prune failed. Skipping write to prevent data corruption."
            )
            logger.error(f"🚨 {self.county}: {msg}")
            try:
                _slack.notify_scraper_error(self.county, msg)
            except Exception:
                pass
            return {
                "county": self.county,
                "records_scraped": 0,
                "elapsed_seconds": 0,
                "error": msg,
            }

        try:
            # ── Step 1: Scrape (BaseScraper transient retry: 2s, 4s, 8s) ──
            records = self._scrape_with_retry()
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()

            raw_record_count = len(records)
            records = self._filter_records_without_source_booking(records)
            if len(records) < raw_record_count:
                logger.warning(
                    "%s: dropped %d rows without a source-provided booking identifier",
                    self.county,
                    raw_record_count - len(records),
                )

            # ── Step 1b: Schema-drift check (fail loudly) ──
            drift = assess_schema_drift(raw_record_count, records)
            if drift is not None and drift.severity == "total":
                # Every row lost its source key: the parser no longer matches
                # the source. Counts as a parse_drift failure (alert + streak).
                raise ParseDriftError(f"schema drift: {drift.detail}")
            if drift is not None:
                self._alert_parse_drift(drift.detail, drift.severity)

            logger.info(
                f"✅ {self.county}: scraped {len(records)} verified-key records in {elapsed:.1f}s"
            )

            # ── Step 2: Score every record (rule-based) ──
            for record in records:
                if record.Lead_Score == 0:
                    _scorer.score_and_update(record)

            hot_count = sum(1 for r in records if r.Lead_Status == "Hot")
            warm_count = sum(1 for r in records if r.Lead_Status == "Warm")
            disqualified = sum(1 for r in records if r.Lead_Status == "Disqualified")

            logger.info(
                f"📊 {self.county}: Scored → "
                f"🔥 {hot_count} Hot | 🟡 {warm_count} Warm | "
                f"❌ {disqualified} Disqualified"
            )

            # ── Step 2b: Hybrid ML scoring + FTA risk overlay ──
            fta_high_count = 0
            if _hybrid_available:
                for record in records:
                    try:
                        record_dict = record.to_mongo_doc()
                        hybrid_result = _hybrid_score(record_dict)

                        # Store FTA risk data in extra_data for MongoDB persistence
                        if not record.extra_data:
                            record.extra_data = {}
                        record.extra_data["fta_risk_score"] = hybrid_result.get("fta_risk_score")
                        record.extra_data["fta_risk_level"] = hybrid_result.get("fta_risk_level")
                        record.extra_data["fta_risk_confidence"] = hybrid_result.get("fta_risk_confidence")
                        record.extra_data["scoring_method"] = hybrid_result.get("method", "rule_based")
                        record.extra_data["ml_score"] = hybrid_result.get("ml_score")

                        # If ML produced a higher-confidence score, upgrade the lead score
                        if hybrid_result.get("method") in ("ml", "hybrid") and hybrid_result.get("score"):
                            record.Lead_Score = hybrid_result["score"]
                            record.Lead_Status = hybrid_result["status"]

                        # Track FTA stats for logging
                        fta_level = hybrid_result.get("fta_risk_level")
                        if fta_level in ("high", "critical"):
                            fta_high_count += 1
                    except Exception as hybrid_err:
                        logger.debug(f"⚠️ Hybrid score failed for {record.Booking_Number}: {hybrid_err}")

                # Recount after potential ML upgrades
                hot_count = sum(1 for r in records if r.Lead_Status == "Hot")
                warm_count = sum(1 for r in records if r.Lead_Status == "Warm")
                disqualified = sum(1 for r in records if r.Lead_Status == "Disqualified")

                if fta_high_count > 0:
                    logger.info(
                        f"🧠 {self.county}: ML scoring complete — "
                        f"⚠️ {fta_high_count} high/critical FTA risk"
                    )

            self.total_records_scraped += len(records)
            self.last_run = datetime.now(timezone.utc)
            self.last_error = None

            # ── Step 3: Write to all configured writers ──
            combined_stats = {
                "county": self.county,
                "records_scraped": len(records),
                "hot_leads": hot_count,
                "warm_leads": warm_count,
                "disqualified": disqualified,
                "elapsed_seconds": round(elapsed, 1),
                "writer_results": [],
            }

            # Keep the county half of the natural key mandatory through write.
            before_filter = len(records)
            records = [r for r in records if (r.County or self.county or "").strip()]
            if len(records) < before_filter:
                logger.warning(
                    "⚠️ %s: dropped %d records missing county before write",
                    self.county,
                    before_filter - len(records),
                )
            # Ensure State is always set for multi-state dedup
            st = (getattr(self, "state", None) or "FL").upper()
            for r in records:
                if not (r.State or "").strip():
                    r.State = st
                if not (r.County or "").strip():
                    r.County = self.county

            if writers and records:
                for writer in writers:
                    try:
                        result = writer.write_records(records, self.county)
                        combined_stats["writer_results"].append(result)

                        # Use first writer's dedup stats for Slack
                        if "new_records" not in combined_stats:
                            combined_stats["new_records"] = result.get("new_records", 0)
                            combined_stats["duplicates_skipped"] = result.get("duplicates_skipped", 0)
                            combined_stats["qualified_records"] = result.get("qualified_records", 0)
                            combined_stats["total_records"] = result.get("total_records", len(records))

                    except Exception as write_err:
                        logger.error(
                            f"❌ {self.county}: writer {type(writer).__name__} "
                            f"failed: {write_err}"
                        )

            # Fill defaults if no writer provided stats
            if "new_records" not in combined_stats:
                combined_stats["new_records"] = len(records)
                combined_stats["duplicates_skipped"] = 0
                combined_stats["qualified_records"] = hot_count
                combined_stats["total_records"] = len(records)

            # ── Step 3a: Broadcast genuinely NEW arrests to the dashboard SSE feed ──
            # Previously only the Lee scraper pushed real-time events; all 200+
            # scrapers now emit uniformly so multi-state activity is live.
            try:
                new_indexes = None
                for _res in combined_stats["writer_results"]:
                    if isinstance(_res, dict) and _res.get("new_record_indexes") is not None:
                        new_indexes = _res["new_record_indexes"]
                        break
                if new_indexes is None:
                    # Fallback heuristic: records booked/arrested today
                    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    new_records = [
                        r for r in records
                        if (r.Arrest_Date or r.Booking_Date or "").startswith(today_str)
                    ]
                else:
                    new_records = [records[i] for i in new_indexes if 0 <= i < len(records)]
                if new_records:
                    self._broadcast_scraper_events(new_records)
            except Exception as broadcast_err:
                logger.debug(f"⚠️ {self.county}: SSE broadcast skipped: {broadcast_err}")

            # ── Step 3b: Check for repeat offenders ──
            try:
                from writers.rearrest_checker import RearrestChecker
                _rearrest = RearrestChecker()
                rearrest_results = _rearrest.check_batch(records, self.county)
                if rearrest_results["matches_found"] > 0:
                    logger.info(
                        f"🚨 {self.county}: {rearrest_results['matches_found']} "
                        f"repeat offender(s) detected!"
                    )
                    combined_stats["rearrest_matches"] = rearrest_results["matches_found"]
                _rearrest.close()
            except Exception as rearrest_err:
                logger.debug(f"⚠️ Rearrest check skipped: {rearrest_err}")

            # ── Step 4: Slack alerts ──
            try:
                _slack.notify_new_arrests(records, self.county, combined_stats)

                # Individual hot lead alerts for high-value bonds
                for record in records:
                    if record.Lead_Status == "Hot" and record._parse_bond_numeric() >= 2500:
                        _slack.notify_hot_lead(record)
            except Exception as slack_err:
                logger.warning(f"⚠️ Slack notification failed: {slack_err}")


            # ── Step 5: Update dashboard (in-memory Flask, legacy) ──
            # Accurate fleet metrics: "ok" only when we actually ingested rows.
            # Soft-empty runs (stubs, empty jail, blocked upstream that return [])
            # use status="empty" so Scraper Health does not inflate Active/Healthy.
            cold_count = sum(1 for r in records if r.Lead_Status == "Cold")
            run_status = "ok" if len(records) > 0 else "empty"
            if _dashboard_available:
                try:
                    update_scraper_status(
                        county=self.county,
                        records=len(records),
                        hot=hot_count,
                        warm=warm_count,
                        cold=cold_count,
                        disqualified=disqualified,
                        duration=elapsed,
                        status=run_status,
                    )
                except Exception:
                    pass

            # ── Step 5b: Persist run status + resilience state (scraper_status) ──
            new_state, reenabled = state_after_success(
                res_state, len(records), datetime.now(timezone.utc), was_canary=was_canary
            )
            self._resilience_state = new_state
            persisted_status = "auto_disabled" if new_state.auto_disabled else run_status
            extra = new_state.to_fields()
            if reenabled:
                extra.update({
                    "reenabled_at": datetime.now(timezone.utc),
                    "reenabled_by": "canary",
                })
            self._persist_status(
                status_writer,
                records=len(records),
                hot=hot_count,
                warm=warm_count,
                cold=cold_count,
                disqualified=disqualified,
                duration=elapsed,
                status=persisted_status,
                error=(
                    "auto-disabled: canary ran but returned 0 records"
                    if new_state.auto_disabled else None
                ),
                extra_fields=extra,
            )
            if reenabled:
                logger.info("✅ %s: canary succeeded — auto-disable cleared", self.county_label)
                try:
                    _slack.notify_scraper_reenabled(
                        self.county_label, how="canary", records=len(records)
                    )
                except Exception:
                    pass

            combined_stats["status"] = persisted_status
            combined_stats["consecutive_failures"] = new_state.consecutive_failures
            if reenabled:
                combined_stats["reenabled"] = True
            return combined_stats

        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            self.last_error = str(e)
            verdict = classify_exception(e)
            label = self.county_label
            logger.error(
                f"❌ {self.county}: scraper failed after {elapsed:.1f}s "
                f"[{verdict.error_class}] — {e}"
            )

            now = datetime.now(timezone.utc)
            threshold = auto_disable_threshold()
            exempt = label in AUTO_DISABLE_EXEMPT_LABELS
            new_state, tripped = state_after_failure(
                res_state,
                verdict,
                now,
                threshold=threshold,
                exempt=exempt,
                was_canary=was_canary,
                reason=f"{verdict.error_class}: {str(e)[:250]}",
            )
            self._resilience_state = new_state
            persisted_status = "auto_disabled" if new_state.auto_disabled else "error"

            # Log to self-hosted error tracker (MongoDB)
            if _error_tracker:
                try:
                    _error_tracker.log_error(
                        source=f"scraper.{self.county}",
                        message=str(e),
                        details={
                            "elapsed": elapsed,
                            "county": self.county,
                            "county_label": label,
                            "error_class": verdict.error_class,
                            "consecutive_failures": new_state.consecutive_failures,
                        },
                        # SlackNotifier below sends the single classified alert;
                        # avoid a duplicate generic post to the same channel.
                        alert_slack=False,
                    )
                except Exception:
                    pass

            # Alert (Slack #scraper-errors). Schema drift gets its own loud
            # alert; a failed canary on an already-disabled scraper stays quiet
            # (it was alerted when it tripped).
            try:
                if verdict.error_class == ERROR_PARSE_DRIFT:
                    self._alert_parse_drift(str(e), "exception")
                elif not (was_canary and not tripped):
                    _slack.notify_scraper_error(
                        label,
                        str(e),
                        error_class=verdict.error_class,
                        consecutive_failures=new_state.consecutive_failures,
                    )
                if tripped:
                    _slack.notify_scraper_auto_disabled(
                        label,
                        failures=new_state.consecutive_failures,
                        error_class=verdict.error_class,
                        last_error=str(e),
                    )
                elif exempt and verdict.counts_toward_disable and new_state.consecutive_failures == threshold:
                    _slack.notify_scraper_auto_disabled(
                        label,
                        failures=new_state.consecutive_failures,
                        error_class=verdict.error_class,
                        last_error=str(e),
                        exempt=True,
                    )
            except Exception:
                pass

            # Real-time dashboard alert (SSE) — frontend listens for 'scraper_error'
            try:
                self._post_dashboard_event("scraper_error", {
                    "county": self.county,
                    "state": (getattr(self, "state", None) or "FL"),
                    "county_label": label,
                    "scraper_id": getattr(self, "scraper_id", None),
                    "error": str(e)[:300],
                    "error_class": verdict.error_class,
                    "consecutive_failures": new_state.consecutive_failures,
                    "auto_disabled": new_state.auto_disabled,
                })
            except Exception:
                pass

            # Update dashboard with error status (in-memory Flask, legacy)
            if _dashboard_available:
                try:
                    update_scraper_status(
                        county=self.county, records=0, hot=0, warm=0,
                        duration=elapsed, status=persisted_status, error=str(e),
                    )
                except Exception:
                    pass

            # Persist error + resilience state to MongoDB scraper_status
            extra = new_state.to_fields()
            extra.update({
                "last_failure_at": now,
                "error_class": verdict.error_class,
                "cooldown_active": verdict.cooldown,
            })
            self._persist_status(
                status_writer,
                records=0, hot=0, warm=0,
                duration=elapsed, status=persisted_status, error=str(e),
                extra_fields=extra,
            )

            return {
                "county": self.county,
                "records_scraped": 0,
                "elapsed_seconds": round(elapsed, 1),
                "status": persisted_status,
                "error": str(e),
                "error_class": verdict.error_class,
                "consecutive_failures": new_state.consecutive_failures,
                "auto_disabled": new_state.auto_disabled,
            }

    # ── Real-time dashboard event relay ────────────────────────────────────────

    def _post_dashboard_event(self, event_type: str, payload: dict) -> None:
        """POST a domain event to the dashboard's scraper-event webhook.

        The webhook (dashboard/routers/webhooks.py) republishes to the SSE
        stream so the Command Center receives live toasts / activity items.
        Fails silently — never let a UI notification break the scrape.
        """
        api_key = os.getenv("GAS_API_KEY", "")
        if not api_key:
            return
        webhook_url = (
            os.getenv("DASHBOARD_INTERNAL_URL", "http://127.0.0.1:5050")
            + "/api/webhooks/scraper-event"
        )
        try:
            import requests as _rq
            _rq.post(
                webhook_url,
                json={"event_type": event_type, "payload": payload},
                headers={"X-Api-Key": api_key},
                timeout=2,
            )
        except Exception as exc:
            logger.debug(f"⚠️ {self.county}: dashboard event relay failed: {exc}")

    def _broadcast_scraper_events(self, new_records: list) -> None:
        """Emit new_arrest (and hot_lead) SSE events for genuinely new records.

        Called from run() after writers persist. Caps volume so a first-run
        backfill of hundreds of records doesn't flood the activity feed.
        """
        st = (getattr(self, "state", None) or "FL")
        for record in new_records[:5]:
            self._post_dashboard_event("new_arrest", {
                "county": self.county,
                "state": st,
                "county_label": f"{self.county} ({st})",
                "full_name": record.Full_Name,
                "booking_number": record.Booking_Number,
                "charges": record.Charges,
                "bond_amount": record.Bond_Amount,
                "mugshot_url": record.Mugshot_URL,
                "lead_status": record.Lead_Status,
                "lead_score": record.Lead_Score,
            })
        # Hot leads get their own event type (frontend badge + toast)
        hot = [r for r in new_records if r.Lead_Status == "Hot"]
        for record in hot[:3]:
            self._post_dashboard_event("hot_lead", {
                "county": self.county,
                "state": st,
                "county_label": f"{self.county} ({st})",
                "full_name": record.Full_Name,
                "booking_number": record.Booking_Number,
                "charges": record.Charges,
                "bond_amount": record.Bond_Amount,
                "lead_score": record.Lead_Score,
            })

    def health_check(self) -> dict:
        """Return scraper health status."""
        return {
            "scraper_id": self.scraper_id,
            "county": self.county,
            "total_runs": self.total_runs,
            "total_records": self.total_records_scraped,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_error": self.last_error,
            "healthy": self.last_error is None,
            "consecutive_failures": getattr(self, "_resilience_state", ResilienceState()).consecutive_failures,
            "auto_disabled": getattr(self, "_resilience_state", ResilienceState()).auto_disabled,
        }
