"""
Base scraper for Eagle Advantage Solutions (EAS) - offenderindex.com
Used by ~40 Georgia counties.
"""

import logging
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

class EASBaseScraper(BaseScraper):
    """
    Base scraper for Eagle Advantage Solutions (offenderindex.com).
    Subclasses only need to provide the county name and slug.
    """
    
    @property
    def county(self) -> str:
        raise NotImplementedError("Subclasses must define county name")
        
    @property
    def eas_slug(self) -> str:
        raise NotImplementedError("Subclasses must define EAS slug (e.g., 'warecoga')")

    def scrape(self) -> List[ArrestRecord]:
        """Fetch all currently booked inmates."""
        start_time = time.time()
        url = f"https://offenderindex.com/{self.eas_slug}/"
        
        logger.info(f"📥 Fetching EAS roster for {self.county} at {url}")
        
        try:
            # Add timeout and user-agent to prevent blocking
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
            resp = requests.get(url, headers=headers, timeout=15)
            
            if resp.status_code != 200:
                logger.error(f"Failed to fetch {url}: HTTP {resp.status_code}")
                return []
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Find the main inmate table
            table = soup.find('table', id='inmateTable')
            if not table:
                # Some counties might use different IDs or no ID
                tables = soup.find_all('table')
                for t in tables:
                    if 'Name' in t.text and 'Booking' in t.text:
                        table = t
                        break
                        
            if not table:
                logger.warning(f"Could not find inmate table for {self.county}")
                return []
                
            records = []
            rows = table.find_all('tr')
            
            # Skip header row
            for row in rows[1:]:
                cols = row.find_all(['td', 'th'])
                if len(cols) < 5:
                    continue
                    
                # Extract basic info
                name_raw = cols[0].text.strip()
                if not name_raw:
                    continue
                    
                # EAS typically formats as "LAST, FIRST MIDDLE"
                name_parts = name_raw.split(',')
                last_name = name_parts[0].strip() if len(name_parts) > 0 else ""
                first_mid = name_parts[1].strip() if len(name_parts) > 1 else ""
                
                fm_parts = first_mid.split(' ')
                first_name = fm_parts[0] if len(fm_parts) > 0 else ""
                middle_name = " ".join(fm_parts[1:]) if len(fm_parts) > 1 else ""
                
                # Extract other columns based on standard EAS layout
                # Usually: Name | Booking Date | Charges | Bond | etc.
                booking_date_raw = cols[1].text.strip() if len(cols) > 1 else ""
                charges_raw = cols[2].text.strip() if len(cols) > 2 else ""
                bond_raw = cols[3].text.strip() if len(cols) > 3 else "0"
                
                # Clean bond amount
                bond_amount = bond_raw.replace('$', '').replace(',', '').strip()
                if not bond_amount or not bond_amount.replace('.', '').isdigit():
                    bond_amount = "0"
                    
                # Create record
                record = ArrestRecord(
                    County=self.county,
                    Full_Name=name_raw,
                    First_Name=first_name,
                    Last_Name=last_name,
                    Middle_Name=middle_name,
                    Booking_Date=booking_date_raw,
                    Charges=charges_raw,
                    Bond_Amount=bond_amount,
                    # EAS doesn't always provide booking numbers on the main list, use name+date as fallback
                    Booking_Number=f"{last_name.upper()}_{booking_date_raw.replace('/', '')}",
                    Status="In Custody"
                )
                records.append(record)
                
            logger.info(f"✅ Found {len(records)} records for {self.county} in {time.time() - start_time:.1f}s")
            return records
            
        except Exception as e:
            logger.error(f"Error scraping EAS {self.county}: {e}")
            return []
