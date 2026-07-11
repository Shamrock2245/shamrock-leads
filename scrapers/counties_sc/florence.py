"""
Florence County (SC) Arrest Scraper.
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

class FlorenceScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Florence"
        
    @property
    def portal_url(self) -> str:
        return "https://www.fcso.org/inmateSearchFeed"

    def scrape(self) -> List[ArrestRecord]:
        records = []
        try:
            self.logger.info("Scraper stub loaded. Needs custom parser implementation.")
        except Exception as e:
            self.logger.error(f"Error scraping {self.portal_url}: {str(e)}")
            
        return records
