"""
Leon County Arrest Scraper — Odyssey REST API.
Source: Leon County Sheriff's Office
URL: https://www.leoncountyso.com/resources/inmate-search
Method: DrissionPage — intercept Odyssey API calls
"""
import logging
import json
import re
import time
from datetime import datetime, timezone
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.leoncountyso.com"
SEARCH_URL = f"{BASE_URL}/resources/inmate-search"
FACILITY = "Leon County Detention Facility"


class LeonCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Leon"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed"); return []

        # Try direct HTTP first
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        records = []

        # Try known Odyssey API patterns
        api_urls = [
            f"{BASE_URL}/api/inmates",
            f"{BASE_URL}/api/bookings",
            "https://inmate.leoncountyso.com/api/inmates",
            "https://jail.leoncountyso.com/api/inmates",
        ]

        for api_url in api_urls:
            try:
                resp = session.get(api_url, timeout=15, params={"status": "IN_CUSTODY", "page": 1, "size": 100})
                if resp.status_code == 200:
                    data = resp.json()
                    records = self._parse_api(data)
                    if records:
                        logger.info(f"Leon: {len(records)} records from API")
                        return records
            except Exception:
                continue

        # Fallback: browser-based scraping
        records = self._browser_scrape()
        logger.info(f"Leon: {len(records)} records")
        return records

    def _browser_scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            return []

        co = ChromiumOptions()
        co.auto_port()
        co.headless(True)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--window-size=1920,1080")
        co.set_argument("--disable-gpu")
        co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36")
        page = ChromiumPage(addr_or_opts=co)

        all_records = []
        api_responses = []

        try:
            page.listen.start("json")
            page.get(SEARCH_URL)
            time.sleep(5)

            # Try to trigger a search
            try:
                search_btn = (
                    page.ele("text:Search", timeout=3) or
                    page.ele("css:button[type='submit']", timeout=2) or
                    page.ele("css:input[type='submit']", timeout=2)
                )
                if search_btn:
                    search_btn.click()
                    time.sleep(4)
            except Exception:
                pass

            # Collect API responses
            for pkt in page.listen.steps(timeout=15):
                try:
                    body = pkt.response.body if hasattr(pkt, "response") and pkt.response else None
                    if isinstance(body, str) and body.strip().startswith(("{", "[")):
                        body = json.loads(body)
                    if isinstance(body, (dict, list)):
                        api_responses.append(body)
                except Exception:
                    pass

            for data in api_responses:
                all_records.extend(self._parse_api(data))

            if not all_records:
                all_records = self._parse_dom(page)

            return all_records

        except Exception as e:
            logger.error(f"Leon browser: {e}"); return []
        finally:
            try:
                page.listen.stop()
                page.quit()
            except Exception:
                pass

    def _parse_api(self, data) -> List[ArrestRecord]:
        records = []
        entries = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for key in ["data", "results", "inmates", "bookings", "items", "records"]:
                if key in data and isinstance(data[key], list):
                    entries = data[key]
                    break

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            full_name = entry.get("fullName") or f"{entry.get('firstName', '')} {entry.get('lastName', '')}".strip()
            if not full_name:
                continue
            f, m, l = self._pn(full_name)
            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=str(entry.get("bookingNumber") or entry.get("bookingId") or ""),
                Full_Name=full_name,
                First_Name=entry.get("firstName", f),
                Middle_Name=entry.get("middleName", m),
                Last_Name=entry.get("lastName", l),
                Booking_Date=str(entry.get("bookingDate") or entry.get("arrestDate") or ""),
                Status="In Custody",
                Facility=FACILITY,
                Race=str(entry.get("race") or ""),
                Sex=str(entry.get("sex") or entry.get("gender") or "")[:1].upper(),
                Charges=str(entry.get("charges") or ""),
                Bond_Amount=str(entry.get("bondAmount") or entry.get("totalBond") or "0"),
                Mugshot_URL=str(entry.get("mugshotUrl") or entry.get("photoUrl") or ""),
                LastCheckedMode="INITIAL",
            ))
        return records

    def _parse_dom(self, page) -> List[ArrestRecord]:
        records = []
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(page.html, "html.parser")
            for table in soup.find_all("table"):
                text = table.get_text(" ").lower()
                if any(kw in text for kw in ["name", "booking", "inmate"]):
                    for row in table.find_all("tr")[1:]:
                        cells = row.find_all("td")
                        if len(cells) < 2:
                            continue
                        texts = [c.get_text(strip=True) for c in cells]
                        full_name = texts[0] if texts else ""
                        if not full_name:
                            continue
                        f, m, l = self._pn(full_name)
                        records.append(ArrestRecord(
                            County=self.county,
                            Full_Name=full_name,
                            First_Name=f,
                            Middle_Name=m,
                            Last_Name=l,
                            Booking_Number=texts[1] if len(texts) > 1 else "",
                            Booking_Date=texts[2] if len(texts) > 2 else "",
                            Status="In Custody",
                            Facility=FACILITY,
                            LastCheckedMode="INITIAL",
                        ))
                    break
        except Exception as e:
            logger.error(f"Leon DOM: {e}")
        return records

    @staticmethod
    def _pn(n):
        if not n:
            return "", "", ""
        n = " ".join(n.strip().split())
        if "," in n:
            p = n.split(",", 1)
            l = p[0].strip()
            fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm) > 1 else ""), l
        p = n.split()
        return p[0], (" ".join(p[2:]) if len(p) > 2 else ""), p[-1] if len(p) >= 2 else ""
