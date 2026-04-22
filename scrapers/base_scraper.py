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
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            self.last_error = str(e)
            logger.error(f"❌ {self.county}: scraper failed after {elapsed:.1f}s — {e}")

            # Alert on scraper failure
            try:
                _slack.notify_scraper_error(self.county, str(e))
            except:
                pass

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
