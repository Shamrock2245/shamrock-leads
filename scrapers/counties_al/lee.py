"""Lee County, Alabama public jail-roster scraper.

Source: https://www.leecosheriffal.gov/inmateSearch

The Lee County Sheriff's Office publishes a public OCV/Next.js roster on its
own domain. The page exposes a public ``NameID`` and booking timestamp but no
field labelled as a booking number. This scraper therefore uses a clearly
labelled deterministic per-booking surrogate made only from those source
values, and fails closed when either value is absent.
"""
from __future__ import annotations

import json
import logging
import re
import time
from html import escape
from typing import Iterable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class LeeALScraper(BaseScraper):
    """Scrape the official public Lee County, Alabama jail roster."""

    PORTAL_URL = "https://www.leecosheriffal.gov/inmateSearch"
    MAX_PAGES = 25
    PAGE_DELAY_SECONDS = 0.25

    @property
    def county(self) -> str:
        return "Lee"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        """Parse only the official sheriff-site public roster response.

        Lee's Next.js response sends card headings in HTML and the matching public
        card fields in its server-provided Flight payload. The parser reads that
        response payload directly; it does not call the denied generic OCV feed,
        automate a browser, or bypass an access control.
        """
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml"})

        records: List[ArrestRecord] = []
        seen: set[str] = set()
        page_limit: Optional[int] = None
        for page_number in range(1, self.MAX_PAGES + 1):
            if page_limit is not None and page_number > page_limit:
                break
            try:
                response = session.get(self._page_url(page_number), timeout=45)
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

    def _page_url(self, page_number: int) -> str:
        return f"{self.PORTAL_URL}?page={page_number}"

    def _parse_page(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []
        for card in soup.select(".bg-content1"):
            text = card.get_text(" ", strip=True)
            if "NameID:" not in text or "Booking Date:" not in text:
                continue
            record = self._card_to_record(card)
            if record is not None:
                records.append(record)

        # Direct responses keep field content in a public serialized Flight
        # fragment rather than the static card DOM. Only use that fallback when
        # ordinary card parsing yields nothing.
        if records:
            return records
        for card in self._flight_cards(soup):
            record = self._card_to_record(card)
            if record is not None:
                records.append(record)
        return records

    @staticmethod
    def _flight_cards(soup: BeautifulSoup) -> Iterable[object]:
        title_content = re.compile(r'"title":"((?:\\\\.|[^"\\])*)","content":"((?:\\\\.|[^"\\])*)"')
        for script in soup.find_all("script"):
            content = script.string or script.get_text()
            if "Booking Date:" not in content or "NameID:" not in content:
                continue
            match = re.search(r"\.push\((\[.*\])\)\s*$", content, flags=re.S)
            if not match:
                continue
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            for entry in payload:
                if not isinstance(entry, str):
                    continue
                for title_encoded, card_html_encoded in title_content.findall(entry):
                    try:
                        title = json.loads(f'"{title_encoded}"')
                        card_html = json.loads(f'"{card_html_encoded}"')
                    except json.JSONDecodeError:
                        continue
                    card_soup = BeautifulSoup(
                        f'<div class="bg-content1"><h2>{escape(title)}</h2>{card_html}</div>',
                        "html.parser",
                    )
                    yield card_soup.div

    def _card_to_record(self, card) -> Optional[ArrestRecord]:
        text = card.get_text("\n", strip=True)
        full_name = self._text(card.find("h2"))
        name_id = self._field(text, "NameID")
        booking_date = self._field(text, "Booking Date")

        # The official source does not label this person identifier as a booking
        # number. Do not substitute it. A stable per-booking key is emitted only
        # when both public source values are available.
        if not full_name or not name_id or not booking_date:
            return None

        booking_key = self._surrogate_booking_key(name_id, booking_date)
        first_name, middle_name, last_name = self._split_name(full_name)
        charges = self._values(text, "Description")
        bond_total = sum(self._money_values(text, "Bond Amount"))
        detail_href = next(
            (
                anchor.get("href", "")
                for anchor in card.find_all("a", href=True)
                if "/inmateSearch/" in anchor.get("href", "")
            ),
            "",
        )
        image = card.find("img", src=True)

        return ArrestRecord(
            County=self.county,
            State=self.state,
            Booking_Number=booking_key,
            Person_ID=name_id,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            Booking_Date=booking_date,
            Status="In Custody",
            Facility="Lee County Detention Facility",
            Agency="Lee County Sheriff",
            Race=self._field(text, "Race"),
            Sex=self._field(text, "Sex"),
            Age_At_Arrest=self._field(text, "Age"),
            Charges=" | ".join(charges) if charges else "Unknown",
            Bond_Amount=f"{bond_total:.2f}" if bond_total else "0",
            Detail_URL=urljoin(self.PORTAL_URL, detail_href) if detail_href else self.PORTAL_URL,
            Mugshot_URL=image.get("src", "") if image else "",
            extra_data={
                "booking_key_origin": "deterministic public NameID + Booking Date; source does not label a booking number",
                "source_name_id": name_id,
            },
        )

    @staticmethod
    def _page_count(html: str) -> int:
        pages = [int(value) for value in re.findall(r"Go to page\s+(\d+)", html, flags=re.I)]
        return max(pages) if pages else 0

    @staticmethod
    def _text(node) -> str:
        if node is None:
            return ""
        return " ".join(node.get_text(" ", strip=True).split())

    @staticmethod
    def _field(text: str, label: str) -> str:
        match = re.search(rf"{re.escape(label)}[ \t]*:[ \t]*([^\r\n]+)", text, flags=re.I)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _values(text: str, label: str) -> List[str]:
        return [value.strip() for value in re.findall(rf"{re.escape(label)}[ \t]*:[ \t]*([^\r\n]+)", text, flags=re.I) if value.strip()]

    @staticmethod
    def _money_values(text: str, label: str) -> Iterable[float]:
        values: List[float] = []
        for raw_value in LeeALScraper._values(text, label):
            try:
                values.append(float(re.sub(r"[^\d.]", "", raw_value) or 0))
            except ValueError:
                continue
        return values

    @staticmethod
    def _surrogate_booking_key(name_id: str, booking_date: str) -> str:
        normalized_date = re.sub(r"\s+", "-", booking_date.strip())
        return f"lee-al-public:{name_id}:{normalized_date}"

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
