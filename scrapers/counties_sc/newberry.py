"""
Newberry County (SC) Arrest Scraper.
Portal page is civic CMS; follow iframe/links to roster if present.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
PORTAL_URL = (
    "https://www.newberrycounty.gov/sheriffs-office/"
    "newberry-county-detention-center/inmate-search"
)


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
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        records: List[ArrestRecord] = []
        try:
            resp = session.get(PORTAL_URL, timeout=25)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            targets = [PORTAL_URL]
            for ifr in soup.find_all("iframe", src=True):
                targets.append(urljoin(PORTAL_URL, ifr["src"]))
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if any(k in href.lower() for k in ("inmate", "roster", "jail", "booking", "zuercher", "southern")):
                    targets.append(urljoin(PORTAL_URL, href))
            seen = set()
            for url in targets:
                if url in seen:
                    continue
                seen.add(url)
                try:
                    r = session.get(url, timeout=20, verify=False)
                    if r.status_code != 200:
                        continue
                    records.extend(self._parse_tables(r.text, url))
                except Exception:
                    continue
                if records:
                    break
            if not records:
                logger.warning("Newberry: CMS page has no scrapeable inmate table")
        except Exception as e:
            logger.error(f"Newberry scrape failed: {e}")
        logger.info(f"Newberry: {len(records)} records in {time.time()-start:.1f}s")
        return records

    def _parse_tables(self, html: str, source: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[ArrestRecord] = []
        for table in soup.find_all("table"):
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) < 2:
                    continue
                name = cells[0]
                if not name or len(name) < 2:
                    continue
                out.append(ArrestRecord(
                    County=self.county, State="SC", Full_Name=name,
                    Booking_Number=str(cells[1] if len(cells) > 1 else f"NEW_{abs(hash(name))%100000}"),
                    Charges=cells[2] if len(cells) > 2 else "Unknown",
                    Bond_Amount=re.sub(r"[^\d.]", "", cells[3] if len(cells) > 3 else "0") or "0",
                    Status="In Custody", Detail_URL=source,
                ))
            if out:
                break
        return out
