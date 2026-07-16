"""
Tennessee TnCIS Scraper v2.1 — Full APE Integration
Enhanced with Autonomous Proxy Engine for maximum stealth and reliability.

Features:
- 4-layer stealth stack (IP + TLS + Engine + Behavior)
- Automatic proxy failover (Warren → S5W2C → Stormsia)
- Sticky session routing for multi-step flows
- Metrics tracking and health checking
- Poison pill detection
"""

import logging
import json
import re
import time
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from scrapers.base_scraper import BaseScraper
from scrapers.stealth_utils import (
    TLSFingerprinter,
    BehaviorSimulator,
    PatchrightBrowserManager,
    CurlCFFISession,
    get_stealth_config,
)
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://lgc-tn.com/tncis-web-inquiry/"


class TennesseeTnCISScraperV2APE(BaseScraper):
    """
    Tennessee TnCIS Scraper with full Autonomous Proxy Engine integration.
    Covers 80+ Tennessee counties via statewide case management system.
    """
    
    @property
    def county(self) -> str:
        return "Tennessee_Statewide"
    
    def scrape(self) -> List[ArrestRecord]:
        """
        Scrape TnCIS using full 4-layer stealth stack with APE.
        
        Fallback chain:
        1. curl_cffi + Warren (residential)
        2. curl_cffi + S5W2C (mobile)
        3. Patchright + Warren
        4. undetected-chrome + Stormsia
        5. Obscura (fallback)
        """
        all_records = []
        
        # Attempt 1: curl_cffi + Warren (fastest, residential)
        try:
            logger.info("TnCIS v2.1: Attempting curl_cffi + Warren...")
            records = self._scrape_with_curl_cffi_ape()
            if records:
                logger.info(f"✅ TnCIS v2.1: curl_cffi + Warren succeeded, found {len(records)} records")
                return records
        except Exception as e:
            logger.warning(f"TnCIS v2.1: curl_cffi + Warren failed: {e}")
        
        # Attempt 2: curl_cffi + S5W2C (mobile data)
        try:
            logger.info("TnCIS v2.1: Attempting curl_cffi + S5W2C...")
            records = self._scrape_with_curl_cffi_s5w2c()
            if records:
                logger.info(f"✅ TnCIS v2.1: curl_cffi + S5W2C succeeded, found {len(records)} records")
                return records
        except Exception as e:
            logger.warning(f"TnCIS v2.1: curl_cffi + S5W2C failed: {e}")
        
        # Attempt 3: Patchright + Warren
        try:
            logger.info("TnCIS v2.1: Attempting Patchright + Warren...")
            records = asyncio.run(self._scrape_with_patchright_ape())
            if records:
                logger.info(f"✅ TnCIS v2.1: Patchright + Warren succeeded, found {len(records)} records")
                return records
        except Exception as e:
            logger.warning(f"TnCIS v2.1: Patchright + Warren failed: {e}")
        
        # Attempt 4: Obscura (fallback)
        try:
            logger.info("TnCIS v2.1: Attempting Obscura fallback...")
            records = asyncio.run(self._scrape_with_obscura())
            if records:
                logger.info(f"✅ TnCIS v2.1: Obscura succeeded, found {len(records)} records")
                return records
        except Exception as e:
            logger.warning(f"TnCIS v2.1: Obscura failed: {e}")
        
        logger.error("TnCIS v2.1: All scraping methods failed")
        return []
    
    def _scrape_with_curl_cffi_ape(self) -> List[ArrestRecord]:
        """
        Layer 2 (TLS) + Layer 1 (IP): curl_cffi with APE proxy selection.
        """
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            raise ImportError("curl_cffi not installed")
        
        records = []
        
        # Get proxy from APE (auto failover)
        proxy = self.get_proxy(prefer_residential=True)
        if not proxy:
            logger.warning("No proxies available from APE")
            return []
        
        logger.debug(f"Using proxy: {proxy[:50]}...")
        
        session = CurlCFFISession.create_session(proxy=proxy)
        
        try:
            # Step 1: Get main page with stealth headers
            logger.info("TnCIS v2.1: Fetching main page with curl_cffi...")
            
            start_time = time.time()
            resp = CurlCFFISession.make_request(
                session,
                BASE_URL,
                method="GET",
                timeout=20
            )
            resp.raise_for_status()
            response_time_ms = (time.time() - start_time) * 1000
            
            # Record success in APE
            self.record_proxy_success(proxy, response_time_ms=response_time_ms)
            
            # Behavioral simulation: random delay
            if get_stealth_config().behavioral_simulation:
                BehaviorSimulator.random_delay(0.5, 2.0)
            
            # Step 2: Parse HTML and find API endpoint
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            
            api_endpoint = self._extract_api_endpoint(soup)
            if not api_endpoint:
                logger.warning("TnCIS v2.1: No API endpoint found")
                return []
            
            # Step 3: Query API with stealth
            logger.info(f"TnCIS v2.1: Querying API: {api_endpoint}")
            
            params = {
                "searchType": "criminal",
                "dateFrom": self._get_date_range_start(),
                "dateTo": datetime.now(timezone.utc).strftime("%m/%d/%Y"),
                "sortBy": "date_desc",
                "pageSize": 500,
            }
            
            api_resp = CurlCFFISession.make_request(
                session,
                api_endpoint,
                method="GET",
                params=params,
                timeout=20
            )
            api_resp.raise_for_status()
            
            # Behavioral simulation: random delay
            if get_stealth_config().behavioral_simulation:
                BehaviorSimulator.random_delay(0.5, 2.0)
            
            # Step 4: Parse response
            if api_resp.headers.get("content-type", "").startswith("application/json"):
                data = api_resp.json()
                records = self._extract_from_api(data)
            else:
                soup = BeautifulSoup(api_resp.text, "html.parser")
                records = self._parse_dom(soup)
            
            return records
        
        except Exception as e:
            # Record failure in APE
            self.record_proxy_failure(proxy)
            logger.error(f"curl_cffi + APE scrape failed: {e}")
            raise
        
        finally:
            session.close()
    
    def _scrape_with_curl_cffi_s5w2c(self) -> List[ArrestRecord]:
        """
        Layer 2 (TLS) + Layer 1 (Mobile IP): curl_cffi with S5W2C mobile proxy.
        """
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            raise ImportError("curl_cffi not installed")
        
        # Get S5W2C proxy (mobile data exit)
        if not self.ape or not self.ape.s5w2c_manager.is_available():
            logger.warning("S5W2C not available")
            return []
        
        proxy = self.ape.s5w2c_manager.get_proxy()
        logger.debug(f"Using S5W2C proxy: {proxy}")
        
        session = CurlCFFISession.create_session(proxy=proxy)
        
        try:
            # Similar flow to curl_cffi_ape but with mobile data
            start_time = time.time()
            resp = CurlCFFISession.make_request(
                session,
                BASE_URL,
                method="GET",
                timeout=20
            )
            resp.raise_for_status()
            response_time_ms = (time.time() - start_time) * 1000
            
            self.record_proxy_success(proxy, response_time_ms=response_time_ms)
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            
            api_endpoint = self._extract_api_endpoint(soup)
            if not api_endpoint:
                return []
            
            params = {
                "searchType": "criminal",
                "dateFrom": self._get_date_range_start(),
                "dateTo": datetime.now(timezone.utc).strftime("%m/%d/%Y"),
                "sortBy": "date_desc",
                "pageSize": 500,
            }
            
            api_resp = CurlCFFISession.make_request(
                session,
                api_endpoint,
                method="GET",
                params=params,
                timeout=20
            )
            api_resp.raise_for_status()
            
            if api_resp.headers.get("content-type", "").startswith("application/json"):
                data = api_resp.json()
                records = self._extract_from_api(data)
            else:
                soup = BeautifulSoup(api_resp.text, "html.parser")
                records = self._parse_dom(soup)
            
            return records
        
        except Exception as e:
            self.record_proxy_failure(proxy)
            logger.error(f"curl_cffi + S5W2C scrape failed: {e}")
            raise
        
        finally:
            session.close()
    
    async def _scrape_with_patchright_ape(self) -> List[ArrestRecord]:
        """
        Layer 3 (Engine) + Layer 1 (IP): Patchright with APE proxy.
        """
        proxy = self.get_proxy(prefer_residential=True)
        if not proxy:
            return []
        
        pw, browser = await PatchrightBrowserManager.create_stealth_browser(proxy=proxy)
        
        try:
            context = await PatchrightBrowserManager.create_stealth_context(browser, proxy=proxy)
            page = await context.new_page()
            
            logger.info("TnCIS v2.1: Navigating with Patchright + APE...")
            start_time = time.time()
            await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
            response_time_ms = (time.time() - start_time) * 1000
            
            self.record_proxy_success(proxy, response_time_ms=response_time_ms)
            
            # Behavioral simulation
            if get_stealth_config().behavioral_simulation:
                await BehaviorSimulator.async_random_delay(0.5, 2.0)
            
            content = await page.content()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "html.parser")
            
            records = self._parse_dom(soup)
            
            await page.close()
            await context.close()
            return records
        
        except Exception as e:
            self.record_proxy_failure(proxy)
            logger.error(f"Patchright + APE scrape failed: {e}")
            raise
        
        finally:
            await browser.close()
            await pw.__aexit__(None, None, None)
    
    async def _scrape_with_obscura(self) -> List[ArrestRecord]:
        """
        Layer 1+2+3 (IP+TLS+Engine): Obscura CDP fallback.
        """
        pw, browser = await self._get_obscura_browser()
        
        try:
            page = await browser.new_page()
            
            logger.info("TnCIS v2.1: Navigating with Obscura...")
            await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
            
            if get_stealth_config().behavioral_simulation:
                await BehaviorSimulator.async_random_delay(0.5, 2.0)
            
            content = await page.content()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "html.parser")
            
            records = self._parse_dom(soup)
            
            await page.close()
            return records
        
        finally:
            await browser.close()
            await pw.__aexit__(None, None, None)
    
    def _extract_api_endpoint(self, soup) -> str:
        """Extract API endpoint from HTML."""
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string:
                if "api" in script.string.lower():
                    matches = re.findall(r'(["\'])(/api/[^"\']+)\1', script.string)
                    if matches:
                        return matches[0][1]
        
        form = soup.find("form")
        if form and form.get("action"):
            return form["action"]
        
        return "https://lgc-tn.com/api/search"
    
    def _extract_from_api(self, data: Any) -> List[ArrestRecord]:
        """Extract records from API JSON response."""
        records = []
        
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
                logger.debug(f"TnCIS v2.1: Failed to parse entry: {e}")
                continue
        
        return records
    
    def _parse_dom(self, soup) -> List[ArrestRecord]:
        """Fallback DOM parsing."""
        records = []
        try:
            for row in soup.select("table tr, .case-row, .result-row, .record"):
                cells = row.find_all(["td", "span", "div"])
                if len(cells) < 2:
                    continue
                
                text = row.get_text(" ", strip=True)
                
                name_match = re.search(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*[A-Z][a-z]+)", text)
                if not name_match:
                    continue
                
                full_name = name_match.group(1)
                first_name, middle_name, last_name = self._parse_name(full_name)
                
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
            logger.warning(f"TnCIS v2.1 DOM parse error: {e}")
        
        return records
    
    @staticmethod
    def _get_field(entry: dict, keys: List[str]) -> str:
        """Get field value from dict using multiple possible keys."""
        for key in keys:
            if key in entry and entry[key]:
                val = entry[key]
                if isinstance(val, list):
                    return " | ".join(str(v) for v in val if v)
                return str(val).strip()
        return ""
    
    @staticmethod
    def _parse_name(name_str: str):
        """Parse name string into first, middle, last."""
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
        """Get start date for case search (last 7 days)."""
        start = datetime.now(timezone.utc) - timedelta(days=7)
        return start.strftime("%m/%d/%Y")
