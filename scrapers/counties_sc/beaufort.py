import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
import logging

class BeaufortSCScraper(BaseScraper):
    @property
    def county(self): return "Beaufort"
    
    @property
    def state(self): return "SC"
    
    TIMEZONE = "America/New_York"
    BASE_URL = "http://mugshots.bcgov.net/jailrostera.xml"

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)

    def scrape(self):
        self.logger.info(f"Starting {self.county} County (SC) scrape via XML feed...")
        try:
            resp = requests.get(self.BASE_URL, timeout=30)
            resp.raise_for_status()
            
            # They serve HTML wrapping the XML, we need to extract just the XML part
            # Or sometimes it's just raw XML. Let's try beautifulsoup first
            soup = BeautifulSoup(resp.content, 'xml')
            inmates = soup.find_all('in')
            
            if not inmates:
                self.logger.warning(f"No inmates found in {self.county} XML feed")
                return []

            records = []
            now = datetime.now(timezone.utc)
            
            for inmate in inmates:
                try:
                    booking_num = inmate.find('bn').text.strip() if inmate.find('bn') else None
                    if not booking_num:
                        continue
                        
                    name = inmate.find('nl').text.strip() if inmate.find('nl') else "Unknown"
                    
                    # Arrest date
                    arr_date_str = inmate.find('dtin').text.strip() if inmate.find('dtin') else None
                    arr_time_str = inmate.find('tmin').text.strip() if inmate.find('tmin') else "00:00"
                    arrest_date = None
                    if arr_date_str:
                        dt_str = f"{arr_date_str} {arr_time_str}"
                        for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y %H%M", "%m/%d/%Y"):
                            try:
                                arrest_date = datetime.strptime(dt_str.strip(), fmt).replace(
                                    tzinfo=timezone.utc
                                )
                                break
                            except ValueError:
                                continue

                    # Charges
                    charges = []
                    bond_total = 0.0
                    
                    charge_nodes = inmate.find_all('of')
                    for cnode in charge_nodes:
                        desc = cnode.find('ol').text.strip() if cnode.find('ol') else None
                        if desc:
                            charges.append(desc)
                            
                        bond_str = cnode.find('ob').text.strip() if cnode.find('ob') else "0"
                        bond_str = bond_str.replace('$', '').replace(',', '')
                        try:
                            bond_total += float(bond_str)
                        except ValueError:
                            pass
                            
                    if not charges:
                        charges = ["UNKNOWN CHARGE"]

                    record = ArrestRecord(
                        County=self.county,
                        State="SC",
                        Booking_Number=booking_num,
                        Full_Name=name,
                        Arrest_Date=arrest_date.isoformat() if arrest_date else "",
                        Booking_Date=arrest_date.isoformat() if arrest_date else "",
                        Charges=" | ".join(charges) if charges else "UNKNOWN CHARGE",
                        Bond_Amount=str(bond_total),
                        Status="In Custody",
                        Detail_URL=self.BASE_URL,
                        Facility="Beaufort County Detention Center",
                    )
                    records.append(record)
                    
                except Exception as e:
                    self.logger.error(f"Error parsing inmate in {self.county}: {e}")
                    continue
                    
            self.logger.info(f"Successfully scraped {len(records)} records from {self.county} County")
            return records
            
        except Exception as e:
            self.logger.error(f"Failed to scrape {self.county} County: {e}")
            return []

if __name__ == "__main__":
    import json
    scraper = BeaufortSCScraper()
    res = scraper.scrape()
    print(f"Found {len(res)} records")
    if res:
        print(vars(res[0]))

# Import alias for scheduler registration
BeaufortScraper = BeaufortSCScraper

