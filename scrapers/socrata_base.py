"""Base scraper for public Socrata Open Data APIs.

A Socrata source is usable only when each emitted item contains a complete public
identity, a source-issued booking identifier, and a booking or arrest date. The
base never synthesizes a booking number from a name or current time.
"""
import logging
import time
from typing import Any, Dict, List, Optional

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class SocrataBaseScraper(BaseScraper):
    """Parse source-faithful public Socrata records and fail closed on gaps."""

    @property
    def county(self) -> str:
        raise NotImplementedError("Subclasses must define county name")

    @property
    def socrata_url(self) -> str:
        raise NotImplementedError("Subclasses must define a public Socrata JSON endpoint")

    @staticmethod
    def _text(value: Any) -> str:
        return " ".join(str(value or "").split())

    @classmethod
    def _record_from_item(cls, item: Dict[str, Any], county: str, state: str) -> Optional[ArrestRecord]:
        """Map one source item only when its identity and booking boundary are explicit."""
        first_name = cls._text(item.get("first_name"))
        last_name = cls._text(item.get("last_name"))
        full_name = cls._text(item.get("name"))
        if not full_name and len(first_name) > 1 and len(last_name) > 1:
            full_name = f"{last_name}, {first_name}"

        booking_number = cls._text(item.get("booking_number") or item.get("so_id"))
        booking_date = cls._text(item.get("booking_date") or item.get("arrest_date"))
        if not full_name or not booking_number or not booking_date:
            return None

        charges = cls._text(item.get("charge") or item.get("charges"))
        if not charges:
            charges = " | ".join(
                cls._text(value)
                for key, value in item.items()
                if "charge" in key.lower() and cls._text(value)
            )

        bond = cls._text(item.get("bond_amount") or item.get("bond"))
        if bond:
            bond = bond.replace("$", "").replace(",", "").strip()
        if not bond or not bond.replace(".", "").isdigit():
            bond = "0"

        source_key_field = "booking_number" if cls._text(item.get("booking_number")) else "so_id"
        return ArrestRecord(
            County=county,
            State=state,
            Full_Name=full_name,
            First_Name=first_name,
            Last_Name=last_name,
            Booking_Number=booking_number,
            Booking_Date=booking_date,
            Charges=charges,
            Bond_Amount=bond,
            Status="Unknown",
            extra_data={"booking_key_origin": f"source-issued public {source_key_field}"},
        )

    def scrape(self) -> List[ArrestRecord]:
        """Fetch one bounded source list and emit only source-safe records."""
        start_time = time.time()
        url = f"{self.socrata_url}?$limit=10000"
        logger.info("Fetching Socrata data for %s", self.county)

        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                },
                timeout=15,
            )
            if response.status_code != 200:
                logger.error("Socrata source returned HTTP %s for %s", response.status_code, self.county)
                return []

            data = response.json()
            if not isinstance(data, list):
                logger.error("Socrata source returned a non-list payload for %s", self.county)
                return []

            state = getattr(self, "state", None) or "FL"
            records = [
                record
                for item in data
                if isinstance(item, dict)
                for record in [self._record_from_item(item, self.county, state)]
                if record is not None
            ]
            logger.info("Parsed %d source-safe Socrata records for %s in %.1fs", len(records), self.county, time.time() - start_time)
            return records
        except Exception as exc:
            logger.error("Socrata scrape failed for %s: %s", self.county, exc)
            return []
