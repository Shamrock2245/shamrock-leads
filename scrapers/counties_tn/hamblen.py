"""
Hamblen County, Tennessee public ISOMS jail-roster scraper.

Source: https://isoms.co.hamblen.tn.us/portal/Jail

The public roster publishes current inmates through server-rendered, paginated
HTML via the county's public ISOMS portal. It publishes demographics, intake date/time,
charges, and individual bond amounts. Because ISOMS does not display a county-issued
booking number on the public cards, this scraper derives a deterministic surrogate
from the publicly displayed full name and intake timestamp to satisfy the
County + Booking_Number dedup key contract.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ROSTER_URL = "https://isoms.co.hamblen.tn.us/portal/Jail"
FACILITY = "Hamblen County Jail"
DEFAULT_AGENCY = "Hamblen County Sheriff's Office"
MAX_PAGES = 30
REQUEST_DELAY_SECONDS = 0.35
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class HamblenScraper(BaseScraper):
    """Scrape the public current-inmate roster for Hamblen County, Tennessee."""

    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = ""

    @property
    def county(self) -> str:
        return "Hamblen"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def scraper_id(self) -> str:
        return "scraper_tn_hamblen"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen: Set[str] = set()
        session = requests.Session()
        session.headers.update(HEADERS)

        try:
            first = self._fetch_page(session, 0)
            if first is None:
                return records

            total_pages = self._page_total(first)
            pages = min(max(total_pages, 1), MAX_PAGES)
            for page_num in range(pages):
                soup = first if page_num == 0 else self._fetch_page(session, page_num)
                if soup is None:
                    logger.warning("Hamblen (TN): stopping at unavailable page %d", page_num)
                    break

                page_records = self._parse_page(soup)
                if not page_records:
                    logger.warning("Hamblen (TN): stopping at empty page %d", page_num)
                    break

                new_records = 0
                for record in page_records:
                    key = record.get_dedup_key()
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(record)
                    new_records += 1

                if new_records == 0:
                    logger.warning("Hamblen (TN): stopping at duplicate-only page %d", page_num)
                    break
                if page_num + 1 < pages:
                    time.sleep(REQUEST_DELAY_SECONDS)
        except requests.RequestException as exc:
            logger.error("Hamblen (TN) roster request failed: %s", exc)
        except Exception as exc:
            logger.error("Hamblen (TN) roster parse failed: %s", exc)

        logger.info("Hamblen (TN): %d records in %.1fs", len(records), time.time() - start)
        return records

    @staticmethod
    def _fetch_page(session: requests.Session, page_num: int) -> Optional[BeautifulSoup]:
        response = session.get(
            ROSTER_URL,
            params={"hours": "0", "pagenum": str(page_num)},
            timeout=45,
        )
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    @staticmethod
    def _page_total(soup: BeautifulSoup) -> int:
        """Return the roster page count advertised by public pagination links."""
        page_numbers = []
        for anchor in soup.select('a[href*="pagenum="]'):
            match = re.search(r"[?&]pagenum=(\d+)", anchor.get("href", ""))
            if match:
                page_numbers.append(int(match.group(1)))
        return max(page_numbers) + 1 if page_numbers else 1

    def _parse_page(self, soup: BeautifulSoup) -> List[ArrestRecord]:
        records: List[ArrestRecord] = []
        for card in soup.select("article.inmate"):
            record = self._card_to_record(card)
            if record:
                records.append(record)
        return records

    def _card_to_record(self, card) -> Optional[ArrestRecord]:
        name_node = card.find("h1")
        full_name = name_node.get_text(" ", strip=True) if name_node else ""
        if not full_name:
            return None

        fields = self._public_fields(card)
        intake = fields.get("intake date", "")
        if not intake:
            logger.warning("Hamblen (TN): skipping card without public intake timestamp")
            return None

        first, middle, last = self._parse_name(full_name)
        race, sex = self._parse_race_sex(fields.get("race/sex", ""))
        release = fields.get("release date", "")
        charges, bond_amount = self._charges_and_bond(card)
        booking_number = self._surrogate_booking_number(full_name, intake)

        return ArrestRecord(
            County=self.county,
            State=self.state,
            Full_Name=full_name.title() if full_name.isupper() else full_name,
            First_Name=first.title() if first.isupper() else first,
            Middle_Name=middle.title() if middle.isupper() else middle,
            Last_Name=last.title() if last.isupper() else last,
            Booking_Number=booking_number,
            Booking_Date=intake,
            Arrest_Date=intake,
            Age_At_Arrest=fields.get("age", ""),
            Race=race,
            Sex=sex,
            City=fields.get("city", ""),
            Agency=fields.get("arrested by department", DEFAULT_AGENCY),
            Charges=charges,
            Bond_Amount=bond_amount,
            Status="Released" if release else "In Custody",
            Release_Date=release,
            Facility=FACILITY,
            Detail_URL=ROSTER_URL,
            LastCheckedMode="INITIAL",
            extra_data={"booking_number_origin": "deterministic_public_roster_surrogate"},
        )

    @staticmethod
    def _public_fields(card) -> dict[str, str]:
        fields: dict[str, str] = {}
        for container in card.find_all("p"):
            label_node = container.find(["h2", "h3"])
            value_node = container.find("data")
            if not label_node or not value_node:
                continue
            label = label_node.get_text(" ", strip=True).rstrip(":").casefold()
            if label:
                fields[label] = value_node.get_text(" ", strip=True)

        if not fields:
            for label_node in card.select("data.data-left"):
                label = label_node.get_text(" ", strip=True).rstrip(":").casefold()
                value_node = label_node.find_next_sibling("data", class_="data-right")
                if label and value_node:
                    fields[label] = value_node.get_text(" ", strip=True)
        return fields

    @staticmethod
    def _parse_name(full_name: str) -> Tuple[str, str, str]:
        if "," in full_name:
            last, remainder = [part.strip() for part in full_name.split(",", 1)]
            parts = remainder.split()
            return (parts[0] if parts else "", " ".join(parts[1:]), last)
        parts = full_name.split()
        if len(parts) < 2:
            return "", "", full_name
        return parts[0], " ".join(parts[1:-1]), parts[-1]

    @staticmethod
    def _parse_race_sex(value: str) -> Tuple[str, str]:
        parts = [part.strip() for part in re.split(r"[/,]", value) if part.strip()]
        return (parts[0] if parts else "", parts[1][:1] if len(parts) > 1 else "")

    @staticmethod
    def _surrogate_booking_number(full_name: str, intake: str) -> str:
        """Create a deterministic identifier for deduplication."""
        normalized = f"{full_name.strip().upper()}|{intake.strip().upper()}"
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
        return f"HAMBLEN-{digest}"

    @staticmethod
    def _charges_and_bond(card) -> Tuple[str, str]:
        charges = []
        bond_total = 0.0
        for row in card.select("table.charges tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
            if len(cells) < 2:
                continue
            charge, bond = cells[0], cells[1]
            if charge:
                charges.append(charge)
            amount = re.sub(r"[^\d.]", "", bond)
            try:
                bond_total += float(amount) if amount else 0.0
            except ValueError:
                continue
        bond = f"{bond_total:.2f}" if bond_total else "0"
        return (" | ".join(charges) if charges else "Unknown", bond)
