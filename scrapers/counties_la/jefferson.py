"""
Jefferson Parish (LA) Arrest Scraper — JPSO InmateSearch portal.

Portal: https://apps.jpso.com/inmatesearch/
Format: Server-rendered HTML table with inline charges, bond amounts,
        CCN (booking ID), race, sex, DOB, arrest date per inmate.

TLS: JPSO enforces strict TLS — requires curl_cffi or StealthSession
     for proper JA3 fingerprinting. Standard requests/urllib3 will fail
     with SSL handshake errors from datacenter IPs.

Data quality: EXCELLENT — charges with RS codes, individual bond amounts,
              arrest dates, and CCN all inline per row.
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

PORTAL_URL = "https://apps.jpso.com/inmatesearch/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://apps.jpso.com/",
}


class JeffersonScraper(BaseScraper):
    """Jefferson Parish (LA) — JPSO InmateSearch portal."""

    @property
    def county(self) -> str:
        return "Jefferson"

    @property
    def state(self) -> str:
        return "LA"

    @property
    def scraper_id(self) -> str:
        return "scraper_la_jefferson"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []

        # Primary: StealthSession (curl_cffi with proxy rotation)
        html = self._fetch_with_stealth()

        # Fallback: DrissionPage browser
        if not html:
            html = self._fetch_with_browser()

        if not html:
            logger.error("Jefferson (LA): all fetch methods failed")
            return []

        # Parse the inmate table
        soup = BeautifulSoup(html, "html.parser")
        records = self._parse_roster(soup)

        elapsed = time.time() - start
        logger.info(f"✅ Jefferson (LA): {len(records)} records in {elapsed:.1f}s")
        return records

    # ── Fetch Methods ────────────────────────────────────────────────────────

    def _fetch_with_stealth(self) -> Optional[str]:
        """Fetch using StealthSession (curl_cffi + proxy rotation)."""
        try:
            from scrapers.proxy_engine import create_stealth_session

            with create_stealth_session(
                sticky_session_id="jpso_jefferson",
                prefer_residential=True,
                allow_direct=True,
            ) as session:
                resp = session.get(PORTAL_URL, headers=HEADERS, timeout=30)
                if resp.status_code == 200 and len(resp.text) > 5000:
                    return resp.text
                logger.warning(
                    f"Jefferson stealth: HTTP {resp.status_code}, "
                    f"len={len(resp.text)}"
                )
                return None
        except Exception as e:
            logger.debug(f"Jefferson stealth failed: {e}")
            return None

    def _fetch_with_browser(self) -> Optional[str]:
        """Fallback: DrissionPage headless browser."""
        try:
            from DrissionPage import ChromiumPage

            co = self._get_browser_options()
            page = ChromiumPage(co)
            page.get(PORTAL_URL)
            page.wait.doc_loaded()
            time.sleep(3)  # Allow JS hydration
            html = page.html
            try:
                page.quit()
            except Exception:
                pass
            if html and len(html) > 5000:
                return html
            return None
        except Exception as e:
            logger.debug(f"Jefferson browser fallback failed: {e}")
            return None

    # ── Parser ───────────────────────────────────────────────────────────────

    def _parse_roster(self, soup: BeautifulSoup) -> List[ArrestRecord]:
        """
        Parse the JPSO inmate search results table.

        Each inmate row contains:
        - Name (bold, first cell)
        - Charges (multiple lines with RS codes, arrest dates, bond amounts)
        - CCN (booking number)
        - Race, Sex, DOB, Arrest Date
        """
        records: List[ArrestRecord] = []
        seen: set = set()

        # Find the main results table
        table = soup.find("table")
        if not table:
            # Try finding by class or id patterns
            for t in soup.find_all("table"):
                if t.find("tr") and len(t.find_all("tr")) > 2:
                    table = t
                    break

        if not table:
            logger.warning("Jefferson: no inmate table found in HTML")
            return []

        rows = table.find_all("tr")
        for row in rows:
            try:
                cells = row.find_all("td")
                if len(cells) < 4:
                    continue

                # First cell contains name (bold) and charges
                name_cell = cells[0]
                name_tag = name_cell.find("b") or name_cell.find("strong")
                if not name_tag:
                    continue

                full_name = name_tag.get_text(strip=True)
                if not full_name or len(full_name) < 2:
                    continue

                # Extract charges and bonds from the cell text
                cell_text = name_cell.get_text("\n", strip=True)
                charges, total_bond = self._parse_charges_block(cell_text)

                # CCN (booking number) — typically second cell
                ccn = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                if not ccn:
                    ccn = f"JEF_{hashlib.md5(f'{full_name}|JEFFERSON_LA'.encode()).hexdigest()[:10]}"

                # Race — third cell
                race = cells[2].get_text(strip=True) if len(cells) > 2 else ""

                # Sex — fourth cell
                sex = cells[3].get_text(strip=True) if len(cells) > 3 else ""

                # DOB — fifth cell
                dob = cells[4].get_text(strip=True) if len(cells) > 4 else ""

                # Arrest date — sixth cell
                arrest_date = cells[5].get_text(strip=True) if len(cells) > 5 else ""

                # Dedup on CCN
                if ccn in seen:
                    continue
                seen.add(ccn)

                # Parse name
                first, last = self._split_name(full_name)

                records.append(ArrestRecord(
                    County=self.county,
                    State="LA",
                    Full_Name=full_name.title(),
                    First_Name=first,
                    Last_Name=last,
                    Booking_Number=str(ccn),
                    Booking_Date=arrest_date,
                    Arrest_Date=arrest_date,
                    DOB=dob,
                    Race=race,
                    Sex=sex[:1].upper() if sex else "",
                    Charges=charges or "Unknown",
                    Bond_Amount=str(total_bond),
                    Status="In Custody",
                    Detail_URL=PORTAL_URL,
                    Facility="Jefferson Parish Correctional Center",
                ))

            except Exception as e:
                logger.debug(f"Jefferson row parse error: {e}")
                continue

        return records

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_charges_block(text: str) -> tuple:
        """
        Parse charge lines from JPSO format:
          Charge: RS14:34.9.1C - AGG ASSAULT DATING PARTN; Arrest Date: 7/23/2026; Bond: No Bond Set;
          Charge: RS14:99 - RECKLESS OPERATION VEHICLE; Arrest Date: 7/23/2026; Bond: $500.00;

        Returns (charges_string, total_bond_float).
        """
        charges_list = []
        total_bond = 0.0

        # Match "Charge: <description>" patterns
        charge_matches = re.findall(
            r"Charge:\s*(.+?)(?:;|$)",
            text,
            re.IGNORECASE,
        )
        for charge in charge_matches:
            # Clean up the charge description
            desc = charge.strip()
            # Remove "Arrest Date:" and "Bond:" suffixes if captured
            desc = re.sub(r"\s*Arrest Date:.*$", "", desc, flags=re.IGNORECASE)
            desc = re.sub(r"\s*Bond:.*$", "", desc, flags=re.IGNORECASE)
            if desc:
                charges_list.append(desc.strip())

        # Match bond amounts
        bond_matches = re.findall(
            r"Bond:\s*\$?([\d,]+(?:\.\d{2})?)",
            text,
            re.IGNORECASE,
        )
        for bond_str in bond_matches:
            try:
                total_bond += float(bond_str.replace(",", ""))
            except ValueError:
                continue

        charges_str = " | ".join(charges_list) if charges_list else ""
        return charges_str, int(total_bond) if total_bond == int(total_bond) else total_bond

    @staticmethod
    def _split_name(name: str) -> tuple:
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
