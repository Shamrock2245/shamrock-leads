"""
Base Scraper — Abstract interface all county scrapers implement.

Every county scraper inherits from BaseScraper and implements:
- scrape() → List[ArrestRecord]
- county property

The scheduler calls run() which handles logging, error handling, and writing.
"""

import logging
from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime, timezone

from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """
    Abstract base class for all county scrapers.

    Subclasses must implement:
    - county (property): Return the county name (e.g., "Lee")
    - scrape(): Fetch and parse arrest records from the county source

    The run() method wraps scrape() with error handling, timing, and
    writer integration.
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
        Execute scrape + write pipeline.

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
            records = self.scrape()
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()

            logger.info(
                f"✅ {self.county}: scraped {len(records)} records in {elapsed:.1f}s"
            )

            self.total_records_scraped += len(records)
            self.last_run = datetime.now(timezone.utc)
            self.last_error = None

            # Write to all configured writers
            combined_stats = {
                "county": self.county,
                "records_scraped": len(records),
                "elapsed_seconds": round(elapsed, 1),
                "writer_results": [],
            }

            if writers and records:
                for writer in writers:
                    try:
                        result = writer.write_records(records, self.county)
                        combined_stats["writer_results"].append(result)
                    except Exception as write_err:
                        logger.error(
                            f"❌ {self.county}: writer {type(writer).__name__} "
                            f"failed: {write_err}"
                        )

            return combined_stats

        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            self.last_error = str(e)
            logger.error(f"❌ {self.county}: scraper failed after {elapsed:.1f}s — {e}")
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
