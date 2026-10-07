"""Newberry County (SC) current-bookings PDF scraper.

The official county inmate-search page temporarily links a current-bookings PDF
instead of a live search interface. This scraper discovers the current Sheriff
PDF from that page on every run and retains only entries carrying a
source-provided booking identifier (``SO-`` / ``NP-`` / ``HP-`` / ``PP-`` /
``HA-`` / ``SL-`` / ``GS-`` and the legacy ``SO#`` form).

Charges are taken from the PDF charge lines under each inmate. Dollar bond
amounts are taken only when the PDF prints a ``$`` amount — release labels
like ``BOND POSTED`` / ``PR BOND`` are not bond dollars and are not invented
into ``Bond_Amount``.
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

# Live 2026-10 PDF: "LAST, FIRST - SO-0024345 - 25" (also NP/HP/PP/HA/SL/GS).
_INMATE_HEADER = re.compile(
    r"(?P<name>[A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-\.]+)*,\s*"
    r"[A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-\.]+)*)"
    r"\s*-\s*(?P<booking>[A-Z]{2}-\d{4,})\s*-\s*(?P<age>\d{1,3})"
    r"(?:\s+(?P<inline_charge>.+))?",
    re.I,
)
# Legacy fixture form: "SO# ABC-123" / "SO- ABC-123"
_LEGACY_SO = re.compile(r"\bSO\s*[-#:]*\s*([A-Z0-9][A-Z0-9-]{2,})\b", re.I)
_LEGACY_NAME = re.compile(
    r"\b([A-Z][A-Z'\-]{1,}(?:\s+[A-Z][A-Z'\-]{1,})*),\s*"
    r"([A-Z][A-Z'\-]{1,}(?:\s+[A-Z][A-Z'\-]{1,})*)\b"
)
_BOND = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
_DATE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")
_RELEASED = re.compile(r"Released\s+(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
_SECTION_DATE = re.compile(r"^\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*$")
_PAGE_NOISE = re.compile(
    r"Prisoners/Charges Booked In by Date Range|^\s*Page\s+\d+\s*$",
    re.I,
)
_SKIP_CHARGE = re.compile(
    r"^(Released\b|Booked\b|Bond\s*\$|SO\s*#|Page\s+\d+|Prisoners/)",
    re.I,
)


class NewberryScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "newberrycounty.gov Sheriff uploads current-bookings PDF; "
        "source booking id (SO/NP/HP/PP/HA/SL/GS-#) required; charges from "
        "PDF charge lines; dollar Bond_Amount only when $ is printed."
    )

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
        modern = self._parse_modern_layout(text, pdf_url)
        if modern:
            return modern
        return self._parse_legacy_so_layout(text, pdf_url)

    def _parse_modern_layout(self, text: str, pdf_url: str) -> List[ArrestRecord]:
        lines = [ln.rstrip() for ln in text.splitlines()]
        headers: List[Tuple[int, re.Match]] = []
        for idx, line in enumerate(lines):
            match = _INMATE_HEADER.search(line)
            if match:
                headers.append((idx, match))
        if not headers:
            return []

        section_dates: List[Tuple[int, str]] = []
        for idx, line in enumerate(lines):
            if _SECTION_DATE.match(line.strip()):
                section_dates.append((idx, line.strip()))

        records: List[ArrestRecord] = []
        seen = set()
        for h_i, (line_idx, match) in enumerate(headers):
            booking_number = match.group("booking").upper()
            if booking_number in seen:
                continue

            end_idx = headers[h_i + 1][0] if h_i + 1 < len(headers) else len(lines)
            block_lines = lines[line_idx:end_idx]

            charges: List[str] = []
            inline = (match.group("inline_charge") or "").strip()
            if inline and not _SKIP_CHARGE.match(inline):
                charges.append(self._clean_charge(inline))

            booking_date = ""
            for prior_idx, prior_date in reversed(section_dates):
                if prior_idx < line_idx:
                    booking_date = prior_date
                    break

            for raw in block_lines[1:]:
                line = raw.strip()
                if not line or _PAGE_NOISE.search(line):
                    continue
                released = _RELEASED.search(line)
                if released:
                    if not booking_date:
                        booking_date = released.group(1)
                    continue
                if _SKIP_CHARGE.match(line):
                    continue
                if _INMATE_HEADER.search(line):
                    continue
                if _SECTION_DATE.match(line):
                    continue
                charge = self._clean_charge(line)
                if charge and charge not in charges:
                    charges.append(charge)

            # Dollar bonds only from explicit Bond lines — never from charge
            # statute text like "VALUE $2,000 OR LESS", and never from release
            # labels like "BOND POSTED" / "PR BOND".
            bond_amount = "0"
            for raw in block_lines:
                line = raw.strip()
                if not re.match(r"(?i)^bond\b", line):
                    continue
                money = _BOND.findall(line)
                if money:
                    bond_amount = money[-1].replace(",", "")

            full_name = re.sub(r"\s+", " ", match.group("name")).strip().title()
            last_name, _, rest = full_name.partition(",")
            last_name = last_name.strip()
            name_parts = rest.strip().split()
            first_name = name_parts[0] if name_parts else ""
            middle_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

            seen.add(booking_number)
            records.append(
                ArrestRecord(
                    County=self.county,
                    State=self.state,
                    Full_Name=f"{last_name}, {first_name} {middle_name}".strip(),
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    Booking_Number=booking_number,
                    Booking_Date=booking_date,
                    Age_At_Arrest=match.group("age"),
                    Charges=" | ".join(charges) if charges else "",
                    Bond_Amount=bond_amount,
                    Status="In Custody",
                    Facility=FACILITY,
                    Agency="Newberry County Sheriff",
                    Detail_URL=pdf_url,
                    LastCheckedMode="INITIAL",
                )
            )
        return records

    def _parse_legacy_so_layout(self, text: str, pdf_url: str) -> List[ArrestRecord]:
        """Retain the earlier SO#-block fixture format for regression tests."""
        records: List[ArrestRecord] = []
        seen = set()
        matches = list(_LEGACY_SO.finditer(text))
        for index, identifier_match in enumerate(matches):
            booking_number = f"SO-{identifier_match.group(1).upper()}"
            if booking_number in seen:
                continue

            previous_boundary = (
                matches[index - 1].end() if index else max(0, identifier_match.start() - 600)
            )
            following_name = _LEGACY_NAME.search(text, identifier_match.end())
            following_identifier = (
                matches[index + 1].start() if index + 1 < len(matches) else len(text)
            )
            next_boundary = min(
                following_name.start() if following_name else len(text),
                following_identifier,
                identifier_match.end() + 900,
            )
            context = text[previous_boundary:next_boundary]
            names = list(_LEGACY_NAME.finditer(context))
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

            # Legacy layout rarely printed charge lines; leave empty rather than
            # inventing "Unknown".
            charges = ""
            charge_lines = []
            for line in context.splitlines():
                stripped = line.strip()
                if not stripped or _LEGACY_NAME.search(stripped) or _LEGACY_SO.search(stripped):
                    continue
                if _SKIP_CHARGE.match(stripped) or _DATE.fullmatch(stripped):
                    continue
                if _BOND.search(stripped) and not re.search(r"[A-Za-z]{3,}", stripped.replace("Bond", "")):
                    continue
                if stripped.lower().startswith("bond"):
                    continue
                cleaned = self._clean_charge(stripped)
                if cleaned and cleaned not in charge_lines:
                    charge_lines.append(cleaned)
            if charge_lines:
                charges = " | ".join(charge_lines)

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
                    Charges=charges,
                    Bond_Amount=bond_amount,
                    Status="In Custody",
                    Facility=FACILITY,
                    Agency="Newberry County Sheriff",
                    Detail_URL=pdf_url,
                    LastCheckedMode="INITIAL",
                )
            )
        return records

    @staticmethod
    def _clean_charge(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip(" -|")
        if len(cleaned) < 3:
            return ""
        return cleaned
