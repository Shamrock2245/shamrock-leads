"""
Base scraper for Zuercher Technologies Portals.
Used by several mid-tier Georgia counties.
Requires headless browser (DrissionPage/Playwright) or API interception.
"""

import logging
import time
import json
from datetime import datetime, timezone
from typing import List

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

class ZuercherBaseScraper(BaseScraper):
    """
    Base scraper for Zuercher Technologies Portals.
    Subclasses only need to provide the county name and subdomain.
    """
    
    @property
    def county(self) -> str:
        raise NotImplementedError("Subclasses must define county name")
        
    @property
    def zuercher_domain(self) -> str:
        raise NotImplementedError("Subclasses must define Zuercher domain (e.g., 'douglas-so-ga.zuercherportal.com')")

    def scrape(self) -> List[ArrestRecord]:
        """
        Fetch from Zuercher portal.
        Many Zuercher portals expose an internal API that we can hit directly
        without needing a full headless browser.
        """
        start_time = time.time()
        base_url = f"https://{self.zuercher_domain}"
        
        logger.info(f"📥 Fetching Zuercher roster for {self.county} at {base_url}")
        
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{base_url}/"
            })
            
            # Step 1: Get the main page to establish session and get CSRF tokens if needed
            resp = session.get(base_url, timeout=15)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch {base_url}: HTTP {resp.status_code}")
                return []
                
            # Step 2: Try to hit the internal API endpoint
            # Common Zuercher API endpoints:
            api_endpoints = [
                f"{base_url}/api/public/inmates",
                f"{base_url}/api/inmates",
                f"{base_url}/Public/Inmates/GetInmates"
            ]
            
            api_data = None
            for endpoint in api_endpoints:
                try:
                    # Try both GET and POST as Zuercher versions vary
                    api_resp = session.get(endpoint, timeout=10)
                    if api_resp.status_code == 200:
                        try:
                            api_data = api_resp.json()
                            break
                        except: pass
                        
                    api_resp = session.post(endpoint, json={"Page": 1, "PageSize": 1000}, timeout=10)
                    if api_resp.status_code == 200:
                        try:
                            api_data = api_resp.json()
                            break
                        except: pass
                except:
                    continue
                    
            if not api_data:
                logger.warning(f"Could not access internal API for {self.county}. Needs DrissionPage implementation.")
                # Note: Full DrissionPage implementation would go here if API fails
                return []
                
            records = []
            
            # Parse the API response (structure varies slightly)
            inmates = []
            if isinstance(api_data, list):
                inmates = api_data
            elif isinstance(api_data, dict):
                inmates = api_data.get('Data', []) or api_data.get('inmates', []) or api_data.get('items', [])
                
            for inmate in inmates:
                # Zuercher JSON usually has clearly named fields
                first_name = inmate.get('FirstName', '')
                last_name = inmate.get('LastName', '')
                middle_name = inmate.get('MiddleName', '')
                
                full_name = f"{last_name}, {first_name} {middle_name}".strip()
                
                booking_num = str(inmate.get('BookingNumber', '') or inmate.get('Id', ''))
                booking_date = str(inmate.get('BookingDate', ''))
                
                # Charges are often an array
                charges_data = inmate.get('Charges', [])
                charges_list = []
                total_bond = 0.0
                
                for c in charges_data:
                    desc = c.get('Description', '')
                    if desc:
                        charges_list.append(desc)
                    
                    bond = c.get('BondAmount', 0)
                    if bond:
                        try:
                            total_bond += float(bond)
                        except: pass
                        
                record = ArrestRecord(
                    County=self.county,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Last_Name=last_name,
                    Middle_Name=middle_name,
                    Booking_Number=booking_num or f"{last_name.upper()}_{int(time.time())}",
                    Booking_Date=booking_date,
                    Charges=" | ".join(charges_list),
                    Bond_Amount=str(total_bond) if total_bond > 0 else "0",
                    Status="In Custody"
                )
                records.append(record)
                
            logger.info(f"✅ Found {len(records)} records for {self.county} in {time.time() - start_time:.1f}s")
            return records
            
        except Exception as e:
            logger.error(f"Error scraping Zuercher {self.county}: {e}")
            return []
