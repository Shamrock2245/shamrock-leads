"""
Citrus County (FL) Arrest Scraper — public recent-arrest PDF roster.

Source contract (recon 2026-10-07, docs/recon/FL_IDLE_EIGHT_2026-10-07.md):
  * Landing: https://www.sheriffcitrus.org/public_info/recent_arrest.php
  * Plain HTTPS returns 200; an iframe points at a dated PDF under
    ``/public info/recent arrests/…Arrests and Charges….pdf``.
  * PDF table columns: Photo / Name / AR # / Date / Arrest Type / Offense / DOB / Bond.
  * Source-issued identifier is ``AR #`` (pattern ``AAYY-NNNNNN``). Rows without AR # are dropped.
  * No DrissionPage / stealth / proxy — ordinary requests + pdfplumber.

Honest access and no invented values (2026-10-15): both requests go through
``scrapers.plain_http`` (honest User-Agent, certificate verified; 401/403/429 or
a challenge raises ``egress_block:``, a real certificate failure
``tls_cert_invalid:``; nothing works around a block). Bond is the published
amount (a published 0 is kept); blank / NO BOND / HOLD / text is ``""``. This
is a recent-arrests list that never states custody, so Status is ``""``.
"""
from __future__ import annotations

import io
import logging
import re
from html import unescape
from typing import List
from urllib.parse import urljoin

from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import make_session, request
from scrapers.published_bond import charge_bond_amount
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sheriffcitrus.org"
PAGE_URL = f"{BASE_URL}/public_info/recent_arrest.php"
FACILITY = "Citrus County Detention Facility"
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
    "Referer": PAGE_URL,
}

_AR_RE = re.compile(r"^[A-Z]{1,4}\d{2}-\d{4,}$", re.I)


class CitrusCountyScraper(BaseScraper):
    """Citrus County (FL) — recent arrest PDF roster (Inverness / Lecanto)."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Citrus"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import pdfplumber  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("Citrus: pdfplumber is required to parse the arrest PDF") from exc

        session = make_session(HEADERS)
        resp = request(session, "GET", PAGE_URL, county="Citrus", timeout=45)
        resp.raise_for_status()
        pdf_url = self._extract_pdf_url(resp.text)
        if not pdf_url:
            logger.warning("Citrus: no arrest PDF iframe/link on landing page")
            return []

        logger.info("Citrus: downloading PDF %s", pdf_url.split("?")[0])
        pdf_resp = request(session, "GET", pdf_url, county="Citrus", timeout=90)
        pdf_resp.raise_for_status()
        if not pdf_resp.content.startswith(b"%PDF"):
            raise RuntimeError("Citrus: arrest PDF response was not a PDF")
        return self._parse_pdf(pdf_resp.content, detail_url=pdf_url.split("?")[0])

    @staticmethod
    def _extract_pdf_url(html: str) -> str:
        m = re.search(r"<iframe[^>]+src=(['\"])(.*?)\1", html, re.I | re.S)
        if m:
            src = unescape(m.group(2)).strip()
            if ".pdf" in src.lower():
                return urljoin(BASE_URL + "/", src)
        for href in re.findall(r'href=(["\'])(.*?)\1', html, re.I):
            src = unescape(href[1]).strip()
            if ".pdf" in src.lower() and re.search(r"arrest", src, re.I):
                return urljoin(BASE_URL + "/", src)
        return ""

    def _parse_pdf(self, pdf_bytes: bytes, detail_url: str = PAGE_URL) -> List[ArrestRecord]:
        import pdfplumber

        records: List[ArrestRecord] = []
        seen: set[str] = set()
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    if not table or len(table) < 2:
                        continue
                    header = [(c or "").strip().lower() for c in table[0]]
                    if "ar #" not in header and "name" not in header:
                        continue
                    col = {h: i for i, h in enumerate(header)}
                    for row in table[1:]:
                        if not row:
                            continue
                        rec = self._row_to_record(row, col, seen, detail_url)
                        if rec:
                            records.append(rec)
        logger.info("Citrus: %d records from PDF", len(records))
        return records

    def _row_to_record(self, row, col: dict, seen: set, detail_url: str):
        def cell(key: str) -> str:
            idx = col.get(key)
            if idx is None or idx >= len(row):
                return ""
            return " ".join(str(row[idx] or "").replace("\n", " ").split())

        booking = cell("ar #") or cell("ar#")
        if not booking or not _AR_RE.match(booking):
            return None
        if booking in seen:
            return None
        seen.add(booking)

        full_name = cell("name")
        if not full_name or len(full_name) < 3:
            return None
        # Name cell sometimes includes a trailing fragment on a second line already flattened
        first, middle, last = self._parse_name(full_name)
        bond_amount = self._parse_bond(cell("bond"))
        return ArrestRecord(
            County=self.county,
            State="FL",
            Booking_Number=booking,
            Full_Name=full_name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            DOB=cell("dob"),
            Booking_Date=cell("date"),
            Status="",  # recent-arrests list; the source never states custody
            Facility=FACILITY,
            Charges=cell("offense"),
            Bond_Amount=bond_amount,
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        )

    @staticmethod
    def _parse_name(name: str):
        if not name:
            return "", "", ""
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            fm = parts[1].strip().split()
            first = fm[0] if fm else ""
            middle = " ".join(fm[1:]) if len(fm) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) == 1:
            return parts[0], "", ""
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return parts[0], " ".join(parts[1:-1]), parts[-1]

    @staticmethod
    def _parse_bond(bond_str: str) -> str:
        """Published amount as a string ("1500", "0", "250.5"); unknown is ""."""
        amount = charge_bond_amount(bond_str)
        if amount is None:
            return ""
        return str(int(amount)) if float(amount).is_integer() else f"{amount:.2f}"
