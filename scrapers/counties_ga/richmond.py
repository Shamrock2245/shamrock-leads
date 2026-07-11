"""
Richmond County (GA) Arrest Scraper.
Custom HTML scraper for ColdFusion inmate-inquiry.cfm
"""
import logging
import time
from typing import List
import requests
from bs4 import BeautifulSoup
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

class RichmondScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Richmond"
        
    def scrape(self) -> List[ArrestRecord]:
        start_time = time.time()
        url = "https://www.richmondcountysheriffsoffice.com/inmate-inquiry.cfm"
        logger.info(f"📥 Fetching Richmond County roster at {url}")
        
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            
            # They use a ColdFusion POST form. We'll send an empty search to get all recent.
            # In ColdFusion, sometimes we need to hit the GET first to establish session.
            resp = session.get(url, timeout=15)
            
            payload = {
                'LastName': '',
                'FirstName': '',
                'Search': 'Search'
            }
            
            resp2 = session.post(url, data=payload, timeout=20)
            if resp2.status_code != 200:
                logger.error(f"Search POST failed: HTTP {resp2.status_code}")
                return []
                
            soup = BeautifulSoup(resp2.text, 'html.parser')
            tables = soup.find_all('table')
            
            # Logic to extract rows will go here
            # Placeholder for now until we can see the exact table structure
            
            records = []
            return records
            
        except Exception as e:
            logger.error(f"Error scraping {self.county}: {e}", exc_info=True)
            return []
