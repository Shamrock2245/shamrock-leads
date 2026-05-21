"""
St. Johns County Arrest Scraper — HTML Inmate Search.
Source: St. Johns County Sheriff's Office
URL: https://www.sjso.org/sj-inmate-search/
Method: requests + BeautifulSoup — HTML table scraping
"""
import logging
import re
import string
import time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sjso.org"
SEARCH_URL = f"{BASE_URL}/sj-inmate-search/"
FACILITY = "St. Johns County Jail"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL,
}


class StJohnsCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "St. Johns"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed"); raise

        session = requests.Session()
        session.headers.update(HEADERS)

        all_records = []
        seen = set()

        # Try alphabetical search
        for letter in string.ascii_uppercase:
            try:
                resp = session.post(SEARCH_URL, data={"last_name": letter, "first_name": ""}, timeout=30)
                if resp.status_code != 200:
                    resp = session.get(f"{SEARCH_URL}?last_name={letter}", timeout=30)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                batch = self._parse_table(soup, seen)
                all_records.extend(batch)
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"St. Johns letter {letter}: {e}")
                continue

        if not all_records:
            try:
                resp = session.get(SEARCH_URL, timeout=30)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                all_records = self._parse_table(soup, seen)
            except Exception as e:
                logger.error(f"St. Johns fallback: {e}")

        logger.info(f"St. Johns: {len(all_records)} records")
        return all_records

    def _parse_table(self, soup, seen: set) -> List[ArrestRecord]:
        records = []
        table = None
        for t in soup.find_all("table"):
            text = t.get_text(" ").lower()
            if any(kw in text for kw in ["name", "booking", "inmate", "arrest"]):
                rows = t.find_all("tr")
                if len(rows) > 1:
                    table = t
                    break

        if not table:
            return []

        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if not any(texts):
                continue

            full_name = texts[0] if len(texts) > 0 else ""
            booking_num = texts[1] if len(texts) > 1 else ""
            booking_date = texts[2] if len(texts) > 2 else ""
            charges = texts[3] if len(texts) > 3 else ""
            bond_raw = texts[4] if len(texts) > 4 else "0"

            key = (full_name, booking_num)
            if key in seen or not full_name:
                continue
            seen.add(key)

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
                        DOB="",
                Booking_Date=self._clean(booking_date),
                Status="In Custody",
                        Release_Date="",
                Facility=FACILITY,
                Charges=self._clean(charges),
                Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                Detail_URL=detail_url,
                LastCheckedMode="INITIAL",
            ))

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
