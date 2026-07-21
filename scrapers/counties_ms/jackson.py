"""
Jackson County (MS) Arrest Scraper.

Portal: https://www.co.jackson.ms.us/324/Inmate-Lookup
API: https://services.co.jackson.ms.us/inmatedocket/_inmateList.php
     ?Function=list&Page=1&Order=BookDesc
Platform: Custom PHP + jQuery (CivicPlus host) + Cloudflare on services.*

Uses APE StealthSession (curl_cffi TLS fingerprint + residential proxy chain)
to bypass the managed challenge. Fail-closed on empty/blocked responses.
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

LIST_URL = (
    "https://services.co.jackson.ms.us/inmatedocket/"
    "_inmateList.php?Function=list&Page={page}&Order=BookDesc"
)
DETAIL_BASE = "https://services.co.jackson.ms.us/inmatedocket/"
# Warm-up page that often sets CF cookies before the API subdomain call
LANDING_URL = "https://www.co.jackson.ms.us/324/Inmate-Lookup"

MAX_PAGES = 40
REQUEST_PAUSE_S = 0.4

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": LANDING_URL,
}


class JacksonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Jackson"

    @property
    def state(self) -> str:
        return "MS"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen: set = set()

        try:
            from scrapers.proxy_engine import create_stealth_session

            with create_stealth_session(
                sticky_session_id="ms_jackson",
                prefer_residential=True,
                allow_direct=True,
            ) as session:
                # Warm CF cookies from the public CivicPlus host when possible
                try:
                    session.get(LANDING_URL, headers=HEADERS, timeout=25)
                except Exception as exc:
                    logger.debug("Jackson MS: landing warm-up failed: %s", exc)

                for page in range(1, MAX_PAGES + 1):
                    url = LIST_URL.format(page=page)
                    try:
                        resp = session.get(url, headers=HEADERS, timeout=30)
                    except Exception as exc:
                        logger.warning("Jackson MS: page %d failed: %s", page, exc)
                        break

                    status = getattr(resp, "status_code", 0)
                    body = getattr(resp, "text", "") or ""

                    if status != 200:
                        logger.warning(
                            "Jackson MS: page %d HTTP %s", page, status
                        )
                        break

                    # Cloudflare challenge page detection (fail closed, no crash)
                    if self._looks_like_cloudflare(body):
                        logger.warning(
                            "Jackson MS: Cloudflare challenge still present "
                            "(need healthy residential exit)"
                        )
                        break

                    page_rows = self._parse_list(body)
                    if not page_rows:
                        if page == 1:
                            logger.warning(
                                "Jackson MS: empty list page 1 — selector change?"
                            )
                        break

                    new = 0
                    for row in page_rows:
                        booking, name, dob, sex, race, book_date, charges, detail = row
                        if booking in seen:
                            continue
                        seen.add(booking)
                        first, last = self._split_name(name)
                        records.append(
                            ArrestRecord(
                                County=self.county,
                                State="MS",
                                Full_Name=name.title() if name.isupper() else name,
                                First_Name=first,
                                Last_Name=last,
                                DOB=dob,
                                Sex=sex,
                                Race=race,
                                Booking_Number=booking,
                                Arrest_Date=book_date,
                                Booking_Date=book_date,
                                Charges=charges or "Unknown",
                                Status="In Custody",
                                Facility="Jackson County Adult Detention Center",
                                Agency="Jackson County Sheriff's Office",
                                Detail_URL=detail or LIST_URL.format(page=1),
                            )
                        )
                        new += 1

                    if new == 0:
                        break
                    time.sleep(REQUEST_PAUSE_S)
        except Exception as exc:
            logger.error("Jackson MS scrape failed: %s", exc)
            return []

        logger.info(
            "✅ Jackson (MS): %d records in %.1fs",
            len(records),
            time.time() - start,
        )
        return records

    @staticmethod
    def _looks_like_cloudflare(html: str) -> bool:
        low = (html or "").lower()
        markers = (
            "cf-browser-verification",
            "cf-challenge",
            "just a moment",
            "attention required",
            "cdn-cgi/challenge",
            "checking your browser",
        )
        return any(m in low for m in markers)

    def _parse_list(
        self, html: str
    ) -> List[Tuple[str, str, str, str, str, str, str, str]]:
        """
        Return list of
        (booking, name, dob, sex, race, book_date, charges, detail_url).
        """
        soup = BeautifulSoup(html, "html.parser")
        out: List[Tuple[str, str, str, str, str, str, str, str]] = []

        # Strategy 1: tables with inmate rows + optional detail links
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [
                th.get_text(" ", strip=True).lower()
                for th in rows[0].find_all(["th", "td"])
            ]
            joined = " ".join(headers)
            if not any(
                k in joined for k in ("name", "inmate", "booking", "book")
            ) and len(rows) < 4:
                continue

            name_i = self._col(headers, "name", "inmate")
            book_i = self._col(headers, "booking", "book #", "jacket", "id")
            dob_i = self._col(headers, "dob", "birth")
            sex_i = self._col(headers, "sex", "gender")
            race_i = self._col(headers, "race")
            date_i = self._col(headers, "book", "arrest", "date")
            charge_i = self._col(headers, "charge", "offense")

            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                name = cells[name_i] if name_i is not None and name_i < len(cells) else cells[0]
                if not name or len(name) < 3:
                    continue
                if name.lower() in ("name", "inmate name"):
                    continue

                booking = (
                    cells[book_i]
                    if book_i is not None and book_i < len(cells)
                    else ""
                )
                link = row.find("a", href=True)
                detail = ""
                if link:
                    detail = urljoin(DETAIL_BASE, link["href"])
                    if not booking:
                        m = re.search(
                            r"(?:id|booking|book)=([A-Za-z0-9_-]+)",
                            link["href"],
                            re.I,
                        )
                        if m:
                            booking = m.group(1)
                if not booking:
                    booking = "JAX_" + hashlib.sha1(
                        name.encode("utf-8", errors="ignore")
                    ).hexdigest()[:12]

                dob = cells[dob_i] if dob_i is not None and dob_i < len(cells) else ""
                sex = cells[sex_i] if sex_i is not None and sex_i < len(cells) else ""
                race = cells[race_i] if race_i is not None and race_i < len(cells) else ""
                book_date = (
                    cells[date_i] if date_i is not None and date_i < len(cells) else ""
                )
                charges = (
                    cells[charge_i]
                    if charge_i is not None and charge_i < len(cells)
                    else ""
                )
                out.append(
                    (booking, name, dob, sex, race, book_date, charges, detail)
                )

            if out:
                return out

        # Strategy 2: card / div list of inmates
        for link in soup.find_all("a", href=re.compile(r"inmate|detail|book", re.I)):
            name = link.get_text(" ", strip=True)
            if not name or len(name) < 3:
                continue
            href = link.get("href", "")
            m = re.search(r"(?:id|booking|book)=([A-Za-z0-9_-]+)", href, re.I)
            booking = m.group(1) if m else (
                "JAX_" + hashlib.sha1(name.encode()).hexdigest()[:12]
            )
            detail = urljoin(DETAIL_BASE, href)
            out.append((booking, name, "", "", "", "", "", detail))

        return out

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
