"""
Pinellas County Arrest Scraper — Sheriff's Inmate Booking via DrissionPage.

Source: Pinellas County Sheriff's Office
URL: https://www.pinellassheriff.gov/InmateBooking
Method: DrissionPage browser automation (migrated from Selenium)

Architecture:
1. Navigate to PCSO Inmate Booking search page
2. Search by booking date (last 3 days, one day at a time)
3. Parse listing page for basic inmate info
4. Visit detail pages for charges + bond amounts
5. Map all fields → ArrestRecord schema

Note: Original solver used Selenium — ported to DrissionPage for
consistency with the rest of the shamrock-leads fleet.
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
BASE_URL = "https://www.pinellassheriff.gov"
SEARCH_URL = f"{BASE_URL}/InmateBooking"
DAYS_BACK = 3
DETAIL_DELAY_S = 1.0


class PinellasCountyScraper(BaseScraper):
    """Pinellas County (FL) arrest scraper — PCSO Inmate Booking."""

    @property
    def county(self) -> str:
        return "Pinellas"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape Pinellas County via DrissionPage browser automation."""
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error(
                "❌ DrissionPage not installed. "
                "Install with: pip install DrissionPage"
            )
            return []

        page = self._setup_browser()
        all_records: List[ArrestRecord] = []

        try:
            # Scrape last N days one at a time
            for days_ago in range(DAYS_BACK):
                target_date = datetime.now() - timedelta(days=days_ago)
                date_str = target_date.strftime("%m/%d/%Y")

                logger.info(f"📅 Pinellas: Searching bookings for {date_str}")

                try:
                    daily_records = self._scrape_date(page, date_str)
                    all_records.extend(daily_records)
                    logger.info(
                        f"✅ {date_str}: {len(daily_records)} records"
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Error on {date_str}: {e}")

                time.sleep(2)

            logger.info(
                f"✅ Scraped {len(all_records)} total records from Pinellas"
            )
            return all_records

        except Exception as e:
            logger.error(f"❌ Pinellas scraper fatal error: {e}")
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

    def _scrape_date(self, page, date_str: str) -> List[ArrestRecord]:
        """Scrape bookings for a single date."""
        records: List[ArrestRecord] = []

        # Navigate to search
        page.get(SEARCH_URL)
        time.sleep(3)

        # Fill in date and submit
        try:
            # Look for date input field
            date_input = (
                page.ele('css:input[type="date"]')
                or page.ele('css:input[name*="date"]')
                or page.ele('css:input[id*="date"]')
                or page.ele('css:#BookingDate')
            )
            if date_input:
                date_input.clear()
                date_input.input(date_str)
                time.sleep(1)

            # Submit search
            search_btn = (
                page.ele('css:button[type="submit"]')
                or page.ele('css:input[type="submit"]')
                or page.ele('text:Search')
            )
            if search_btn:
                search_btn.click()
                time.sleep(3)

        except Exception as e:
            logger.warning(f"⚠️ Form submission error: {e}")
            return records

        # Parse results
        rows = page.eles('xpath://table//tr[td]')
        if not rows:
            rows = page.eles('css:.inmate-row') or page.eles('css:.booking-row')

        logger.info(f"📋 Found {len(rows)} booking rows for {date_str}")

        for idx, row in enumerate(rows):
            try:
                record = self._parse_booking_row(row, date_str)
                if record and record.Full_Name and record.Booking_Number:
                    records.append(record)
            except Exception as e:
                logger.warning(f"⚠️ Row parse error: {e}")
                continue

        return records

    def _parse_booking_row(self, row, date_str: str) -> Optional[ArrestRecord]:
        """Parse a single booking row into an ArrestRecord."""
        cells = row.eles('tag:td')
        if len(cells) < 3:
            return None

        cell_texts = [self._clean(c.text) for c in cells]

        # PCSO table layout varies, but typically:
        # Name | Booking # | Booking Date | Charges | Bond
        name = cell_texts[0] if len(cell_texts) > 0 else ""
        booking_num = cell_texts[1] if len(cell_texts) > 1 else ""
        booking_date = cell_texts[2] if len(cell_texts) > 2 else date_str
        charges = cell_texts[3] if len(cell_texts) > 3 else ""
        bond = cell_texts[4] if len(cell_texts) > 4 else "0"
        race = cell_texts[5] if len(cell_texts) > 5 else ""
        sex = cell_texts[6] if len(cell_texts) > 6 else ""

        # Parse name
        first_name, middle_name, last_name = self._parse_name(name)

        # Parse bond
        bond_amount = self._parse_bond(bond)

        # Detail URL
        detail_url = ""
        try:
            link = row.ele('tag:a')
            if link:
                href = link.attr("href") or ""
                if href and not href.startswith("http"):
                    href = f"{BASE_URL}{href}"
                detail_url = href
        except Exception:
            pass

        return ArrestRecord(
            County=self.county,
            Booking_Number=self._clean(booking_num),
            Full_Name=name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            Booking_Date=booking_date,
            Arrest_Date=booking_date,
            Status="In Custody",
            Facility="Pinellas County Jail",
            Race=self._clean(race),
            Sex=self._clean(sex),
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
