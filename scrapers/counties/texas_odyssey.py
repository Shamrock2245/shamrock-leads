"""
Texas Odyssey (Tyler Technologies) Scraper.
Source: Tyler Technologies Odyssey Public Access
URL: https://portal-txhoward.tylertech.cloud/PublicAccess/default.aspx (example)
Method: curl_cffi + Obscura (for Amazon WAF + CAPTCHA)
Detail URL: https://portal-txhoward.tylertech.cloud/
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

# Odyssey portal URLs by county (sample)
ODYSSEY_PORTALS = {
    "Harris": "https://portal-txharris.tylertech.cloud/PublicAccess/default.aspx",
    "Dallas": "https://portal-txdallas.tylertech.cloud/PublicAccess/default.aspx",
    "Tarrant": "https://portal-txtarrant.tylertech.cloud/PublicAccess/default.aspx",
    "Bexar": "https://portal-txbexar.tylertech.cloud/PublicAccess/default.aspx",
    "Travis": "https://portal-txtravis.tylertech.cloud/PublicAccess/default.aspx",
}

class TexasOdysseyMultiCountyScraper(BaseScraper):
    """
    Multi-county Texas Odyssey scraper.
    Targets major Texas counties using Tyler Technologies Odyssey platform.
    Handles Amazon WAF and CAPTCHA using Obscura.
    """
    
    @property
    def county(self) -> str:
        return "Texas_Odyssey"
    
    def scrape(self) -> List[ArrestRecord]:
        """
        Scrape multiple Texas Odyssey counties.
        """
        all_records = []
        
        for county_name, portal_url in ODYSSEY_PORTALS.items():
            try:
                logger.info(f"Texas: Scraping {county_name} County...")
                records = self._scrape_county(county_name, portal_url)
                all_records.extend(records)
            except Exception as e:
                logger.warning(f"Texas: {county_name} failed: {e}")
                continue
        
        logger.info(f"Texas: Total records: {len(all_records)}")
        return all_records
    
    def _scrape_county(self, county_name: str, portal_url: str) -> List[ArrestRecord]:
        """
        Scrape a single county's Odyssey portal.
        """
        records = []
        
        # Attempt 1: curl_cffi for quick API discovery
        try:
            logger.info(f"Texas {county_name}: Attempting curl_cffi...")
            records = self._scrape_with_curl_cffi(county_name, portal_url)
            if records:
                return records
        except Exception as e:
            logger.debug(f"Texas {county_name}: curl_cffi failed: {e}")
        
        # Attempt 2: Obscura for WAF bypass
        try:
            logger.info(f"Texas {county_name}: Falling back to Obscura...")
            records = self._scrape_with_obscura(county_name, portal_url)
            if records:
                return records
        except Exception as e:
            logger.warning(f"Texas {county_name}: Obscura failed: {e}")
        
        return []
    
    def _scrape_with_curl_cffi(self, county_name: str, portal_url: str) -> List[ArrestRecord]:
        """
        Use curl_cffi to bypass Amazon WAF with JA3 fingerprinting.
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
            # Fetch main page
            logger.info(f"Texas {county_name}: Fetching main portal...")
            resp = session.get(portal_url, headers=headers, impersonate="chrome126", timeout=15)
            resp.raise_for_status()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Look for "Jail Records" link
            jail_records_link = None
            for link in soup.find_all("a"):
                if "jail" in link.get_text(strip=True).lower():
                    href = link.get("href", "")
                    if href:
                        if href.startswith("http"):
                            jail_records_link = href
                        elif href.startswith("/"):
                            jail_records_link = portal_url.split("/PublicAccess")[0] + href
                        break
            
            if not jail_records_link:
                logger.warning(f"Texas {county_name}: No jail records link found")
                return []
            
            logger.info(f"Texas {county_name}: Accessing jail records...")
            jail_resp = session.get(
                jail_records_link,
                headers=headers,
                impersonate="chrome126",
                timeout=15
            )
            jail_resp.raise_for_status()
            
            # Check for JSON API response
            if jail_resp.headers.get("content-type", "").startswith("application/json"):
                data = jail_resp.json()
                records = self._extract_from_api(county_name, data)
            else:
                # Parse HTML
                soup = BeautifulSoup(jail_resp.text, "html.parser")
                records = self._parse_dom(county_name, soup)
            
            return records
        
        except Exception as e:
            logger.error(f"Texas {county_name} curl_cffi error: {e}")
            raise
        finally:
            session.close()
    
    def _scrape_with_obscura(self, county_name: str, portal_url: str) -> List[ArrestRecord]:
        """
        Use Obscura browser for Amazon WAF + CAPTCHA bypass.
        """
        import asyncio
        
        async def _async_scrape():
            pw, browser = await self._get_obscura_browser()
            try:
                page = await browser.new_page()
                await page.goto(portal_url, wait_until="networkidle")
                
                # Wait for page to load
                await page.wait_for_timeout(3000)
                
                # Look for "Jail Records" button/link
                jail_link = await page.query_selector("a:has-text('Jail Records')")
                if not jail_link:
                    jail_link = await page.query_selector("button:has-text('Jail Records')")
                
                if jail_link:
                    await jail_link.click()
                    await page.wait_for_timeout(2000)
                
                # Get content
                content = await page.content()
                
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(content, "html.parser")
                
                records = self._parse_dom(county_name, soup)
                
                await page.close()
                return records
            finally:
                await browser.close()
                await pw.__aexit__(None, None, None)
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(_async_scrape())
    
    def _extract_from_api(self, county_name: str, data: Any) -> List[ArrestRecord]:
        """
        Extract records from Odyssey API response.
        """
        records = []
        
        # Handle nested structures
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
                booking_number = self._get_field(entry, ["bookingNumber", "booking_number", "id", "inmateId"])
                booking_date = self._get_field(entry, ["bookingDate", "booking_date", "arrestDate", "date"])
                charges = self._get_field(entry, ["charges", "charge", "offense"])
                bond_amount = self._get_field(entry, ["bond", "bondAmount", "bond_amount"])
                
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
                    Bond_Amount=bond_amount,
                    Status="In Custody",
                    Detail_URL=ODYSSEY_PORTALS.get(county_name, ""),
                    Facility=f"{county_name} County Jail",
                    LastCheckedMode="INITIAL"
                )
                records.append(record)
            except Exception as e:
                logger.debug(f"Texas {county_name}: Failed to parse entry: {e}")
                continue
        
        return records
    
    def _parse_dom(self, county_name: str, soup) -> List[ArrestRecord]:
        """
        Fallback DOM parsing for Odyssey HTML.
        """
        records = []
        try:
            # Look for inmate tables
            for row in soup.select("table tr, .inmate-row, .booking-row"):
                cells = row.find_all(["td", "span", "div"])
                if len(cells) < 2:
                    continue
                
                text = row.get_text(" ", strip=True)
                
                # Extract name
                name_match = re.search(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*[A-Z][a-z]+)", text)
                if not name_match:
                    continue
                
                full_name = name_match.group(1)
                first_name, middle_name, last_name = self._parse_name(full_name)
                
                # Extract booking number
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
            logger.warning(f"Texas {county_name} DOM parse error: {e}")
        
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
