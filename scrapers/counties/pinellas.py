"""
Pinellas County Arrest Scraper — Sheriff's Inmate Booking (ASP.NET WebForms).
Source: Pinellas County Sheriff's Office
URL: https://www.pinellassheriff.gov/InmateBooking/
Method: requests POST with ViewState harvesting — no browser needed.

The search page is an ASP.NET WebForms app. We:
  1. GET the page to harvest __VIEWSTATE, __EVENTVALIDATION, etc.
  2. POST with date + "include charge" checkbox to get results table.
  3. Parse the HTML table: Name | R | S | Date of Birth | Booking Date/Location | Docket #

Each name is a link to a detail page — we capture that for Detail_URL.
The "Booking Date/Location" column contains both datetime + custody status (IN CUSTODY / RELEASED).
"""
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.pinellassheriff.gov"
SEARCH_URL = f"{BASE_URL}/InmateBooking/"
DAYS_BACK = 3  # Runs every 90 min — 3 days covers plenty of ground
PAGE_SIZE = 100  # Request max results per page

# Race code expansion
RACE_MAP = {
    "W": "White", "B": "Black", "H": "Hispanic",
    "A": "Asian", "I": "American Indian", "U": "Unknown",
    "O": "Other",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": SEARCH_URL,
    "DNT": "1",
    "Connection": "keep-alive",
}


class PinellasCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Pinellas"

    def scrape(self) -> List[ArrestRecord]:
        all_records = []
        for days_ago in range(DAYS_BACK):
            target_date = datetime.now() - timedelta(days=days_ago)
            date_str = target_date.strftime("%m/%d/%Y")
            try:
                daily = self._scrape_date(date_str)
                all_records.extend(daily)
                logger.info(f"Pinellas {date_str}: {len(daily)} records")
            except Exception as e:
                logger.warning(f"Pinellas {date_str} error: {e}")
            time.sleep(1)  # Rate-limit

        logger.info(f"Pinellas: {len(all_records)} total records")
        return all_records

    def _scrape_date(self, date_str: str) -> List[ArrestRecord]:
        """Scrape a single date using requests POST to the ASP.NET form."""
        import requests
        from bs4 import BeautifulSoup

        session = requests.Session()
        session.headers.update(HEADERS)
        records = []

        # Step 1: GET to harvest ViewState tokens
        try:
            r0 = session.get(SEARCH_URL, timeout=30)
            r0.raise_for_status()
        except Exception as e:
            logger.error(f"Pinellas GET failed: {e}")
            return records

        soup0 = BeautifulSoup(r0.text, "html.parser")

        def _hidden(name):
            el = soup0.find("input", {"name": name})
            return el["value"] if el and el.get("value") else ""

        # Step 2: POST with date, "include charge" checkbox, large page size
        post_data = {
            "_TSM_HiddenField_": _hidden("_TSM_HiddenField_"),
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            "__VIEWSTATE": _hidden("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _hidden("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _hidden("__EVENTVALIDATION"),
            "__ncforminfo": _hidden("__ncforminfo"),
            "txtLastName": "",
            "txtFirstName": "",
            "drpRace": "Any",
            "drpSex": "Any",
            "txtDocketNumber": "",
            "txtBookingDate": date_str,
            "drpAgencies": "",
            "drpCharge": "",
            "drpChargeType": "",
            "chkIncludeCharge": "on",  # Include charge in response
            "drpSortBy": "Name",
            "drpPageSize": str(PAGE_SIZE),
            "btnSearch": "Search",
            "hdnType": "",
        }

        try:
            r1 = session.post(SEARCH_URL, data=post_data, timeout=60)
            r1.raise_for_status()
        except Exception as e:
            logger.error(f"Pinellas POST failed for {date_str}: {e}")
            return records

        soup1 = BeautifulSoup(r1.text, "html.parser")
        records = self._parse_results_table(soup1, date_str)
        return records

    def _parse_results_table(self, soup, date_str: str) -> List[ArrestRecord]:
        """Parse the results table from the search response.

        Table columns (from live recon):
          Name | R | S | Date of Birth | Booking Date/Location | Docket #
          (with optional Charge column when checkbox is checked)
        """
        records = []

        # Find the green-header results table
        result_table = None
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            # Check header row for known columns
            first_row_text = rows[0].get_text(" ", strip=True).lower()
            if "name" in first_row_text and "docket" in first_row_text:
                result_table = table
                break

        if not result_table:
            logger.debug(f"Pinellas: no results table for {date_str}")
            return records

        rows = result_table.find_all("tr")
        if len(rows) < 2:
            return records

        # Map header columns by index
        header_cells = rows[0].find_all(["th", "td"])
        headers = [h.get_text(strip=True).lower() for h in header_cells]
        col_map = {}
        for i, h in enumerate(headers):
            if "name" in h:
                col_map["name"] = i
            elif h in ("r", "race"):
                col_map["race"] = i
            elif h in ("s", "sex", "gender"):
                col_map["sex"] = i
            elif "date of birth" in h or "dob" in h:
                col_map["dob"] = i
            elif "booking date" in h or "booking" in h:
                col_map["booking"] = i
            elif "docket" in h:
                col_map["docket"] = i
            elif "charge" in h:
                col_map["charge"] = i

        seen = set()

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells or len(cells) < 3:
                continue

            def _get(key):
                idx = col_map.get(key)
                if idx is not None and idx < len(cells):
                    return cells[idx].get_text(strip=True)
                return ""

            # Name + detail URL
            name = _get("name")
            if not name:
                continue

            detail_url = ""
            name_idx = col_map.get("name", 0)
            if name_idx < len(cells):
                link = cells[name_idx].find("a")
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"{BASE_URL}{href}"
                    detail_url = href

            # Race/Sex
            race_code = _get("race")
            sex = _get("sex")

            # DOB
            dob = _get("dob")

            # Docket #
            docket = _get("docket")

            # Booking Date/Location column — contains datetime + status
            booking_raw = _get("booking")
            booking_date_parsed = date_str
            status = "In Custody"
            booking_time = ""

            if booking_raw:
                # Format: "5/10/2026 4:18:30 PM\nIN CUSTODY" or "5/10/2026 12:04:43 AM\nRELEASED"
                lines = [l.strip() for l in booking_raw.split("\n") if l.strip()]
                if lines:
                    # First line is date/time
                    dt_str = lines[0]
                    try:
                        dt = datetime.strptime(dt_str, "%m/%d/%Y %I:%M:%S %p")
                        booking_date_parsed = dt.strftime("%Y-%m-%d")
                        booking_time = dt.strftime("%H:%M:%S")
                    except ValueError:
                        try:
                            dt = datetime.strptime(dt_str, "%m/%d/%Y %H:%M:%S")
                            booking_date_parsed = dt.strftime("%Y-%m-%d")
                            booking_time = dt.strftime("%H:%M:%S")
                        except ValueError:
                            booking_date_parsed = dt_str

                    # Second line is status
                    if len(lines) > 1:
                        status_raw = lines[1].upper()
                        if "RELEASED" in status_raw:
                            status = "Released"
                        else:
                            status = "In Custody"

            # Charges (if column exists)
            charges = _get("charge")

            # Dedup
            dedup_key = docket or name
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Parse name
            first, middle, last = self._parse_name(name)

            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=self._clean(docket),
                Full_Name=self._clean(name),
                First_Name=first,
                Middle_Name=middle,
                Last_Name=last,
                DOB=dob,
                Booking_Date=booking_date_parsed,
                Booking_Time=booking_time,
                Status=status,
                Facility="Pinellas County Jail",
                Race=RACE_MAP.get(race_code.upper(), race_code) if race_code else "",
                Sex=self._clean(sex),
                Charges=self._clean(charges),
                Bond_Amount="0",  # Bond not on list page — requires detail page
                Detail_URL=detail_url or SEARCH_URL,
                LastCheckedMode="INITIAL",
            ))

        return records

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

    @staticmethod
    def _parse_bond(bond_str):
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
