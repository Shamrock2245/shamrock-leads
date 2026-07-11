"""
Gwinnett County (GA) Arrest Scraper.
Custom HTML scraper for SmartWebClient (ASP.NET).
"""
import logging
import time
from datetime import datetime
from typing import List

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

class GwinnettScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Gwinnett"
        
    def scrape(self) -> List[ArrestRecord]:
        start_time = time.time()
        base_url = "https://www.gwinnettcountysheriff.com/SmartWebClient/"
        logger.info(f"📥 Fetching Gwinnett County roster at {base_url}")
        
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            
            # 1. Get the initial page to grab ViewState and EventValidation
            resp = session.get(base_url, timeout=15)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch {base_url}: HTTP {resp.status_code}")
                return []
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Extract all hidden inputs required for ASP.NET postback
            payload = {i.get('name'): i.get('value', '') for i in soup.find_all('input') if i.get('name')}
            
            # Find the search button to trigger the postback
            btn = soup.find('input', {'type': 'submit'}) or soup.find('input', {'type': 'button'})
            if btn and btn.get('name'):
                payload[btn.get('name')] = btn.get('value', 'Search')
                
            # Ensure we search for everyone (empty name fields)
            payload['txbLastName'] = ''
            payload['txbFirstName'] = ''
            
            # 2. Post the search to get the results table
            logger.info("Submitting search payload to Gwinnett SmartWebClient...")
            resp2 = session.post(base_url, data=payload, timeout=20)
            
            if resp2.status_code != 200:
                logger.error(f"Search POST failed: HTTP {resp2.status_code}")
                return []
                
            soup2 = BeautifulSoup(resp2.text, 'html.parser')
            tables = soup2.find_all('table')
            
            records = []
            
            # The data is usually in the first or second table that has many rows
            data_table = None
            for t in tables:
                if len(t.find_all('tr')) > 5:
                    data_table = t
                    break
                    
            if not data_table:
                logger.warning("Could not find data table in Gwinnett response")
                return []
                
            rows = data_table.find_all('tr')
            # Skip header row
            for row in rows[1:]:
                cells = row.find_all('td')
                if len(cells) < 4:
                    continue
                    
                # The exact column layout depends on their specific SmartWeb config
                # Usually: [Photo, Name, Booking Date, Charges...]
                # For now we'll extract text from all cells
                cell_texts = [c.text.strip() for c in cells]
                
                # We need to parse the name (Last, First)
                name_parts = cell_texts[1].split(',') if len(cell_texts) > 1 else ["Unknown", ""]
                last_name = name_parts[0].strip()
                first_name = name_parts[1].strip() if len(name_parts) > 1 else ""
                
                booking_date = cell_texts[2] if len(cell_texts) > 2 else ""
                
                # Create the record
                record = ArrestRecord(
                    First_Name=first_name,
                    Last_Name=last_name,
                    Booking_Date=booking_date,
                    County=self.county,
                    State="GA",
                    Status="In Custody",
                    Detail_URL=base_url
                )
                records.append(record)
                
            logger.info(f"✅ Extracted {len(records)} records from Gwinnett")
            return records
            
        except Exception as e:
            logger.error(f"Error scraping {self.county}: {e}", exc_info=True)
            return []
