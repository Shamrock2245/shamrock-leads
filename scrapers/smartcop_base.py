"""
SmartCOP Base Scraper.
Handles SmartWebClient ASP.NET portals (e.g. Putnam, Sumter, Taylor).
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional
import urllib.parse

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

class SmartCOPBaseScraper(BaseScraper):
    @property
    def portal_url(self) -> str:
        raise NotImplementedError
        
    def get_headers(self) -> dict:
        return {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    def scrape(self) -> List[ArrestRecord]:
        records = []
        try:
            # 1. Get initial page to get ViewState
            session = requests.Session()
            resp = session.get(self.portal_url, headers=self.get_headers(), timeout=30, verify=False)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            viewstate = soup.find('input', {'name': '__VIEWSTATE'})
            viewstategenerator = soup.find('input', {'name': '__VIEWSTATEGENERATOR'})
            eventvalidation = soup.find('input', {'name': '__EVENTVALIDATION'})
            
            if not viewstate:
                self.logger.warning(f"Could not find ViewState at {self.portal_url}")
                return records
                
            # SmartCOP usually loads a default table or requires a "Search" click
            # If there's a table already, parse it. Otherwise POST to search.
            table = soup.find('table', id=lambda x: x and 'GridView' in x)
            
            if not table:
                # Need to POST to search all
                payload = {
                    '__VIEWSTATE': viewstate['value'] if viewstate else '',
                    '__VIEWSTATEGENERATOR': viewstategenerator['value'] if viewstategenerator else '',
                    '__EVENTVALIDATION': eventvalidation['value'] if eventvalidation else '',
                    'ctl00$ContentPlaceHolder1$btnSearch': 'Search' # Common SmartCOP search button ID
                }
                
                resp = session.post(self.portal_url, data=payload, headers=self.get_headers(), timeout=30, verify=False)
                soup = BeautifulSoup(resp.text, 'html.parser')
                table = soup.find('table', id=lambda x: x and 'GridView' in x)
                
            if not table:
                self.logger.warning(f"Could not find data table after search at {self.portal_url}")
                return records

            rows = table.find_all('tr')
            for row in rows[1:]: # Skip header
                cols = row.find_all('td')
                if len(cols) < 4:
                    continue
                    
                texts = [c.text.strip() for c in cols]
                
                # Format: [Photo, Name, Booking Date, Charges...]
                name_col = texts[1]
                if "," in name_col:
                    parts = name_col.split(",", 1)
                    last_name = parts[0].strip()
                    first_name = parts[1].strip()
                else:
                    last_name = name_col
                    first_name = ""
                    
                booking_date_str = texts[2]
                try:
                    dt = datetime.strptime(booking_date_str.split(' ')[0], '%m/%d/%Y')
                    booking_date = dt.strftime('%Y-%m-%d')
                except:
                    booking_date = datetime.now().strftime('%Y-%m-%d')
                    
                booking_number = f"{last_name.upper()}_{booking_date.replace('-','')}"
                
                charges = " | ".join([t for t in texts[3:] if t and len(t) > 3])
                
                record = ArrestRecord(
                    county=self.county,
                    booking_number=booking_number,
                    first_name=first_name,
                    last_name=last_name,
                    booking_date=booking_date,
                    charges=charges,
                    bond_amount=None,
                    source_url=self.portal_url
                )
                records.append(record)
                
            self.logger.info(f"Successfully parsed {len(records)} records from {self.portal_url}")
            
        except Exception as e:
            self.logger.error(f"Error scraping {self.portal_url}: {str(e)}")
            
        return records
