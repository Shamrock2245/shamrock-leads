"""
Citrus County Arrest Scraper — PHP Recent Arrest Endpoint.
Source: Citrus County Sheriff's Office
URL: https://www.sheriffcitrus.org/public_info/recent_arrest.php
Method: requests + BeautifulSoup — simple GET, parse HTML table
"""
import logging
import time
import re
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sheriffcitrus.org"
SEARCH_URL = f"{BASE_URL}/public_info/recent_arrest.php"
FACILITY = "Citrus County Detention Facility"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL,
}


class CitrusCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Citrus"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed"); return []

        session = requests.Session()
        session.headers.update(HEADERS)

        try:
            resp = session.get(SEARCH_URL, timeout=30)
            time.sleep(1)  # Rate limit
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Citrus: failed to load page: {e}"); return []

        soup = BeautifulSoup(resp.text, "html.parser")
        records = []

        # Find the main data table
        table = None
        for t in soup.find_all("table"):
            text = t.get_text(" ").lower()
            if any(kw in text for kw in ["name", "booking", "arrest", "inmate"]):
                rows = t.find_all("tr")
                if len(rows) > 1:
                    table = t
                    break

        if not table:
            logger.warning("Citrus: no data table found")
            return []

        rows = table.find_all("tr")[1:]
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if not any(texts):
                continue

            full_name = texts[0] if len(texts) > 0 else ""
            booking_num = texts[1] if len(texts) > 1 else ""
            arrest_date = texts[2] if len(texts) > 2 else ""
            charges = texts[3] if len(texts) > 3 else ""
            bond_raw = texts[4] if len(texts) > 4 else "0"
            race = texts[5] if len(texts) > 5 else ""
            sex = texts[6] if len(texts) > 6 else ""

            detail_url = ""
            link = row.find("a", href=True)
            if link:
                href = link["href"]
                if not href.startswith("http"):
                    href = f"{BASE_URL}/{href.lstrip('/')}"
                detail_url = href

            f, m, l = self._pn(full_name)
            bond_amount = self._parse_bond(bond_raw)

            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=self._clean(booking_num),
                Full_Name=full_name,
                First_Name=f,
                Middle_Name=m,
                Last_Name=l,
                Arrest_Date=self._clean(arrest_date),
                Booking_Date=self._clean(arrest_date),
                Status="In Custody",
                Facility=FACILITY,
                Race=self._clean(race),
                Sex=self._clean(sex)[:1].upper() if sex else "",
                Charges=self._clean(charges),
                Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                Detail_URL=detail_url,
                LastCheckedMode="INITIAL",
            ))

        logger.info(f"Citrus: {len(records)} records")
        return records

    @staticmethod
    def _clean(text):
        if not text:
            return ""
        return " ".join(str(text).strip().split())

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
        return p[0], (" ".join(p[2:]) if len(p) > 2 else ""), p[-1] if len(p) >= 2 else ""

    @staticmethod
    def _parse_bond(bond_str):
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
