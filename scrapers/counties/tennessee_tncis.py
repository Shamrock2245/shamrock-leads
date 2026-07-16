"""
Tennessee TnCIS Statewide Scraper — Administrative Office of the Courts.
Source: TnCIS Web Inquiry (LGC)
URL: https://lgc-tn.com/tncis-web-inquiry/
Method: curl_cffi (TLS fingerprinting for Cloudflare bypass) + API interception
Detail URL: https://lgc-tn.com/tncis-web-inquiry/
"""
import logging
import json
import re
import time
from datetime import datetime, timezone
from typing import List, Dict, Any
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://lgc-tn.com/tncis-web-inquiry/"

class TennesseeTnCISScraper(BaseScraper):
    """
    TnCIS Scraper using curl_cffi for Cloudflare bypass.
    Targets the statewide case management system covering 80+ Tennessee counties.
    """
    
    @property
    def county(self) -> str:
        return "Tennessee_Statewide"
    
    def scrape(self) -> List[ArrestRecord]:
        """
        Scrape TnCIS using curl_cffi for TLS fingerprinting.
        Falls back to Obscura browser if curl_cffi fails.
        """
        all_records = []
        
        # Attempt 1: curl_cffi with TLS fingerprinting (fastest)
        try:
            logger.info("TnCIS: Attempting curl_cffi with TLS fingerprinting...")
            records = self._scrape_with_curl_cffi()
            if records:
                logger.info(f"TnCIS: curl_cffi succeeded, found {len(records)} records")
                return records
        except Exception as e:
            logger.warning(f"TnCIS: curl_cffi failed: {e}")
        
        # Attempt 2: Obscura browser (slower but more reliable)
        try:
            logger.info("TnCIS: Falling back to Obscura browser...")
            records = self._scrape_with_obscura()
            if records:
                logger.info(f"TnCIS: Obscura succeeded, found {len(records)} records")
                return records
        except Exception as e:
            logger.warning(f"TnCIS: Obscura failed: {e}")
        
        logger.error("TnCIS: All scraping methods failed")
        return []
    
    def _scrape_with_curl_cffi(self) -> List[ArrestRecord]:
        """
        Use curl_cffi to bypass Cloudflare with JA3 fingerprinting.
        curl_cffi impersonates Chrome's TLS signature, making it invisible to Cloudflare.
        """
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            raise ImportError("curl_cffi not installed. Run: pip install curl_cffi")
        
        records = []
        
        # Step 1: Get the initial page to establish session
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://lgc-tn.com/",
        }
        
        session = cffi_requests.Session()
        
        try:
            # Request the main page
            logger.info("TnCIS: Fetching main page...")
            resp = session.get(BASE_URL, headers=headers, impersonate="chrome126", timeout=15)
            resp.raise_for_status()
            
            # Parse the HTML to find the search form or API endpoint
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Look for hidden API endpoints or form actions
            api_endpoint = self._extract_api_endpoint(soup)
            if not api_endpoint:
                logger.warning("TnCIS: No API endpoint found in HTML")
                return []
            
            # Step 2: Make API request for recent cases
            logger.info(f"TnCIS: Querying API endpoint: {api_endpoint}")
            
            # Common search parameters for "recent arrests"
            params = {
                "searchType": "criminal",
                "dateFrom": self._get_date_range_start(),
                "dateTo": datetime.now(timezone.utc).strftime("%m/%d/%Y"),
                "sortBy": "date_desc",
                "pageSize": 500,
            }
            
            api_resp = session.get(
                api_endpoint,
                params=params,
                headers=headers,
                impersonate="chrome126",
                timeout=15
            )
            api_resp.raise_for_status()
            
            # Parse JSON response
            if api_resp.headers.get("content-type", "").startswith("application/json"):
                data = api_resp.json()
                records = self._extract_from_api(data)
            else:
                # Fallback to HTML parsing
                soup = BeautifulSoup(api_resp.text, "html.parser")
                records = self._parse_dom(soup)
            
            return records
        
        except Exception as e:
            logger.error(f"TnCIS curl_cffi error: {e}")
            raise
        finally:
            session.close()
    
    def _scrape_with_obscura(self) -> List[ArrestRecord]:
        """
        Use Obscura browser (Playwright over CDP) for Cloudflare bypass.
        This is slower but more reliable for complex JavaScript-heavy sites.
        """
        import asyncio
        
        async def _async_scrape():
            pw, browser = await self._get_obscura_browser()
            try:
                page = await browser.new_page()
                await page.goto(BASE_URL, wait_until="networkidle")
                
                # Wait for content to load
                await page.wait_for_timeout(3000)
                
                # Extract API responses from network logs
                records = []
                
                # Try to find and click search button
                search_btn = await page.query_selector("button[type='submit']")
                if search_btn:
                    await search_btn.click()
                    await page.wait_for_timeout(2000)
                
                # Get page content
                content = await page.content()
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(content, "html.parser")
                
                records = self._parse_dom(soup)
                
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
    
    def _extract_api_endpoint(self, soup) -> str:
        """
        Extract the API endpoint from the HTML.
        Look for fetch calls, XHR endpoints, or form actions.
        """
        # Look for JavaScript that contains API URLs
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string:
                # Look for common API patterns
                if "api" in script.string.lower() or "endpoint" in script.string.lower():
                    # Try to extract URL
                    matches = re.findall(r'(["\'])(/api/[^"\']+)\1', script.string)
                    if matches:
                        return matches[0][1]
        
        # Look for form actions
        form = soup.find("form")
        if form and form.get("action"):
            return form["action"]
        
        # Default API endpoint (common pattern)
        return "https://lgc-tn.com/api/search"
    
    def _extract_from_api(self, data: Any) -> List[ArrestRecord]:
        """
        Extract arrest records from API JSON response.
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
                # Extract fields with flexible key matching
                full_name = self._get_field(entry, ["name", "fullName", "defendant", "defendantName"])
                booking_number = self._get_field(entry, ["caseNumber", "case_number", "bookingNumber", "id"])
                booking_date = self._get_field(entry, ["filedDate", "filed_date", "arrestDate", "date"])
                charges = self._get_field(entry, ["charges", "charge", "offense"])
                county = self._get_field(entry, ["county", "jurisdiction"]) or "Tennessee"
                
                if not full_name or not booking_number:
                    continue
                
                first_name, middle_name, last_name = self._parse_name(full_name)
                
                record = ArrestRecord(
                    County=county,
                    Booking_Number=booking_number,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    Booking_Date=booking_date,
                    Charges=charges,
                    Status="In Custody",
                    Detail_URL=BASE_URL,
                    Facility="Tennessee Court System",
                    LastCheckedMode="INITIAL"
                )
                records.append(record)
            except Exception as e:
                logger.debug(f"TnCIS: Failed to parse entry: {e}")
                continue
        
        return records
    
    def _parse_dom(self, soup) -> List[ArrestRecord]:
        """
        Fallback DOM parsing if API fails.
        """
        records = []
        try:
            # Look for case tables or result rows
            for row in soup.select("table tr, .case-row, .result-row, .record"):
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
                
                # Extract case number
                case_match = re.search(r"\b(\d{2}-[A-Z]{2}-\d{6})\b", text)
                
                record = ArrestRecord(
                    County="Tennessee",
                    Booking_Number=case_match.group(1) if case_match else "",
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    Status="In Custody",
                    Facility="Tennessee Court System",
                    LastCheckedMode="INITIAL"
                )
                records.append(record)
        except Exception as e:
            logger.warning(f"TnCIS DOM parse error: {e}")
        
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
    
    @staticmethod
    def _get_date_range_start() -> str:
        """
        Get start date for case search (last 7 days).
        """
        from datetime import timedelta
        start = datetime.now(timezone.utc) - timedelta(days=7)
        return start.strftime("%m/%d/%Y")
