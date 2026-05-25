"""
Highlands County Arrest Scraper — Direct OCV JSON.
Source: Highlands County Sheriff's Office App
URL: https://cdn.myocv.com/ocvapps/a26133870/inmates.json
Method: Direct GET query on OCV JSON endpoint (highly stable & browserless)
"""
import logging
import datetime
import urllib3
import re
from typing import List
import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

FACILITY = "Highlands County Jail"
COUNTY = "Highlands"
JSON_URL = "https://cdn.myocv.com/ocvapps/a26133870/inmates.json"

class HighlandsCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Highlands"

    def scrape(self) -> List[ArrestRecord]:
        logger.info("Highlands: Querying OCV inmate JSON...")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        try:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = requests.get(JSON_URL, headers=headers, verify=False, timeout=25)
            if resp.status_code != 200:
                raise Exception(f"Failed to fetch Highlands OCV JSON: HTTP {resp.status_code}")
                
            data = resp.json()
        except Exception as e:
            logger.error(f"Highlands OCV JSON fetch failed: {e}")
            raise
            
        items = data.get("Information", []) if isinstance(data, dict) else data
        logger.info(f"Highlands: Found {len(items)} total inmates in OCV database")
        
        # Sort items by BookingDateTime descending to get most recent first
        def parse_dt(x):
            b_dt = x.get("BookingDateTime", "")
            if b_dt:
                try:
                    # e.g. "2022-07-27T21:10:39-04:00"
                    # Python 3.7+ fromisoformat handles offsets
                    # replace the last colon in timezone if needed, or strip offset for easy parsing
                    clean_dt = re.sub(r'[-+]\d{2}:\d{2}$', '', b_dt)
                    return datetime.datetime.fromisoformat(clean_dt)
                except Exception:
                    pass
            return datetime.datetime.min
            
        items_sorted = sorted(items, key=parse_dt, reverse=True)
        
        # We process all of them since it's browserless and fast
        records = []
        for x in items_sorted:
            try:
                last_name = x.get("LName", "").strip()
                first_name = x.get("FName", "").strip()
                middle_name = x.get("MName", "").strip()
                full_name = x.get("Name", "").strip()
                
                if not full_name:
                    full_name = f"{last_name}, {first_name}"
                    if middle_name:
                        full_name += f" {middle_name}"
                        
                booking_no = x.get("BookingNumber", "").strip()
                if not booking_no:
                    continue
                    
                dob_raw = x.get("DOB", "").strip()
                dob = ""
                if dob_raw and "T" in dob_raw:
                    dob, _ = dob_raw.split("T", 1)
                else:
                    dob = dob_raw
                    
                booking_dt_raw = x.get("BookingDateTime", "").strip()
                booking_date = ""
                booking_time = ""
                if booking_dt_raw and "T" in booking_dt_raw:
                    booking_date, booking_time_raw = booking_dt_raw.split("T", 1)
                    # clean timezone offset from booking_time
                    booking_time = re.sub(r'[-+]\d{2}:\d{2}$', '', booking_time_raw)
                else:
                    booking_date = booking_dt_raw
                    
                gender = x.get("Gender", "").strip()
                race = x.get("Race", "").strip()
                age = str(x.get("Age", ""))
                
                # Mugshot (use image S3 URL if present, else fallback to PhotoURL)
                mugshot_url = x.get("image", x.get("PhotoURL", "")).strip()
                
                # Address
                address = x.get("Address", "").strip()
                city = ""
                if address and "SEBRING" in address.upper():
                    city = "SEBRING"
                elif address:
                    city_m = re.match(r"^([A-Za-z\s]+)\b", address)
                    if city_m:
                        city = city_m.group(1).strip()
                
                # Charges
                charges_raw = x.get("Charges", "")
                charges_list = [c.strip() for c in re.split(r'[\n,]+', charges_raw) if c.strip()]
                charges = " | ".join(charges_list)
                
                # Bond Amount
                bond_raw = x.get("BondAmount", "0")
                bond_amount = self._parse_bond_val(bond_raw)
                bond_amount_str = str(int(bond_amount)) if bond_amount.is_integer() else f"{bond_amount:.2f}"
                
                records.append(ArrestRecord(
                    Scrape_Timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    County=self.county,
                    Booking_Number=booking_no,
                    Full_Name=full_name.upper(),
                    First_Name=first_name.upper(),
                    Middle_Name=middle_name.upper(),
                    Last_Name=last_name.upper(),
                    DOB=dob,
                    Arrest_Date=booking_date,
                    Arrest_Time=booking_time,
                    Booking_Date=booking_date,
                    Booking_Time=booking_time,
                    Status="In Custody",
                    Facility=FACILITY,
                    Race=race,
                    Sex=gender,
                    Age_At_Arrest=age,
                    City=city.upper(),
                    State="FL",
                    Mugshot_URL=mugshot_url,
                    Charges=charges,
                    Bond_Amount=bond_amount_str,
                    LastCheckedMode="INITIAL"
                ))
            except Exception as ex:
                logger.warning(f"Highlands: failed to parse inmate JSON record: {ex}", exc_info=True)
                
        logger.info(f"Highlands: parsed {len(records)} records")
        return records

    @staticmethod
    def _parse_bond_val(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        import re
        cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
