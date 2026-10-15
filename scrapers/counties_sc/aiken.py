"""
Aiken County (SC) Arrest Scraper.

Public Sheriff page embeds:
  https://lookups.aikencountysc.gov/DTNSearch/dtnSchInmSchPublicFlex.php

Contract (2026-09-24 Mac smoke):
- Broad list via last-name letter walk (A–Z) POST to DtnSchInmDspPublic_newFlex.php
- Source key: Inmate ID# / qSO_NO on detail links (numeric; never AIK_ hashes)
- List fields: last, first, arrest date, age, sex, race
- Detail (optional): charges, bond, arrest agency/status

Honest access (2026-10-15): plain HTTPS through ``scrapers.plain_http``
(honest User-Agent, certificate verified). No curl_cffi, Chrome disguise,
stealth, proxy, impersonation, or ``verify=False`` fallback. Blocks raise
``egress_block:`` / ``connection_reset:`` / ``tls_cert_invalid:``.

Bond is published-only (``""`` when unknown; a published 0 is kept).
Status is never invented as "In Custody" (``""`` unless the source publishes it).
"""
from __future__ import annotations

import logging
import re
import string
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers import plain_http
from scrapers.base_scraper import BaseScraper
from scrapers.published_bond import charge_bond_amount
from scrapers.scraper_resilience import EgressBlocked

logger = logging.getLogger(__name__)

PORTAL_URL = "https://www.aikencountysheriff.net/185/Inmate-Search"
SEARCH_URL = "https://lookups.aikencountysc.gov/DTNSearch/dtnSchInmSchPublicFlex.php"
RESULTS_URL = "https://lookups.aikencountysc.gov/DTNSearch/DtnSchInmDspPublic_newFlex.php"
DETAIL_BASE = "https://lookups.aikencountysc.gov/DTNSearch/"

MAX_DETAIL_FETCHES = 400
DETAIL_DELAY_S = 0.15


def published_status(detail: dict, list_row: dict | None = None) -> str:
    """Custody status only as the source published it; unknown is ""."""
    for src in (detail, list_row or {}):
        if not isinstance(src, dict):
            continue
        for key in ("status", "Status", "custody_status", "custodyStatus"):
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def published_bond(detail: dict) -> str:
    """Detail bond text -> amount string; missing/blank/NO BOND/text -> ""."""
    if not isinstance(detail, dict) or "bond" not in detail:
        return ""
    raw = detail.get("bond")
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return ""
    amt = charge_bond_amount(raw if not isinstance(raw, (int, float)) else repr(raw))
    if amt is None:
        return ""
    return str(int(amt)) if float(amt).is_integer() else f"{amt:.2f}"


class AikenScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "lookups.aikencountysc.gov DTNSearch letter-walk POST; Inmate ID# / "
        "qSO_NO source key; arrest date on list; optional detail charges/bond."
    )

    @property
    def county(self) -> str:
        return "Aiken"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = plain_http.make_session({
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        })
        # Warm the search form; blocks / TLS failures raise (never swallowed).
        plain_http.request(session, "GET", SEARCH_URL, county="Aiken", timeout=30)

        by_id: Dict[str, dict] = {}
        for letter in string.ascii_uppercase:
            html = self._post_letter(session, letter)
            if not html:
                continue
            for row in self._parse_results(html):
                so = row["inmate_id"]
                if so and so not in by_id:
                    by_id[so] = row
            time.sleep(0.05)

        if not by_id:
            logger.warning("Aiken: letter walk returned 0 Inmate ID# rows")
            return []

        detail_budget = MAX_DETAIL_FETCHES
        records: List[ArrestRecord] = []
        for so, row in by_id.items():
            detail: Dict[str, str] = {}
            if detail_budget > 0 and row.get("detail_url"):
                try:
                    detail = self._fetch_detail(session, row["detail_url"])
                    detail_budget -= 1
                    time.sleep(DETAIL_DELAY_S)
                except EgressBlocked:
                    raise
                except Exception as e:
                    logger.debug("Aiken detail fail %s: %s", so, type(e).__name__)
            rec = self._to_record(row, detail)
            if rec:
                records.append(rec)

        logger.info(
            "Aiken: %d records (unique Inmate ID#) in %.1fs",
            len(records),
            time.time() - start,
        )
        return records

    def _post_letter(self, session, letter: str) -> str:
        data = {
            "LNAME": letter,
            "FNAME": "",
            "InSex": "All",
            "InRace": "All",
            "Search": "Search",
        }
        resp = plain_http.request(
            session,
            "POST",
            RESULTS_URL,
            county="Aiken",
            data=data,
            timeout=45,
            headers={"Referer": SEARCH_URL},
        )
        if resp.status_code != 200:
            return ""
        return resp.text or ""

    def _parse_results(self, html: str) -> List[dict]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[dict] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = re.search(r"qSO_NO=(\d+)", href)
            if not m:
                continue
            so = m.group(1)
            tr = a.find_parent("tr")
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")] if tr else []
            last = re.sub(r"^\W+", "", a.get_text(" ", strip=True)).strip()
            first = cells[1] if len(cells) > 1 else ""
            arrest = cells[2] if len(cells) > 2 else ""
            age = cells[3] if len(cells) > 3 else ""
            sex = (cells[4][:1].upper() if len(cells) > 4 and cells[4] else "")
            race = cells[5] if len(cells) > 5 else ""
            if not last:
                continue
            full = f"{last}, {first}".strip(", ")
            out.append(
                {
                    "inmate_id": so,
                    "last": last,
                    "first": first,
                    "full": full,
                    "arrest": arrest,
                    "age": age,
                    "sex": sex,
                    "race": race,
                    "detail_url": urljoin(DETAIL_BASE, href),
                }
            )
        return out

    def _fetch_detail(self, session, url: str) -> Dict[str, str]:
        resp = plain_http.request(
            session,
            "GET",
            url,
            county="Aiken",
            timeout=30,
            headers={"Referer": RESULTS_URL},
        )
        if resp.status_code != 200:
            return {}
        soup = BeautifulSoup(resp.text or "", "html.parser")
        text = soup.get_text("\n", strip=True)
        info: Dict[str, str] = {}
        m = re.search(r"Inmate ID#\s*:?\s*(\d+)", text, re.I)
        if m:
            info["inmate_id"] = m.group(1)
        charges: List[str] = []
        for line in text.splitlines():
            if re.match(r"^(Offense|Charge)\s*:", line, re.I):
                val = line.split(":", 1)[-1].strip()
                if val and val.upper() not in {"", "N/A", "NONE"}:
                    charges.append(val)
            sm = re.match(r"^Status\s*:\s*(.+)$", line, re.I)
            if sm and "status" not in info:
                info["status"] = sm.group(1).strip()
        if charges:
            info["charges"] = " | ".join(charges[:12])
        bm = re.search(r"Bond\s*:?\s*(\$?[\d,]+(?:\.\d+)?|NO\s*BOND|N/?A|NONE|HOLD)", text, re.I)
        if bm:
            info["bond"] = bm.group(1).strip()
        am = re.search(
            r"Arrest Date\s*:?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})",
            text,
            re.I,
        )
        if am:
            info["arrest"] = am.group(1)
        return info

    def _to_record(self, row: dict, detail: Dict[str, str]) -> Optional[ArrestRecord]:
        booking = (detail.get("inmate_id") or row.get("inmate_id") or "").strip()
        if not booking or not booking.isdigit():
            return None
        first = row.get("first") or ""
        last = row.get("last") or ""
        full = row.get("full") or f"{last}, {first}".strip(", ")
        arrest = detail.get("arrest") or row.get("arrest") or ""
        charges = detail.get("charges") or ""
        return ArrestRecord(
            County=self.county,
            State="SC",
            Full_Name=full,
            First_Name=first,
            Last_Name=last,
            Booking_Number=booking,
            Arrest_Date=arrest,
            Booking_Date=arrest,
            Age_At_Arrest=str(row.get("age") or ""),
            Sex=row.get("sex") or "",
            Race=row.get("race") or "",
            Charges=charges,
            Bond_Amount=published_bond(detail),
            Status=published_status(detail, row),
            Detail_URL=row.get("detail_url") or PORTAL_URL,
            Facility="Aiken County Detention Center",
        )
