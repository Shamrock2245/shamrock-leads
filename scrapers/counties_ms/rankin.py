"""Rankin County, MS official current-roster scraper (listing-only)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www2.rankincounty.org/so/inmate/current.php"
TIME_FORMATS = ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M")


class RankinScraper(BaseScraper):
    """Parse only public roster rows that satisfy the booking identity contract."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Rankin"

    @property
    def state(self) -> str:
        return "MS"

    def scrape(self) -> List[ArrestRecord]:
        try:
            response = requests.get(ROSTER_URL, timeout=25)
            if response.status_code != 200:
                logger.warning("Rankin MS roster HTTP %s", response.status_code)
                return []
            return self._parse_listing(response.text)
        except requests.RequestException as exc:
            logger.warning("Rankin MS roster request failed: %s", exc)
            return []

    def _parse_listing(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []
        seen: set[str] = set()
        for row in soup.select("table tr")[1:]:
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
            if len(cells) < 6:
                continue
            name, source_id, intake, agency = cells[1], cells[2], cells[4], cells[5]
            booking_date = self._normalize_time(intake)
            if not name or not source_id or not booking_date or source_id in seen:
                continue
            first, last = self._split_name(name)
            seen.add(source_id)
            records.append(ArrestRecord(
                County=self.county,
                State=self.state,
                Full_Name=name.title() if name.isupper() else name,
                First_Name=first,
                Last_Name=last,
                Booking_Number=source_id,
                Booking_Date=booking_date,
                Arrest_Date=booking_date,
                Status="In Custody",
                Facility="Rankin County Detention Center",
                Agency=agency or "Rankin County Sheriff's Office",
            ))
        return records

    @staticmethod
    def _normalize_time(value: str) -> str:
        for fmt in TIME_FORMATS:
            try:
                return datetime.strptime(value, fmt).isoformat()
            except ValueError:
                pass
        return ""

    @staticmethod
    def _split_name(name: str) -> tuple[str, str]:
        if "," in name:
            last, rest = name.split(",", 1)
            return (rest.strip().split() or [""])[0].title(), last.strip().title()
        bits = name.split()
        return (bits[0].title(), bits[-1].title()) if len(bits) >= 2 else (name.title(), "")
