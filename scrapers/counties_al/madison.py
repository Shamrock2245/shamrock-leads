"""
Madison County (AL) Arrest Scraper — Huntsville metro.

Portal: https://www.madisoncountyal.gov/departments/sheriff/inmate-information
Platform: CivicPlus (often iframe / external vendor redirect)

Uses APE StealthSession to reach the CivicPlus page, then follows common
inmate-roster iframes / links. Fail-closed if the live endpoint is still
unknown or blocked.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

LANDING_URL = (
    "https://www.madisoncountyal.gov/departments/sheriff/inmate-information"
)
# Known/possible roster endpoints discovered during recon (may 403 without residential).
# TODO: verify endpoint URL after first successful residential probe
CANDIDATE_ROSTER_URLS = [
    # TODO: verify endpoint URL — CivicPlus often embeds an external JMS
    "https://www.madisoncountyal.gov/departments/sheriff/inmate-information",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class MadisonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Madison"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []

        try:
            from scrapers.proxy_engine import create_stealth_session

            with create_stealth_session(
                sticky_session_id="al_madison",
                prefer_residential=True,
                allow_direct=True,
            ) as session:
                # 1) Landing page — harvest iframes / roster links
                try:
                    landing = session.get(LANDING_URL, headers=HEADERS, timeout=30)
                except Exception as exc:
                    logger.warning("Madison AL: landing fetch failed: %s", exc)
                    landing = None

                targets = list(CANDIDATE_ROSTER_URLS)
                if landing is not None and getattr(landing, "status_code", 0) == 200:
                    targets = self._discover_roster_urls(landing.text, LANDING_URL) + targets

                seen_urls: set = set()
                for url in targets:
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    try:
                        resp = session.get(url, headers=HEADERS, timeout=35)
                        if resp.status_code != 200:
                            logger.warning(
                                "Madison AL: %s → HTTP %s",
                                url[:80],
                                resp.status_code,
                            )
                            continue
                        batch = self._parse_roster_html(resp.text, url)
                        if batch:
                            records.extend(batch)
                            break
                    except Exception as exc:
                        logger.debug("Madison AL: target %s failed: %s", url[:60], exc)
                        continue
        except Exception as exc:
            logger.error("Madison AL scrape failed: %s", exc)
            return []

        # Dedup on booking
        deduped: List[ArrestRecord] = []
        seen_ids: set = set()
        for rec in records:
            if rec.Booking_Number in seen_ids:
                continue
            seen_ids.add(rec.Booking_Number)
            deduped.append(rec)

        if not deduped:
            logger.warning(
                "Madison AL: no roster rows parsed — endpoint may still be "
                "WAF-blocked or behind an unlisted vendor iframe. "
                "TODO: verify endpoint URL with residential exit IP."
            )

        logger.info(
            "✅ Madison (AL): %d records in %.1fs",
            len(deduped),
            time.time() - start,
        )
        return deduped

    def _discover_roster_urls(self, html: str, base: str) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        found: List[str] = []
        for iframe in soup.find_all("iframe", src=True):
            src = iframe["src"].strip()
            if not src:
                continue
            full = urljoin(base, src)
            if full not in found:
                found.append(full)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = (a.get_text(" ", strip=True) or "").lower()
            hl = href.lower()
            if any(
                k in hl or k in text
                for k in (
                    "inmate",
                    "roster",
                    "jail",
                    "whos-in",
                    "currentinmate",
                    "booking",
                )
            ):
                full = urljoin(base, href)
                if full not in found and full.startswith("http"):
                    found.append(full)
        return found

    def _parse_roster_html(self, html: str, source_url: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []
        seen: set = set()

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [
                th.get_text(" ", strip=True).lower()
                for th in rows[0].find_all(["th", "td"])
            ]
            joined = " ".join(headers)
            if not any(k in joined for k in ("name", "inmate", "booking", "charge")):
                if len(rows) < 4:
                    continue

            name_i = self._col(headers, "name", "inmate")
            book_i = self._col(headers, "booking", "book", "id")
            charge_i = self._col(headers, "charge", "offense")
            bond_i = self._col(headers, "bond", "bail")
            date_i = self._col(headers, "date", "booked", "arrest")

            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                name = cells[name_i] if name_i is not None and name_i < len(cells) else cells[0]
                if not name or len(name) < 3:
                    continue
                if name.lower() in ("name", "inmate", "inmate name"):
                    continue
                booking = (
                    cells[book_i]
                    if book_i is not None and book_i < len(cells)
                    else ""
                )
                if not booking:
                    booking = "MAD_" + hashlib.sha1(
                        name.encode("utf-8", errors="ignore")
                    ).hexdigest()[:12]
                if booking in seen:
                    continue
                seen.add(booking)

                charges = (
                    cells[charge_i]
                    if charge_i is not None and charge_i < len(cells)
                    else "Unknown"
                ) or "Unknown"
                bond = "0"
                if bond_i is not None and bond_i < len(cells):
                    bond = re.sub(r"[^\d.]", "", cells[bond_i]) or "0"
                arrest_date = (
                    cells[date_i]
                    if date_i is not None and date_i < len(cells)
                    else ""
                )
                first, last = self._split_name(name)
                records.append(
                    ArrestRecord(
                        County=self.county,
                        State="AL",
                        Full_Name=name.title() if name.isupper() else name,
                        First_Name=first,
                        Last_Name=last,
                        Booking_Number=str(booking),
                        Arrest_Date=arrest_date,
                        Booking_Date=arrest_date,
                        Charges=charges,
                        Bond_Amount=bond,
                        Status="In Custody",
                        Facility="Madison County Jail",
                        Agency="Madison County Sheriff's Office",
                        Detail_URL=source_url,
                    )
                )
            if records:
                break
        return records

    @staticmethod
    def _col(headers: List[str], *keys: str) -> Optional[int]:
        for i, h in enumerate(headers):
            for k in keys:
                if k in h:
                    return i
        return None

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
