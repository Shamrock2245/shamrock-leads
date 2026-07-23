"""
East Baton Rouge Parish (LA) Arrest Scraper — EBRSO Prison Inmate List.

Portal: https://www.ebrso.org/resources/prison-inmate-list/
Access: Disclaimer gate → redirect to inmate list
        (requires accepting disclaimer via session cookie)

The EBRSO portal is Cloudflare-protected. Requires StealthSession or
DrissionPage for reliable access. The inmate list is HTML-rendered with
name and DOB visible; detailed charge/bond info requires phone call
(225-308-3400) or VINE lookup.

Alternative data source: Louisiana VINE (vinelink.vineapps.com/state/LA)
which provides custody status and may include bond information.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import List, Optional

from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

DISCLAIMER_URL = "https://www.ebrso.org/resources/prison-inmate-list-disclaimer/"
INMATE_LIST_URL = "https://www.ebrso.org/resources/prison-inmate-list/"
PORTAL_URL = INMATE_LIST_URL

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.ebrso.org/",
}


class EastBatonRougeScraper(BaseScraper):
    """East Baton Rouge Parish (LA) — EBRSO Prison Inmate List."""

    @property
    def county(self) -> str:
        return "East Baton Rouge"

    @property
    def state(self) -> str:
        return "LA"

    @property
    def scraper_id(self) -> str:
        return "scraper_la_east_baton_rouge"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []

        # Primary: StealthSession (handles Cloudflare)
        html = self._fetch_with_stealth()

        # Fallback: DrissionPage browser
        if not html:
            html = self._fetch_with_browser()

        if not html:
            logger.error("East Baton Rouge (LA): all fetch methods failed")
            return []

        # Parse the inmate list
        soup = BeautifulSoup(html, "html.parser")
        records = self._parse_inmate_list(soup)

        elapsed = time.time() - start
        logger.info(f"✅ East Baton Rouge (LA): {len(records)} records in {elapsed:.1f}s")
        return records

    # ── Fetch Methods ────────────────────────────────────────────────────────

    def _fetch_with_stealth(self) -> Optional[str]:
        """Fetch using StealthSession with Cloudflare bypass."""
        try:
            from scrapers.proxy_engine import create_stealth_session

            with create_stealth_session(
                sticky_session_id="ebrso_ebr",
                prefer_residential=True,
                allow_direct=True,
            ) as session:
                # First hit the disclaimer page to get cookies
                resp1 = session.get(DISCLAIMER_URL, headers=HEADERS, timeout=30)
                if resp1.status_code != 200:
                    logger.debug(f"EBR disclaimer: HTTP {resp1.status_code}")

                time.sleep(1)

                # Then fetch the actual inmate list
                resp2 = session.get(INMATE_LIST_URL, headers=HEADERS, timeout=30)
                if resp2.status_code == 200 and len(resp2.text) > 3000:
                    return resp2.text
                logger.warning(
                    f"EBR stealth: HTTP {resp2.status_code}, len={len(resp2.text)}"
                )
                return None
        except Exception as e:
            logger.debug(f"EBR stealth failed: {e}")
            return None

    def _fetch_with_browser(self) -> Optional[str]:
        """Fallback: DrissionPage headless browser with stealth."""
        try:
            from DrissionPage import ChromiumPage

            co = self._get_browser_options()
            page = ChromiumPage(co)

            # Navigate to disclaimer first
            page.get(DISCLAIMER_URL)
            page.wait.doc_loaded()
            time.sleep(2)

            # Click "I AGREE" link/button
            try:
                agree_link = page.ele(
                    'xpath://a[contains(text(), "AGREE") or '
                    'contains(text(), "PROCEED")]'
                )
                if agree_link:
                    agree_link.click()
                    time.sleep(2)
            except Exception:
                # Direct navigation if agree button not found
                page.get(INMATE_LIST_URL)
                time.sleep(2)

            page.wait.doc_loaded()
            time.sleep(2)
            html = page.html

            try:
                page.quit()
            except Exception:
                pass

            if html and len(html) > 3000:
                return html
            return None
        except Exception as e:
            logger.debug(f"EBR browser fallback failed: {e}")
            return None

    # ── Parser ───────────────────────────────────────────────────────────────

    def _parse_inmate_list(self, soup: BeautifulSoup) -> List[ArrestRecord]:
        """
        Parse the EBRSO inmate list page.

        The page typically renders inmates in a table or list format with:
        - Full name (LAST, FIRST MIDDLE)
        - DOB
        - Booking number / jacket number
        - Some pages include charges
        """
        records: List[ArrestRecord] = []
        seen: set = set()

        # Strategy 1: Look for structured table
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            headers = [
                th.get_text(" ", strip=True).lower()
                for th in (rows[0].find_all(["th", "td"]))
            ]

            # Check if this looks like an inmate table
            if not any(
                kw in " ".join(headers)
                for kw in ("name", "inmate", "last", "offender")
            ):
                continue

            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue

                rec = self._parse_table_row(cells, headers)
                if rec and rec.Booking_Number not in seen:
                    seen.add(rec.Booking_Number)
                    records.append(rec)

            if records:
                break

        # Strategy 2: Parse text blocks (name + DOB pattern)
        if not records:
            records = self._parse_text_blocks(soup, seen)

        return records

    def _parse_table_row(
        self, cells: List[str], headers: List[str]
    ) -> Optional[ArrestRecord]:
        """Parse a single table row into an ArrestRecord."""
        name = ""
        booking_num = ""
        dob = ""
        charges = "Unknown"
        bond = "0"
        race = ""
        sex = ""

        for i, h in enumerate(headers):
            if i >= len(cells):
                break
            val = cells[i].strip()
            if not val:
                continue

            if any(kw in h for kw in ("name", "inmate", "offender", "last")):
                name = val
            elif "book" in h and "date" not in h:
                booking_num = val
            elif "jacket" in h or "id" in h:
                booking_num = booking_num or val
            elif "dob" in h or "birth" in h:
                dob = val
            elif "charge" in h or "offense" in h:
                charges = val
            elif "bond" in h or "bail" in h:
                bond = re.sub(r"[^\d.]", "", val) or "0"
            elif "race" in h:
                race = val
            elif "sex" in h or "gender" in h:
                sex = val[:1].upper()

        if not name or len(name) < 2:
            return None

        if not booking_num:
            booking_num = (
                f"EBR_{hashlib.md5(f'{name}|EBR_LA'.encode()).hexdigest()[:10]}"
            )

        first, last = self._split_name_static(name)

        return ArrestRecord(
            County=self.county,
            State="LA",
            Full_Name=name.title(),
            First_Name=first,
            Last_Name=last,
            DOB=dob,
            Booking_Number=str(booking_num),
            Race=race,
            Sex=sex,
            Charges=charges,
            Bond_Amount=bond,
            Status="In Custody",
            Detail_URL=PORTAL_URL,
            Facility="East Baton Rouge Parish Prison",
        )

    def _parse_text_blocks(
        self, soup: BeautifulSoup, seen: set
    ) -> List[ArrestRecord]:
        """
        Fallback parser for unstructured text content.
        Looks for patterns like:
          LAST, FIRST MIDDLE
          DOB: MM/DD/YYYY
        """
        records: List[ArrestRecord] = []
        text = soup.get_text("\n", strip=True)

        # Pattern: NAME followed by DOB line
        name_dob_pattern = re.findall(
            r"([A-Z][A-Z\s,'-]+(?:JR|SR|II|III|IV)?)\s*\n\s*"
            r"(?:DOB|Date of Birth|D\.O\.B\.?)[:.]?\s*(\d{1,2}/\d{1,2}/\d{4})",
            text,
            re.IGNORECASE,
        )

        for name_raw, dob in name_dob_pattern:
            name = name_raw.strip()
            if len(name) < 3 or name.isnumeric():
                continue

            booking_num = (
                f"EBR_{hashlib.md5(f'{name}|EBR_LA'.encode()).hexdigest()[:10]}"
            )
            if booking_num in seen:
                continue
            seen.add(booking_num)

            first, last = self._split_name_static(name)

            records.append(ArrestRecord(
                County=self.county,
                State="LA",
                Full_Name=name.title(),
                First_Name=first,
                Last_Name=last,
                DOB=dob,
                Booking_Number=booking_num,
                Charges="Unknown",
                Bond_Amount="0",
                Status="In Custody",
                Detail_URL=PORTAL_URL,
                Facility="East Baton Rouge Parish Prison",
            ))

        return records

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _split_name_static(name: str) -> tuple:
        """Split 'LAST, FIRST' into (first, last)."""
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip().title()
            first = parts[1].strip().title()
            return first, last
        bits = name.split()
        if len(bits) >= 2:
            return bits[0].title(), bits[-1].title()
        return name.title(), ""
