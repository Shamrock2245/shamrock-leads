"""
Orange County Arrest Scraper — OCSO Inmate Search via DrissionPage.

Source: Orange County Corrections Division
URL: https://apps.ocfl.net/bailbond/default.asp
Method: DrissionPage browser automation + HTML table parsing

Architecture:
1. Navigate to OCSO inmate search portal
2. Search for recent bookings (last 3 days)
3. Parse listing results into individual records
4. Visit detail pages to enrich with charges & bond amounts
5. Map all fields → ArrestRecord schema

Note: Orange County uses an ASP-based legacy portal.
DrissionPage handles the form submission and JS rendering.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
BASE_URL = "https://apps.ocfl.net/bailbond"
SEARCH_URL = f"{BASE_URL}/default.asp"
DETAIL_BASE = f"{BASE_URL}/detail.asp"
DAYS_BACK = 3
MAX_RESULTS = 500
DETAIL_DELAY_S = 0.8


class OrangeCountyScraper(BaseScraper):
    """Orange County (FL) arrest scraper — OCSO Inmate Search."""

    @property
    def county(self) -> str:
        return "Orange"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape Orange County via DrissionPage browser automation."""
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
            records = self._scrape_inmate_search(page)
            logger.info(f"✅ Scraped {len(records)} records from Orange County")
            return records

        except Exception as e:
            logger.error(f"❌ Orange County scraper fatal error: {e}")
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

    def _scrape_inmate_search(self, page) -> List[ArrestRecord]:
        """Navigate to the inmate search and extract records."""
        records: List[ArrestRecord] = []

        # Navigate to search page
        logger.info(f"📡 Loading Orange County search: {SEARCH_URL}")
        page.get(SEARCH_URL)
        time.sleep(3)

        # Try to search by recent bookings
        try:
            # The OCSO portal typically allows searching by date range or name
            # Try clicking "Search" directly for recent bookings first
            search_btn = page.ele('xpath://input[@type="submit"]') or page.ele('text:Search')
            if search_btn:
                search_btn.click()
                time.sleep(3)
        except Exception as e:
            logger.warning(f"⚠️ Could not submit search form: {e}")

        # Extract records from the results table
        try:
            records = self._parse_results_page(page)
        except Exception as e:
            logger.error(f"❌ Error parsing results: {e}")

        return records

    def _parse_results_page(self, page) -> List[ArrestRecord]:
        """Parse the inmate listing table and extract records."""
        records: List[ArrestRecord] = []

        # Find result rows — typically in a table
        rows = page.eles('xpath://table//tr[td]')
        if not rows:
            # Try alternate selectors
            rows = page.eles('css:table.results tr') or page.eles('css:#results tr')

        logger.info(f"📋 Found {len(rows)} result rows")

        for idx, row in enumerate(rows):
            try:
                cells = row.eles('tag:td')
                if len(cells) < 4:
                    continue

                # Extract cell text (layout varies by portal version)
                cell_texts = [self._clean(c.text) for c in cells]

                # Try to identify name, booking #, date, charges from cell positions
                record = self._map_row_to_record(cell_texts, row)
                if record and record.Full_Name and record.Booking_Number:
                    records.append(record)

                if idx > 0 and idx % 25 == 0:
                    logger.info(f"🔍 Progress: {idx}/{len(rows)} rows parsed")

            except Exception as e:
                logger.warning(f"⚠️ Error parsing row {idx}: {e}")
                continue

        return records

    def _map_row_to_record(self, cells: list, row) -> Optional[ArrestRecord]:
        """Map cell values to ArrestRecord fields."""
        if len(cells) < 4:
            return None

        # Common OCSO table layout:
        # [0] Name, [1] Booking #, [2] DOB, [3] Booking Date,
        # [4] Charges, [5] Bond Amount, [6] Status
        name = cells[0] if len(cells) > 0 else ""
        booking_num = cells[1] if len(cells) > 1 else ""
        dob = cells[2] if len(cells) > 2 else ""
        booking_date = cells[3] if len(cells) > 3 else ""
        charges = cells[4] if len(cells) > 4 else ""
        bond = cells[5] if len(cells) > 5 else "0"
        status = cells[6] if len(cells) > 6 else "In Custody"

        # Parse name
        first_name, middle_name, last_name = self._parse_name(name)
        full_name = name

        # Parse bond
        bond_amount = self._parse_bond(bond)

        # Try to get detail URL
        detail_url = ""
        try:
            link = row.ele('tag:a')
            if link:
                href = link.attr("href") or ""
                if href and not href.startswith("http"):
                    href = f"{BASE_URL}/{href}"
                detail_url = href
        except Exception:
            pass

        return ArrestRecord(
            County=self.county,
            Booking_Number=self._clean(booking_num),
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB=self._clean(dob),
            Booking_Date=self._clean(booking_date),
            Arrest_Date=self._clean(booking_date),
            Status=self._clean(status) or "In Custody",
            Facility="Orange County Jail",
            Charges=self._clean(charges),
            Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
            Bond_Paid="NO",
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        )

    # ── Utilities ──

    @staticmethod
    def _clean(text: str) -> str:
        """Clean and normalize text."""
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _parse_name(name_str: str):
        """Parse 'LAST, FIRST MIDDLE' into components."""
        if not name_str:
            return "", "", ""

        name_str = " ".join(name_str.strip().split())

        if "," in name_str:
            parts = name_str.split(",", 1)
            last_name = parts[0].strip()
            first_middle = parts[1].strip() if len(parts) > 1 else ""

            name_parts = first_middle.split()
            first_name = name_parts[0] if name_parts else ""
            middle_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

            return first_name, middle_name, last_name

        parts = name_str.split()
        if len(parts) >= 2:
            return parts[0], " ".join(parts[2:]) if len(parts) > 2 else "", parts[-1]

        return name_str, "", ""

    @staticmethod
    def _parse_bond(bond_str: str) -> float:
        """Extract numeric bond amount from string."""
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
