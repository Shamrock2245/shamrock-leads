"""
Osceola County Arrest Scraper — Daily Reports via DrissionPage.

Source: Osceola County Corrections
URL: https://apps.osceola.org/Apps/CorrectionsReports/Report/Daily/
Method: DrissionPage browser automation (migrated from Playwright)

Architecture:
1. Navigate to daily corrections report page
2. Select date from dropdown (last 3 days)
3. Parse listing table for basic inmate info + inmate IDs
4. Visit each detail page to get bond amounts + demographics
5. Map all fields → ArrestRecord schema

Note: Original solver used Playwright — ported to DrissionPage for
fleet consistency. Date dropdown limited to ~30 days of history.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
BASE_URL = "https://apps.osceola.org/Apps/CorrectionsReports"
DAILY_URL = f"{BASE_URL}/Report/Daily/"
DETAIL_URL_TPL = f"{BASE_URL}/Report/Details/{{}}"
DAYS_BACK = 3
DETAIL_DELAY_S = 0.5


class OsceolaCountyScraper(BaseScraper):
    """Osceola County (FL) arrest scraper — Daily Corrections Reports."""

    @property
    def county(self) -> str:
        return "Osceola"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape Osceola County via DrissionPage browser automation."""
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
            for days_ago in range(DAYS_BACK):
                target_date = datetime.now() - timedelta(days=days_ago)
                date_str = target_date.strftime("%m/%d/%Y")

                logger.info(f"📅 Osceola: Scraping date {date_str}")

                try:
                    daily = self._scrape_daily_report(page, target_date)
                    all_records.extend(daily)
                    logger.info(f"✅ {date_str}: {len(daily)} records")
                except Exception as e:
                    logger.warning(f"⚠️ Error on {date_str}: {e}")

                time.sleep(1)

            logger.info(
                f"✅ Scraped {len(all_records)} total records from Osceola"
            )
            return all_records

        except Exception as e:
            logger.error(f"❌ Osceola scraper fatal error: {e}")
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

    def _scrape_daily_report(
        self, page, target_date: datetime
    ) -> List[ArrestRecord]:
        """Scrape all inmates from a specific date's daily report."""
        records: List[ArrestRecord] = []
        date_str = target_date.strftime("%m/%d/%Y")

        # Navigate to daily report page
        page.get(DAILY_URL)
        time.sleep(2)

        # Select date from dropdown
        try:
            date_select = page.ele('css:#date')
            if date_select:
                # Check if date is available
                options = page.eles('css:#date option')
                date_found = False
                for opt in options:
                    opt_text = self._clean(opt.text)
                    if opt_text == date_str:
                        date_found = True
                        break

                if not date_found:
                    logger.info(f"   ⚠️ Date {date_str} not in dropdown")
                    return records

                # Select by text
                date_select.select(date_str)
                time.sleep(2)
        except Exception as e:
            logger.warning(f"   Error selecting date: {e}")
            return records

        # Find all inmate detail links
        detail_links = page.eles('xpath://table//a[contains(@href, "Details")]')
        logger.info(f"   Found {len(detail_links)} inmates for {date_str}")

        # Extract basic info from listing
        inmates_basic = []
        for link in detail_links:
            try:
                href = link.attr("href") or ""
                id_match = re.search(r'/Details/(\d+)', href)
                if not id_match:
                    continue

                inmate_id = id_match.group(1)
                name_text = self._clean(link.text)

                # Get parent row for additional data
                row = link.parent('tag:tr')
                booking_num = ""
                dob = ""
                agency = "OCSO"
                charges_summary = ""

                if row:
                    row_text = self._clean(row.text)

                    booking_match = re.search(r'Booking #:\s*(\d+)', row_text)
                    if booking_match:
                        booking_num = booking_match.group(1)

                    dob_match = re.search(
                        r'Birthdate:\s*([A-Za-z]+ \d+, \d{4})', row_text
                    )
                    if dob_match:
                        dob = self._parse_date(dob_match.group(1))

                    agency_match = re.search(r'By Agency:\s*(\w+)', row_text)
                    if agency_match:
                        agency = agency_match.group(1)

                    # Last cell often has charges
                    cells = row.eles('tag:td')
                    if cells and len(cells) >= 3:
                        charges_summary = self._clean(cells[-1].text)

                inmates_basic.append({
                    "inmate_id": inmate_id,
                    "name": name_text,
                    "booking_number": booking_num,
                    "dob": dob,
                    "agency": agency,
                    "charges_summary": charges_summary,
                    "arrest_date": date_str,
                })

            except Exception as e:
                logger.debug(f"   Error parsing link: {e}")
                continue

        # Visit detail pages for bond amounts
        for idx, basic in enumerate(inmates_basic):
            if idx % 20 == 0:
                logger.info(
                    f"   [{idx + 1}/{len(inmates_basic)}] Getting details..."
                )

            detail = self._scrape_detail(page, basic["inmate_id"])

            first_name, middle_name, last_name = self._parse_name(basic["name"])

            record = ArrestRecord(
                County=self.county,
                Booking_Number=basic["booking_number"],
                Full_Name=basic["name"],
                First_Name=first_name,
                Middle_Name=middle_name,
                Last_Name=last_name,
                DOB=detail.get("dob") or basic["dob"],
                Arrest_Date=basic["arrest_date"],
                Booking_Date=basic["arrest_date"],
                Agency=basic["agency"],
                Status="In Custody",
                Facility="Osceola County Jail",
                Race=detail.get("race", ""),
                Sex=detail.get("sex", ""),
                Height=detail.get("height", ""),
                Weight=detail.get("weight", ""),
                Charges=basic["charges_summary"],
                Bond_Amount=detail.get("bond_amount", "0"),
                Bond_Paid="NO",
                Case_Number=", ".join(detail.get("case_numbers", [])),
                Mugshot_URL=detail.get("mugshot_url", ""),
                Detail_URL=DETAIL_URL_TPL.format(basic["inmate_id"]),
                LastCheckedMode="INITIAL",
            )

            if record.Full_Name and record.Booking_Number:
                records.append(record)

            time.sleep(DETAIL_DELAY_S)

        return records

    def _scrape_detail(self, page, inmate_id: str) -> Dict[str, Any]:
        """Scrape individual inmate detail page for bond and demographics."""
        detail_url = DETAIL_URL_TPL.format(inmate_id)
        result: Dict[str, Any] = {
            "bond_amount": "0",
            "mugshot_url": "",
            "race": "",
            "sex": "",
            "dob": "",
            "height": "",
            "weight": "",
            "case_numbers": [],
        }

        try:
            page.get(detail_url)
            time.sleep(1)

            page_html = page.html or ""

            # Total Bond
            bond_match = re.search(
                r'Total Bond:\s*\$?([\d,]+(?:\.\d{2})?)', page_html
            )
            if bond_match:
                result["bond_amount"] = bond_match.group(1).replace(",", "")

            # Demographics
            for field, pattern in [
                ("race", r'Race:\s*</td>\s*<td[^>]*>\s*(\w+)'),
                ("sex", r'Sex:\s*</td>\s*<td[^>]*>\s*(\w+)'),
                ("dob", r'DOB:\s*</td>\s*<td[^>]*>\s*([\d/]+)'),
                ("height", r'Height:\s*</td>\s*<td[^>]*>\s*([^<]+)'),
                ("weight", r'Weight:\s*</td>\s*<td[^>]*>\s*(\d+)'),
            ]:
                match = re.search(pattern, page_html)
                if match:
                    val = self._clean(match.group(1))
                    if field == "dob":
                        val = self._parse_date(val)
                    result[field] = val

            # Case numbers
            case_matches = re.findall(
                r'(\d{4}\s*(?:CF|CT|MM|TR)\s*\d+)', page_html
            )
            result["case_numbers"] = list(set(case_matches))

            # Mugshot
            img_els = page.eles('tag:img')
            for img in img_els:
                src = img.attr("src") or ""
                if src and any(
                    kw in src.lower()
                    for kw in ["inmate", "photo", "image", "mugshot"]
                ):
                    if src.startswith("/"):
                        src = f"https://apps.osceola.org{src}"
                    result["mugshot_url"] = src
                    break

        except Exception as e:
            logger.debug(f"   Detail error for {inmate_id}: {e}")

        return result

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
    def _parse_date(date_str: str) -> str:
        """Parse various date formats to MM/DD/YYYY."""
        if not date_str:
            return ""
        for fmt in ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%b %d, %Y"]:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.strftime("%m/%d/%Y")
            except ValueError:
                continue
        return date_str

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
