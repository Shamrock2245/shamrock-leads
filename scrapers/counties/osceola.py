"""
Osceola County Arrest Scraper — Corrections Daily Arrest Report CSV
Source: Osceola County Corrections and Jail Services
URL: https://apps.osceola.org/Apps/CorrectionsReports/Report/Download/YYYY-MM-DD
Method: curl_cffi GET → CSV download (one row per charge, grouped by ARREST_NUMBER)

Fix 2026-05-18: Replaced broken DrissionPage scraper with direct CSV download.
                The old scraper used DrissionPage to interact with a date dropdown,
                but the app exposes a clean CSV export endpoint at /Download/YYYY-MM-DD
                that requires no browser automation and returns structured data.
                SSL cert on apps.osceola.org is self-signed — verify=False required.
"""

import io
import logging
from datetime import datetime, timedelta, timezone
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://apps.osceola.org/Apps/CorrectionsReports/Report/Download"
FACILITY = "Osceola County Jail"
DAYS_BACK = 7
IMPERSONATE = "chrome131"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://apps.osceola.org/Apps/CorrectionsReports/Report/Daily/",
}


class OsceolaCountyScraper(BaseScraper):
    """Osceola County (FL) — Corrections Daily Arrest Report CSV"""

    @property
    def county(self) -> str:
        return "Osceola"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cf
            import pandas as pd
        except ImportError as e:
            logger.error(f"Osceola: missing dependency: {e}")
            return []

        session = cf.Session()
        all_records: dict = {}  # keyed by ARREST_NUMBER to merge charges

        for days_ago in range(DAYS_BACK):
            date = datetime.now() - timedelta(days=days_ago)
            date_str = date.strftime("%Y-%m-%d")
            url = f"{BASE_URL}/{date_str}"

            try:
                r = session.get(
                    url,
                    headers=HEADERS,
                    timeout=20,
                    impersonate=IMPERSONATE,
                    verify=False,
                )
                if r.status_code != 200 or len(r.content) < 100:
                    logger.debug(f"Osceola: no data for {date_str} ({r.status_code})")
                    continue

                # Parse CSV — one row per charge, same ARREST_NUMBER repeated per charge
                df = pd.read_csv(
                    io.StringIO(r.text),
                    dtype=str,
                    on_bad_lines="skip",
                )
                df.columns = [c.strip() for c in df.columns]

                for arrest_num, group in df.groupby("ARREST_NUMBER", sort=False):
                    if arrest_num in all_records:
                        continue  # already processed from a more recent day

                    row = group.iloc[0]  # first row has all personal info
                    charges = " | ".join(
                        s.strip() for s in group["STATUTE_LIST"].dropna().unique()
                        if s.strip()
                    )

                    # Name
                    first = (row.get("FIRST_NAME") or "").strip()
                    last = (row.get("LAST_NAME") or "").strip()
                    middle = (row.get("MIDDLE_NAME") or "").strip()
                    full_name = f"{last}, {first}" + (f" {middle}" if middle else "")

                    # Dates
                    arrest_date = self._fmt_date(row.get("ARREST_DATE", ""))
                    dob = self._fmt_date(row.get("BIRTH_DATE", ""))

                    all_records[arrest_num] = ArrestRecord(
                        County=self.county,
                        State="FL",
                        Facility=FACILITY,
                        Full_Name=full_name.upper(),
                        First_Name=first.upper(),
                        Middle_Name=middle.upper(),
                        Last_Name=last.upper(),
                        DOB=dob,
                        Booking_Number=str(arrest_num).strip(),
                        Booking_Date=arrest_date,
                        Arrest_Date=arrest_date,
                        City=(row.get("CITY") or "").strip(),
                        Charges=charges,
                        Agency=(row.get("ARRESTING_AGENCY") or "").strip(),
                        Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                        LastChecked=datetime.now(timezone.utc).isoformat(),
                        LastCheckedMode="INITIAL",
                    )

            except Exception as e:
                logger.warning(f"Osceola: error fetching {date_str}: {e}")

        records = list(all_records.values())
        logger.info(f"Osceola: {len(records)} records from last {DAYS_BACK} days")
        return records

    @staticmethod
    def _fmt_date(raw: str) -> str:
        """Normalize M/D/YYYY to MM/DD/YYYY."""
        if not raw:
            return ""
        raw = str(raw).strip()
        try:
            return datetime.strptime(raw, "%m/%d/%Y").strftime("%m/%d/%Y")
        except ValueError:
            return raw
