"""Tangipahoa Parish, Louisiana public jail-roster scraper.

Source: https://tbs-web.com/jail/TangipahoaJail/roster

The Tangipahoa Parish Sheriff's Office links this public, paginated roster. Its
listing exposes a numeric source roster ID and booking timestamp, but does not
label the number as a booking number. The scraper therefore creates a clearly
labelled deterministic per-booking key from both values and does not fetch
individual detail pages.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class TangipahoaScraper(BaseScraper):
    """Scrape the official public Tangipahoa Parish current jail roster."""

    PORTAL_URL = "https://tbs-web.com/jail/TangipahoaJail/roster"
    MAX_PAGES = 100
    PAGE_DELAY_SECONDS = 0.25

    @property
    def county(self) -> str:
        return "Tangipahoa"

    @property
    def state(self) -> str:
        return "LA"

    def scrape(self) -> List[ArrestRecord]:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml"})

        records: List[ArrestRecord] = []
        seen: set[str] = set()
        page_limit: Optional[int] = None
        for page_number in range(1, self.MAX_PAGES + 1):
            if page_limit is not None and page_number > page_limit:
                break
            try:
                response = session.get(self.PORTAL_URL, params={"page": page_number}, timeout=45)
                response.raise_for_status()
            except requests.RequestException as exc:
                logger.error("%s %s official roster page %d failed: %s", self.county, self.state, page_number, exc)
                break

            page_records = self._parse_page(response.text)
            if page_number == 1:
                page_limit = min(self._page_count(response.text) or 1, self.MAX_PAGES)
            if not page_records:
                logger.warning("%s %s official roster page %d yielded no parseable records", self.county, self.state, page_number)
                break

            added = 0
            for record in page_records:
                dedup_key = record.get_dedup_key()
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    records.append(record)
                    added += 1
            if added == 0:
                logger.warning("%s %s official roster page %d yielded no new records; stopping", self.county, self.state, page_number)
                break

            if page_limit is None or page_number >= page_limit:
                break
            time.sleep(self.PAGE_DELAY_SECONDS)

        logger.info("%s %s official public roster: %d records", self.county, self.state, len(records))
        return records

    def _parse_page(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        table = self._roster_table(soup)
        if table is None:
            return []

        records: List[ArrestRecord] = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            record = self._row_to_record(cells)
            if record is not None:
                records.append(record)
        return records

    @staticmethod
    def _roster_table(soup: BeautifulSoup):
        for table in soup.find_all("table"):
            headers = [cell.get_text(" ", strip=True).lower() for cell in table.find_all("th")]
            if "name" in headers and "booking date" in headers:
                return table
        return None

    def _row_to_record(self, cells) -> Optional[ArrestRecord]:
        name_lines = list(cells[0].stripped_strings)
        if len(name_lines) < 2:
            return None
        full_name = " ".join(name_lines[0].split())
        source_id = name_lines[1].strip()
        booking_date = " ".join(cells[2].get_text(" ", strip=True).split())
        if not full_name or not source_id.isdigit() or not booking_date:
            return None

        demographics = list(cells[1].stripped_strings)
        dob = demographics[0].strip() if demographics else ""
        race, sex = self._race_sex(demographics[1] if len(demographics) > 1 else "")
        first_name, middle_name, last_name = self._split_name(full_name)
        detail_link = cells[3].find("a", href=True)
        detail_url = urljoin(self.PORTAL_URL, detail_link.get("href", "")) if detail_link else self.PORTAL_URL

        return ArrestRecord(
            County=self.county,
            State=self.state,
            Booking_Number=self._surrogate_booking_key(source_id, booking_date),
            Person_ID=source_id,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB=dob,
            Booking_Date=booking_date,
            Status="In Custody",
            Facility="Tangipahoa Parish Jail",
            Agency="Tangipahoa Parish Sheriff",
            Race=race,
            Sex=sex,
            Charges="Unknown",
            Bond_Amount="0",
            Detail_URL=detail_url,
            extra_data={
                "booking_key_origin": "deterministic public roster ID + Booking Date; source does not label a booking number",
                "source_roster_id": source_id,
            },
        )

    @staticmethod
    def _page_count(html: str) -> int:
        pages = [int(value) for value in re.findall(r"Go to page\s+(\d+)", html, flags=re.I)]
        return max(pages) if pages else 0

    @staticmethod
    def _race_sex(value: str) -> tuple[str, str]:
        parts = [part.strip() for part in value.split("/")]
        return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")

    @staticmethod
    def _surrogate_booking_key(source_id: str, booking_date: str) -> str:
        normalized_date = re.sub(r"\s+", "-", booking_date.strip())
        return f"tangipahoa-public:{source_id}:{normalized_date}"

    @staticmethod
    def _split_name(full_name: str) -> tuple[str, str, str]:
        if "," in full_name:
            last_name, remaining = [part.strip() for part in full_name.split(",", 1)]
            parts = remaining.split()
            return (
                parts[0].title() if parts else "",
                " ".join(parts[1:]).title() if len(parts) > 1 else "",
                last_name.title(),
            )
        parts = full_name.split()
        return (
            parts[0].title() if parts else "",
            " ".join(parts[1:-1]).title() if len(parts) > 2 else "",
            parts[-1].title() if len(parts) > 1 else "",
        )
