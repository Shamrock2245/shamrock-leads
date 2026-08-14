"""Newberry County (SC) current-bookings PDF scraper.

The official county inmate-search page temporarily links a current-bookings PDF
instead of a live search interface. This scraper discovers the current Sheriff
PDF from that page on every run and retains only entries carrying the document's
source-provided ``SO`` identifier as ``Booking_Number``.
"""
from __future__ import annotations

import io
import logging
import re
import time
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PORTAL_URL = (
    "https://www.newberrycounty.gov/sheriffs-office/"
    "newberry-county-detention-center/inmate-search"
)
FACILITY = "Newberry County Detention Center"

_SO_IDENTIFIER = re.compile(r"\bSO\s*[-#:]*\s*([A-Z0-9][A-Z0-9-]{2,})\b", re.I)
_NAME = re.compile(
    r"\b([A-Z][A-Z'\-]{1,}(?:\s+[A-Z][A-Z'\-]{1,})*),\s*"
    r"([A-Z][A-Z'\-]{1,}(?:\s+[A-Z][A-Z'\-]{1,})*)\b"
)
_BOND = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
_DATE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")


class NewberryScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Newberry"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
            }
        )

        pdf_url, content = self._fetch_current_bookings_pdf(session)
        if not content or not pdf_url:
            logger.warning("Newberry: no current official bookings PDF available")
            return []

        text = self._extract_text(content)
        if not text:
            logger.warning("Newberry: current bookings PDF has no extractable text")
            return []

        records = self._parse_pdf_text(text, pdf_url)
        logger.info(
            "Newberry: %d verified-key records from current PDF in %.1fs",
            len(records),
            time.time() - start,
        )
        return records

    def _fetch_current_bookings_pdf(
        self, session: requests.Session
    ) -> Tuple[Optional[str], Optional[bytes]]:
        try:
            page = session.get(PORTAL_URL, timeout=35, verify=False)
            page.raise_for_status()
        except Exception as exc:
            logger.warning("Newberry inmate-search page unavailable: %s", exc)
            return None, None

        pdf_urls = self._discover_sheriff_pdf_urls(page.text)
        if not pdf_urls:
            logger.warning("Newberry: no Sheriff bookings PDF link found on official page")
            return None, None

        for pdf_url in pdf_urls:
            try:
                response = session.get(pdf_url, timeout=45, verify=False)
                if response.status_code == 200 and response.content.startswith(b"%PDF"):
                    return pdf_url, response.content
            except Exception as exc:
                logger.debug("Newberry PDF fetch failed: %s", exc)
        return None, None

    @staticmethod
    def _discover_sheriff_pdf_urls(html: str) -> List[str]:
        """Return only current-bookings PDF links published under Sheriff uploads."""
        links = set()
        for href in re.findall(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', html, re.I):
            href = href.replace("\\/", "/")
            if "/departments/sheriff-s-office/" not in href.lower():
                continue
            links.add(urljoin("https://www.newberrycounty.gov", href))
        return sorted(links)

    @staticmethod
    def _extract_text(content: bytes) -> str:
        try:
            import pypdf

            reader = pypdf.PdfReader(io.BytesIO(content))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            logger.debug("Newberry pypdf extraction failed: %s", exc)
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(content)) as pdf:
                return "\n".join((page.extract_text() or "") for page in pdf.pages)
        except Exception as exc:
            logger.warning("Newberry PDF extraction failed: %s", exc)
            return ""

    def _parse_pdf_text(self, text: str, pdf_url: str) -> List[ArrestRecord]:
        """Parse source IDs while keeping every record tied to an official PDF key."""
        records: List[ArrestRecord] = []
        seen = set()
        matches = list(_SO_IDENTIFIER.finditer(text))
        for index, identifier_match in enumerate(matches):
            booking_number = f"SO-{identifier_match.group(1).upper()}"
            if booking_number in seen:
                continue

            previous_boundary = matches[index - 1].end() if index else max(0, identifier_match.start() - 600)
            following_name = _NAME.search(text, identifier_match.end())
            following_identifier = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            next_boundary = min(
                following_name.start() if following_name else len(text),
                following_identifier,
                identifier_match.end() + 900,
            )
            context = text[previous_boundary:next_boundary]
            names = list(_NAME.finditer(context))
            if not names:
                continue
            name_match = names[-1]
            last_name = name_match.group(1).title()
            first_middle = name_match.group(2).title().split()
            first_name = first_middle[0] if first_middle else ""
            middle_name = " ".join(first_middle[1:]) if len(first_middle) > 1 else ""
            full_name = f"{last_name}, {first_name} {middle_name}".strip()

            dates = _DATE.findall(context)
            bonds = _BOND.findall(context)
            bond_amount = "0"
            if bonds:
                bond_amount = bonds[-1].replace(",", "")

            seen.add(booking_number)
            records.append(
                ArrestRecord(
                    County=self.county,
                    State=self.state,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    Booking_Number=booking_number,
                    Booking_Date=dates[-1] if dates else "",
                    Charges="Unknown",
                    Bond_Amount=bond_amount,
                    Status="In Custody",
                    Facility=FACILITY,
                    Agency="Newberry County Sheriff",
                    Detail_URL=pdf_url,
                    LastCheckedMode="INITIAL",
                )
            )
        return records
