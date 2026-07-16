"""
Alabama & Mississippi Multi-County Scraper.
Sources:
  - Alabama: Alacourt.com (statewide), county-specific portals
  - Mississippi: MEC (Mississippi Electronic Courts), county jails
Method: curl_cffi + nodriver for JavaScript
Detail URLs: https://pa.alacourt.com, https://courts.ms.gov/mec/
"""
import logging
import json
import re
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# Alabama county jail portals
ALABAMA_PORTALS = {
    "Jefferson": "https://www.jccal.org/jail/",  # Birmingham
    "Mobile": "https://www.mobilecountysheriff.org/jail/",
    "Madison": "https://www.madisoncountysheriff.org/",
}

# Mississippi county jail portals
MISSISSIPPI_PORTALS = {
    "Hinds": "https://www.co.hinds.ms.us/pgs/apps/inmate/inmate_query.asp",
    "Jackson": "https://www.co.jackson.ms.us/324/Inmate-Lookup",
    "DeSoto": "https://www.desotocountysheriff.org/",
}

class AlabamaMultiCountyScraper(BaseScraper):
    """
    Alabama multi-county scraper targeting major jails.
    """
    
    @property
    def county(self) -> str:
        return "Alabama_Multi"
    
    def scrape(self) -> List[ArrestRecord]:
        """
        Scrape multiple Alabama counties.
        """
        all_records = []
        
        for county_name, portal_url in ALABAMA_PORTALS.items():
            try:
                logger.info(f"Alabama: Scraping {county_name} County...")
                records = self._scrape_county(county_name, portal_url, state="AL")
                all_records.extend(records)
            except Exception as e:
                logger.warning(f"Alabama: {county_name} failed: {e}")
                continue
        
        logger.info(f"Alabama: Total records: {len(all_records)}")
        return all_records
    
    def _scrape_county(self, county_name: str, portal_url: str, state: str) -> List[ArrestRecord]:
        """
        Scrape a single county.
        """
        records = []
        
        # Attempt 1: curl_cffi
        try:
            logger.info(f"{state} {county_name}: Attempting curl_cffi...")
            records = self._scrape_with_curl_cffi(county_name, portal_url, state)
            if records:
                return records
        except Exception as e:
            logger.debug(f"{state} {county_name}: curl_cffi failed: {e}")
        
        # Attempt 2: nodriver
        try:
            logger.info(f"{state} {county_name}: Falling back to nodriver...")
            records = self._scrape_with_nodriver(county_name, portal_url, state)
            if records:
                return records
        except Exception as e:
            logger.warning(f"{state} {county_name}: nodriver failed: {e}")
        
        return []
    
    def _scrape_with_curl_cffi(self, county_name: str, portal_url: str, state: str) -> List[ArrestRecord]:
        """
        Use curl_cffi for quick scraping.
        """
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            raise ImportError("curl_cffi not installed")
        
        records = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        session = cffi_requests.Session()
        
        try:
            logger.info(f"{state} {county_name}: Fetching portal...")
            resp = session.get(portal_url, headers=headers, impersonate="chrome126", timeout=15)
            resp.raise_for_status()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Check for JSON API
            if resp.headers.get("content-type", "").startswith("application/json"):
                data = resp.json()
                records = self._extract_from_api(county_name, data, state)
            else:
                records = self._parse_dom(county_name, soup, state)
            
            return records
        
        except Exception as e:
            logger.error(f"{state} {county_name} curl_cffi error: {e}")
            raise
        finally:
            session.close()
    
    def _scrape_with_nodriver(self, county_name: str, portal_url: str, state: str) -> List[ArrestRecord]:
        """
        Use nodriver for JavaScript rendering.
        """
        try:
            import nodriver
        except ImportError:
            raise ImportError("nodriver not installed")
        
        import asyncio
        
        async def _async_scrape():
            browser = await nodriver.start()
            try:
                page = await browser.get(portal_url)
                await page.wait(2)
                
                content = await page.get_content()
                
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(content, "html.parser")
                
                records = self._parse_dom(county_name, soup, state)
                
                return records
            finally:
                await browser.close()
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(_async_scrape())
    
    def _extract_from_api(self, county_name: str, data: Any, state: str) -> List[ArrestRecord]:
        """
        Extract records from API response.
        """
        records = []
        
        entries = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for key in ["data", "results", "inmates", "bookings", "records", "items"]:
                if key in data and isinstance(data[key], list):
                    entries = data[key]
                    break
        
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            
            try:
                full_name = self._get_field(entry, ["name", "fullName", "inmateName", "defendant"])
                booking_number = self._get_field(entry, ["bookingNumber", "booking_number", "id", "caseNumber"])
                booking_date = self._get_field(entry, ["bookingDate", "booking_date", "arrestDate", "date"])
                charges = self._get_field(entry, ["charges", "charge", "offense"])
                
                if not full_name or not booking_number:
                    continue
                
                first_name, middle_name, last_name = self._parse_name(full_name)
                
                record = ArrestRecord(
                    County=county_name,
                    Booking_Number=booking_number,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    Booking_Date=booking_date,
                    Charges=charges,
                    Status="In Custody",
                    Detail_URL="",
                    Facility=f"{county_name} County Jail",
                    LastCheckedMode="INITIAL"
                )
                records.append(record)
            except Exception as e:
                logger.debug(f"{state} {county_name}: Failed to parse entry: {e}")
                continue
        
        return records
    
    def _parse_dom(self, county_name: str, soup, state: str) -> List[ArrestRecord]:
        """
        Fallback DOM parsing.
        """
        records = []
        try:
            for row in soup.select("table tr, .inmate-row, .booking-row"):
                cells = row.find_all(["td", "span", "div"])
                if len(cells) < 2:
                    continue
                
                text = row.get_text(" ", strip=True)
                
                name_match = re.search(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*[A-Z][a-z]+)", text)
                if not name_match:
                    continue
                
                full_name = name_match.group(1)
                first_name, middle_name, last_name = self._parse_name(full_name)
                
                booking_match = re.search(r"\b(\d{6,})\b", text)
                
                record = ArrestRecord(
                    County=county_name,
                    Booking_Number=booking_match.group(1) if booking_match else "",
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    Status="In Custody",
                    Facility=f"{county_name} County Jail",
                    LastCheckedMode="INITIAL"
                )
                records.append(record)
        except Exception as e:
            logger.warning(f"{state} {county_name} DOM parse error: {e}")
        
        return records
    
    @staticmethod
    def _get_field(entry: dict, keys: List[str]) -> str:
        """
        Get field value from dict using multiple possible keys.
        """
        for key in keys:
            if key in entry and entry[key]:
                val = entry[key]
                if isinstance(val, list):
                    return " | ".join(str(v) for v in val if v)
                return str(val).strip()
        return ""
    
    @staticmethod
    def _parse_name(name_str: str):
        """
        Parse name string into first, middle, last.
        """
        if not name_str:
            return "", "", ""
        
        if "," in name_str:
            parts = name_str.split(",", 1)
            last_name = parts[0].strip()
            first_middle = parts[1].strip() if len(parts) > 1 else ""
            name_parts = first_middle.split()
            first_name = name_parts[0] if name_parts else ""
            middle_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
            return first_name, middle_name, last_name
        
        parts = name_str.split()
        return parts[0], "", parts[-1] if len(parts) >= 2 else ""


class MississippiMultiCountyScraper(BaseScraper):
    """
    Mississippi multi-county scraper targeting MEC and county jails.
    """
    
    @property
    def county(self) -> str:
        return "Mississippi_Multi"
    
    def scrape(self) -> List[ArrestRecord]:
        """
        Scrape multiple Mississippi counties.
        """
        all_records = []
        
        for county_name, portal_url in MISSISSIPPI_PORTALS.items():
            try:
                logger.info(f"Mississippi: Scraping {county_name} County...")
                scraper = AlabamaMultiCountyScraper()
                records = scraper._scrape_county(county_name, portal_url, state="MS")
                all_records.extend(records)
            except Exception as e:
                logger.warning(f"Mississippi: {county_name} failed: {e}")
                continue
        
        logger.info(f"Mississippi: Total records: {len(all_records)}")
        return all_records
