"""York County, South Carolina public detention-roster scraper.

The official ASP.NET listing renders one nested table per person. This parser uses
only the public listing cards and fails closed without the source-issued Booking
Number, booking date, and complete displayed name. It never creates a synthetic
booking identifier.
"""
import logging
import re
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup, Tag

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)
PORTAL_URL = "https://inmatesinjail.yorkcountygov.com/detentioncenter/inmatesinjail.aspx"


class YorkScraper(BaseScraper):

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "The configured York roster path timed out through ordinary access; no booking-safe broad roster contract is revalidated."
    )
    """Parse source-faithful York County public booking cards."""

    @property
    def county(self) -> str:
        return "York"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def roster_url(self) -> str:
        return PORTAL_URL

    @staticmethod
    def _text(node: Optional[Tag]) -> str:
        return " ".join(node.get_text(" ", strip=True).split()) if node else ""

    @classmethod
    def _labeled_values(cls, card: Tag) -> Dict[str, str]:
        """Read only explicit label/value pairs in a top-level booking-card row."""
        values: Dict[str, str] = {}
        rows = card.find_all("tr", recursive=False)
        for row_index, row in enumerate(rows):
            cells = row.find_all(["th", "td"], recursive=False)
            texts = [cls._text(cell) for cell in cells]
            normalized = [text.rstrip(":").casefold() for text in texts]

            # Live York cards put labels in one row and values at the same
            # column positions in the following row. Controlled test cards
            # use label/value pairs instead, so retain that safe fallback.
            try:
                booking_index = normalized.index("booking number")
                date_index = normalized.index("booking date")
            except ValueError:
                booking_index = date_index = -1
            if booking_index >= 0 and date_index == booking_index + 1 and row_index + 1 < len(rows):
                next_cells = rows[row_index + 1].find_all(["th", "td"], recursive=False)
                next_texts = [cls._text(cell) for cell in next_cells]
                if booking_index < len(next_texts) and next_texts[booking_index]:
                    values["booking number"] = next_texts[booking_index]
                if date_index < len(next_texts) and next_texts[date_index]:
                    values["booking date"] = next_texts[date_index]
                continue

            for index, label in enumerate(normalized[:-1]):
                if label in {"booking number", "booking date", "bond", "bond amount"}:
                    value = texts[index + 1]
                    if value:
                        values[label] = value
        return values

    @classmethod
    def _charges(cls, card: Tag) -> str:
        """Collect public charge descriptions from the card's nested charge table."""
        charges: List[str] = []
        for table in card.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            headers = [cls._text(cell).casefold() for cell in rows[0].find_all(["th", "td"])]
            if "charge description" not in headers:
                continue
            charge_index = headers.index("charge description")
            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) > charge_index:
                    charge = cls._text(cells[charge_index])
                    if charge:
                        charges.append(charge)
        return " | ".join(dict.fromkeys(charges))

    @classmethod
    def _record_from_card(cls, card: Tag) -> Optional[ArrestRecord]:
        direct_rows = card.find_all("tr", recursive=False)
        if not direct_rows:
            return None
        name_cell = direct_rows[0].find(["th", "td"], recursive=False)
        full_name = cls._text(name_cell)
        values = cls._labeled_values(card)
        booking_number = values.get("booking number", "")
        booking_date = values.get("booking date", "")
        if not full_name or not booking_number or not booking_date:
            return None

        first_name = last_name = middle_name = ""
        if "," in full_name:
            last_name, remainder = [part.strip() for part in full_name.split(",", 1)]
            parts = remainder.split()
            if parts:
                first_name = parts[0]
                middle_name = " ".join(parts[1:])

        bond = values.get("bond amount", values.get("bond", "0"))
        bond = re.sub(r"[^\d.]", "", bond) or "0"
        return ArrestRecord(
            County="York",
            State="SC",
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            Booking_Number=booking_number,
            Booking_Date=booking_date,
            Charges=cls._charges(card),
            Bond_Amount=bond,
            Status="Unknown",
            Detail_URL=PORTAL_URL,
            extra_data={"booking_key_origin": "source-issued public Booking Number"},
        )

    def scrape(self) -> List[ArrestRecord]:
        try:
            response = requests.get(
                PORTAL_URL,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ShamrockRoster/1.0)"},
                timeout=30,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            records: List[ArrestRecord] = []
            seen_booking_numbers = set()
            for card in soup.find_all("table"):
                record = self._record_from_card(card)
                if record is None or record.Booking_Number in seen_booking_numbers:
                    continue
                seen_booking_numbers.add(record.Booking_Number)
                records.append(record)
            logger.info("Parsed %d source-safe public York booking cards", len(records))
            return records
        except requests.RequestException as exc:
            logger.error("York public roster request failed: %s", exc)
            return []
