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
from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime, timezone

from core.models import ArrestRecord
from scoring.lead_scorer import LeadScorer

# Hybrid scorer: ML + rule-based blending + FTA risk overlay
try:
    from scoring.hybrid_scorer import hybrid_score as _hybrid_score
    _hybrid_available = True
except ImportError:
    _hybrid_available = False

try:
    from dashboard.server import update_scraper_status
    _dashboard_available = True
except ImportError:
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
except ImportError:
    _error_tracker = None

logger = logging.getLogger(__name__)

# Shared instances (initialized once)
_scorer = LeadScorer()
_slack = SlackNotifier()


class BaseScraper(ABC):
    """
    Abstract base class for all county scrapers.

    Subclasses must implement:
    - county (property): Return the county name (e.g., "Lee")
    - scrape(): Fetch and parse arrest records from the county source

    The run() method wraps scrape() with error handling, timing,
    lead scoring, writer integration, and Slack alerts.
    """

    def __init__(self):
        self.last_run: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.total_runs: int = 0
        self.total_records_scraped: int = 0

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
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-gpu")
        co.set_argument("--ignore-certificate-errors")
        co.set_argument("--ignore-ssl-errors")

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

        # Chrome memory optimization and crash mitigation for Docker
        co.set_argument("--disable-background-networking")
        co.set_argument("--disable-background-timer-throttling")
        co.set_argument("--disable-backgrounding-occluded-windows")
        co.set_argument("--disable-breakpad")
        co.set_argument("--disable-component-update")
        co.set_argument("--disable-domain-reliability")
        co.set_argument("--disable-hang-monitor")
        co.set_argument("--disable-ipc-flooding-protection")
        co.set_argument("--disable-renderer-backgrounding")
        co.set_argument("--disable-sync")
        # Suppress "Chrome is being controlled by automated software" infobar
        co.set_argument("--disable-infobars")
        co.set_argument("--disable-extensions")

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

    @property
    def scraper_id(self) -> str:
        """Unique identifier for this scraper."""
        return f"scraper_{self.county.lower().replace(' ', '_')}"

    def run(self, writers: list = None) -> dict:
        """
        Execute the full scrape → score → write → alert pipeline.

        Args:
            writers: List of writer instances (MongoWriter, SheetsWriter, etc.)

        Returns:
            Combined statistics dict.
        """
        start = datetime.now(timezone.utc)
        self.total_runs += 1

        logger.info(f"{'═' * 50}")
        logger.info(f"🚦 Starting {self.county} County scraper (run #{self.total_runs})")
        logger.info(f"{'═' * 50}")

        try:
            # ── Step 1: Scrape ──
            records = self.scrape()
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()

            logger.info(
                f"✅ {self.county}: scraped {len(records)} records in {elapsed:.1f}s"
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
            cold_count = sum(1 for r in records if r.Lead_Status == "Cold")
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
                        status="ok",
                    )
                except Exception:
                    pass

            # ── Step 5b: Persist run status to MongoDB scraper_status collection ──
            for _writer in (writers or []):
                if hasattr(_writer, 'upsert_scraper_status'):
                    try:
                        _writer.upsert_scraper_status(
                            county=self.county,
                            records=len(records),
                            hot=hot_count,
                            warm=warm_count,
                            cold=cold_count,
                            disqualified=disqualified,
                            duration=elapsed,
                            status="ok",
                        )
                    except Exception as _e:
                        logger.warning(f"⚠️ {self.county}: scraper_status upsert failed: {_e}")
                    break  # Only need one writer to persist status

            return combined_stats

        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            self.last_error = str(e)
            logger.error(f"❌ {self.county}: scraper failed after {elapsed:.1f}s — {e}")

            # Log to self-hosted error tracker (MongoDB)
            if _error_tracker:
                try:
                    _error_tracker.log_error(
                        source=f"scraper.{self.county}",
                        message=str(e),
                        details={"elapsed": elapsed, "county": self.county},
                    )
                except Exception:
                    pass

            # Alert on scraper failure (Slack)
            try:
                _slack.notify_scraper_error(self.county, str(e))
            except Exception:
                pass


            # Update dashboard with error status (in-memory Flask, legacy)
            if _dashboard_available:
                try:
                    update_scraper_status(
                        county=self.county, records=0, hot=0, warm=0,
                        duration=elapsed, status="error", error=str(e),
                    )
                except Exception:
                    pass

            # Persist error status to MongoDB scraper_status collection
            for _writer in (writers or []):
                if hasattr(_writer, 'upsert_scraper_status'):
                    try:
                        _writer.upsert_scraper_status(
                            county=self.county, records=0, hot=0, warm=0,
                            duration=elapsed, status="error", error=str(e),
                        )
                    except Exception:
                        pass
                    break

            return {
                "county": self.county,
                "records_scraped": 0,
                "elapsed_seconds": round(elapsed, 1),
                "error": str(e),
            }

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
        }
