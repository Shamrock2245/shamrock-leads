"""
Seminole County Arrest Scraper — NorthPointe Custody Portal
Source: Seminole County Sheriff's Office
URL: https://seminole.northpointesuite.com/custodyportal
Method: nodriver (headless Chromium) for roster page → curl_cffi for detail pages

Architecture:
1. nodriver loads the custody portal and clicks Search (no filters) → 500 inmates
2. Parse goToDetails JSON from each searchDataRow → name, DOB, race, sex, personId
3. For each inmate, fetch /Home/Details?data=<JSON> via curl_cffi → booking#, date, charges
4. Date-gate: only process inmates whose detail page shows booking within DAYS_BACK

Fix 2026-05-18: Replaced Selenium with nodriver + curl_cffi.
                Roster is JS-rendered; detail pages are accessible via plain HTTP.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://seminole.northpointesuite.com"
PORTAL_URL = f"{BASE_URL}/custodyportal"
DETAIL_URL = f"{BASE_URL}/custodyportal/Home/Details"
FACILITY = "John E Polk Correctional Facility"
DAYS_BACK = 7
MAX_DETAIL_FETCHES = 150  # Cap to avoid excessive bandwidth (each detail ~1.6MB)
IMPERSONATE = "chrome131"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": PORTAL_URL,
}


class SeminoleCountyScraper(BaseScraper):
    """Seminole County (FL) — NorthPointe Custody Portal (nodriver + curl_cffi)"""

    @property
    def county(self) -> str:
        return "Seminole"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import nodriver as uc
            from curl_cffi import requests as cf
            from bs4 import BeautifulSoup  # noqa — imported in sub-methods
        except ImportError as e:
            logger.error(f"Seminole: missing dependency: {e}")
            return []

        # Step 1: Load roster via nodriver (JS-rendered page)
        roster_html = asyncio.run(self._load_roster(uc))
        if not roster_html:
            logger.error("Seminole: failed to load roster")
            return []

        # Step 2: Parse all goToDetails JSON objects from roster
        inmates = self._parse_roster(roster_html)
        logger.info(f"Seminole: {len(inmates)} inmates on roster")
        if not inmates:
            return []

        # Step 3: Fetch detail pages for recent inmates via curl_cffi
        cutoff = datetime.now() - timedelta(days=DAYS_BACK)
        session = cf.Session()
        records = []
        seen: set = set()
        fetched = 0

        for inmate in inmates:
            if fetched >= MAX_DETAIL_FETCHES:
                break
            person_id = inmate.get("personId")
            if not person_id or person_id in seen:
                continue
            try:
                record = self._fetch_detail(session, inmate, cutoff)
                if record:
                    seen.add(person_id)
                    records.append(record)
                    fetched += 1
            except Exception as e:
                logger.debug(f"Seminole detail error for {inmate.get('lastName')}: {e}")

        logger.info(f"Seminole: {len(records)} records within {DAYS_BACK} days")
        return records

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_roster(self, uc) -> Optional[str]:
        """Use nodriver to load the custody portal and click Search."""
        browser = None
        try:
            browser = await uc.start(
                browser_executable_path="/usr/bin/chromium",
                headless=True,
                browser_args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                ],
            )
            page = await browser.get(PORTAL_URL)
            await asyncio.sleep(5)

            # Click Search with no filters to get all current inmates
            search_btn = await page.find("#searchBtn", timeout=10)
            await search_btn.click()
            await asyncio.sleep(8)

            return await page.get_content()

        except Exception as e:
            logger.error(f"Seminole nodriver error: {e}")
            return None
        finally:
            if browser:
                try:
                    browser.stop()
                except Exception:
                    pass

    def _parse_roster(self, html: str) -> List[dict]:
        """Parse goToDetails JSON from each searchDataRow div."""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        inmates = []
        for row in soup.find_all("div", class_="searchDataRow"):
            link = row.find("a", href=re.compile(r"goToDetails"))
            if not link:
                continue
            href = link.get("href", "")
            json_match = re.search(r"goToDetails\((\{[^)]+\})\)", href)
            if not json_match:
                continue
            try:
                inmates.append(json.loads(json_match.group(1)))
            except (json.JSONDecodeError, ValueError):
                continue
        return inmates

    def _fetch_detail(
        self, session, inmate: dict, cutoff: datetime
    ) -> Optional[ArrestRecord]:
        """Fetch the detail page for one inmate and extract booking info."""
        from bs4 import BeautifulSoup

        data_param = json.dumps(inmate)
        try:
            r = session.get(
                DETAIL_URL,
                params={"data": data_param},
                headers=HEADERS,
                timeout=45,
                impersonate=IMPERSONATE,
            )
            r.raise_for_status()
        except Exception as e:
            raise RuntimeError(f"Detail fetch failed: {e}")

        soup = BeautifulSoup(r.text, "html.parser")

        # Booking number from "Booking - 202600003587" header
        booking_header = soup.find("div", class_="bookingHeader")
        if not booking_header:
            return None
        bk_match = re.search(r"Booking\s*-\s*(\d+)", booking_header.get_text())
        booking_num = bk_match.group(1) if bk_match else ""

        # Booking date
        booking_date = self._label_next(soup, r"^Booking Date$")

        # Date gate — skip if older than cutoff
        if booking_date:
            try:
                bd = datetime.strptime(booking_date, "%m/%d/%Y")
                if bd < cutoff:
                    return None
            except ValueError:
                pass

        # Other fields
        status = self._label_next(soup, r"^Status$") or "In Custody"
        bond = self._label_next(soup, r"^Total Bond$") or "0"
        release_date = self._label_next(soup, r"^Projected Release Date$") or ""

        # Charges
        charges = []
        for div in soup.find_all("div", class_=re.compile(r"chargeRow|chargeData", re.I)):
            text = div.get_text(separator=" ", strip=True)
            if text:
                charges.append(text)

        # Name components
        first = inmate.get("firstName", "")
        last = inmate.get("lastName", "")
        middle = inmate.get("middleName") or ""
        full_name = f"{last}, {first}" + (f" {middle}" if middle else "")

        # DOB
        dob_raw = inmate.get("dateOfBirth", "")
        dob = ""
        if dob_raw:
            try:
                dob = datetime.fromisoformat(dob_raw.replace("T00:00:00", "")).strftime(
                    "%m/%d/%Y"
                )
            except ValueError:
                dob = dob_raw[:10]

        return ArrestRecord(
            County=self.county,
            State="FL",
            Facility=FACILITY,
            Full_Name=full_name.upper(),
            First_Name=first.upper(),
            Middle_Name=middle.upper(),
            Last_Name=last.upper(),
            DOB=dob,
            Booking_Number=booking_num,
            Booking_Date=booking_date,
            Arrest_Date=booking_date,
            Status=status,
            Release_Date=release_date,
            Charges=" | ".join(charges),
            Bond_Amount=str(self._parse_bond(bond)),
            Race=inmate.get("race", ""),
            Sex=inmate.get("gender", ""),
            Height=inmate.get("height", ""),
            Weight=inmate.get("weight", ""),
            Detail_URL=f"{DETAIL_URL}?data={data_param}",
            Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
            LastChecked=datetime.now(timezone.utc).isoformat(),
            LastCheckedMode="INITIAL",
        )

    @staticmethod
    def _label_next(soup, label_pattern: str) -> str:
        """Find a label by regex and return the text of the next sibling element."""
        el = soup.find(string=re.compile(label_pattern))
        if not el:
            return ""
        nxt = el.parent.find_next_sibling()
        return nxt.get_text(strip=True) if nxt else ""

    @staticmethod
    def _parse_bond(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
