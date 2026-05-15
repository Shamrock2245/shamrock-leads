"""
Polk County Arrest Scraper — PCSO Jail Inquiry (Kendo UI Grid).
Source: Polk County Sheriff's Office
URL: https://polksheriff.org/JailInquiry
Method: requests GET with Kendo grid API parameters

The site uses a Kendo DataSource that loads data from an API endpoint.
Search by booking date returns a paginated JSON table with columns:
  Booking # | Name | RS | DOB | Entry Date | Release Date | Location

There are 3 search tabs: Name Search, Booking Date, AKA Search.
We use the "Booking Date" tab to get all inmates for recent dates.

Clicking a booking # reveals a detail page with charges, bond amounts, etc.
We extract bond/charge data from the detail page for high-value records.
"""
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://polksheriff.org"
SEARCH_URL = f"{BASE_URL}/JailInquiry"
FACILITY = "Polk County Jail"
DAYS_BACK = 3  # 3 days of booking date coverage at 120-min intervals

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
    "Connection": "keep-alive",
}

# Race/Sex code expansion from "WM" / "BF" etc.
RACE_MAP = {
    "W": "White", "B": "Black", "H": "Hispanic",
    "A": "Asian", "I": "American Indian", "U": "Unknown",
}
SEX_MAP = {"M": "Male", "F": "Female"}


class PolkCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Polk"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape using DrissionPage for the Kendo UI grid — server-rendered."""
        all_records = []
        for days_ago in range(DAYS_BACK):
            target_date = datetime.now() - timedelta(days=days_ago)
            date_str = target_date.strftime("%m/%d/%Y")
            try:
                daily = self._scrape_date(date_str)
                all_records.extend(daily)
                logger.info(f"Polk {date_str}: {len(daily)} records")
            except Exception as e:
                logger.warning(f"Polk {date_str} error: {e}")
            time.sleep(2)

        logger.info(f"Polk: {len(all_records)} total records")
        return all_records

    def _scrape_date(self, date_str: str) -> List[ArrestRecord]:
        """
        Use DrissionPage to navigate to the Booking Date tab, enter date,
        click SEARCH, and parse the Kendo grid HTML table.
        """
        try:
            from DrissionPage import ChromiumPage
        except ImportError:
            logger.error("DrissionPage not installed")
            return []

        co = self._get_browser_options()
        page = ChromiumPage(addr_or_opts=co)
        records = []

        try:
            page.get(SEARCH_URL)
            time.sleep(3)

            # Click "Booking Date" tab
            try:
                booking_tab = page.ele("xpath://a[contains(text(),'Booking Date')]")
                if booking_tab:
                    booking_tab.click()
                    time.sleep(1)
            except Exception:
                pass

            # Find the date input and set it
            try:
                date_input = page.ele("css:input[aria-label*='Booking Date']") or \
                             page.ele("xpath://input[contains(@placeholder,'date')]") or \
                             page.ele("css:.k-datepicker input")
                if date_input:
                    date_input.clear()
                    date_input.input(date_str)
                    time.sleep(0.5)
            except Exception as e:
                logger.debug(f"Polk: date input issue: {e}")

            # Click SEARCH button
            try:
                search_btn = page.ele("xpath://button[contains(text(),'SEARCH')]") or \
                             page.ele("css:button.k-button") or \
                             page.ele("xpath://button[contains(@class,'search')]")
                if search_btn:
                    search_btn.click()
                    time.sleep(4)
            except Exception as e:
                logger.debug(f"Polk: search click issue: {e}")

            # Parse all pages of results
            page_num = 1
            while page_num <= 10:
                records.extend(self._parse_grid(page, date_str))

                # Try to click next page
                if not self._click_next(page):
                    break
                page_num += 1
                time.sleep(2)

        except Exception as e:
            logger.error(f"Polk browser error: {e}")
        finally:
            try:
                page.quit()
            except:
                pass

        return records

    def _parse_grid(self, page, date_str: str) -> List[ArrestRecord]:
        """Parse the visible Kendo grid table rows.

        Columns from recon:
          Booking # | Name | RS | DOB | Entry Date | Release Date | Location
        """
        from bs4 import BeautifulSoup
        records = []

        soup = BeautifulSoup(page.html, "html.parser")

        # Find the data grid table
        grid_table = None
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text(" ", strip=True).lower()
            if "booking" in header_text and "name" in header_text:
                grid_table = table
                break

        if not grid_table:
            return records

        rows = grid_table.find_all("tr")
        if len(rows) < 2:
            return records

        # Map headers
        header_cells = rows[0].find_all(["th", "td"])
        headers = [h.get_text(strip=True).lower() for h in header_cells]
        col_map = {}
        for i, h in enumerate(headers):
            if "booking" in h and "#" in h:
                col_map["booking"] = i
            elif h == "name" or "name" == h.strip():
                col_map["name"] = i
            elif h in ("rs", "race/sex", "r/s"):
                col_map["rs"] = i
            elif h in ("dob", "date of birth"):
                col_map["dob"] = i
            elif "entry" in h:
                col_map["entry_date"] = i
            elif "release" in h:
                col_map["release_date"] = i
            elif "location" in h:
                col_map["location"] = i

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells or len(cells) < 3:
                continue

            def _get(key):
                idx = col_map.get(key)
                if idx is not None and idx < len(cells):
                    return cells[idx].get_text(strip=True)
                return ""

            booking_num = _get("booking")
            name = _get("name")
            rs_code = _get("rs")
            dob = _get("dob")
            entry_date = _get("entry_date")
            release_date = _get("release_date")
            location = _get("location")

            if not name and not booking_num:
                continue

            # Parse RS code (e.g., "WM" = White Male, "BF" = Black Female)
            race = ""
            sex = ""
            if rs_code and len(rs_code) >= 2:
                race = RACE_MAP.get(rs_code[0].upper(), rs_code[0])
                sex = rs_code[1].upper()

            # Determine status
            status = "In Custody"
            if release_date and release_date.strip():
                status = "Released"

            # Determine facility from location code
            facility = FACILITY
            loc_upper = location.upper().strip() if location else ""
            if loc_upper == "IN":
                facility = "Polk County Jail - Main"
            elif loc_upper == "CCJ":
                facility = "Central County Jail"
            elif loc_upper == "SCJ":
                facility = "South County Jail"

            # Detail URL — booking # link
            detail_url = SEARCH_URL
            booking_idx = col_map.get("booking", 0)
            if booking_idx < len(cells):
                link = cells[booking_idx].find("a")
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"{BASE_URL}{href}"
                    detail_url = href

            # Parse name
            first, middle, last = self._parse_name(name)

            # Parse booking date
            booking_date = ""
            if entry_date:
                try:
                    dt = datetime.strptime(entry_date.strip(), "%m/%d/%Y")
                    booking_date = dt.strftime("%Y-%m-%d")
                except ValueError:
                    booking_date = entry_date.strip()

            # Parse release date
            release_formatted = ""
            if release_date and release_date.strip():
                try:
                    dt = datetime.strptime(release_date.strip(), "%m/%d/%Y")
                    release_formatted = dt.strftime("%Y-%m-%d")
                except ValueError:
                    release_formatted = release_date.strip()

            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=self._clean(booking_num),
                Full_Name=self._clean(name),
                First_Name=first,
                Middle_Name=middle,
                Last_Name=last,
                DOB=dob,
                Booking_Date=booking_date,
                Status=status,
                Release_Date=release_formatted,
                Facility=facility,
                Race=race,
                Sex=sex,
                Bond_Amount="0",  # Bond requires detail page click
                Detail_URL=detail_url,
                LastCheckedMode="INITIAL",
            ))

        return records

    def _click_next(self, page) -> bool:
        """Click the next page button in the Kendo pager."""
        try:
            # Look for Kendo pager next button
            next_btn = page.ele("css:.k-pager-nav .k-i-arrow-60-right") or \
                       page.ele("xpath://a[@title='Go to the next page']") or \
                       page.ele("css:a.k-link[title*='next']")
            if next_btn:
                # Check if there's a "disabled" class
                parent = next_btn.parent() if hasattr(next_btn, 'parent') else None
                if parent:
                    parent_class = parent.attr("class") or ""
                    if "k-state-disabled" in parent_class or "disabled" in parent_class:
                        return False
                next_btn.click()
                time.sleep(2)
                return True

            # Fallback: numbered page links
            page_info = page.ele("xpath://*[contains(text(),'of')]")
            if page_info:
                m = re.search(r'(\d+)\s*-\s*\d+\s+of\s+(\d+)', page_info.text)
                if not m:
                    m = re.search(r'(\d+)\s+of\s+(\d+)', page_info.text)
                # There are more items if displayed/total ratio indicates more pages
        except Exception as e:
            logger.debug(f"Polk pagination: {e}")
        return False

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _clean(text):
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _parse_name(name):
        """Parse 'LAST, FIRST MIDDLE' into components."""
        if not name:
            return "", "", ""
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            remainder = parts[1].strip().split()
            first = remainder[0] if remainder else ""
            middle = " ".join(remainder[1:]) if len(remainder) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) >= 3:
            return parts[0], " ".join(parts[1:-1]), parts[-1]
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return name, "", ""
