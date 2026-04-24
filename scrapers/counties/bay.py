"""
Bay County Arrest Scraper — Bay County Sheriff's Office Inmate Search.
Source: Bay County Sheriff's Office
URL: https://www.baysomobile.org/is/
Method: requests + session — UniGUI/Ext.js hyb.dll HandleEvent API
Pattern: GET page to get session ID → POST HandleEvent to search → parse HTML response
Note: Old URL baysomobile.org/inmates was wrong; correct URL is baysomobile.org/is/
"""
import logging
import re
import time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.baysomobile.org/is"
HANDLE_URL = f"{BASE_URL}/hyb.dll/HandleEvent"
FACILITY = "Bay County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class BayCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Bay"

    @property
    def interval_minutes(self) -> int:
        return 120

    def scrape(self) -> List[ArrestRecord]:
        try:
            return self._scrape_unigui()
        except Exception as e:
            logger.error(f"[BAY] Scrape failed: {e}")
            return []

    def _scrape_unigui(self) -> List[ArrestRecord]:
        """Scrape Bay County using UniGUI/hyb.dll session-based API."""
        import requests
        from bs4 import BeautifulSoup

        session = requests.Session()
        session.headers.update(HEADERS)

        # Step 1: GET the page to establish session and get _S_ID
        logger.info("[BAY] Loading inmate search page...")
        resp = session.get(f"{BASE_URL}/", timeout=30)
        resp.raise_for_status()

        # Extract session ID from page source
        sid = ""
        for pattern in [r'_S_ID["\s]*[:=]["\s]*([A-Za-z0-9]+)', r'"_S_ID":"([^"]+)"', r'_S_ID=([A-Za-z0-9]+)']:
            m = re.search(pattern, resp.text)
            if m:
                sid = m.group(1)
                logger.info(f"[BAY] Got session ID: {sid[:8]}...")
                break

        if not sid:
            logger.warning("[BAY] Could not extract session ID")

        # Step 2: POST HandleEvent to trigger search with empty last name (returns all)
        search_headers = {
            **HEADERS,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE_URL}/",
        }

        post_data = f"Ajax=1&IsEvent=1&Obj=O68&Evt=click&this=O68&_S_ID={sid}&_seq_=3&_uo_=O0"

        logger.info("[BAY] Submitting search...")
        search_resp = session.post(
            HANDLE_URL,
            data=post_data,
            headers=search_headers,
            timeout=30,
        )

        records = []
        if search_resp.status_code == 200:
            records = self._parse_html(search_resp.text)

        # If no records from HandleEvent, try parsing the initial page
        if not records:
            logger.info("[BAY] Trying initial page parse...")
            records = self._parse_html(resp.text)

        logger.info(f"[BAY] Found {len(records)} records")
        return records

    def _parse_html(self, html: str) -> List[ArrestRecord]:
        """Parse UniGUI HTML response for inmate records."""
        from bs4 import BeautifulSoup

        records = []
        seen = set()
        soup = BeautifulSoup(html, "html.parser")

        # UniGUI grid rows
        rows = soup.find_all("tr", class_=re.compile(r"x-grid-row|unigrid-row|grid-row", re.I))

        if not rows:
            # Try any table with multiple rows
            for table in soup.find_all("table"):
                trows = table.find_all("tr")
                if len(trows) > 2:
                    rows = trows[1:]
                    break

        for row in rows:
            try:
                cells = row.find_all(["td", "div"], class_=re.compile(r"x-grid-cell|grid-cell", re.I))
                if not cells:
                    cells = row.find_all("td")
                if len(cells) < 2:
                    continue

                cell_texts = [c.get_text(" ", strip=True) for c in cells]
                booking_text = " ".join(cell_texts)

                # Extract name (LAST, FIRST pattern)
                name_match = re.search(r"([A-Z][A-Z\s,'-]{2,}),\s*([A-Z][A-Z\s'-]+)", booking_text)
                if not name_match:
                    continue

                last_name = name_match.group(1).strip()
                first_name = name_match.group(2).strip()

                booking_match = re.search(r"(?:Booking|Book)\s*#?\s*:?\s*([A-Z0-9-]+)", booking_text, re.I)
                booking_num = booking_match.group(1) if booking_match else ""

                dob_match = re.search(r"(?:DOB|Date of Birth)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", booking_text, re.I)
                dob = dob_match.group(1) if dob_match else ""

                date_match = re.search(r"(?:Booking Date|Booked)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", booking_text, re.I)
                booking_date = date_match.group(1) if date_match else ""

                charges_text = cell_texts[-1] if len(cell_texts) > 2 else ""

                bond_match = re.search(r"\$[\d,]+\.?\d*", booking_text)
                bond_amount = bond_match.group(0) if bond_match else ""

                key = (last_name, booking_num)
                if key in seen:
                    continue
                seen.add(key)

                records.append(ArrestRecord(
                    County=self.county,
                    First_Name=first_name,
                    Last_Name=last_name,
                    Booking_Number=booking_num,
                    Booking_Date=booking_date,
                    DOB=dob,
                    Charges=charges_text[:500],
                    Bond_Amount=bond_amount,
                    Facility=FACILITY,
                    Status="In Custody",
                    LastCheckedMode="INITIAL",
                ))

            except Exception as e:
                logger.debug(f"[BAY] Row parse error: {e}")
                continue

        return records
