"""
Osceola County Arrest Scraper — Corrections Daily Arrest Report CSV
Source: Osceola County Corrections and Jail Services
URL: https://apps.osceola.org/Apps/CorrectionsReports/Report/Download/YYYY-MM-DD
Method: plain requests GET → CSV download (one row per charge, grouped by ARREST_NUMBER)

Fix 2026-05-18: Replaced broken DrissionPage scraper with direct CSV download.
                The old scraper used DrissionPage to interact with a date dropdown,
                but the app exposes a clean CSV export endpoint at /Download/YYYY-MM-DD
                that requires no browser automation and returns structured data.
Fix 2026-10-15: plain ``requests`` via ``scrapers.plain_http`` (honest
                User-Agent, TLS always verified). No curl_cffi Chrome
                impersonation, no browser User-Agent, no ``verify=False``.
                The download now redirects (307) to legacy-apps.osceola.org,
                whose server omits its intermediate certificate, so
                verification fails and the run raises SourceTLSInvalid
                (``tls_cert_invalid:``) instead of skipping checks. It is
                re-raised past the per-day handler so Health records it.
                Same handling as the Nassau TLS chain hold.
"""

import io
import logging
from datetime import datetime, timedelta, timezone
from typing import List

from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import make_session, request
from scrapers.scraper_resilience import AntiBotBlocked
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://apps.osceola.org/Apps/CorrectionsReports/Report/Download"
FACILITY = "Osceola County Jail"
DAYS_BACK = 7

HEADERS = {
    "Accept": "text/csv,text/plain,*/*;q=0.8",
    "Referer": "https://apps.osceola.org/Apps/CorrectionsReports/Report/Daily/",
}


class OsceolaCountyScraper(BaseScraper):
    """Osceola County (FL) — Corrections Daily Arrest Report CSV"""

    @property
    def county(self) -> str:
        return "Osceola"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import pandas as pd
        except ImportError as e:
            logger.error(f"Osceola: missing dependency: {e}")
            raise

        session = make_session(HEADERS)
        all_records: dict = {}  # keyed by ARREST_NUMBER to merge charges

        for days_ago in range(DAYS_BACK):
            date = datetime.now() - timedelta(days=days_ago)
            date_str = date.strftime("%Y-%m-%d")
            url = f"{BASE_URL}/{date_str}"

            try:
                r = request(session, "GET", url, timeout=20, county="Osceola")
                if r.status_code != 200 or len(r.content) < 100:
                    logger.debug(f"Osceola: no data for {date_str} ({r.status_code})")
                    continue

                # Parse CSV — one row per charge, same ARREST_NUMBER repeated per charge
                df = pd.read_csv(
                    io.StringIO(r.text),
                    dtype=str,
                    keep_default_na=False,  # blank cells stay "" (NaN broke .strip() and dropped the whole day)
                    index_col=False,
                    on_bad_lines="skip",
                )
                df.columns = [c.strip() for c in df.columns]

                for arrest_num, group in df.groupby("ARREST_NUMBER", sort=False):
                    if arrest_num in all_records:
                        continue  # already processed from a more recent day

                    arrest_num_str = str(arrest_num).strip()
                    if not arrest_num_str.isdigit():
                        logger.warning(
                            f"Osceola: skipping shifted/malformed row. "
                            f"ARREST_NUMBER is not digits: '{arrest_num_str}'"
                        )
                        continue

                    row = group.iloc[0]  # first row has all personal info
                    charges = " | ".join(
                        s.strip() for s in group["STATUTE_LIST"].dropna().unique()
                        if s.strip()
                    )

                    # Name
                    first = (row.get("FIRST_NAME") or "").strip()
                    last = (row.get("LAST_NAME") or "").strip()
                    middle = (row.get("MIDDLE_NAME") or "").strip()

                    # Verify names do not contain digits or slashes (indicators of column shifting)
                    if first.isdigit() or "/" in first or last.isdigit() or "/" in last:
                        logger.warning(
                            f"Osceola: skipping shifted/malformed row for booking {arrest_num_str}. "
                            f"FIRST_NAME='{first}', LAST_NAME='{last}'"
                        )
                        continue

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
                        Booking_Number=arrest_num_str,
                        Booking_Date=arrest_date,
                        Arrest_Date=arrest_date,
                        City=(row.get("CITY") or "").strip(),
                        Charges=charges,
                        Agency=(row.get("ARRESTING_AGENCY") or "").strip(),
                        # The CSV publishes no bond; unknown is "", never the model's "0" default.
                        Bond_Amount="",
                        Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                        LastChecked=datetime.now(timezone.utc).isoformat(),
                        LastCheckedMode="INITIAL",
                    )

            except AntiBotBlocked:
                raise
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
