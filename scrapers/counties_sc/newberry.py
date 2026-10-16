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

2026-10-15: honest identity, published-only bond and status. Every request
goes through scrapers.plain_http (honest User-Agent, certificate verified; no
verify=False). A 401/403/429, challenge page, dropped handshake or bad
certificate raises (egress_block: / tls_cert_invalid:), and a missing PDF link
or an unreadable PDF raises NewberryContractError instead of an empty success.
Bond_Amount is the explicit ``Bond $N`` line only; with none it is "" (a
printed $0.00 is kept), never "0". Status: the PDF is "Prisoners/Charges
Booked In by Date Range" and lists released people ("Released MM/DD/YYYY - ..."),
so it is NOT custody-only and there is no current-only label or view. A
published "Released <date>" line gives Status "Released" + Release_Date
(status_source "published_release_line"); anything else is "". Booking_Date is
the PDF's section date only (never the release date). See
docs/recon/SC_NEWBERRY_ROSTER_SCOPE_2026-10-15.md.
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
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.plain_http import request as plain_request
from scrapers.scraper_resilience import EgressBlocked

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


STATUS_SOURCE_RELEASE = "published_release_line"
_BLOCKS = (EgressBlocked, SourceTLSInvalid)


class NewberryContractError(RuntimeError):
    """The official page/PDF no longer matches the verified contract."""


class _HonestSession(requests.Session):
    """requests.Session whose every call goes through plain_http (honest UA, verify on, typed blocks)."""

    def __init__(self):
        super().__init__()
        self.headers.update({"User-Agent": HONEST_USER_AGENT})
        self.verify = True

    def request(self, method, url, **kwargs):  # noqa: D401
        sess = self

        class _Raw:
            def request(self, m, u, **kw):
                return requests.Session.request(sess, m, u, **kw)

        return plain_request(_Raw(), method, url, county="Newberry", **kwargs)


def published_release(lines) -> str:
    """Release date from a published "Released MM/DD/YYYY" line, else ""."""
    for raw in lines:
        m = _RELEASED.search(raw or "")
        if m:
            return m.group(1)
    return ""


def status_fields(release_date: str) -> Tuple[str, str]:
    """(Status, status_source): published release -> Released; else "" (not custody-only)."""
    if release_date:
        return "Released", STATUS_SOURCE_RELEASE
    return "", ""


def _explicit_bond(lines) -> str:
    """Dollar amount from an explicit ``Bond $N`` line (last wins); else "" (unknown)."""
    amount = ""
    for raw in lines:
        line = (raw or "").strip()
        if not re.match(r"(?i)^bond\b", line):
            continue
        money = _BOND.findall(line)
        if money:
            amount = money[-1].replace(",", "")
    return amount


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
        session = _HonestSession()

        pdf_url, content = self._fetch_current_bookings_pdf(session)
        if not content or not pdf_url:
            raise NewberryContractError("Newberry: no official bookings PDF could be fetched")

        text = self._extract_text(content)
        if not text:
            raise NewberryContractError("Newberry: bookings PDF has no extractable text")

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
            page = session.get(PORTAL_URL, timeout=35)
            page.raise_for_status()
        except _BLOCKS:
            raise
        except Exception as exc:
            logger.warning("Newberry inmate-search page unavailable: %s", exc)
            return None, None

        pdf_urls = self._discover_sheriff_pdf_urls(page.text)
        if not pdf_urls:
            logger.warning("Newberry: no Sheriff bookings PDF link found on official page")
            return None, None

        for pdf_url in pdf_urls:
            try:
                response = session.get(pdf_url, timeout=45)
                if response.status_code == 200 and response.content.startswith(b"%PDF"):
                    return pdf_url, response.content
            except _BLOCKS:
                raise
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
                if _RELEASED.search(line):
                    continue  # release line: status below; never used as Booking_Date
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
            bond_amount = _explicit_bond(block_lines)
            release_date = published_release(block_lines)
            status, status_source = status_fields(release_date)

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
                    Status=status,
                    Release_Date=release_date,
                    Facility=FACILITY,
                    Agency="Newberry County Sheriff",
                    Detail_URL=pdf_url,
                    LastCheckedMode="INITIAL",
                    extra_data={"status_source": status_source},
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
            bond_amount = _explicit_bond(context.splitlines())
            release_date = published_release(context.splitlines())
            status, status_source = status_fields(release_date)

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
                    Status=status,
                    Release_Date=release_date,
                    Facility=FACILITY,
                    Agency="Newberry County Sheriff",
                    Detail_URL=pdf_url,
                    LastCheckedMode="INITIAL",
                    extra_data={"status_source": status_source},
                )
            )
        return records

    @staticmethod
    def _clean_charge(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip(" -|")
        if len(cleaned) < 3:
            return ""
        return cleaned
