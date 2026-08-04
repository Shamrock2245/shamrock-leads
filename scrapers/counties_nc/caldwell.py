"""
Caldwell County (NC) Arrest Scraper — Daily In-Custody PDF.

URL: https://www.caldwellcountync.org/DocumentCenter/View/1696/List-of-Current-Inmates-PDF

Published daily as a multi-page PDF. Parse with pypdf (preferred) or pdfplumber.
"""
from __future__ import annotations

import io
import logging
import re
import time
from typing import List, Optional, Tuple

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PDF_URL = (
    "https://www.caldwellcountync.org/DocumentCenter/View/1696/"
    "List-of-Current-Inmates-PDF"
)
PORTAL = "https://www.caldwellcountync.org/"

# Name line: "Last, First Middle … 73568 …"  (5-digit booking, not year 20xx)
_NAME_LINE = re.compile(
    r"^([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)*),\s+"
    r"([A-Za-z][A-Za-z'\-\.]+(?:\s+[A-Za-z][A-Za-z'\-\.]+)*)"
)
# PDF glues booking to race letter: "73568W FA" / "70431A MA"
_BOOKING_NUM = re.compile(r"\b(\d{5})([WBMUA])\b", re.I)

# Charge line with optional bond: "... / $2,000 / ACTI / SECU / ..."
_BOND = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
_DOCKET = re.compile(r"(\d{2}[A-Z]{2}\d{4,}-\d{3})")


class CaldwellScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Caldwell"

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
        try:
            resp = session.get(PDF_URL, timeout=60, verify=False)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Caldwell PDF GET failed: %s", e)
            return []

        text = self._extract_text(resp.content)
        if not text:
            logger.warning("Caldwell: empty PDF text")
            return []

        records = self._parse_text(text)
        logger.info(
            "Caldwell: %d inmates from PDF in %.1fs",
            len(records),
            time.time() - start,
        )
        return records

    def _extract_text(self, content: bytes) -> str:
        # Prefer pypdf
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception as e:
            logger.debug("pypdf failed: %s", e)
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                return "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception as e:
            logger.error("Caldwell PDF extract failed: %s", e)
            return ""

    def _parse_text(self, text: str) -> List[ArrestRecord]:
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if ln]

        inmates: List[dict] = []
        current: Optional[dict] = None
        skip_prefixes = (
            "user:", "daily in custody", "caldwell county", "all facilities",
            "facility:", "days in", "name ", "bk #", "booked date",
        )

        for ln in lines:
            low = ln.lower()
            if any(low.startswith(p) for p in skip_prefixes):
                continue
            if low in ("dc", "sup", "custody"):
                continue

            m = _NAME_LINE.match(ln)
            if m:
                last, first_rest = m.group(1), m.group(2)
                bm = _BOOKING_NUM.search(ln)
                if not bm:
                    continue  # name without booking (header fragment / wrap)
                bk = bm.group(1)
                parts = first_rest.split()
                first = parts[0] if parts else ""
                middle = " ".join(parts[1:]) if len(parts) > 1 else ""
                full = f"{last}, {first_rest}".strip()
                current = {
                    "name": full,
                    "last": last,
                    "first": first,
                    "middle": middle,
                    "booking": bk,
                    "charges": [],
                    "bond": 0.0,
                    "dockets": [],
                }
                inmates.append(current)
                # Remainder after booking may include a charge
                rest = ln[bm.end():].strip(" /")
                if rest and len(rest) > 8:
                    self._add_charge_bits(current, rest)
                continue

            if current and ("/" in ln or "$" in ln or _DOCKET.search(ln)):
                # charge continuation
                if not any(low.startswith(x) for x in ("page ", "report ")):
                    self._add_charge_bits(current, ln)

        records = []
        for row in inmates:
            charges = " | ".join(row["charges"]) if row["charges"] else "Unknown"
            records.append(ArrestRecord(
                County=self.county,
                State="NC",
                Full_Name=row["name"],
                First_Name=row["first"],
                Middle_Name=row["middle"],
                Last_Name=row["last"],
                Booking_Number=str(row["booking"]),
                Case_Number=" | ".join(row["dockets"][:5]),
                Charges=charges,
                Bond_Amount=f"{row['bond']:.2f}" if row["bond"] else "0",
                Status="In Custody",
                Facility="Caldwell County Detention",
                Agency="Caldwell County Sheriff",
                Detail_URL=PDF_URL,
            ))
        return records

    @staticmethod
    def _add_charge_bits(current: dict, text: str) -> None:
        # Charge description is usually before first " / "
        charge = text.split(" / ")[0].strip()
        # Drop leftover race/sex + jammed dates (e.g. "FA 507/30/2026 1526")
        if re.match(r"^[WBMUA]?\s*[FA]\s*\d", charge, re.I) or re.match(
            r"^(MA|FA|W|B)\s", charge, re.I
        ):
            charge = ""
        if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}", charge):
            charge = ""
        if charge and len(charge) > 4 and charge not in current["charges"]:
            if not charge.lower().startswith("http") and not charge.isdigit():
                # strip leading jammed booking fragments
                charge = re.sub(r"^\d{4,5}[WBMUA]?\s*", "", charge, flags=re.I).strip()
                if len(charge) > 4:
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
