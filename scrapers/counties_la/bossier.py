"""Bossier Parish, Louisiana official public-roster scraper.

Source: https://www.bossiersheriff.com/inmateLookup

The Bossier Parish Sheriff's Office publishes a normal-access, paginated public
listing. Its server response contains public listing cards with complete name
fields, a source-issued ``inmateID``, and a labelled ``Booked Date`` with time.
This scraper reads only those listing cards. It never requests individual
profiles, images, notification routes, or search-only endpoints.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Iterable, List

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class BossierParishScraper(BaseScraper):
    """Scrape Bossier's official public inmate-listing cards conservatively."""

    PORTAL_URL = "https://www.bossiersheriff.com/inmateLookup"
    MAX_PAGES = 150
    PAGE_DELAY_SECONDS = 0.25

    @property
    def county(self) -> str:
        return "Bossier"

    @property
    def state(self) -> str:
        return "LA"

    def scrape(self) -> List[ArrestRecord]:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml",
            }
        )

        records: List[ArrestRecord] = []
        seen: set[str] = set()
        for page_number in range(1, self.MAX_PAGES + 1):
            try:
                response = session.get(
                    self.PORTAL_URL,
                    params={"page": page_number},
                    timeout=45,
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                logger.error(
                    "%s %s official roster page %d failed: %s",
                    self.county,
                    self.state,
                    page_number,
                    exc,
                )
                break

            page_records = self._parse_page(response.text)
            if not page_records:
                logger.warning(
                    "%s %s official roster page %d yielded no parseable records",
                    self.county,
                    self.state,
                    page_number,
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
                    page_number,
                )
                break

            if len(page_records) < 10:
                break
            time.sleep(self.PAGE_DELAY_SECONDS)

        logger.info(
            "%s %s official public roster: %d records",
            self.county,
            self.state,
            len(records),
        )
        return records

    def _parse_page(self, html: str) -> List[ArrestRecord]:
        """Extract source card objects from Next Flight data in the public page."""
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script"):
            body = script.string or script.get_text() or ""
            if "Inmate ID" not in body:
                continue
            flight = self._flight_payload(body)
            if not flight:
                continue
            records = [
                record
                for card in self._flight_cards(flight)
                if (record := self._card_to_record(card)) is not None
            ]
            if records:
                return records
        return []

    @staticmethod
    def _flight_payload(script_body: str) -> str:
        match = re.search(r"\.push\((.*)\)\s*$", script_body, flags=re.S)
        if not match:
            return ""
        try:
            envelope = json.loads(match.group(1))
        except json.JSONDecodeError:
            return ""
        if not isinstance(envelope, list) or len(envelope) < 2:
            return ""
        return envelope[1] if isinstance(envelope[1], str) else ""

    @staticmethod
    def _flight_cards(flight: str) -> Iterable[dict]:
        decoder = json.JSONDecoder()
        for object_start in (match.start() for match in re.finditer(r'\{"_id"', flight)):
            try:
                card, _ = decoder.raw_decode(flight[object_start:])
            except json.JSONDecodeError:
                continue
            if not isinstance(card, dict):
                continue
            if not all(key in card for key in ("inmateID", "firstName", "lastName", "content")):
                continue
            if "Inmate ID" not in str(card["content"]) or "Booked Date" not in str(card["content"]):
                continue
            yield card

    def _card_to_record(self, card: dict) -> ArrestRecord | None:
        first_source = " ".join(str(card.get("firstName", "")).split())
        last_source = " ".join(str(card.get("lastName", "")).split())
        inmate_id = " ".join(str(card.get("inmateID", "")).split())
        content = BeautifulSoup(str(card.get("content", "")), "html.parser").get_text("\n", strip=True)
        booked = self._field(content, "Booked Date")
        booking_date, booking_time = self._parse_booked_date(booked)
        if not first_source or not last_source or not inmate_id or not booking_date or not booking_time:
            return None

        first_name, middle_name = self._split_first_name(first_source)
        full_name = f"{first_source} {last_source}".strip()
        return ArrestRecord(
            County=self.county,
            State=self.state,
            Booking_Number=inmate_id,
            Person_ID=inmate_id,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_source.title(),
            Booking_Date=booking_date,
            Booking_Time=booking_time,
            Status="In Custody",
            Facility="Bossier Parish Jail",
            Agency="Bossier Parish Sheriff",
            Charges="Unknown",
            Bond_Amount="0",
            Detail_URL=self.PORTAL_URL,
            extra_data={
                "booking_key_origin": "source-issued public Inmate ID",
                "booking_datetime_origin": "source-issued public Booked Date",
            },
        )

    @staticmethod
    def _field(text: str, label: str) -> str:
        match = re.search(rf"{re.escape(label)}\s*:\s*([^\r\n]+)", text, flags=re.I)
        value = match.group(1).strip() if match else ""
        if re.fullmatch(r"[A-Za-z #]+:", value):
            return ""
        return value

    @staticmethod
    def _parse_booked_date(value: str) -> tuple[str, str]:
        match = re.fullmatch(
            r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})(?:\s+[A-Z]{2,5})?",
            value.strip(),
        )
        if not match:
            return "", ""
        try:
            parsed = datetime.strptime(
                f"{match.group(1)} {match.group(2)}",
                "%m/%d/%Y %H:%M:%S",
            )
        except ValueError:
            return "", ""
        return parsed.strftime("%Y-%m-%d"), parsed.strftime("%H:%M:%S")

    @staticmethod
    def _split_first_name(first_source: str) -> tuple[str, str]:
        parts = first_source.split()
        return (
            parts[0].title() if parts else "",
            " ".join(parts[1:]).title() if len(parts) > 1 else "",
        )
