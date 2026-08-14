"""Etowah County, Alabama official current-inmate roster scraper.

Source: https://www.etowahcountysheriff.com/roster.php

The public current-roster cards provide complete names, source-issued Booking #
values, booking dates/times, charges, and bond amounts. This scraper reads only
that roster surface, uses its public ``grp`` pagination parameter, and never
fetches an individual profile, image, or CAPTCHA-protected service.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class EtowahScraper(BaseScraper):
    """Scrape Etowah County Sheriff's Office's public current roster."""

    ROSTER_URL = "https://www.etowahcountysheriff.com/roster.php"
    PAGE_SIZE = 20
    MAX_PAGES = 100
    PAGE_DELAY_SECONDS = 0.25

    @property
    def county(self) -> str:
        return "Etowah"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        session = requests.Session()
        session.headers.update(
            {"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml"}
        )

        records: List[ArrestRecord] = []
        seen: set[str] = set()
        for page_index in range(self.MAX_PAGES):
            try:
                response = session.get(self._page_url(page_index), timeout=45)
                response.raise_for_status()
            except requests.RequestException as exc:
                logger.error(
                    "%s %s official roster page %d failed: %s",
                    self.county,
                    self.state,
                    page_index + 1,
                    exc,
                )
                break

            page_records = self._parse_page(response.text)
            if not page_records:
                logger.warning(
                    "%s %s official roster page %d yielded no parseable records",
                    self.county,
                    self.state,
                    page_index + 1,
                )
                break

            added = 0
            for record in page_records:
                dedup_key = record.get_dedup_key()
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    records.append(record)
                    added += 1
            if added == 0:
                logger.warning(
                    "%s %s official roster page %d yielded no new records; stopping",
                    self.county,
                    self.state,
                    page_index + 1,
                )
                break
            if not self._has_next_page(response.text):
                break
            time.sleep(self.PAGE_DELAY_SECONDS)

        logger.info("%s %s official public roster: %d records", self.county, self.state, len(records))
        return records

    def _page_url(self, page_index: int) -> str:
        if page_index <= 0:
            return self.ROSTER_URL
        return f"{self.ROSTER_URL}?grp={page_index * self.PAGE_SIZE}"

    def _parse_page(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []
        for card in soup.select(".inmate_div"):
            record = self._card_to_record(card)
            if record is not None:
                records.append(record)
        return records

    def _card_to_record(self, card) -> ArrestRecord | None:
        lines = [" ".join(value.split()) for value in card.stripped_strings]
        if not lines:
            return None
        full_name = self._name_from_card(card, lines)
        booking_number = self._label_value(lines, "Booking #")
        booking_date = self._label_value(lines, "Booking Date")
        if not full_name or not booking_number or not booking_date:
            return None

        first_name, middle_name, last_name = self._split_name(full_name)
        if not first_name or not last_name:
            return None

        charges = self._label_block(lines, "Charges", {"Bond", "Age", "Booking #", "Booking Date"})
        bond = self._label_value(lines, "Bond") or "0"
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
            Facility="Etowah County Detention Center",
            Agency="Etowah County Sheriff",
            Age_At_Arrest=self._label_value(lines, "Age"),
            Charges=charges or "Unknown",
            Bond_Amount=re.sub(r"[^\d.]", "", bond) or "0",
            Detail_URL=self.ROSTER_URL,
            Mugshot_URL="",
            extra_data={"booking_key_origin": "source-issued public Booking #"},
        )

    @staticmethod
    def _name_from_card(card, lines: list[str]) -> str:
        heading = card.find(["h1", "h2", "h3", "h4", "h5"])
        value = heading.get_text(" ", strip=True) if heading else lines[0]
        return " ".join(value.split())

    @staticmethod
    def _label_value(lines: list[str], label: str) -> str:
        pattern = re.compile(rf"^{re.escape(label)}\s*:\s*(.*)$", flags=re.I)
        for index, line in enumerate(lines):
            match = pattern.match(line)
            if not match:
                continue
            value = match.group(1).strip()
            if value:
                return value
            if index + 1 < len(lines) and not re.match(r"^[A-Za-z #]+:\s*$", lines[index + 1]):
                return lines[index + 1].strip()
        return ""

    @staticmethod
    def _label_block(lines: list[str], label: str, stop_labels: set[str]) -> str:
        pattern = re.compile(rf"^{re.escape(label)}\s*:\s*(.*)$", flags=re.I)
        for index, line in enumerate(lines):
            match = pattern.match(line)
            if not match:
                continue
            values = [match.group(1).strip()] if match.group(1).strip() else []
            for following in lines[index + 1 :]:
                if any(re.match(rf"^{re.escape(stop)}\s*:", following, flags=re.I) for stop in stop_labels):
                    break
                if following:
                    values.append(following)
            return " | ".join(dict.fromkeys(values))
        return ""

    @staticmethod
    def _has_next_page(html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        return any(
            anchor.get_text(" ", strip=True) == ">" and "grp=" in anchor.get("href", "")
            for anchor in soup.find_all("a", href=True)
        )

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
