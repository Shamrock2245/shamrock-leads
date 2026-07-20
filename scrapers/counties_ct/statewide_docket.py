"""
Connecticut Statewide Criminal Docket Scraper.

Portal: https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx
Platform: ASP.NET WebForms (ViewState + EventValidation)
Coverage: All 8 Judicial Districts + Geographical Areas (40+ court locations)
Data: Daily criminal docket — defendants with pending hearings

Verified 2026-07-20: Plain requests + form POST works from datacenter IPs.
No Cloudflare, no CAPTCHA, no bot detection observed.

Dedup key: Docket_Number (mapped to Booking_Number for chain compatibility)
"""
from __future__ import annotations

import logging
import time
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx"
DETAIL_BASE = "https://www.jud2.ct.gov/crdockets/"

# Top-volume courts to scrape (Judicial Districts + major GAs)
# These cover the highest-population areas where bail bonds are most common
PRIORITY_COURTS = [
    # (form option value, human label) — verified against live ddlCourts 2026-07-20
    ("F02B", "Bridgeport GA 2"),
    ("FBT", "Bridgeport JD"),
    ("H14H", "Hartford GA 14"),
    ("HHD", "Hartford JD"),
    ("N06N", "New Haven GA 06"),
    ("NNH", "New Haven JD"),
    ("U04W", "Waterbury GA 4"),
    ("UWY", "Waterbury JD"),
    ("S01S", "Stamford GA 1"),
    ("FST", "Stamford JD"),
    ("HHB", "New Britain JD"),
    ("D03D", "Danbury GA 3/JD"),
]

# Limit courts per run to avoid overloading (rotate through full list over time)
MAX_COURTS_PER_RUN = 6
# Max docket entries per court to avoid memory blowout
MAX_ENTRIES_PER_COURT = 200


class CTStatewideDockerScraper(BaseScraper):
    """
    Scrapes the CT Judicial Branch criminal docket by court location.
    Returns defendants with pending hearings as ArrestRecord objects.
    """

    @property
    def county(self) -> str:
        return "Statewide"

    @property
    def state(self) -> str:
        return "CT"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        all_records: List[ArrestRecord] = []
        seen_dockets: set = set()

        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        # Rotate which courts we hit each run (round-robin by hour)
        import datetime
        hour = datetime.datetime.now().hour
        start_idx = (hour % (len(PRIORITY_COURTS) // MAX_COURTS_PER_RUN)) * MAX_COURTS_PER_RUN
        courts_this_run = PRIORITY_COURTS[start_idx:start_idx + MAX_COURTS_PER_RUN]
        if not courts_this_run:
            courts_this_run = PRIORITY_COURTS[:MAX_COURTS_PER_RUN]

        for court_code, court_name in courts_this_run:
            try:
                records = self._scrape_court(session, court_code, court_name, seen_dockets)
                all_records.extend(records)
                time.sleep(1.0)  # polite delay between courts
            except Exception as exc:
                logger.warning(f"CT {court_name}: scrape failed: {exc}")
                continue

        logger.info(
            f"✅ CT Statewide: {len(all_records)} docket entries from "
            f"{len(courts_this_run)} courts in {time.time() - start:.1f}s"
        )
        return all_records

    def _scrape_court(
        self,
        session: requests.Session,
        court_code: str,
        court_name: str,
        seen: set,
    ) -> List[ArrestRecord]:
        """Fetch the daily docket for one court location."""
        # Step 1: GET the search page to extract ASP.NET form tokens
        try:
            resp = session.get(SEARCH_URL, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            logger.error(f"CT {court_name}: GET failed: {exc}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        viewstate = self._field(soup, "__VIEWSTATE")
        viewstategen = self._field(soup, "__VIEWSTATEGENERATOR")
        eventval = self._field(soup, "__EVENTVALIDATION")

        if not viewstate:
            logger.error(f"CT {court_name}: missing __VIEWSTATE")
            return []

        # Step 2: POST the form with the selected court
        payload = {
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": viewstategen,
            "__EVENTVALIDATION": eventval,
            "_ctl0:cphBody:ddlCourts": court_code,
            "_ctl0:cphBody:btnSearch": "Submit",
        }
        try:
            resp = session.post(SEARCH_URL, data=payload, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            logger.error(f"CT {court_name}: POST failed: {exc}")
            return []

        # Step 3: Parse the docket grid
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", id="cphBody_grdDockets")
        if not table:
            # Try alternate ID patterns
            table = soup.find("table", id=lambda x: x and "grdDocket" in str(x))
        if not table:
            logger.debug(f"CT {court_name}: no docket table found")
            return []

        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        records: List[ArrestRecord] = []
        for row in rows[1:MAX_ENTRIES_PER_COURT + 1]:
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if len(cells) < 7:
                continue

            docket_no = cells[0].strip()
            if not docket_no or docket_no in seen:
                continue
            seen.add(docket_no)

            docket_type = cells[1].strip().replace("*", "").strip()
            court_loc = cells[2].strip()
            activity = cells[3].strip()
            hearing_date = cells[4].strip()
            defendant_name = cells[6].strip() if len(cells) > 6 else ""
            birth_year = cells[7].strip() if len(cells) > 7 else ""

            if not defendant_name:
                continue

            # Parse name (format: "LAST FIRST MIDDLE")
            first, last = self._split_name(defendant_name)

            # Determine hearing type for court_type field
            court_type = activity or docket_type

            records.append(
                ArrestRecord(
                    County="Statewide",
                    State="CT",
                    Full_Name=defendant_name.title(),
                    First_Name=first,
                    Last_Name=last,
                    DOB=birth_year,
                    Booking_Number=docket_no,
                    Case_Number=docket_no,
                    Court_Date=hearing_date.split(" ")[0] if hearing_date else "",
                    Court_Time=" ".join(hearing_date.split(" ")[1:]) if " " in hearing_date else "",
                    Court_Location=court_loc or court_name,
                    Court_Type=court_type,
                    Status="Pending",
                    Charges=f"{docket_type} - {activity}" if activity else docket_type,
                    Detail_URL=SEARCH_URL,
                )
            )

        logger.info(f"  CT {court_name}: {len(records)} docket entries")
        return records

    @staticmethod
    def _field(soup: BeautifulSoup, field_name: str) -> str:
        tag = soup.find("input", {"name": field_name})
        return tag.get("value", "") if tag else ""

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        """'LAST FIRST MIDDLE' → (first, last)."""
        parts = name.split()
        if len(parts) >= 2:
            return parts[1].title(), parts[0].title()
        return name.title(), ""
