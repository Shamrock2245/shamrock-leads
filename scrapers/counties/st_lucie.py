"""
St. Lucie County Arrest Scraper — PHP Inmate List
Source: St. Lucie County Sheriff's Office
URL: https://jail.stluciesheriff.com/inmateList.php (POST with Last=%)
Method: requests + BeautifulSoup — simple POST, returns HTML table with 1000+ records
Note: The main site /215/Inmate-Lookup embeds an iframe at jail.stluciesheriff.com
      The iframe form POSTs to inmateList.php — no JS required.
Columns: Name | DOB | Age | Inmate Id | Booking Date | Release Date
"""
import logging
import re
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://jail.stluciesheriff.com"
SEARCH_URL = f"{BASE_URL}/inmateSearch.php"
LIST_URL = f"{BASE_URL}/inmateList.php"
FACILITY = "St. Lucie County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SEARCH_URL,
    "Content-Type": "application/x-www-form-urlencoded",
}


class StLucieCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "St. Lucie"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            raise

        session = requests.Session()
        session.headers.update(HEADERS)

        try:
            resp = session.post(LIST_URL, data={"First": "", "Last": "%", "Submit": "Search"}, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"St. Lucie: failed to fetch inmate list: {e}")
            raise

        soup = BeautifulSoup(resp.text, "html.parser")
        records = []
        seen = set()

        # Find the inmate data table — header: Name DOB Age Inmate Id Booking Date Release Date
        target_table = None
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text(" ").lower()
            if "name" in header_text and ("booking" in header_text or "inmate" in header_text):
                target_table = table
                break

        if not target_table:
            logger.warning("St. Lucie: no inmate table found")
            return []

        for row in target_table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if not texts[0]:
                continue

            full_name = texts[0]
            dob = texts[1] if len(texts) > 1 else ""
            inmate_id = texts[3] if len(texts) > 3 else ""
            booking_date = texts[4] if len(texts) > 4 else ""
            release_date = texts[5] if len(texts) > 5 else ""
            status = "Released" if release_date and release_date.strip() not in (":", "", "N/A") else "In Custody"

            key = inmate_id or full_name
            if not key or key in seen:
                continue
            seen.add(key)

            detail_url = ""
            link = row.find("a", href=True)
            if link:
                href = link["href"]
                detail_url = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"

            f, m, l = self._pn(full_name)
            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=inmate_id,
                Full_Name=full_name,
                First_Name=f,
                Middle_Name=m,
                Last_Name=l,
                DOB=dob,
                Booking_Date=booking_date,
                Status=status,
                        Release_Date="",
                Facility=FACILITY,
                Detail_URL=detail_url,
                LastCheckedMode="INITIAL",
            ))

        logger.info(f"St. Lucie: {len(records)} records")
        return records

    @staticmethod
    def _pn(n):
        if not n:
            return "", "", ""
        n = " ".join(n.strip().split())
        if "," in n:
            p = n.split(",", 1)
            l = p[0].strip()
            fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm) > 1 else ""), l
        p = n.split()
        return p[0], (" ".join(p[2:]) if len(p) > 2 else ""), (p[-1] if len(p) >= 2 else "")
