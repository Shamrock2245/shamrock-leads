"""Marshall County, Alabama public jail-roster scraper.

Source: https://www.marshallso.org/inmate-roster/filters/current/booking_time=desc/1

The Marshall County Sheriff's Office publishes a broad public roster with a
source-issued Booking # and Booking Date. The scraper uses those fields directly
and does not fetch individual profile pages.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class MarshallALScraper(BaseScraper):
    """Scrape Marshall County Sheriff's Office's public current roster."""

    PORTAL_URL = "https://www.marshallso.org/inmate-roster/filters/current/booking_time=desc/1"
    MAX_PAGES = 100
    PAGE_DELAY_SECONDS = 0.25

    @property
    def county(self) -> str:
        return "Marshall"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml"})

        records: List[ArrestRecord] = []
        seen: set[str] = set()
        for page_number in range(1, self.MAX_PAGES + 1):
            try:
                response = session.get(self._page_url(page_number), timeout=45)
                response.raise_for_status()
            except requests.RequestException as exc:
                logger.error("%s %s official roster page %d failed: %s", self.county, self.state, page_number, exc)
                break

            page_records = self._parse_page(response.text)
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

            if not self._has_next_page(response.text):
                break
            time.sleep(self.PAGE_DELAY_SECONDS)

        logger.info("%s %s official public roster: %d records", self.county, self.state, len(records))
        return records

    def _page_url(self, page_number: int) -> str:
        return re.sub(r"/\d+$", f"/{page_number}", self.PORTAL_URL)

    def _parse_page(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []
        for card in soup.select(".col-lg-6"):
            if "Booking #:" not in card.get_text(" ", strip=True):
                continue
            record = self._card_to_record(card)
            if record is not None:
                records.append(record)
        return records

    def _card_to_record(self, card) -> ArrestRecord | None:
        lines = list(card.stripped_strings)
        if not lines:
            return None
        full_name = " ".join(lines[0].split())
        text = card.get_text("\n", strip=True)
        booking_number = self._field(text, "Booking #")
        booking_date = self._field(text, "Booking Date")
        if not full_name or not booking_number or not booking_date:
            return None

        first_name, middle_name, last_name = self._split_name(full_name)
        profile_link = next(
            (
                anchor.get("href", "")
                for anchor in card.find_all("a", href=True)
                if "inmate-roster/" in anchor.get("href", "")
            ),
            "",
        )
        image = card.find("img", src=True)
        return ArrestRecord(
            County=self.county,
            State=self.state,
            Booking_Number=booking_number,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            Booking_Date=booking_date,
            Status="In Custody",
            Facility="Marshall County Jail",
            Agency="Marshall County Sheriff",
            Age_At_Arrest=self._field(text, "Age"),
            Charges=self._field(text, "Charges") or "Unknown",
            Bond_Amount="0",
            Detail_URL=urljoin(self.PORTAL_URL, profile_link) if profile_link else self.PORTAL_URL,
            Mugshot_URL=urljoin(self.PORTAL_URL, image.get("src", "")) if image else "",
            extra_data={"booking_key_origin": "source-issued public Booking #"},
        )

    @staticmethod
    def _field(text: str, label: str) -> str:
        match = re.search(rf"{re.escape(label)}\s*:\s*([^\r\n]+)", text, flags=re.I)
        value = match.group(1).strip() if match else ""
        # Do not let an absent value consume the next public field label.
        if re.fullmatch(r"[A-Za-z #]+:", value):
            return ""
        return value

    @staticmethod
    def _has_next_page(html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        return any("next" in anchor.get_text(" ", strip=True).lower() for anchor in soup.find_all("a", href=True))

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
