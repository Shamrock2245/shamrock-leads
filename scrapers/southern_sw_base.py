"""
Base scraper for Southern Software Citizen Connect.
Used by several Georgia counties.
"""

import logging
import time
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

class SouthernSWBaseScraper(BaseScraper):
    """
    Base scraper for Southern Software Citizen Connect.
    Subclasses only need to provide the county name and AgencyID.
    """
    
    @property
    def county(self) -> str:
        raise NotImplementedError("Subclasses must define county name")
        
    @property
    def agency_id(self) -> str:
        raise NotImplementedError("Subclasses must define AgencyID (e.g., 'BanksCoGA')")

    def scrape(self) -> List[ArrestRecord]:
        """Fetch bookings from Southern Software portal."""
        start_time = time.time()
        url = f"https://cc.southernsoftware.com/index/index.php?AgencyID={self.agency_id}"
        
        logger.info(f"📥 Fetching Southern Software roster for {self.county} at {url}")
        
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            })
            
            # Step 1: Get the main page to establish session/cookies
            resp = session.get(url, timeout=15)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch {url}: HTTP {resp.status_code}")
                return []
                
            # Step 2: Look for the Inmate Confinements link or POST form
            # Southern Software usually requires a POST to filter by "Last 24 Hours" or "Currently Confined"
            search_url = f"https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID={self.agency_id}"
            
            # Default to getting the last 24 hours to keep payload small
            payload = {
                "SearchType": "Last24Hours", # or "CurrentlyConfined"
                "Submit": "Search"
            }
            
            search_resp = session.post(search_url, data=payload, timeout=15)
            if search_resp.status_code != 200:
                logger.warning(f"POST search failed, falling back to GET parsing")
                html = resp.text
            else:
                html = search_resp.text
                
            soup = BeautifulSoup(html, 'html.parser')
            
            # Look for inmate cards or table rows
            # Southern SW often uses div cards with class 'inmate-card' or similar
            cards = soup.find_all('div', class_=re.compile(r'inmate.*', re.I))
            
            if not cards:
                # Try table fallback
                tables = soup.find_all('table')
                for t in tables:
                    if 'Name' in t.text and ('Booking' in t.text or 'Charge' in t.text):
                        cards = t.find_all('tr')[1:] # Skip header
                        break
                        
            if not cards:
                logger.warning(f"Could not find inmate data for {self.county}")
                return []
                
            records = []
            for card in cards:
                text = card.text.strip()
                if not text or len(text) < 10:
                    continue
                    
                # Extract basic info - highly dependent on specific county layout
                # Usually: Name is in a header tag
                name_elem = card.find(['h3', 'h4', 'strong'])
                name_raw = name_elem.text.strip() if name_elem else "Unknown"
                
                # Try to parse name
                name_parts = name_raw.split(',')
                last_name = name_parts[0].strip() if len(name_parts) > 0 else ""
                first_mid = name_parts[1].strip() if len(name_parts) > 1 else ""
                
                # Create record
                record = ArrestRecord(
                    County=self.county,
                    Full_Name=name_raw,
                    First_Name=first_mid.split(' ')[0] if first_mid else "",
                    Last_Name=last_name,
                    Status="In Custody"
                )
                
                # Extract details via regex or specific labels
                labels = card.find_all('label')
                for label in labels:
                    lbl_text = label.text.strip().lower()
                    val_elem = label.find_next_sibling()
                    val_text = val_elem.text.strip() if val_elem else ""
                    
                    if 'booking' in lbl_text and 'date' in lbl_text:
                        record.Booking_Date = val_text
                    elif 'booking' in lbl_text and 'number' in lbl_text:
                        record.Booking_Number = val_text
                    elif 'charge' in lbl_text:
                        record.Charges = val_text
                    elif 'bond' in lbl_text:
                        bond = val_text.replace('$', '').replace(',', '').strip()
                        if bond and bond.replace('.', '').isdigit():
                            record.Bond_Amount = bond
                            
                # Fallback for booking number
                if not record.Booking_Number:
                    record.Booking_Number = f"{last_name.upper()}_{int(time.time())}"
                    
                records.append(record)
                
            logger.info(f"✅ Found {len(records)} records for {self.county} in {time.time() - start_time:.1f}s")
            return records
            
        except Exception as e:
            logger.error(f"Error scraping Southern SW {self.county}: {e}")
            return []
