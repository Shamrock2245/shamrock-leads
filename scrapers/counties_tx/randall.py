"""Randall County, Texas public jail-roster scraper.

Source: https://www.randallso.gov/inmateSearch

Randall County's official OCV roster renders public inmate cards at the sheriff
site.  Its direct OCV S3 feed is access-denied, so this scraper deliberately
uses only the official public page and its documented ``?page=<n>`` pagination.
The roster exposes an ``Inmate ID`` and booking timestamp, but does not label a
county-issued booking number.  A deterministic, explicitly labelled surrogate
combines those two public source values only when both are present.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Iterable, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class RandallScraper(BaseScraper):
    """Scrape the official Randall County, Texas current jail roster."""

    PORTAL_URL = "https://www.randallso.gov/inmateSearch"
    MAX_PAGES = 100
    PAGE_DELAY_SECONDS = 0.25

    @property
    def county(self) -> str:
        return "Randall"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        """Render and parse only the official public roster pages.

        The direct Next.js response contains only card headings; field content is
        populated during normal page hydration.  This source does not require a
        login or WAF workaround, so the existing browser runtime is used solely
        to render the public page before parsing it.
        """
        page = self._get_browser_page()
        if page is None:
            logger.error("%s official roster browser is unavailable", self.county)
            return []

        records: List[ArrestRecord] = []
        seen: set[str] = set()
        page_limit: Optional[int] = None
        try:
            for page_number in range(1, self.MAX_PAGES + 1):
                if page_limit is not None and page_number > page_limit:
                    break

                try:
                    page.get(self._page_url(page_number))
                    page.wait.doc_loaded()
                    time.sleep(1)
                    html = page.html
                except Exception as exc:
                    logger.error("%s official roster page %d failed: %s", self.county, page_number, exc)
                    break

                page_records = self._parse_page(html)
                if page_number == 1:
                    page_limit = min(self._page_count(html) or 1, self.MAX_PAGES)

                if not page_records:
                    logger.warning("%s official roster page %d yielded no parseable records", self.county, page_number)
                    break

                added = 0
                for record in page_records:
                    dedup_key = record.get_dedup_key()
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        records.append(record)
                        added += 1

                if added == 0:
                    logger.warning("%s official roster page %d yielded no new records; stopping", self.county, page_number)
                    break

                if page_limit is None or page_number >= page_limit:
                    break
                time.sleep(self.PAGE_DELAY_SECONDS)
        finally:
            try:
                page.quit()
            except Exception:
                pass

        logger.info("%s official public roster: %d records", self.county, len(records))
        return records

    def _get_browser_page(self):
        try:
            from DrissionPage import ChromiumPage
            return ChromiumPage(addr_or_opts=self._get_browser_options())
        except Exception as exc:
            logger.error("%s browser initialization failed: %s", self.county, exc)
            return None

    def _page_url(self, page_number: int) -> str:
        return f"{self.PORTAL_URL}?page={page_number}"

    def _parse_page(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []

        # The official Next.js response server-renders each result into a card
        # with ``bg-content1``.  Targeting the card itself is more stable than
        # walking from a label text node, which changes between direct requests
        # and a browser-rendered DOM.
        for card in soup.select(".bg-content1"):
            if "Inmate Information:" not in card.get_text(" ", strip=True):
                continue
            record = self._card_to_record(card)
            if record is not None:
                records.append(record)
        return records

    def _card_to_record(self, card) -> Optional[ArrestRecord]:
        text = card.get_text("\n", strip=True)
        full_name = self._text(card.find("h2"))
        inmate_id = self._field(text, "Inmate ID")
        booking_date = self._field(text, "Booking Date")

        # The source does not publish a label called "Booking Number".  Do not
        # treat its inmate/person ID as one.  Without both values, emitting a
        # deterministic per-booking surrogate would be unsafe, so fail closed.
        if not full_name or not inmate_id or not booking_date:
            return None

        booking_key = self._surrogate_booking_key(inmate_id, booking_date)
        first_name, middle_name, last_name = self._split_name(full_name)
        charges = self._values(text, "Description")
        bond_total = sum(self._money_values(text, "Bond Amount Required"))
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
            Person_ID=inmate_id,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            Booking_Date=booking_date,
            Status="In Custody",
            Facility="Randall County Jail",
            Agency=self._field(text, "Arresting Agency") or "Randall County Sheriff",
            Race=self._field(text, "Race"),
            Sex=self._field(text, "Gender"),
            Height=self._field(text, "Height"),
            Weight=self._field(text, "Weight"),
            Age_At_Arrest=self._field(text, "Age"),
            Charges=" | ".join(charges) if charges else "Unknown",
            Bond_Amount=f"{bond_total:.2f}" if bond_total else "0",
            Case_Number=self._field(text, "Cause Number"),
            Detail_URL=urljoin(self.PORTAL_URL, detail_href) if detail_href else self.PORTAL_URL,
            Mugshot_URL=image.get("src", "") if image else "",
            extra_data={
                "booking_key_origin": "deterministic public Inmate ID + Booking Date; source does not label a booking number",
                "source_inmate_id": inmate_id,
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
        for raw_value in RandallScraper._values(text, label):
            try:
                values.append(float(re.sub(r"[^\d.]", "", raw_value) or 0))
            except ValueError:
                continue
        return values

    @staticmethod
    def _surrogate_booking_key(inmate_id: str, booking_date: str) -> str:
        normalized_date = re.sub(r"\s+", "-", booking_date.strip())
        return f"randall-public:{inmate_id}:{normalized_date}"

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
