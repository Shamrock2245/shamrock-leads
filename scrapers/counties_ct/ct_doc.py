"""
Connecticut Department of Correction (CT DOC) Inmate Scraper.

Portal: https://www.ctinmateinfo.state.ct.us/
Coverage: Statewide CT correctional facilities (Bridgeport CC, Hartford CC, New Haven CC,
          Corrigan-Radgowski, MacDougall-Walker, York CI, Brooklyn CI, etc.)
Data: Real-time inmate roster — unsentenced & sentenced inmates, bond amounts, controlling offenses

Dedup key: Inmate_Number (mapped to Booking_Number)
"""
from __future__ import annotations

import logging
import time
from typing import List, Tuple

from curl_cffi import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.ctinmateinfo.state.ct.us/"
POST_URL = "https://www.ctinmateinfo.state.ct.us/resultsupv.asp"
DETAIL_BASE = "https://www.ctinmateinfo.state.ct.us/"

# Common last name prefixes to rotate through
LAST_NAME_PREFIXES = [
    "SM", "JO", "WI", "BR", "DA", "MILL", "GARC", "RODR", "MART",
    "JOH", "CL", "THO", "JACK", "WHIT", "HARR", "TAYL",
    "AL", "BA", "CA", "DE", "FL", "GA", "HA", "LE", "MA", "PA", "RE", "SA", "VA"
]

MAX_PREFIXES_PER_RUN = 3
MAX_DETAILS_PER_PREFIX = 100


class CTDOCInmateScraper(BaseScraper):
    """
    Scrapes the Connecticut Department of Correction inmate roster.
    Returns ArrestRecord objects for active CT DOC inmates.
    """

    @property
    def county(self) -> str:
        return "CT DOC"

    @property
    def state(self) -> str:
        return "CT"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        all_records: List[ArrestRecord] = []
        seen_inmates: set = set()

        session = requests.Session(impersonate="chrome124")
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        # Landing request to initialize session cookies
        try:
            session.get(SEARCH_URL, timeout=15, verify=False)
        except Exception as exc:
            logger.error(f"CT DOC landing GET failed: {exc}")

        # Rotate search prefixes by hour
        import datetime
        hour = datetime.datetime.now().hour
        start_idx = (hour % (len(LAST_NAME_PREFIXES) // MAX_PREFIXES_PER_RUN)) * MAX_PREFIXES_PER_RUN
        prefixes_this_run = LAST_NAME_PREFIXES[start_idx:start_idx + MAX_PREFIXES_PER_RUN]
        if not prefixes_this_run:
            prefixes_this_run = LAST_NAME_PREFIXES[:MAX_PREFIXES_PER_RUN]

        for prefix in prefixes_this_run:
            try:
                records = self._scrape_prefix(session, prefix, seen_inmates)
                all_records.extend(records)
                time.sleep(1.0)
            except Exception as exc:
                logger.warning(f"CT DOC prefix '{prefix}' failed: {exc}")
                continue

        logger.info(
            f"✅ CT DOC: {len(all_records)} inmate records from {len(prefixes_this_run)} "
            f"prefixes in {time.time() - start:.1f}s"
        )
        return all_records

    def _scrape_prefix(
        self,
        session: requests.Session,
        prefix: str,
        seen: set,
    ) -> List[ArrestRecord]:
        """Post search criteria for a last name prefix and fetch inmate details."""
        payload = {
            "id_inmt_num": "",
            "nm_inmt_last": prefix,
            "nm_inmt_first": "",
            "dt_inmt_birth": "",
            "submit1": "Search All Inmates",
        }
        try:
            resp = session.post(POST_URL, data=payload, timeout=30, verify=False)
            resp.raise_for_status()
        except Exception as exc:
            logger.error(f"CT DOC POST '{prefix}' failed: {exc}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        detail_links = soup.find_all("a", href=lambda h: h and "details" in str(h))

        records: List[ArrestRecord] = []
        count = 0

        for link in detail_links:
            if count >= MAX_DETAILS_PER_PREFIX:
                break

            href = link.get("href", "")
            if not href:
                continue

            # Extract inmate ID from URL
            inmate_id = ""
            if "id_inmt_num=" in href:
                inmate_id = href.split("id_inmt_num=")[-1].split("&")[0].strip()

            if not inmate_id or inmate_id in seen:
                continue

            seen.add(inmate_id)
            detail_url = DETAIL_BASE + href if not href.startswith("http") else href

            try:
                record = self._parse_detail(session, detail_url, inmate_id)
                if record:
                    records.append(record)
                    count += 1
                time.sleep(0.15)
            except Exception as exc:
                logger.debug(f"CT DOC detail {inmate_id} failed: {exc}")
                continue

        logger.info(f"  CT DOC '{prefix}': {len(records)} inmates extracted")
        return records

    def _parse_detail(
        self,
        session: requests.Session,
        url: str,
        inmate_id: str,
    ) -> ArrestRecord | None:
        """Fetch and parse detailed inmate profile."""
        resp = session.get(url, timeout=15, verify=False)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        data: dict = {}

        # Parse key-value pairs from table cells
        for tr in soup.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) >= 2:
                key = cells[0].rstrip(":").strip()
                val = cells[1].strip()
                if key and val:
                    data[key] = val

        name = data.get("Inmate Name", "")
        if not name:
            return None

        first, last = self._split_name(name)
        dob = data.get("Date of Birth", "")
        facility = data.get("Current Location", "CT DOC Facility")
        status_raw = data.get("Status", "In Custody")
        bond_raw = data.get("Bond Amount", "")
        offense = data.get("Controlling Offense*", "") or data.get("Controlling Offense", "Unknown")

        # Map CT DOC status
        custody_status = "In Custody"
        if "UNSENTENCED" in status_raw.upper():
            custody_status = "In Custody (Unsentenced)"
        elif "SENTENCED" in status_raw.upper():
            custody_status = "In Custody (Sentenced)"

        # Normalize bond amount
        bond_amount = self._normalize_bond(bond_raw)

        return ArrestRecord(
            County="Statewide",
            State="CT",
            Full_Name=name.title() if name.isupper() else name,
            First_Name=first,
            Last_Name=last,
            DOB=dob,
            Booking_Number=inmate_id,
            Person_ID=inmate_id,
            Facility=facility,
            Status=custody_status,
            Charges=offense,
            Bond_Amount=bond_amount,
            Agency="Connecticut Department of Correction",
            Detail_URL=url,
        )

    @staticmethod
    def _normalize_bond(val: str) -> str:
        """Strip currency symbols and return integer string or empty string."""
        if not val or val == "0":
            return ""
        clean = "".join(c for c in str(val) if c.isdigit())
        return clean if clean and clean != "0" else ""

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        """'LAST,FIRST MIDDLE' → (first, last)."""
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip().title()
            first = parts[1].strip().title()
            return first, last
        parts = name.split()
        if len(parts) >= 2:
            return parts[0].title(), parts[-1].title()
        return name.title(), ""
