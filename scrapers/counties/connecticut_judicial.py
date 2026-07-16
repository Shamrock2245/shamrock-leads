"""
Connecticut Statewide Judicial Branch Scraper.
Source: CT Judicial Branch Criminal/Motor Vehicle Case Look-up
URL: https://www.jud.ct.gov/crim.htm
Method: curl_cffi + Playwright (nodriver for stealth)
Detail URL: https://www.jud.ct.gov/
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
BASE_URL = "https://www.jud.ct.gov/crim.htm"
SEARCH_API = "https://www.jud.ct.gov/api/search"  # Placeholder, will be discovered

class ConnecticutJudicialScraper(BaseScraper):
    """
    Connecticut Judicial Branch Scraper using curl_cffi + nodriver.
    Targets pending criminal cases and daily dockets statewide.
    """
    
    @property
    def county(self) -> str:
        return "Connecticut_Statewide"
    
    def scrape(self) -> List[ArrestRecord]:
        """
        Scrape CT Judicial using curl_cffi for initial reconnaissance,
        then nodriver for JavaScript-heavy pages.
        """
        all_records = []
        
        # Attempt 1: curl_cffi for quick API discovery
        try:
            logger.info("Connecticut: Attempting curl_cffi for API discovery...")
            records = self._scrape_with_curl_cffi()
            if records:
                logger.info(f"Connecticut: curl_cffi succeeded, found {len(records)} records")
                return records
        except Exception as e:
            logger.warning(f"Connecticut: curl_cffi failed: {e}")
        
        # Attempt 2: nodriver for JavaScript rendering
        try:
            logger.info("Connecticut: Falling back to nodriver...")
            records = self._scrape_with_nodriver()
            if records:
                logger.info(f"Connecticut: nodriver succeeded, found {len(records)} records")
                return records
        except Exception as e:
            logger.warning(f"Connecticut: nodriver failed: {e}")
        
        logger.error("Connecticut: All scraping methods failed")
        return []
    
    def _scrape_with_curl_cffi(self) -> List[ArrestRecord]:
        """
        Use curl_cffi to bypass any protections and discover API endpoints.
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
            # Fetch the main page
            logger.info("Connecticut: Fetching main page...")
            resp = session.get(BASE_URL, headers=headers, impersonate="chrome126", timeout=15)
            resp.raise_for_status()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Extract search links for pending cases
            pending_links = []
            for link in soup.find_all("a"):
                href = link.get("href", "")
                text = link.get_text(strip=True).lower()
                if "pending" in text or "search" in text:
                    if href.startswith("http"):
                        pending_links.append(href)
                    elif href.startswith("/"):
                        pending_links.append("https://www.jud.ct.gov" + href)
            
            logger.info(f"Connecticut: Found {len(pending_links)} search links")
            
            # Try each search link
            for search_url in pending_links[:3]:  # Limit to first 3
                try:
                    logger.info(f"Connecticut: Searching {search_url}...")
                    search_resp = session.get(
                        search_url,
                        headers=headers,
                        impersonate="chrome126",
                        timeout=15
                    )
                    search_resp.raise_for_status()
                    
                    # Check if response is JSON
                    if search_resp.headers.get("content-type", "").startswith("application/json"):
                        data = search_resp.json()
                        records.extend(self._extract_from_api(data))
                    else:
                        # Parse HTML
                        soup = BeautifulSoup(search_resp.text, "html.parser")
                        records.extend(self._parse_dom(soup))
                
                except Exception as e:
                    logger.debug(f"Connecticut: Search link failed: {e}")
                    continue
            
            return records
        
        except Exception as e:
            logger.error(f"Connecticut curl_cffi error: {e}")
            raise
        finally:
            session.close()
    
    def _scrape_with_nodriver(self) -> List[ArrestRecord]:
        """
        Use nodriver (undetected Playwright) for JavaScript rendering.
        nodriver is faster than standard Playwright and harder to detect.
        """
        try:
            import nodriver
        except ImportError:
            raise ImportError("nodriver not installed. Run: pip install nodriver")
        
        import asyncio
        
        async def _async_scrape():
            browser = await nodriver.start()
            try:
                page = await browser.get(BASE_URL)
                
                # Wait for page to load
                await page.wait(2)
                
                # Look for pending case search link
                pending_link = await page.find("a", containing="Pending Case")
                if pending_link:
                    await pending_link.click()
                    await page.wait(3)
                
                # Get content
                content = await page.get_content()
                
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(content, "html.parser")
                
                records = self._parse_dom(soup)
                
                return records
            finally:
                await browser.close()
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(_async_scrape())
    
    def _extract_from_api(self, data: Any) -> List[ArrestRecord]:
        """
        Extract records from API JSON response.
        """
        records = []
        
        # Handle nested structures
        entries = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for key in ["data", "results", "cases", "records", "items", "entries"]:
                if key in data and isinstance(data[key], list):
                    entries = data[key]
                    break
        
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            
            try:
                full_name = self._get_field(entry, ["name", "fullName", "defendant", "defendantName"])
                docket_number = self._get_field(entry, ["docketNumber", "docket_number", "caseNumber", "id"])
                filed_date = self._get_field(entry, ["filedDate", "filed_date", "date"])
                charges = self._get_field(entry, ["charges", "charge", "offense"])
                court = self._get_field(entry, ["court", "courthouse", "jurisdiction"])
                
                if not full_name or not docket_number:
                    continue
                
                first_name, middle_name, last_name = self._parse_name(full_name)
                
                record = ArrestRecord(
                    County=court or "Connecticut",
                    Booking_Number=docket_number,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    Booking_Date=filed_date,
                    Charges=charges,
                    Status="Pending",
                    Detail_URL=BASE_URL,
                    Facility="Connecticut Judicial System",
                    LastCheckedMode="INITIAL"
                )
                records.append(record)
            except Exception as e:
                logger.debug(f"Connecticut: Failed to parse entry: {e}")
                continue
        
        return records
    
    def _parse_dom(self, soup) -> List[ArrestRecord]:
        """
        Fallback DOM parsing.
        """
        records = []
        try:
            # Look for case tables
            for row in soup.select("table tr, .case-row, .result-row"):
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
                
                # Extract docket number
                docket_match = re.search(r"(\d{2}-[A-Z]{2}-\d{6})", text)
                
                record = ArrestRecord(
                    County="Connecticut",
                    Booking_Number=docket_match.group(1) if docket_match else "",
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    Status="Pending",
                    Facility="Connecticut Judicial System",
                    LastCheckedMode="INITIAL"
                )
                records.append(record)
        except Exception as e:
            logger.warning(f"Connecticut DOM parse error: {e}")
        
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
