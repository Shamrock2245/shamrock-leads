"""
Base scraper for Socrata Open Data API.
Used by Fulton County (largest in GA) and potentially others.
"""

import logging
import time
from typing import List

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

class SocrataBaseScraper(BaseScraper):
    """
    Base scraper for Socrata Open Data APIs.
    Subclasses only need to provide the county name and API endpoint.
    """
    
    @property
    def county(self) -> str:
        raise NotImplementedError("Subclasses must define county name")
        
    @property
    def socrata_url(self) -> str:
        raise NotImplementedError("Subclasses must define Socrata JSON URL (e.g., 'https://sharefulton.fultoncountyga.gov/resource/3vfv-9mmr.json')")

    def scrape(self) -> List[ArrestRecord]:
        """Fetch records from Socrata API."""
        start_time = time.time()
        
        # Add limit to get all records (default is often 1000)
        url = f"{self.socrata_url}?$limit=10000"
        
        logger.info(f"📥 Fetching Socrata data for {self.county} at {url}")
        
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json"
            }
            resp = requests.get(url, headers=headers, timeout=15)
            
            if resp.status_code != 200:
                logger.error(f"Failed to fetch {url}: HTTP {resp.status_code}")
                return []
                
            data = resp.json()
            if not isinstance(data, list):
                logger.error(f"Expected list from Socrata API, got {type(data)}")
                return []
                
            records = []
            
            for item in data:
                # Socrata field names are usually lowercase with underscores
                first_name = item.get('first_name', '')
                last_name = item.get('last_name', '')
                
                full_name = item.get('name', '')
                if not full_name and last_name:
                    full_name = f"{last_name}, {first_name}".strip()
                    
                booking_num = str(item.get('booking_number', '') or item.get('so_id', ''))
                booking_date = str(item.get('booking_date', '') or item.get('arrest_date', ''))
                
                # Combine multiple charge fields if they exist
                charges = item.get('charge', '') or item.get('charges', '')
                if not charges:
                    charge_parts = []
                    for k, v in item.items():
                        if 'charge' in k.lower() and v:
                            charge_parts.append(str(v))
                    charges = " | ".join(charge_parts)
                    
                bond = str(item.get('bond_amount', '') or item.get('bond', '0'))
                bond = bond.replace('$', '').replace(',', '').strip()
                if not bond or not bond.replace('.', '').isdigit():
                    bond = "0"
                    
                record = ArrestRecord(
                    County=self.county,
                    State=(getattr(self, "state", None) or "FL"),
                    Full_Name=full_name,
                    First_Name=first_name,
                    Last_Name=last_name,
                    Booking_Number=booking_num or f"{last_name.upper()}_{int(time.time())}",
                    Booking_Date=booking_date,
                    Charges=charges,
                    Bond_Amount=bond,
                    Status="In Custody"
                )
                records.append(record)
                
            logger.info(f"✅ Found {len(records)} records for {self.county} in {time.time() - start_time:.1f}s")
            return records
            
        except Exception as e:
            logger.error(f"Error scraping Socrata {self.county}: {e}")
            return []
