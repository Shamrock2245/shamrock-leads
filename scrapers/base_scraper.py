"""
Base Scraper — Abstract interface all county scrapers implement.

Every county scraper inherits from BaseScraper and implements:
- scrape() → List[ArrestRecord]
- county property

The scheduler calls run() which handles:
1. Pre-flight URL health check (detect 404/403/SSL before scraping)
2. Scraping with retry + exponential backoff
3. Lead scoring (auto-score every record)
4. Writing to MongoDB/Sheets
5. Slack notifications (hot leads + summaries)
6. Self-healing: auto-disable after consecutive failures
"""

import logging
import time
import requests as http_requests
from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime, timezone

from core.models import ArrestRecord
from scoring.lead_scorer import LeadScorer
from writers.slack_notifier import SlackNotifier

logger = logging.getLogger(__name__)

# Shared instances (initialized once)
_scorer = LeadScorer()
_slack = SlackNotifier()

# ── Self-Heal Constants ──
MAX_CONSECUTIVE_FAILURES = 5
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds: 2, 4, 8


class BaseScraper(ABC):
    """
    Abstract base class for all county scrapers.

    Subclasses must implement:
    - county (property): Return the county name (e.g., "Lee")
    - scrape(): Fetch and parse arrest records from the county source

    The run() method wraps scrape() with error handling, timing,
    lead scoring, writer integration, Slack alerts, and self-healing.

    Self-Healing Features:
    - Pre-flight URL health check (optional, override `roster_url`)
    - Retry with exponential backoff on transient failures
    - Auto-disable after MAX_CONSECUTIVE_FAILURES (5)
    - Failure classification (network, anti-bot, url_changed, parse_error)
    - Diagnostic logging for agent retrospectives
    """

    def __init__(self):
        self.last_run: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.last_success: Optional[datetime] = None
        self.total_runs: int = 0
        self.total_records_scraped: int = 0
        self.consecutive_failures: int = 0
        self.is_disabled: bool = False
        self.failure_history: list = []  # Last 10 failures for diagnosis

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

    @property
    def roster_url(self) -> Optional[str]:
        """
        Override in subclass to enable pre-flight URL health checks.
        Return the base URL of the county jail roster.
        """
        return None

    @property
    def scraper_id(self) -> str:
        """Unique identifier for this scraper."""
        return f"scraper_{self.county.lower().replace(' ', '_')}"

    # ── Self-Healing: URL Health Check ──
    def _preflight_check(self) -> dict:
        """
        Pre-flight health check on the roster URL.
        Returns: {"healthy": bool, "status_code": int, "error_type": str}
        """
        url = self.roster_url
        if not url:
            return {"healthy": True, "status_code": 0, "error_type": None}

        try:
            resp = http_requests.head(
                url,
                timeout=10,
                allow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )

            if resp.status_code == 200:
                return {"healthy": True, "status_code": 200, "error_type": None}

            error_type = self._classify_http_error(resp.status_code)
            logger.warning(
                f"⚠️ {self.county} preflight: HTTP {resp.status_code} → {error_type}"
            )
            return {
                "healthy": False,
                "status_code": resp.status_code,
                "error_type": error_type,
            }

        except http_requests.exceptions.SSLError:
            return {"healthy": False, "status_code": 0, "error_type": "ssl_error"}
        except http_requests.exceptions.ConnectionError:
            return {"healthy": False, "status_code": 0, "error_type": "connection_error"}
        except http_requests.exceptions.Timeout:
            return {"healthy": False, "status_code": 0, "error_type": "timeout"}
        except Exception as e:
            return {"healthy": False, "status_code": 0, "error_type": f"unknown: {e}"}

    @staticmethod
    def _classify_http_error(status_code: int) -> str:
        """Classify an HTTP error into a self-heal category."""
        if status_code == 403:
            return "anti_bot"
        elif status_code == 404:
            return "url_changed"
        elif status_code in (301, 302, 308):
            return "redirect"
        elif status_code == 429:
            return "rate_limited"
        elif status_code >= 500:
            return "server_error"
        return f"http_{status_code}"

    @staticmethod
    def _classify_exception(e: Exception) -> str:
        """Classify a Python exception into a self-heal category."""
        ename = type(e).__name__
        msg = str(e).lower()

        if "ssl" in ename.lower() or "certificate" in msg:
            return "ssl_error"
        elif "connection" in ename.lower() or "timeout" in msg:
            return "network"
        elif "captcha" in msg or "403" in msg:
            return "anti_bot"
        elif "404" in msg or "not found" in msg:
            return "url_changed"
        elif ename in ("KeyError", "IndexError", "AttributeError"):
            return "parse_error"
        elif "json" in ename.lower():
            return "response_format_changed"
        return "unknown"

    def _record_failure(self, error: str, error_type: str):
        """Record a failure for self-healing diagnosis."""
        self.consecutive_failures += 1
        self.last_error = error

        failure_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "county": self.county,
            "error": error[:200],  # Truncate for storage
            "error_type": error_type,
            "consecutive": self.consecutive_failures,
        }
        self.failure_history.append(failure_entry)
        if len(self.failure_history) > 10:
            self.failure_history = self.failure_history[-10:]

        # Auto-disable after too many consecutive failures
        if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self.is_disabled = True
            logger.critical(
                f"🚫 {self.county}: AUTO-DISABLED after {self.consecutive_failures} "
                f"consecutive failures. Last error type: {error_type}. "
                f"Manual intervention required."
            )

    def _record_success(self):
        """Reset failure counters on success."""
        self.consecutive_failures = 0
        self.last_error = None
        self.last_success = datetime.now(timezone.utc)
        # Re-enable if it was auto-disabled and now works
        if self.is_disabled:
            self.is_disabled = False
            logger.info(f"✅ {self.county}: AUTO-RE-ENABLED after successful run")

    def run(self, writers: list = None) -> dict:
        """
        Execute the full scrape → score → write → alert pipeline.

        Self-healing features:
        - Pre-flight URL check before scraping
        - Retry with exponential backoff (up to 3 attempts)
        - Failure classification and recording
        - Auto-disable after 5 consecutive failures

        Args:
            writers: List of writer instances (MongoWriter, SheetsWriter, etc.)

        Returns:
            Combined statistics dict.
        """
        start = datetime.now(timezone.utc)
        self.total_runs += 1

        # ── Guard: Auto-disabled scraper ──
        if self.is_disabled:
            logger.warning(
                f"🚫 {self.county}: Scraper is auto-disabled "
                f"({self.consecutive_failures} consecutive failures). "
                f"Attempting recovery..."
            )
            # Try one recovery attempt
            preflight = self._preflight_check()
            if not preflight["healthy"]:
                logger.error(
                    f"🚫 {self.county}: Recovery failed — "
                    f"{preflight['error_type']}. Still disabled."
                )
                return {
                    "county": self.county,
                    "records_scraped": 0,
                    "elapsed_seconds": 0,
                    "error": f"Auto-disabled: {preflight['error_type']}",
                    "status": "disabled",
                }
            # URL is back — re-enable and continue
            logger.info(f"✅ {self.county}: URL responsive, attempting re-enable...")

        logger.info(f"{'═' * 50}")
        logger.info(f"🚦 Starting {self.county} County scraper (run #{self.total_runs})")
        logger.info(f"{'═' * 50}")

        # ── Pre-flight Check ──
        preflight = self._preflight_check()
        if not preflight["healthy"] and preflight["error_type"] == "url_changed":
            error_msg = f"Pre-flight failed: {preflight['error_type']} (HTTP {preflight['status_code']})"
            self._record_failure(error_msg, preflight["error_type"])
            try:
                _slack.notify_scraper_error(self.county, error_msg)
            except:
                pass
            return {
                "county": self.county,
                "records_scraped": 0,
                "elapsed_seconds": 0,
                "error": error_msg,
                "error_type": preflight["error_type"],
            }

        # ── Retry Loop ──
        last_exception = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # ── Step 1: Scrape ──
                records = self.scrape()
                elapsed = (datetime.now(timezone.utc) - start).total_seconds()

                logger.info(
                    f"✅ {self.county}: scraped {len(records)} records in {elapsed:.1f}s"
                    + (f" (attempt {attempt})" if attempt > 1 else "")
                )

                # ── Step 2: Score every record ──
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

                self.total_records_scraped += len(records)
                self.last_run = datetime.now(timezone.utc)
                self._record_success()

                # ── Step 3: Write to all configured writers ──
                combined_stats = {
                    "county": self.county,
                    "records_scraped": len(records),
                    "hot_leads": hot_count,
                    "warm_leads": warm_count,
                    "disqualified": disqualified,
                    "elapsed_seconds": round(elapsed, 1),
                    "writer_results": [],
                    "attempt": attempt,
                    "status": "success",
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

                # ── Step 4: Slack alerts ──
                try:
                    _slack.notify_new_arrests(records, self.county, combined_stats)

                    # Individual hot lead alerts for high-value bonds
                    for record in records:
                        if record.Lead_Status == "Hot" and record._parse_bond_numeric() >= 2500:
                            _slack.notify_hot_lead(record)
                except Exception as slack_err:
                    logger.warning(f"⚠️ Slack notification failed: {slack_err}")

                return combined_stats

            except Exception as e:
                last_exception = e
                error_type = self._classify_exception(e)
                elapsed = (datetime.now(timezone.utc) - start).total_seconds()

                if attempt < MAX_RETRIES:
                    backoff = RETRY_BACKOFF_BASE ** attempt
                    logger.warning(
                        f"⚠️ {self.county}: attempt {attempt}/{MAX_RETRIES} failed "
                        f"({error_type}: {e}). Retrying in {backoff}s..."
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        f"❌ {self.county}: all {MAX_RETRIES} attempts failed "
                        f"after {elapsed:.1f}s — {error_type}: {e}"
                    )

        # All retries exhausted
        error_type = self._classify_exception(last_exception)
        self._record_failure(str(last_exception), error_type)

        # Alert on scraper failure
        try:
            _slack.notify_scraper_error(
                self.county,
                f"[{error_type}] {last_exception} "
                f"(consecutive failures: {self.consecutive_failures})"
            )
        except:
            pass

        return {
            "county": self.county,
            "records_scraped": 0,
            "elapsed_seconds": round(
                (datetime.now(timezone.utc) - start).total_seconds(), 1
            ),
            "error": str(last_exception),
            "error_type": error_type,
            "consecutive_failures": self.consecutive_failures,
            "status": "disabled" if self.is_disabled else "failed",
        }

    def health_check(self) -> dict:
        """Return scraper health status with self-healing diagnostics."""
        return {
            "scraper_id": self.scraper_id,
            "county": self.county,
            "total_runs": self.total_runs,
            "total_records": self.total_records_scraped,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "is_disabled": self.is_disabled,
            "healthy": self.last_error is None and not self.is_disabled,
            "recent_failures": self.failure_history[-3:] if self.failure_history else [],
        }

    def force_enable(self):
        """Manually re-enable a disabled scraper (human override)."""
        self.is_disabled = False
        self.consecutive_failures = 0
        self.last_error = None
        logger.info(f"✅ {self.county}: Manually re-enabled by operator")
