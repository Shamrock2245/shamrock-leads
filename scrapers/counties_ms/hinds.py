"""
Hinds County (MS) Arrest Scraper — Jackson, MS metro (state capital).

Portal: https://www.co.hinds.ms.us/pgs/apps/inmate/inmate_list.asp
Platform: Classic ASP with GET pagination (ScrollAction=Page N, ~48 pages).
Detail: inmate_detail.asp?ID=<pin> — charges, address, arresting agency.

Verified 2026-07-20: plain requests works from datacenter IPs (no WAF).

Dedup key: Pin Number (Booking_Number) + County.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE = "https://www.co.hinds.ms.us/pgs/apps/inmate/"
LIST_URL = BASE + "inmate_list.asp"

MAX_PAGES = 60          # portal shows ~48; cap defensively
MAX_DETAILS = 40        # detail fetches per run (newest arrests first priority)
REQUEST_PAUSE_S = 0.35

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class HindsScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Hinds"

    @property
    def state(self) -> str:
        return "MS"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)

        records: List[ArrestRecord] = []
        seen_ids: set = set()

        for page in range(1, MAX_PAGES + 1):
            html = self._fetch_list_page(session, page)
            if not html:
                break
            page_rows = self._parse_list(html)
            if not page_rows:
                break
            new = 0
            for pin_id, name, dob, sex, race, arrest_date in page_rows:
                if pin_id in seen_ids:
                    continue
                seen_ids.add(pin_id)
                first, last = self._split_name(name)
                records.append(
                    ArrestRecord(
                        County=self.county,
                        State="MS",
                        Full_Name=name.title(),
                        First_Name=first,
                        Last_Name=last,
                        DOB=dob,
                        Sex=sex,
                        Race=race,
                        Booking_Number=pin_id,
                        Arrest_Date=arrest_date,
                        Booking_Date=arrest_date,
                        Status="In Custody",
                        Facility="Hinds County Detention Center",
                        Agency="Hinds County Sheriff's Office",
                        Detail_URL=f"{BASE}inmate_detail.asp?ID={pin_id}",
                    )
                )
                new += 1
            if new == 0:
                break  # pagination looped back — stop
            time.sleep(REQUEST_PAUSE_S)

        # Enrich the most recent arrests with charges from detail pages
        recent = sorted(
            records,
            key=lambda r: r.Arrest_Date or "",
            reverse=True,
        )[:MAX_DETAILS]
        for rec in recent:
            try:
                self._enrich_from_detail(session, rec)
                time.sleep(REQUEST_PAUSE_S)
            except Exception as exc:
                logger.debug(f"Hinds: detail enrich failed for {rec.Booking_Number}: {exc}")

        logger.info(
            f"✅ Hinds MS: {len(records)} inmates "
            f"({len(recent)} enriched) in {time.time() - start:.1f}s"
        )
        return records

    # ── List page ───────────────────────────────────────────────────────────

    def _fetch_list_page(self, session: requests.Session, page: int) -> Optional[str]:
        params = {
            "name_sch": "",
            "SS1": "1",
            "search_by_city": "",
            "search_by": "",
        }
        if page > 1:
            params["ScrollAction"] = f"Page {page}"
        try:
            resp = session.get(LIST_URL, params=params, timeout=20)
            if resp.status_code != 200:
                logger.warning(f"Hinds: list page {page} HTTP {resp.status_code}")
                return None
            return resp.text
        except Exception as exc:
            logger.error(f"Hinds: list page {page} failed: {exc}")
            return None

    def _parse_list(self, html: str) -> List[Tuple[str, str, str, str, str, str]]:
        """Return [(pin_id, name, dob, sex, race, arrest_date)] from a list page."""
        soup = BeautifulSoup(html, "html.parser")
        out: List[Tuple[str, str, str, str, str, str]] = []
        for link in soup.find_all("a", href=re.compile(r"inmate_detail\.asp\?ID=")):
            try:
                m = re.search(r"ID=(\d+)", link["href"])
                if not m:
                    continue
                pin_id = m.group(1)
                name = link.get_text(" ", strip=True)
                if not name or len(name) < 3:
                    continue
                # Row cells: Name | DOB | Height | Weight | Sex | Race | Arrest Date
                row = link.find_parent("tr")
                dob = sex = race = arrest_date = ""
                if row:
                    cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                    if len(cells) >= 7:
                        dob = cells[1]
                        sex = cells[4]
                        race = cells[5]
                        arrest_date = cells[6]
                out.append((pin_id, name, dob, sex, race, arrest_date))
            except Exception as exc:
                logger.debug(f"Hinds: skipped malformed row: {exc}")
                continue
        return out

    # ── Detail page ─────────────────────────────────────────────────────────

    def _enrich_from_detail(self, session: requests.Session, rec: ArrestRecord) -> None:
        """Fetch inmate_detail.asp and fill Charges / Address / Agency."""
        resp = session.get(rec.Detail_URL, timeout=20)
        if resp.status_code != 200:
            return
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(" ", strip=True)

        # Charges: "Charge 1 <desc> () Felony / Misdemeanor ..." repeated.
        # Non-greedy up to the nearest 'Felony / Misdemeanor' delimiter; strip the
        # optional empty parenthetical the portal appends.
        charges = re.findall(
            r"Charge\s+\d+\s+(.+?)\s*(?:\(\s*\))?\s*Felony / Misdemeanor",
            text,
        )
        charges = [
            re.sub(r"\s{2,}", " ", c).strip().rstrip("()").strip()
            for c in charges
            if c.strip() and "Warrant #" not in c
        ]
        if charges:
            rec.Charges = " | ".join(dict.fromkeys(charges))  # de-dup, keep order

        # Address
        m = re.search(r"Address\s+(.+?)\s+Date of Birth", text)
        if m:
            rec.Address = re.sub(r"\s{2,}", " ", m.group(1)).strip()

        # Arresting agency
        m = re.search(r"Arresting Agency\s+([A-Z0-9 ]+?)\s+Arrest Date", text)
        if m:
            rec.Agency = m.group(1).strip()

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        if "," in name:
            last, rest = name.split(",", 1)
            first = rest.strip().split()[0] if rest.strip() else ""
            return first.title(), last.strip().title()
        bits = name.split()
        if len(bits) >= 2:
            return bits[0].title(), bits[-1].title()
        return name.title(), ""
