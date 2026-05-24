"""
Pasco County Arrest Scraper — Direct REST API Search.
Source: Pasco County Sheriff's Office / Corrections
URL: https://jailinfo.pascocorrections.net/jmc/#/inCustody
Method: Direct REST API endpoints
"""
import logging
import datetime
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://jailinfo.pascocorrections.net/jmc/#/inCustody"
CUSTODY_API = "https://jailinfo.pascocorrections.net/jmcapi/custody/GetInCustody"
CHARGE_API = "https://jailinfo.pascocorrections.net/jmcapi/arrest/GetUserArrestCharge?nameId={name_id}"
FACILITY = "Pasco County Jail - Land O' Lakes"

class PascoCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Pasco"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            import requests as cffi_requests

        logger.info(f"Pasco: Querying custody roster from {CUSTODY_API}")
        try:
            # We disable SSL verification because of the government's misconfigured intermediate certificates
            resp = cffi_requests.get(CUSTODY_API, verify=False, timeout=30)
            if resp.status_code != 200:
                raise Exception(f"Failed to fetch custody roster: {resp.status_code}")
            inmates = resp.json()
        except Exception as e:
            logger.error(f"Pasco roster fetch error: {e}")
            raise

        logger.info(f"Pasco: Found {len(inmates)} total inmates in custody")
        
        # Sort by bookingDate descending so we process most recent first
        parsed_inmates = []
        for x in inmates:
            bdate_str = x.get("bookingDate", "")
            try:
                dt = datetime.datetime.strptime(bdate_str, "%m/%d/%Y")
            except Exception:
                dt = datetime.datetime.min
            parsed_inmates.append((dt, x))
            
        parsed_inmates.sort(key=lambda x: x[0], reverse=True)
        
        # We only query charge/bond details for the last 5 days of bookings to be fast and respectful.
        # Today is 2026-05-24, but we compute dynamically relative to the latest booking date in the system
        if parsed_inmates:
            latest_date = parsed_inmates[0][0]
        else:
            latest_date = datetime.datetime.now()
            
        cutoff_date = latest_date - datetime.timedelta(days=5)
        logger.info(f"Pasco: Cutoff date for detailed charge fetching is {cutoff_date.strftime('%m/%d/%Y')}")
        
        records = []
        for dt, x in parsed_inmates:
            # Check if booking is within cutoff
            is_recent = dt >= cutoff_date
            
            first_name = x.get("firstName", "").strip()
            middle_name = x.get("middleName", "").strip()
            last_name = x.get("lastName", "").strip()
            suffix = x.get("suffix", "").strip()
            
            full_name = f"{last_name}, {first_name}"
            if middle_name:
                full_name += f" {middle_name}"
            if suffix:
                full_name += f" {suffix}"
                
            booking_number = str(x.get("bookingNumber", ""))
            name_id = x.get("nameId")
            dob = x.get("dob", "")
            booking_date = x.get("bookingDate", "")
            booking_time = x.get("bookingTime", "")
            
            charges_str = ""
            total_bond = 0.0
            
            # Fetch detailed charges/bonds only for recent bookings
            if is_recent and name_id:
                charge_url = CHARGE_API.format(name_id=name_id)
                try:
                    logger.debug(f"Pasco: Fetching charges for {full_name} ({name_id})")
                    c_resp = cffi_requests.get(charge_url, verify=False, timeout=15)
                    if c_resp.status_code == 200:
                        charge_list = c_resp.json()
                        charge_items = []
                        for c in charge_list:
                            c_code = c.get("arrestCharge", "").strip()
                            c_desc = c.get("chargeDescription", "").strip()
                            c_bond = c.get("bondAmount", "0").strip()
                            
                            item = f"{c_code} - {c_desc}" if c_code and c_desc else c_code or c_desc
                            if item:
                                charge_items.append(item)
                                
                            # Parse bond
                            bond_val = self._parse_bond_val(c_bond)
                            total_bond += bond_val
                            
                        charges_str = " | ".join(charge_items)
                except Exception as ce:
                    logger.warning(f"Pasco: Failed to fetch charges for nameId {name_id}: {ce}")
                    
            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=booking_number,
                Full_Name=full_name,
                First_Name=first_name,
                Middle_Name=middle_name,
                Last_Name=last_name,
                DOB=dob,
                Booking_Date=booking_date,
                Charges=charges_str,
                Bond_Amount=str(int(total_bond)) if total_bond.is_integer() else f"{total_bond:.2f}",
                Status="In Custody",
                Detail_URL=BASE_URL,
                Facility=FACILITY,
                LastCheckedMode="INITIAL"
            ))
            
        logger.info(f"Pasco: parsed {len(records)} records")
        return records

    @staticmethod
    def _parse_bond_val(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        import re
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
