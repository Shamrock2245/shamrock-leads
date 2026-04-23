"""
Seminole County Arrest Scraper — NorthPointe Suite via DrissionPage.

Source: Seminole County Sheriff's Office
URL: https://seminole.northpointesuite.com/custodyportal
Method: DrissionPage browser automation (migrated from Selenium)

Architecture:
1. Navigate to NorthPointe Suite Custody Portal
2. Click Search to load all current inmates
3. Parse inmate data from JavaScript goToDetails() calls in page source
4. Visit detail pages for booking #, charges, bond amounts
5. Map all fields → ArrestRecord schema

Note: Original solver used Selenium — ported to DrissionPage.
Data is embedded as JSON in `javascript:goToDetails({...})` links.
Results capped at ~500 inmates per search.
"""

import logging
import re
import json
import html as html_lib
import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
BASE_URL = "https://seminole.northpointesuite.com"
PORTAL_URL = f"{BASE_URL}/custodyportal"
DETAIL_URL_TPL = f"{PORTAL_URL}/details/{{}}"
MAX_DETAIL_RECORDS = 150
DETAIL_DELAY_S = 1.0


class SeminoleCountyScraper(BaseScraper):
    """Seminole County (FL) arrest scraper — NorthPointe Suite Portal."""

    @property
    def county(self) -> str:
        return "Seminole"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape Seminole County via DrissionPage browser automation."""
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error(
                "❌ DrissionPage not installed. "
                "Install with: pip install DrissionPage"
            )
            return []

        page = self._setup_browser()

        try:
            records = self._scrape_portal(page)
            logger.info(
                f"✅ Scraped {len(records)} records from Seminole"
            )
            return records

        except Exception as e:
            logger.error(f"❌ Seminole scraper fatal error: {e}")
            return []

        finally:
            try:
                page.quit()
            except Exception:
                pass

    # ── Browser Setup ──

    @staticmethod
    def _setup_browser():
        """Configure and launch DrissionPage Chromium browser."""
        from DrissionPage import ChromiumPage, ChromiumOptions

        co = ChromiumOptions()
        co.auto_port()
        co.headless(True)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--window-size=1920,1080")
        co.set_argument("--disable-gpu")
        co.set_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        return ChromiumPage(addr_or_opts=co)

    def _scrape_portal(self, page) -> List[ArrestRecord]:
        """Navigate portal, search, and extract inmate data."""
        records: List[ArrestRecord] = []

        # Navigate to custody portal
        logger.info(f"📡 Loading Seminole portal: {PORTAL_URL}")
        page.get(PORTAL_URL)
        time.sleep(5)

        # Click Search button to load all inmates
        try:
            search_btn = (
                page.ele('css:#searchBtn')
                or page.ele('text:Search')
                or page.ele('css:button[type="submit"]')
            )
            if search_btn:
                search_btn.click()
                time.sleep(3)
        except Exception as e:
            logger.warning(f"⚠️ Search button click failed: {e}")

        # Wait for results to load
        for i in range(15):
            time.sleep(2)
            try:
                page_text = page.html or ""
                if "Searching..." not in page_text:
                    results_match = re.search(
                        r'Search Results \((\d+)\)', page_text
                    )
                    if results_match and int(results_match.group(1)) > 0:
                        logger.info(
                            f"   Results loaded: {results_match.group(1)} inmates"
                        )
                        break
            except Exception:
                continue

        time.sleep(3)

        # Extract records from page source (goToDetails JSON)
        page_source = page.html or ""
        basic_records = self._extract_from_page_source(page_source)
        logger.info(f"📋 Extracted {len(basic_records)} inmates from page source")

        # Enrich with detail pages
        for idx, basic in enumerate(basic_records[:MAX_DETAIL_RECORDS]):
            person_id = basic.get("person_id", "")
            if not person_id:
                records.append(self._basic_to_record(basic))
                continue

            if idx % 25 == 0:
                logger.info(
                    f"   [{idx + 1}/{min(len(basic_records), MAX_DETAIL_RECORDS)}] "
                    f"Fetching details..."
                )

            try:
                details = self._fetch_detail(page, person_id)
                basic.update(details)
            except Exception as e:
                logger.debug(f"   Detail error: {e}")

            records.append(self._basic_to_record(basic))
            time.sleep(DETAIL_DELAY_S)

        # Add remaining records without detail enrichment
        for basic in basic_records[MAX_DETAIL_RECORDS:]:
            records.append(self._basic_to_record(basic))

        return records

    def _extract_from_page_source(self, page_source: str) -> List[Dict[str, Any]]:
        """Extract inmate data from goToDetails({...}) calls in page source."""
        results = []

        # Decode HTML entities
        decoded = html_lib.unescape(page_source)

        # Find all goToDetails JSON objects
        pattern = r'javascript:goToDetails\((\{[^}]+\})\)'
        matches = re.findall(pattern, decoded)

        for match in matches:
            try:
                data = json.loads(match)

                first = data.get("firstName", "") or ""
                last = data.get("lastName", "") or ""
                middle = data.get("middleName", "") or ""

                if middle:
                    full_name = f"{last}, {first} {middle}"
                else:
                    full_name = f"{last}, {first}"

                # Parse DOB
                dob = ""
                dob_raw = data.get("dateOfBirth", "")
                if dob_raw:
                    try:
                        dob_dt = datetime.fromisoformat(
                            dob_raw.replace("Z", "+00:00")
                        )
                        dob = dob_dt.strftime("%m/%d/%Y")
                    except Exception:
                        pass

                results.append({
                    "full_name": full_name.upper(),
                    "first_name": first.upper(),
                    "middle_name": middle.upper() if middle else "",
                    "last_name": last.upper(),
                    "person_id": str(data.get("personId", "")),
                    "booking_number": str(data.get("personId", "")),
                    "age": str(data.get("age", "")),
                    "sex": data.get("gender", ""),
                    "race": data.get("race", ""),
                    "height": data.get("height", ""),
                    "weight": data.get("weight", ""),
                    "dob": dob,
                    "charges": "",
                    "bond_amount": "0",
                    "status": "In Custody",
                    "booking_date": "",
                    "arrest_agency": "",
                })

            except json.JSONDecodeError:
                continue
            except Exception:
                continue

        return results

    def _fetch_detail(self, page, person_id: str) -> Dict[str, Any]:
        """Navigate to detail page and extract booking/charge info."""
        details: Dict[str, Any] = {}
        detail_url = DETAIL_URL_TPL.format(person_id)

        try:
            page.get(detail_url)
            time.sleep(2)

            details["detail_url"] = detail_url
            page_html = page.html or ""

            # Helper: find value by label text in HTML
            def get_val(label_text):
                pattern = rf'{label_text}\s*</td>\s*<td[^>]*>\s*([^<]+)'
                m = re.search(pattern, page_html, re.IGNORECASE)
                return self._clean(m.group(1)) if m else None

            # Booking Number
            bn = get_val("Booking Number") or get_val("Booking #")
            if bn:
                details["booking_number"] = bn

            # Booking Date
            bd = get_val("Booking Date")
            if bd:
                details["booking_date"] = bd

            # Arrest Date
            ad = get_val("Arrest Date")
            if ad:
                details["arrest_date"] = ad

            # Arresting Agency
            aa = get_val("Arresting Agency")
            if aa:
                details["arrest_agency"] = aa

            # Total Bond
            tb = get_val("Total Bond")
            if tb:
                details["bond_amount"] = tb.replace("$", "").replace(",", "")

            # Status
            st = get_val("Status")
            if st:
                details["status"] = st

            # Charges — look for tables with charge headers
            charges = []
            charge_rows = page.eles(
                'xpath://table[.//th[contains(text(), "Offense") or '
                'contains(text(), "Statute") or '
                'contains(text(), "Charge")]]//tr[td]'
            )
            for cr in charge_rows:
                cells = cr.eles('tag:td')
                if len(cells) >= 2:
                    charge_parts = [
                        self._clean(c.text) for c in cells
                        if self._clean(c.text)
                    ]
                    if charge_parts:
                        charges.append(" ".join(charge_parts))

            if charges:
                details["charges"] = " | ".join(charges)

        except Exception as e:
            logger.debug(f"   Detail fetch error: {e}")

        return details

    def _basic_to_record(self, data: Dict[str, Any]) -> ArrestRecord:
        """Convert basic data dict to ArrestRecord."""
        return ArrestRecord(
            County=self.county,
            Booking_Number=data.get("booking_number", ""),
            Person_ID=data.get("person_id", ""),
            Full_Name=data.get("full_name", ""),
            First_Name=data.get("first_name", ""),
            Middle_Name=data.get("middle_name", ""),
            Last_Name=data.get("last_name", ""),
            DOB=data.get("dob", ""),
            Arrest_Date=data.get("arrest_date", ""),
            Booking_Date=data.get("booking_date", ""),
            Status=data.get("status", "In Custody"),
            Facility="John E Polk Correctional Facility",
            Agency=data.get("arrest_agency", ""),
            Race=data.get("race", ""),
            Sex=data.get("sex", ""),
            Height=data.get("height", ""),
            Weight=data.get("weight", ""),
            Charges=data.get("charges", ""),
            Bond_Amount=data.get("bond_amount", "0"),
            Bond_Paid="NO",
            Detail_URL=data.get("detail_url", ""),
            LastCheckedMode="INITIAL",
        )

    # ── Utilities ──

    @staticmethod
    def _clean(text: str) -> str:
        if not text:
            return ""
        return " ".join(str(text).strip().split())
