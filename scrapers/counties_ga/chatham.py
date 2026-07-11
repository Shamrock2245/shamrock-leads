"""
Chatham County (GA) Arrest Scraper.
Custom HTML table parsing for Savannah area.
"""

import logging
import time
from typing import List

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

class ChathamScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Chatham"

    def scrape(self) -> List[ArrestRecord]:
        start_time = time.time()
        # days=1 gets last 24 hours, days=7 gets last week
        url = "https://sheriff.chathamcountyga.gov/Corrections/Bookings?days=1"
        
        logger.info(f"📥 Fetching Chatham roster at {url}")
        
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            resp = requests.get(url, headers=headers, timeout=15)
            
            if resp.status_code != 200:
                logger.error(f"Failed to fetch {url}: HTTP {resp.status_code}")
                return []
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            table = soup.find('table', class_='table')
            
            if not table:
                logger.warning(f"Could not find inmate table for Chatham")
                return []
                
            records = []
            rows = table.find_all('tr')[1:] # Skip header
            
            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 4:
                    continue
                    
                name_raw = cols[0].text.strip()
                booking_date = cols[1].text.strip()
                charges = cols[2].text.strip()
                
                name_parts = name_raw.split(',')
                last_name = name_parts[0].strip() if len(name_parts) > 0 else ""
                first_mid = name_parts[1].strip() if len(name_parts) > 1 else ""
                
                record = ArrestRecord(
                    County=self.county,
                    Full_Name=name_raw,
                    First_Name=first_mid.split(' ')[0] if first_mid else "",
                    Last_Name=last_name,
                    Booking_Date=booking_date,
                    Charges=charges,
                    Booking_Number=f"{last_name.upper()}_{booking_date.replace('/', '').replace(':', '').replace(' ', '')}",
                    Status="In Custody"
                )
                records.append(record)
                
            logger.info(f"✅ Found {len(records)} records for Chatham in {time.time() - start_time:.1f}s")
            return records
            
        except Exception as e:
            logger.error(f"Error scraping Chatham: {e}")
            return []
