"""
Denton County (TX) Arrest Scraper — Denton Police Department Athena JailView.

Portal: https://athena.dentonpolice.com/JailView/  ("DENTON POLICE DEPARTMENT |
CITY JAIL CUSTODY REPORT")
API:    POST JailView.aspx/GetInmates  (body ``{}``; the page's own jQuery call)
        → ``{"d": "<JSON array string>"}`` with ``bookno``, ``bookhandle``,
        ``datetimebooked``, ``name``, ``charges``, ``outstandingbonds``,
        ``detainers``, ``amount`` (+ an inline base64 mugshot, not stored).

Scope: this is the **City of Denton jail** (small, short-stay population), not
the Denton County Sheriff's jail. The county jail's public lookup is Tyler
Odyssey PublicAccess "Jail Records" (justice1.dentoncounty.gov), which requires
a last **and** first name per search — no broad public listing — so county-jail
coverage stays out of scope (documented in
docs/recon/GAP_QUEUE_NC_TX_SC_2026-09-25.md).

Key: the source ``bookno`` (8 digits, ``YYNNNNNN``) verbatim. The previous
implementation prefixed it (``DEN_…``) and went through the stealth request
stack; this one uses plain ``requests`` and never alters or synthesizes keys.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Optional, Tuple

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.scraper_resilience import ParseDriftError

logger = logging.getLogger(__name__)

PORTAL_URL = "https://athena.dentonpolice.com/JailView/"
JAILVIEW_URL = "https://athena.dentonpolice.com/JailView/JailView.aspx/GetInmates"
BOOKNO_RE = re.compile(r"^\d{6,10}$")


def decode_inmates(payload: dict) -> List[dict]:
    """Decode the ASP.NET WebMethod ``{"d": "<json>"}`` envelope."""
    if not isinstance(payload, dict) or "d" not in payload:
        raise ParseDriftError("Denton: GetInmates response has no 'd' envelope")
    raw = payload.get("d")
    if raw in ("", None):
        return []
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, list):
        raise ParseDriftError("Denton: GetInmates 'd' is not a list")
    return data


def _money(raw: str) -> float:
    cleaned = re.sub(r"[^0-9.]", "", raw or "")
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def _split_name(name: str) -> Tuple[str, str, str]:
    name = re.sub(r"\s+", " ", (name or "").replace("\xa0", " ")).strip()
    if "," in name:
        last, rest = name.split(",", 1)
        parts = rest.split()
        return last.strip(), (parts[0] if parts else ""), " ".join(parts[1:])
    parts = name.split()
    if len(parts) >= 2:
        return parts[-1], parts[0], " ".join(parts[1:-1])
    return name, "", ""


class DentonScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "Denton PD Athena JailView GetInmates (plain HTTPS, city jail custody "
        "report); source bookno (8 digits)."
    )

    @property
    def county(self) -> str:
        return "Denton"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        })
        landing = session.get(PORTAL_URL, timeout=30)
        landing.raise_for_status()
        if "GetInmates" not in landing.text and "getInmates" not in landing.text:
            raise ParseDriftError("Denton: JailView page no longer calls GetInmates")
        resp = session.post(
            JAILVIEW_URL,
            data="{}",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": PORTAL_URL,
            },
            timeout=45,
        )
        resp.raise_for_status()
        inmates = decode_inmates(resp.json())

        records: List[ArrestRecord] = []
        seen: set = set()
        for inmate in inmates:
            rec = self.build_record(inmate)
            if rec is None or rec.Booking_Number in seen:
                continue
            seen.add(rec.Booking_Number)
            records.append(rec)
        if inmates and not records:
            raise ParseDriftError("Denton: inmates returned but none carried a source bookno")
        logger.info("Denton (TX): %d records in %.1fs", len(records), time.time() - start)
        return records

    def build_record(self, inmate: dict) -> Optional[ArrestRecord]:
        book_no = str(inmate.get("bookno") or "").strip()
        name = str(inmate.get("name") or "").strip()
        if not BOOKNO_RE.match(book_no) or len(name) < 2:
            return None
        charges = str(inmate.get("charges") or "").strip()
        detainers = str(inmate.get("detainers") or "").strip()
        if detainers:
            charges = f"{charges}; DETAINER: {detainers}" if charges else f"DETAINER: {detainers}"
        bond = _money(str(inmate.get("amount") or "")) or _money(str(inmate.get("outstandingbonds") or ""))
        booked = str(inmate.get("datetimebooked") or "").strip()
        bdate, _, btime = booked.partition(" ")
        last, first, middle = _split_name(name)
        return ArrestRecord(
            County=self.county,
            State=self.state,
            Booking_Number=book_no,
            Person_ID=str(inmate.get("bookhandle") or "").strip(),
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Charges=charges or "Unknown",
            Bond_Amount=f"{bond:.2f}" if bond else "0",
            Status="In Custody",
            Facility="Denton City Jail",
            Agency="Denton Police Department",
            Booking_Date=bdate,
            Booking_Time=btime.strip(),
            Arrest_Date=bdate,
            Detail_URL=PORTAL_URL,
        )
