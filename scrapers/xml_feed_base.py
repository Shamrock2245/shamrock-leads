"""
Base scraper for direct XML jail roster feeds.
Used by Walton County and potentially others.
"""

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

class XMLFeedBaseScraper(BaseScraper):
    """
    Base scraper for direct XML jail roster feeds.
    Subclasses only need to provide the county name and feed URL.
    """
    
    @property
    def county(self) -> str:
        raise NotImplementedError("Subclasses must define county name")
        
    @property
    def feed_url(self) -> str:
        raise NotImplementedError("Subclasses must define feed URL")

    def scrape(self) -> List[ArrestRecord]:
        """Fetch and parse the XML feed."""
        start_time = time.time()
        
        logger.info(f"📥 Fetching XML feed for {self.county} at {self.feed_url}")
        
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/xml,text/xml,*/*;q=0.9"
            }
            resp = requests.get(self.feed_url, headers=headers, timeout=15)
            
            if resp.status_code != 200:
                logger.error(f"Failed to fetch {self.feed_url}: HTTP {resp.status_code}")
                return []
                
            # Parse XML
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError as e:
                logger.error(f"Failed to parse XML for {self.county}: {e}")
                return []
                
            records = []
            
            # XML structures vary, but typically have an <inmates> or <roster> root
            # containing <inmate> or <record> elements
            inmate_nodes = root.findall('.//inmate')
            if not inmate_nodes:
                inmate_nodes = root.findall('.//record')
            if not inmate_nodes:
                # Try finding any repeating element that looks like a record
                for child in root:
                    if len(child) > 3: # Likely a record with multiple fields
                        inmate_nodes.append(child)
                        
            if not inmate_nodes:
                logger.warning(f"Could not find inmate nodes in XML for {self.county}")
                return []
                
            for node in inmate_nodes:
                # Extract fields - handle different naming conventions
                def get_text(tags):
                    for tag in tags:
                        elem = node.find(tag)
                        if elem is not None and elem.text:
                            return elem.text.strip()
                        # Try case-insensitive search
                        for child in node:
                            if child.tag.lower() == tag.lower() and child.text:
                                return child.text.strip()
                    return ""
                    
                first_name = get_text(['FirstName', 'first_name', 'First', 'fname'])
                last_name = get_text(['LastName', 'last_name', 'Last', 'lname'])
                middle_name = get_text(['MiddleName', 'middle_name', 'Middle', 'mname'])
                
                full_name = get_text(['FullName', 'full_name', 'Name', 'name'])
                if not full_name and last_name:
                    full_name = f"{last_name}, {first_name} {middle_name}".strip()
                    
                booking_num = get_text(['BookingNumber', 'booking_number', 'BookingId', 'booking_id', 'ID'])
                booking_date = get_text(['BookingDate', 'booking_date', 'Date', 'date'])
                
                # Handle charges which might be nested
                charges = ""
                charges_node = node.find('charges') or node.find('Charges')
                if charges_node is not None:
                    charge_list = []
                    for c in charges_node:
                        desc = c.find('description') or c.find('Description')
                        if desc is not None and desc.text:
                            charge_list.append(desc.text.strip())
                    charges = " | ".join(charge_list)
                else:
                    charges = get_text(['Charge', 'charge', 'Charges', 'charges', 'Offense'])
                    
                bond = get_text(['BondAmount', 'bond_amount', 'Bond', 'bond'])
                bond = bond.replace('$', '').replace(',', '').strip()
                if not bond or not bond.replace('.', '').isdigit():
                    bond = "0"
                    
                record = ArrestRecord(
                    County=self.county,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Last_Name=last_name,
                    Middle_Name=middle_name,
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
            logger.error(f"Error scraping XML {self.county}: {e}")
            return []
