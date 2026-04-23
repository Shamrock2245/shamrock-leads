"""
Palm Beach County Arrest Scraper — PBSO Blotter via DrissionPage.

Source: Palm Beach County Sheriff's Office
URL: https://www3.pbso.org/blotter/index.cfm
Method: DrissionPage browser automation

Architecture:
1. Navigate to PBSO Blotter search page
2. Search by date range (last 3 days, oldest to newest)
3. Handle potential hCaptcha challenges (may require manual intervention)
4. Parse paginated results for inmate records
5. Map all fields → ArrestRecord schema

Note: PBSO Blotter uses ColdFusion backend and may present hCaptcha.
In headless mode, captcha bypass is unreliable — scraper gracefully
degrades and returns whatever data it can access.
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
BASE_URL = "https://www3.pbso.org/blotter"
SEARCH_URL = f"{BASE_URL}/index.cfm"
DAYS_BACK = 3
PAGE_DELAY_S = 2.0
MAX_PAGES = 10


class PalmBeachCountyScraper(BaseScraper):
    """Palm Beach County (FL) arrest scraper — PBSO Blotter."""

    @property
    def county(self) -> str:
        return "Palm Beach"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape Palm Beach County via DrissionPage browser automation."""
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
            # Scrape date range (oldest to newest)
            for days_ago in range(DAYS_BACK, 0, -1):
                target_date = datetime.now() - timedelta(days=days_ago)
                date_str = target_date.strftime("%m/%d/%Y")

                logger.info(f"📅 Palm Beach: Searching bookings for {date_str}")

                try:
                    daily = self._scrape_date(page, target_date)
                    all_records.extend(daily)
                    logger.info(f"✅ {date_str}: {len(daily)} records")
                except Exception as e:
                    logger.warning(f"⚠️ Error on {date_str}: {e}")

                time.sleep(2)

            logger.info(
                f"✅ Scraped {len(all_records)} total records from Palm Beach"
            )
            return all_records

        except Exception as e:
            logger.error(f"❌ Palm Beach scraper fatal error: {e}")
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

    def _scrape_date(self, page, target_date: datetime) -> List[ArrestRecord]:
        """Scrape bookings for a single date from PBSO Blotter."""
        records: List[ArrestRecord] = []
        date_str = target_date.strftime("%m/%d/%Y")

        # Navigate to blotter
        page.get(SEARCH_URL)
        time.sleep(3)

        # Check for hCaptcha
        captcha_el = page.ele('css:iframe[src*="hcaptcha.com"]')
        if captcha_el:
            logger.warning(
                "⚠️ hCaptcha detected on PBSO Blotter. "
                "Attempting to proceed without solving..."
            )
            # In headless mode, we can't solve hCaptcha
            # Try waiting to see if it clears automatically
            time.sleep(10)
            captcha_still = page.ele('css:iframe[src*="hcaptcha.com"]')
            if captcha_still:
                logger.warning(
                    "❌ hCaptcha still present — Palm Beach may return 0 records"
                )
                return records

        # Fill date fields
        try:
            # Find date input(s) — PBSO typically has from/to date fields
            date_from = (
                page.ele('css:#fromDate')
                or page.ele('css:input[name*="from"]')
                or page.ele('css:input[name*="From"]')
                or page.ele('css:input[name*="startDate"]')
            )
            date_to = (
                page.ele('css:#toDate')
                or page.ele('css:input[name*="to"]')
                or page.ele('css:input[name*="To"]')
                or page.ele('css:input[name*="endDate"]')
            )

            if date_from:
                date_from.clear()
                date_from.input(date_str)
            if date_to:
                date_to.clear()
                date_to.input(date_str)

            time.sleep(1)

            # Submit search
            search_btn = (
                page.ele('css:input[type="submit"]')
                or page.ele('css:button[type="submit"]')
                or page.ele('text:Search')
                or page.ele('text:Submit')
            )
            if search_btn:
                search_btn.click()
                time.sleep(4)

        except Exception as e:
            logger.warning(f"⚠️ Form submission error: {e}")
            return records

        # Parse paginated results
        page_num = 1
        while page_num <= MAX_PAGES:
            page_records = self._parse_results_page(page, date_str)
            records.extend(page_records)

            if not page_records:
                break

            # Check for next page
            next_btn = (
                page.ele('text:Next')
                or page.ele('css:a.next')
                or page.ele('css:.pagination .next a')
            )
            if not next_btn:
                break

            try:
                next_btn.click()
                time.sleep(PAGE_DELAY_S)
                page_num += 1
            except Exception:
                break

        return records

    def _parse_results_page(
        self, page, date_str: str
    ) -> List[ArrestRecord]:
        """Parse a single page of PBSO blotter results."""
        records: List[ArrestRecord] = []

        rows = page.eles('xpath://table//tr[td]')
        if not rows:
            rows = page.eles('css:.blotter-row') or page.eles('css:.arrest-row')

        for row in rows:
            try:
                cells = row.eles('tag:td')
                if len(cells) < 3:
                    continue

                cell_texts = [self._clean(c.text) for c in cells]

                record = self._map_row_to_record(cell_texts, row, date_str)
                if record and record.Full_Name and record.Booking_Number:
                    records.append(record)

            except Exception as e:
                logger.debug(f"   Row parse error: {e}")
                continue

        return records

    def _map_row_to_record(
        self, cells: list, row, date_str: str
    ) -> Optional[ArrestRecord]:
        """Map PBSO blotter row to ArrestRecord."""
        if len(cells) < 3:
            return None

        # PBSO blotter layout varies, typical:
        # [0] Name, [1] DOB/Age, [2] Race/Sex, [3] Charges,
        # [4] Booking Date, [5] Bond, [6] Status/Location
        name = cells[0] if len(cells) > 0 else ""
        dob_or_age = cells[1] if len(cells) > 1 else ""
        race_sex = cells[2] if len(cells) > 2 else ""
        charges = cells[3] if len(cells) > 3 else ""
        booking_date = cells[4] if len(cells) > 4 else date_str
        bond = cells[5] if len(cells) > 5 else "0"
        status = cells[6] if len(cells) > 6 else "In Custody"

        # Parse name
        first_name, middle_name, last_name = self._parse_name(name)

        # Parse race/sex
        race, sex = self._parse_race_sex(race_sex)

        # Generate booking number from name + date (PBSO doesn't always show one)
        booking_num = ""
        try:
            link = row.ele('tag:a')
            if link:
                href = link.attr("href") or ""
                # Extract booking/case ID from URL
                id_match = re.search(r'(?:id|booking|case)=(\w+)', href, re.I)
                if id_match:
                    booking_num = id_match.group(1)
        except Exception:
            pass

        if not booking_num:
            # Generate a deterministic ID from name + date
            clean_name = re.sub(r'\W+', '', name.upper())
            clean_date = re.sub(r'\W+', '', date_str)
            booking_num = f"PB-{clean_date}-{clean_name[:20]}"

        # Parse bond
        bond_amount = self._parse_bond(bond)

        # Detail URL
        detail_url = ""
        try:
            link = row.ele('tag:a')
            if link:
                href = link.attr("href") or ""
                if href and not href.startswith("http"):
                    href = f"{BASE_URL}/{href.lstrip('/')}"
                detail_url = href
        except Exception:
            pass

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_num,
            Full_Name=name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB=self._clean(dob_or_age) if re.search(r'\d{2}/\d{2}', dob_or_age) else "",
            Booking_Date=booking_date if re.search(r'\d', booking_date) else date_str,
            Arrest_Date=date_str,
            Status=self._clean(status) or "In Custody",
            Facility="Palm Beach County Jail",
            Race=race,
            Sex=sex,
            Charges=self._clean(charges),
            Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
            Bond_Paid="NO",
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        )

    # ── Utilities ──

    @staticmethod
    def _clean(text: str) -> str:
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _parse_name(name_str: str):
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
    def _parse_race_sex(race_sex_str: str):
        """Parse combined race/sex field (e.g., 'W/M', 'B/F')."""
        if not race_sex_str:
            return "", ""
        parts = race_sex_str.strip().split("/")
        race = parts[0].strip() if len(parts) > 0 else ""
        sex = parts[1].strip() if len(parts) > 1 else ""
        return race, sex

    @staticmethod
    def _parse_bond(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
