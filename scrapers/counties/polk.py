"""
Polk County Arrest Scraper — PCSO Jail Inquiry (Kendo UI JSON API).
Source: Polk County Sheriff's Office
URL: https://polksheriff.org/JailInquiry
Method: Direct HTTP requests POST to SearchJail and GetCharges API endpoints.
"""
import logging
import re
import time
from datetime import datetime, timedelta
from typing import List
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from scrapers.published_bond import charge_bond_amount, total_published_bond
from scrapers.plain_http import make_session, request
from scrapers.scraper_resilience import AntiBotBlocked
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.polksheriff.org"
SEARCH_URL = f"{BASE_URL}/detention/jail-inquiry/SearchJail/"
CHARGES_URL = f"{BASE_URL}/inmate/GetCharges"
FACILITY = "Polk County Jail"
DAYS_BACK = 3  # 3 days of booking date coverage

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
}


RACE_MAP = {
    "W": "White", "B": "Black", "H": "Hispanic",
    "A": "Asian", "I": "American Indian", "U": "Unknown",
}

_STATUS_KEYS = ("Status", "status", "CustodyStatus", "InmateStatus")


def published_status(b, release_date: str = "") -> str:
    """Release date -> "Released"; else a published status string; else "" (never invented)."""
    if release_date:
        return "Released"
    if isinstance(b, dict):
        for key in _STATUS_KEYS:
            val = b.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


class PolkCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Polk"

    def scrape(self) -> List[ArrestRecord]:
        session = make_session(HEADERS)
        
        all_records = []
        seen_bookings = set()
        
        # Loop for target dates
        for days_ago in range(DAYS_BACK):
            target_date = datetime.now() - timedelta(days=days_ago)
            date_str = target_date.strftime("%Y-%m-%d")  # API expects YYYY-MM-DD
            logger.info(f"Polk: Querying bookings for date {date_str}...")
            
            payload = {
                "SelectedSearchType": "BookingDate",
                "BookingDate": date_str
            }
            
            try:
                # Certificate verification stays on; a bad chain raises SourceTLSInvalid.
                resp = request(session, "POST", SEARCH_URL, data=payload, timeout=30, county="Polk")
                if resp.status_code != 200:
                    logger.warning(f"Polk: SearchJail for {date_str} returned {resp.status_code}")
                    continue
                
                bookings = resp.json()
                logger.info(f"Polk: Found {len(bookings)} bookings for {date_str}")
                
                for b in bookings:
                    booking_num = b.get("BookingNum")
                    if not booking_num or booking_num in seen_bookings:
                        continue
                    seen_bookings.add(booking_num)
                    
                    try:
                        record = self._scrape_booking_details(session, b)
                        if record:
                            all_records.append(record)
                    except AntiBotBlocked:
                        raise
                    except Exception as be:
                        logger.warning(f"Polk: Failed to fetch details for booking {booking_num}: {be}")
                        
                    # Polite rate-limiting between detail calls
                    time.sleep(0.1)
            except AntiBotBlocked:
                raise
            except Exception as e:
                logger.error(f"Polk: Search error for {date_str}: {e}")
                
        logger.info(f"Polk County Scrape Complete: {len(all_records)} total records")
        return all_records

    def _scrape_booking_details(self, session, b) -> ArrestRecord:
        booking_num = str(b.get("BookingNum", "")).strip()
        full_name = str(b.get("FullName", "")).strip()
        rs_code = str(b.get("RS", "")).strip()
        dob = str(b.get("DOB", "")).strip()
        entry_date = str(b.get("EntryDate", "")).strip()
        release_date = str(b.get("ReleaseDate", "")).strip()
        location = str(b.get("Location", "")).strip()
        
        # Parse Race and Sex from RS code (e.g. "WM", "BF")
        race = ""
        sex = ""
        if rs_code and len(rs_code) >= 2:
            race = RACE_MAP.get(rs_code[0].upper(), rs_code[0])
            sex = rs_code[1].upper()
            
        first_name, middle_name, last_name = self._parse_name(full_name)
        
        # Status only as published: a release date gives "Released", a published
        # status field is kept, else "". SearchJail is a booking-date search that
        # returns released bookings too, so presence is not custody.
        status = published_status(b, release_date)
            
        # Determine facility from location code
        facility = FACILITY
        loc_upper = location.upper().strip()
        if loc_upper == "IN":
            facility = "Polk County Jail - Main"
        elif loc_upper == "CCJ":
            facility = "Central County Jail"
        elif loc_upper == "SCJ":
            facility = "South County Jail"
            
        # Format booking date (EntryDate is typically MM/DD/YYYY)
        booking_date = ""
        if entry_date:
            try:
                dt = datetime.strptime(entry_date, "%m/%d/%Y")
                booking_date = dt.strftime("%Y-%m-%d")
            except ValueError:
                booking_date = entry_date

        release_formatted = ""
        if release_date:
            try:
                dt = datetime.strptime(release_date, "%m/%d/%Y")
                release_formatted = dt.strftime("%Y-%m-%d")
            except ValueError:
                release_formatted = release_date
                
        detail_url = f"{BASE_URL}/inmate-profile/{booking_num}"
        
        # Load profile page to extract CSRF token for charges API
        token = ""
        try:
            profile_resp = request(session, "GET", detail_url, timeout=20, county="Polk")
            if profile_resp.status_code == 200:
                soup = BeautifulSoup(profile_resp.text, "html.parser")
                token_el = soup.find("input", {"name": "__RequestVerificationToken"})
                if token_el:
                    token = token_el["value"]
        except AntiBotBlocked:
            raise
        except Exception as pe:
            logger.warning(f"Polk: Profile page fetch failed for {booking_num}: {pe}")
            
        charges_list = []
        charge_bonds = []  # published amounts per charge; None = unknown
        
        # Fetch detailed charges/bonds if token is available
        if token:
            post_headers = {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": BASE_URL,
                "Referer": detail_url
            }
            charges_payload = {
                "bookingNum": booking_num,
                "__RequestVerificationToken": token
            }
            try:
                charges_resp = request(session, "POST", CHARGES_URL, data=charges_payload, headers=post_headers, timeout=20, county="Polk")
                if charges_resp.status_code == 200:
                    content_type = charges_resp.headers.get("Content-Type", "")
                    if "json" in content_type.lower():
                        charges_data = charges_resp.json()
                        for chg in charges_data:
                            statute = chg.get("Statute", "").strip()
                            desc = chg.get("ChargeDesc", "").strip()
                            bond_str = str(chg.get("BondAmountDue") or "").strip()
                            
                            item = f"{statute} - {desc}" if statute and desc else statute or desc
                            if item:
                                charges_list.append(item)
                                
                            # Only a published dollar amount counts (a real 0 included).
                            charge_bonds.append(charge_bond_amount(bond_str))
                    else:
                        logger.debug(f"Polk: GetCharges returned non-JSON/HTML for {booking_num} (likely no charges).")
            except AntiBotBlocked:
                raise
            except Exception as ce:
                logger.warning(f"Polk: GetCharges call failed for {booking_num}: {ce}")
                
        charges_str = " | ".join(charges_list)
        
        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_num,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB=dob,
            Booking_Date=booking_date,
            Status=status,
            Release_Date=release_formatted,
            Facility=facility,
            Race=race,
            Sex=sex,
            Bond_Amount=total_published_bond(charge_bonds),
            Charges=charges_str,
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL"
        )

    @staticmethod
    def _parse_name(name):
        """Parse 'LAST, FIRST MIDDLE' into components."""
        if not name:
            return "", "", ""
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            remainder = parts[1].strip().split()
            first = remainder[0] if remainder else ""
            middle = " ".join(remainder[1:]) if len(remainder) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) >= 3:
            return parts[0], " ".join(parts[1:-1]), parts[-1]
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return name, "", ""
