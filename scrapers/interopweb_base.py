"""
InteropWeb Base Scraper.
Handles the standard interopweb.com HTML table structure used by ~35 Georgia counties.
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional
import urllib.parse

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

class InteropWebBaseScraper(BaseScraper):
    """
    Base class for counties using InteropWeb jail roster software.
    Typical URL: https://www.interopweb.com/[county]/ or https://[county]jailroster.org/
    """
    
    @property
    def portal_url(self) -> str:
        """Must be implemented by subclass. The URL to the main inmate list page."""
        raise NotImplementedError
        
    def get_headers(self) -> dict:
        return {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    def _parse_date(self, date_str: str) -> Optional[str]:
        if not date_str or not date_str.strip():
            return None
        try:
            # Common format: MM/DD/YYYY or MM/DD/YYYY HH:MM AM/PM
            clean_str = date_str.strip()
            if ' ' in clean_str:
                dt = datetime.strptime(clean_str, '%m/%d/%Y %I:%M:%S %p')
            else:
                dt = datetime.strptime(clean_str, '%m/%d/%Y')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            return date_str.strip()

    def scrape(self) -> List[ArrestRecord]:
        records = []
        try:
            # Fetch the main roster page
            resp = requests.get(self.portal_url, headers=self.get_headers(), timeout=30, verify=False)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # InteropWeb typically uses a large table, often with ID or class 'dgInmates' or just a standard table
            # Find the main data table
            table = soup.find('table', id=lambda x: x and 'dgInmates' in x)
            if not table:
                # Fallback to looking for standard headers
                tables = soup.find_all('table')
                for t in tables:
                    if 'Name' in t.text and 'Booking' in t.text:
                        table = t
                        break
                        
            if not table:
                self.logger.warning(f"Could not find main data table at {self.portal_url}")
                return records

            # Process rows
            rows = table.find_all('tr')
            if not rows:
                return records
                
            # Usually row 0 is header
            for row in rows[1:]:
                cols = row.find_all(['td', 'th'])
                if len(cols) < 3:
                    continue
                    
                # The exact column layout varies slightly by county, but generally:
                # [Photo/Link, Name, Booking Date, Charges, Bond]
                # We extract text from all columns and try to map them
                
                texts = [c.text.strip() for c in cols]
                
                # Attempt to extract a link to a detail page if present
                detail_link = None
                a_tag = row.find('a', href=True)
                if a_tag:
                    detail_link = urllib.parse.urljoin(self.portal_url, a_tag['href'])
                
                # Name is usually the first text-heavy column
                name_col = texts[1] if len(texts) > 1 else texts[0]
                
                # Parse Name (Last, First Middle)
                last_name = ""
                first_name = ""
                if "," in name_col:
                    parts = name_col.split(",", 1)
                    last_name = parts[0].strip()
                    first_name = parts[1].strip()
                else:
                    last_name = name_col
                
                # Try to find booking date (looks like MM/DD/YYYY)
                booking_date = None
                for t in texts:
                    if '/' in t and sum(c.isdigit() for c in t) >= 4:
                        parsed = self._parse_date(t)
                        if parsed:
                            booking_date = parsed
                            break
                            
                # Default to today if no booking date found
                if not booking_date:
                    booking_date = datetime.now().strftime('%Y-%m-%d')
                    
                # Use name + date as a pseudo booking number if none exists
                booking_number = f"{last_name.upper()}_{booking_date.replace('-','')}"
                
                # Try to find charges (usually the longest text column)
                charges = ""
                max_len = 0
                for t in texts[2:]:
                    if len(t) > max_len and not '/' in t and not '$' in t:
                        max_len = len(t)
                        charges = t
                        
                # Try to find bond amount
                bond_amount = None
                for t in texts:
                    if '$' in t:
                        try:
                            clean_bond = t.replace('$', '').replace(',', '').strip()
                            bond_amount = float(clean_bond)
                        except ValueError:
                            pass
                
                record = ArrestRecord(
                    county=self.county,
                    booking_number=booking_number,
                    first_name=first_name,
                    last_name=last_name,
                    booking_date=booking_date,
                    charges=charges,
                    bond_amount=bond_amount,
                    source_url=detail_link or self.portal_url
                )
                records.append(record)
                
            self.logger.info(f"Successfully parsed {len(records)} records from {self.portal_url}")
            
        except Exception as e:
            self.logger.error(f"Error scraping {self.portal_url}: {str(e)}")
            
        return records
