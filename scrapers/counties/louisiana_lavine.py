"""
Louisiana LAVINE Statewide Scraper.
Source: Louisiana VINE (Victim Information and Notification Everyday)
URL: https://lcle.la.gov/programs/lavine/lavine_rosters/
Method: curl_cffi (TLS fingerprinting) + nodriver for JavaScript
Detail URL: https://lcle.la.gov/
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
BASE_URL = "https://lcle.la.gov/programs/lavine/lavine_rosters/"

# Parish-specific LAVINE endpoints
PARISH_ROSTERS = {
    "Orleans": "https://orleans.lavine.org",
    "Lafayette": "https://lafayette.lavine.org",
    "Jefferson": "https://jefferson.lavine.org",
    "St_Bernard": "https://stbernard.lavine.org",
    "Plaquemines": "https://plaquemines.lavine.org",
}

class LouisianaLAVINEScraper(BaseScraper):
    """
    Louisiana LAVINE multi-parish scraper.
    LAVINE is highly sensitive to automation; uses curl_cffi for stealth.
    """
    
    @property
    def county(self) -> str:
        return "Louisiana_LAVINE"
    
    def scrape(self) -> List[ArrestRecord]:
        """
        Scrape multiple Louisiana parishes via LAVINE.
        """
        all_records = []
        
        for parish_name, roster_url in PARISH_ROSTERS.items():
            try:
                logger.info(f"Louisiana: Scraping {parish_name} Parish...")
                records = self._scrape_parish(parish_name, roster_url)
                all_records.extend(records)
            except Exception as e:
                logger.warning(f"Louisiana: {parish_name} failed: {e}")
                continue
        
        logger.info(f"Louisiana: Total records: {len(all_records)}")
        return all_records
    
    def _scrape_parish(self, parish_name: str, roster_url: str) -> List[ArrestRecord]:
        """
        Scrape a single parish's LAVINE roster.
        """
        records = []
        
        # Attempt 1: curl_cffi with aggressive stealth
        try:
            logger.info(f"Louisiana {parish_name}: Attempting curl_cffi...")
            records = self._scrape_with_curl_cffi(parish_name, roster_url)
            if records:
                return records
        except Exception as e:
            logger.debug(f"Louisiana {parish_name}: curl_cffi failed: {e}")
        
        # Attempt 2: nodriver for JavaScript rendering
        try:
            logger.info(f"Louisiana {parish_name}: Falling back to nodriver...")
            records = self._scrape_with_nodriver(parish_name, roster_url)
            if records:
                return records
        except Exception as e:
            logger.warning(f"Louisiana {parish_name}: nodriver failed: {e}")
        
        return []
    
    def _scrape_with_curl_cffi(self, parish_name: str, roster_url: str) -> List[ArrestRecord]:
        """
        Use curl_cffi with aggressive stealth headers.
        LAVINE is known for strict bot detection.
        """
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            raise ImportError("curl_cffi not installed")
        
        records = []
        
        # Aggressive stealth headers
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
        }
        
        session = cffi_requests.Session()
        
        try:
            # Add random delay to avoid detection
            time.sleep(2 + (hash(parish_name) % 3))
            
            logger.info(f"Louisiana {parish_name}: Fetching roster...")
            resp = session.get(
                roster_url,
                headers=headers,
                impersonate="chrome126",
                timeout=20
            )
            resp.raise_for_status()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Check for JSON API response
            if resp.headers.get("content-type", "").startswith("application/json"):
                data = resp.json()
                records = self._extract_from_api(parish_name, data)
            else:
                # Parse HTML table
                records = self._parse_dom(parish_name, soup)
            
            return records
        
        except Exception as e:
            logger.error(f"Louisiana {parish_name} curl_cffi error: {e}")
            raise
        finally:
            session.close()
    
    def _scrape_with_nodriver(self, parish_name: str, roster_url: str) -> List[ArrestRecord]:
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
                page = await browser.get(roster_url)
                
                # Wait for content to load
                await page.wait(3)
                
                # Get content
                content = await page.get_content()
                
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(content, "html.parser")
                
                records = self._parse_dom(parish_name, soup)
                
                return records
            finally:
                await browser.close()
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(_async_scrape())
    
    def _extract_from_api(self, parish_name: str, data: Any) -> List[ArrestRecord]:
        """
        Extract records from LAVINE API response.
        """
        records = []
        
        # Handle nested structures
        entries = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for key in ["data", "results", "inmates", "bookings", "records", "items", "roster"]:
                if key in data and isinstance(data[key], list):
                    entries = data[key]
                    break
        
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            
            try:
                full_name = self._get_field(entry, ["name", "fullName", "inmateName", "defendant"])
                booking_number = self._get_field(entry, ["bookingNumber", "booking_number", "id", "inmateId"])
                booking_date = self._get_field(entry, ["bookingDate", "booking_date", "arrestDate", "date"])
                charges = self._get_field(entry, ["charges", "charge", "offense"])
                
                if not full_name or not booking_number:
                    continue
                
                first_name, middle_name, last_name = self._parse_name(full_name)
                
                record = ArrestRecord(
                    County=parish_name,
                    Booking_Number=booking_number,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    Booking_Date=booking_date,
                    Charges=charges,
                    Status="In Custody",
                    Detail_URL=PARISH_ROSTERS.get(parish_name, ""),
                    Facility=f"{parish_name} Parish Jail",
                    LastCheckedMode="INITIAL"
                )
                records.append(record)
            except Exception as e:
                logger.debug(f"Louisiana {parish_name}: Failed to parse entry: {e}")
                continue
        
        return records
    
    def _parse_dom(self, parish_name: str, soup) -> List[ArrestRecord]:
        """
        Fallback DOM parsing for LAVINE HTML.
        """
        records = []
        try:
            # Look for inmate tables or lists
            for row in soup.select("table tr, .inmate-row, .booking-row, .roster-item"):
                cells = row.find_all(["td", "span", "div"])
                if len(cells) < 2:
                    continue
                
                text = row.get_text(" ", strip=True)
                
                # Extract name (common pattern: "Last, First")
                name_match = re.search(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*[A-Z][a-z]+)", text)
                if not name_match:
                    continue
                
                full_name = name_match.group(1)
                first_name, middle_name, last_name = self._parse_name(full_name)
                
                # Extract booking number
                booking_match = re.search(r"\b(\d{6,})\b", text)
                
                record = ArrestRecord(
                    County=parish_name,
                    Booking_Number=booking_match.group(1) if booking_match else "",
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    Status="In Custody",
                    Facility=f"{parish_name} Parish Jail",
                    LastCheckedMode="INITIAL"
                )
                records.append(record)
        except Exception as e:
            logger.warning(f"Louisiana {parish_name} DOM parse error: {e}")
        
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
