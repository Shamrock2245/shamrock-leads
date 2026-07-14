"""
Glynn County (GA) Arrest Scraper.
Custom HTML scraper.
"""
import logging
import time
from typing import List
import requests
from bs4 import BeautifulSoup
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

class GlynnScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Glynn"
        
    @property
    def state(self) -> str:
        return "GA"

    def scrape(self) -> List[ArrestRecord]:
        start_time = time.time()
        url = "https://glynncountysheriff.org/inmate-search"
        logger.info(f"📥 Fetching Glynn County roster at {url}")
        
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            
            resp = session.get(url, timeout=15)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch {url}: HTTP {resp.status_code}")
                return []
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Logic to extract rows will go here
            # Placeholder for now until we can see the exact table structure
            
            records = []
            return records
            
        except Exception as e:
            logger.error(f"Error scraping {self.county}: {e}", exc_info=True)
            return []
