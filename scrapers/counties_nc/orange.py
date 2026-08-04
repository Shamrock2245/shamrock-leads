"""
Orange County (NC) Arrest Scraper — Daily In-Custody PDF on OCS website.

Portal: https://www.ocsonc.com/detention/current-detainees
PDF:    Discover ``/_files/ugd/*.pdf`` links on the page; pick newest by
        embedded report timestamp (same XFRX layout as Caldwell).
"""
from __future__ import annotations

import io
import logging
import re
import time
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://www.ocsonc.com/detention/current-detainees"
# Known fallbacks if page scrape fails (newest first preference still applied)
_FALLBACK_PDFS = [
    "https://www.ocsonc.com/_files/ugd/56522e_872f92177c7c4121ad2f5ee01d8ba2b4.pdf",
    "https://www.ocsonc.com/_files/ugd/56522e_5f46e4680df643428204723b611821d2.pdf",
]

_NAME_LINE = re.compile(
    r"^([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)*),\s+"
    r"([A-Za-z][A-Za-z'\-\.]+(?:\s+[A-Za-z][A-Za-z'\-\.]+)*)"
)
_BOOKING_NUM = re.compile(r"\b(\d{5})([WBMUA])\b", re.I)
_BOND = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
_DOCKET = re.compile(r"(\d{2}[A-Z]{2}\d{4,}(?:-\d+)?)")
_REPORT_TS = re.compile(r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})")


class OrangeScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Orange"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        })

        pdf_url, content = self._fetch_best_pdf(session)
        if not content:
            logger.error("Orange: no PDF roster available")
            return []

        text = self._extract_text(content)
        if not text:
            logger.warning("Orange: empty PDF text from %s", pdf_url)
            return []

        records = self._parse_text(text, pdf_url)
        logger.info(
            "Orange: %d inmates from %s in %.1fs",
            len(records),
            pdf_url.split("/")[-1][:40],
            time.time() - start,
        )
        return records

    def _fetch_best_pdf(self, session: requests.Session) -> Tuple[Optional[str], Optional[bytes]]:
        urls = list(self._discover_pdf_urls(session))
        for u in _FALLBACK_PDFS:
            if u not in urls:
                urls.append(u)

        best_url, best_body, best_ts = None, None, None
        for url in urls:
            try:
                resp = session.get(url, timeout=45, verify=False)
                if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
                    continue
                ts = self._pdf_report_timestamp(resp.content)
                if best_ts is None or (ts and ts > best_ts) or (ts is None and best_body is None):
                    best_url, best_body, best_ts = url, resp.content, ts or best_ts
                    if ts is None and best_body is None:
                        best_body = resp.content
            except Exception as e:
                logger.debug("Orange PDF fetch %s: %s", url, e)
        if best_url:
            logger.info("Orange using PDF %s (report_ts=%s)", best_url, best_ts)
        return best_url, best_body

    def _discover_pdf_urls(self, session: requests.Session) -> List[str]:
        try:
            resp = session.get(PORTAL_URL, timeout=40, verify=False)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Orange portal GET failed: %s", e)
            return []
        found = set()
        for m in re.findall(
            r'https?://(?:www\.)?ocsonc\.com/_files/ugd/[a-zA-Z0-9_]+\.pdf',
            resp.text.replace("\\/", "/"),
        ):
            found.add(m)
        # relative
        for m in re.findall(r'/_files/ugd/[a-zA-Z0-9_]+\.pdf', resp.text.replace("\\/", "/")):
            found.add(urljoin("https://www.ocsonc.com", m))
        return sorted(found)

    def _pdf_report_timestamp(self, content: bytes) -> Optional[datetime]:
        text = self._extract_text(content, max_pages=1)
        m = _REPORT_TS.search(text or "")
        if not m:
            return None
        try:
            return datetime.strptime(m.group(1), "%m/%d/%Y %H:%M:%S")
        except ValueError:
            return None

    def _extract_text(self, content: bytes, max_pages: Optional[int] = None) -> str:
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            pages = reader.pages
            if max_pages:
                pages = pages[:max_pages]
            return "\n".join((p.extract_text() or "") for p in pages)
        except Exception as e:
            logger.debug("pypdf: %s", e)
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                pages = pdf.pages
                if max_pages:
                    pages = pages[:max_pages]
                return "\n".join((p.extract_text() or "") for p in pages)
        except Exception as e:
            logger.error("Orange PDF extract failed: %s", e)
            return ""

    def _parse_text(self, text: str, pdf_url: str) -> List[ArrestRecord]:
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if ln]
        inmates: List[dict] = []
        current = None
        skip = (
            "user:", "daily in custody", "orange county", "all facilities",
            "facility:", "days in", "confinement", "bk #",
        )
        for ln in lines:
            low = ln.lower()
            if any(low.startswith(p) for p in skip):
                continue
            m = _NAME_LINE.match(ln)
            if m:
                bm = _BOOKING_NUM.search(ln)
                if not bm:
                    continue
                last, first_rest, bk = m.group(1), m.group(2), bm.group(1)
                parts = first_rest.split()
                first = parts[0] if parts else ""
                middle = " ".join(parts[1:]) if len(parts) > 1 else ""
                # PDF sometimes glues next name onto middle — stop at race codes
                if middle:
                    mid_parts = []
                    for p in middle.split():
                        if re.fullmatch(r"[WBMUA]", p, re.I) or re.fullmatch(r"\d{5}[WBMUA]?", p, re.I):
                            break
                        mid_parts.append(p)
                    middle = " ".join(mid_parts)
                current = {
                    "name": f"{last}, {first} {middle}".strip(),
                    "last": last,
                    "first": first,
                    "middle": middle,
                    "booking": bk,
                    "charges": [],
                    "bond": 0.0,
                    "dockets": [],
                }
                inmates.append(current)
                rest = ln[bm.end():].strip(" /")
                if rest and len(rest) > 8:
                    self._add_charge_bits(current, rest)
                continue
            if current and ("/" in ln or "$" in ln or _DOCKET.search(ln)):
                self._add_charge_bits(current, ln)

        records = []
        for row in inmates:
            records.append(ArrestRecord(
                County=self.county,
                State="NC",
                Full_Name=row["name"],
                First_Name=row["first"],
                Middle_Name=row["middle"],
                Last_Name=row["last"],
                Booking_Number=str(row["booking"]),
                Case_Number=" | ".join(row["dockets"][:5]),
                Charges=" | ".join(row["charges"]) if row["charges"] else "Unknown",
                Bond_Amount=f"{row['bond']:.2f}" if row["bond"] else "0",
                Status="In Custody",
                Facility="Orange County Detention",
                Agency="Orange County Sheriff",
                Detail_URL=pdf_url or PORTAL_URL,
            ))
        return records

    @staticmethod
    def _add_charge_bits(current: dict, text: str) -> None:
        charge = text.split(" / ")[0].strip()
        if re.match(r"^[WBMUA]?\s*[FA]\s*\d", charge, re.I) or re.match(
            r"^(MA|FA|W|B)\s", charge, re.I
        ):
            charge = ""
        if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}", charge):
            charge = ""
        if charge and len(charge) > 4 and charge not in current["charges"]:
            if not charge.lower().startswith("http") and not charge.isdigit():
                charge = re.sub(r"^\d{4,5}[WBMUA]?\s*", "", charge, flags=re.I).strip()
                # drop glued second-person names
                if len(charge) > 4 and not re.match(r"^[A-Z][a-z]+,", charge):
                    current["charges"].append(charge[:120])
        for bm in _BOND.finditer(text):
            try:
                current["bond"] += float(bm.group(1).replace(",", ""))
            except ValueError:
                pass
        for dm in _DOCKET.finditer(text):
            d = dm.group(1)
            if d not in current["dockets"]:
                current["dockets"].append(d)
