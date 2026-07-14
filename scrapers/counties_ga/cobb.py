"""
Cobb County (GA) Arrest Scraper.
Custom HTML scraper for http://inmate-search.cobbsheriff.org
"""
import logging
import time
import requests
from bs4 import BeautifulSoup
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

class CobbScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Cobb"
        
    @property
    def state(self) -> str:
        return "GA"

    def scrape(self) -> List[ArrestRecord]:
        start_time = time.time()
        url = "http://inmate-search.cobbsheriff.org/inquiry.asp?inmate_name=&serial="
        logger.info(f"📥 Fetching Cobb County roster at {url}")
        
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
            tables = soup.find_all('table')
            
            if not tables:
                logger.warning("No tables found in Cobb response")
                return []
                
            records = []
            # In the basic test we saw 0 tables with empty search, meaning it might require a letter.
            # We'll do an alphabet search if empty fails.
            return records
            
        except Exception as e:
            logger.error(f"Error scraping {self.county}: {e}", exc_info=True)
            return []
