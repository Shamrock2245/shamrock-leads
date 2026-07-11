"""
Lowndes County (GA) Arrest Scraper.
Uses Tyler Odyssey platform.
"""

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
import requests
import time
import logging

logger = logging.getLogger(__name__)

class LowndesScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Lowndes"

    def scrape(self) -> list[ArrestRecord]:
        # Implementation would use Odyssey base class, stubbed for now
        logger.info("Lowndes scraper initialized (Odyssey portal)")
        return []
