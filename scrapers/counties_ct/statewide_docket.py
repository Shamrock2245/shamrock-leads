"""
Connecticut Statewide Criminal Docket Scraper.

Portal: https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx
Platform: ASP.NET WebForms (ViewState + EventValidation)
Coverage: All Judicial Districts + Geographical Areas (40+ court locations)

Verified: curl_cffi chrome impersonation required (plain requests → SSL handshake fail).
No Cloudflare CAPTCHA observed.

Dedup key: Docket_Number → Booking_Number
Dashboard label: ``Statewide (CT)``
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import List, Optional, Tuple

from curl_cffi import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx"

# Full court list (form option values) — verified against live ddlCourts 2026-08-04.
# Housing courts omitted (civil eviction focus).
ALL_COURTS = [
    ("F02B", "Bridgeport GA 2"),
    ("FBT", "Bridgeport JD"),
    ("H17B", "Bristol GA 17"),
    ("D03D", "Danbury GA 3/JD"),
    ("W11D", "Danielson GA 11/JD"),
    ("A05D", "Derby GA 5"),
    ("H13W", "Enfield GA 13"),
    ("H14C", "Hartford Community Court"),
    ("H14H", "Hartford GA 14"),
    ("HHD", "Hartford JD"),
    ("LLI", "Litchfield JD"),
    ("H12M", "Manchester GA 12"),
    ("N07M", "Meriden GA 7"),
    ("MMX", "Middlesex JD"),
    ("M09M", "Middletown GA 9"),
    ("A22M", "Milford GA 22"),
    ("AAN", "Milford JD"),
    ("H15N", "New Britain GA 15"),
    ("HHB", "New Britain JD"),
    ("N06N", "New Haven GA 06"),
    ("N08W", "New Haven GA 08"),
    ("N23N", "New Haven GA 23"),
    ("NNH", "New Haven JD"),
    ("K10K", "New London GA 10"),
    ("KNL", "New London JD"),
    ("S20N", "Norwalk GA 20"),
    ("K21N", "Norwich GA 21"),
    ("T19R", "Rockville GA 19"),
    ("S01S", "Stamford GA 1"),
    ("FST", "Stamford JD"),
    ("TTD", "Tolland JD"),
    ("L18W", "Torrington GA 18"),
    ("U04C", "Waterbury Community Court"),
    ("U04W", "Waterbury GA 4"),
    ("UWY", "Waterbury JD"),
]

# High-volume first for partial-run value; full list rotates by hour.
PRIORITY_COURTS = [
    ("F02B", "Bridgeport GA 2"),
    ("FBT", "Bridgeport JD"),
    ("H14H", "Hartford GA 14"),
    ("HHD", "Hartford JD"),
    ("N23N", "New Haven GA 23"),
    ("NNH", "New Haven JD"),
    ("U04W", "Waterbury GA 4"),
    ("UWY", "Waterbury JD"),
    ("S01S", "Stamford GA 1"),
    ("FST", "Stamford JD"),
    ("HHB", "New Britain JD"),
    ("D03D", "Danbury GA 3/JD"),
    ("S20N", "Norwalk GA 20"),
    ("H15N", "New Britain GA 15"),
    ("KNL", "New London JD"),
    ("LLI", "Litchfield JD"),
]

# Courts per scheduled run (full coverage over a few cycles)
MAX_COURTS_PER_RUN = 12
MAX_ENTRIES_PER_COURT = 500
COURT_DELAY_S = 0.8
# Soft cap so a single run cannot flood M0 (dockets churn daily; retention purges Pending)
MAX_RECORDS_PER_RUN = int(__import__("os").environ.get("CT_DOCKET_MAX_RECORDS", "4000"))


class CTStatewideDockerScraper(BaseScraper):
    """
    Scrapes the CT Judicial Branch criminal docket by court location.
    Returns defendants with pending hearings as ArrestRecord objects.
    """

    # When True, hit every court in ALL_COURTS (slower; good for one-shots).
    scrape_all_courts: bool = False

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

        session = requests.Session(impersonate="chrome124")
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        courts_this_run = self._courts_for_run()
        ok_courts = 0
        empty_courts = 0

        for court_code, court_name in courts_this_run:
            try:
                records = self._scrape_court(session, court_code, court_name, seen_dockets)
                all_records.extend(records)
                if records:
                    ok_courts += 1
                else:
                    empty_courts += 1
                time.sleep(COURT_DELAY_S)
            except Exception as exc:
                logger.warning("CT %s: scrape failed: %s", court_name, exc)
                continue

        if len(all_records) > MAX_RECORDS_PER_RUN:
            logger.info(
                "  CT Statewide: capping %d → %d records for M0 storage",
                len(all_records),
                MAX_RECORDS_PER_RUN,
            )
            all_records = all_records[:MAX_RECORDS_PER_RUN]

        logger.info(
            "✅ CT Statewide: %d docket entries from %d courts "
            "(%d with rows, %d empty) in %.1fs",
            len(all_records),
            len(courts_this_run),
            ok_courts,
            empty_courts,
            time.time() - start,
        )
        return all_records

    def _courts_for_run(self) -> List[Tuple[str, str]]:
        if self.scrape_all_courts:
            return list(ALL_COURTS)
        # Round-robin slice of priority courts by hour
        hour = datetime.now().hour
        n = len(PRIORITY_COURTS)
        if n == 0:
            return list(ALL_COURTS)[:MAX_COURTS_PER_RUN]
        start_idx = (hour * MAX_COURTS_PER_RUN) % n
        courts: List[Tuple[str, str]] = []
        for i in range(MAX_COURTS_PER_RUN):
            courts.append(PRIORITY_COURTS[(start_idx + i) % n])
        # Dedup while preserving order
        seen = set()
        out = []
        for c in courts:
            if c[0] not in seen:
                seen.add(c[0])
                out.append(c)
        return out

    def _scrape_court(
        self,
        session: requests.Session,
        court_code: str,
        court_name: str,
        seen: set,
    ) -> List[ArrestRecord]:
        """Fetch the daily docket for one court location."""
        try:
            resp = session.get(SEARCH_URL, timeout=20, verify=False)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("CT %s: GET failed: %s", court_name, exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        viewstate = self._field(soup, "__VIEWSTATE")
        viewstategen = self._field(soup, "__VIEWSTATEGENERATOR")
        eventval = self._field(soup, "__EVENTVALIDATION")

        if not viewstate:
            logger.error("CT %s: missing __VIEWSTATE", court_name)
            return []

        # Button value on live form is "Search" (not "Submit")
        payload = {
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": viewstategen,
            "__EVENTVALIDATION": eventval,
            "_ctl0:cphBody:ddlCourts": court_code,
            "_ctl0:cphBody:btnSearch": "Search",
        }
        try:
            resp = session.post(SEARCH_URL, data=payload, timeout=40, verify=False)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("CT %s: POST failed: %s", court_name, exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", id="cphBody_grdDockets")
        if not table:
            table = soup.find("table", id=lambda x: x and "grdDocket" in str(x))
        if not table:
            logger.debug("CT %s: no docket table", court_name)
            return []

        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        records: List[ArrestRecord] = []
        for row in rows[1 : MAX_ENTRIES_PER_COURT + 1]:
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if len(cells) < 7:
                continue

            docket_no = cells[0].strip()
            # Skip empty placeholder rows (some courts return a blank shell)
            if not docket_no or not re.search(r"[A-Z0-9]", docket_no, re.I):
                continue
            if docket_no in seen:
                continue
            seen.add(docket_no)

            docket_type = cells[1].strip().replace("*", "").strip()
            court_loc = cells[2].strip()
            activity = cells[3].strip()
            hearing_date = cells[4].strip()
            defendant_name = cells[6].strip() if len(cells) > 6 else ""
            birth_year = cells[7].strip() if len(cells) > 7 else ""

            if not defendant_name or len(defendant_name) < 2:
                continue

            first, last = self._split_name(defendant_name)
            court_type = activity or docket_type

            records.append(
                ArrestRecord(
                    County=self.county,
                    State="CT",
                    Full_Name=defendant_name.title(),
                    First_Name=first,
                    Last_Name=last,
                    DOB=birth_year,
                    Booking_Number=docket_no,
                    Case_Number=docket_no,
                    Court_Date=hearing_date.split(" ")[0] if hearing_date else "",
                    Court_Time=(
                        " ".join(hearing_date.split(" ")[1:])
                        if " " in hearing_date
                        else ""
                    ),
                    Court_Location=court_loc or court_name,
                    Court_Type=court_type,
                    Status="Pending",
                    Charges=f"{docket_type} - {activity}" if activity else docket_type,
                    Facility=court_loc or court_name,
                    Agency="Connecticut Judicial Branch",
                    Detail_URL=SEARCH_URL,
                )
            )

        logger.info("  CT %s: %d docket entries", court_name, len(records))
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
