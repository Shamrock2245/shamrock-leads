"""
Alachua County Arrest Scraper — ASP.NET ViewState + "View All" button.
Source: Alachua County Sheriff's Office
URL: https://asosite.alachuasheriff.org/ASOInmateLookup.aspx
Method: requests + BeautifulSoup — POST with ViewState to get all inmates
"""
import logging
import re
import time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://asosite.alachuasheriff.org/ASOInmateLookup.aspx"
FACILITY = "Alachua County Jail"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL,
}


class AlachuaCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Alachua"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed"); return []

        session = requests.Session()
        session.headers.update(HEADERS)

        # Step 1: GET page to harvest ASP.NET tokens
        try:
            resp = session.get(BASE_URL, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Alachua: failed to load page: {e}"); return []

        soup = BeautifulSoup(resp.text, "html.parser")

        def _get_hidden(name):
            el = soup.find("input", {"name": name})
            return el["value"] if el and el.get("value") else ""

        viewstate = _get_hidden("__VIEWSTATE")
        viewstate_gen = _get_hidden("__VIEWSTATEGENERATOR")
        event_validation = _get_hidden("__EVENTVALIDATION")

        if not viewstate:
            logger.warning("Alachua: no __VIEWSTATE found — page structure may have changed")

        # Step 2: POST with "View All" button
        # The button name varies — try both the full ContentPlaceHolder prefix and the short form
        post_data = {
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": viewstate_gen,
            "__EVENTVALIDATION": event_validation,
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "ButtonView": "View All",
            "ctl00$ContentPlaceHolder1$ButtonView": "View All",
            "txtLName": "",
            "txtFName": "",
            "txtBookNo": "",
        }

        try:
            resp2 = session.post(BASE_URL, data=post_data, timeout=60)
            resp2.raise_for_status()
        except Exception as e:
            logger.error(f"Alachua: POST failed: {e}"); return []

        soup2 = BeautifulSoup(resp2.text, "html.parser")
        records = []

        # Find the GridView table
        table = soup2.find("table", id=re.compile(r"GridView|InmateGrid|gvInmates", re.I))
        if not table:
            # Fallback: find any table with booking data
            for t in soup2.find_all("table"):
                headers_row = t.find("tr")
                if headers_row:
                    header_text = headers_row.get_text(" ").lower()
                    if any(kw in header_text for kw in ["name", "booking", "inmate"]):
                        table = t
                        break

        if not table:
            logger.warning("Alachua: no data table found")
            return []

        rows = table.find_all("tr")[1:]  # Skip header
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if not any(texts):
                continue

            # Common column order: Name, Booking#, Booking Date, Charges, Bond
            full_name = texts[0] if len(texts) > 0 else ""
            booking_num = texts[1] if len(texts) > 1 else ""
            booking_date = texts[2] if len(texts) > 2 else ""
            charges = texts[3] if len(texts) > 3 else ""
            bond_raw = texts[4] if len(texts) > 4 else "0"

            # Try to find a detail link
            detail_url = ""
            link = row.find("a", href=True)
            if link:
                href = link["href"]
                if not href.startswith("http"):
                    href = f"https://asosite.alachuasheriff.org/{href.lstrip('/')}"
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
                Booking_Date=self._clean(booking_date),
                Status="In Custody",
                Facility=FACILITY,
                Charges=self._clean(charges),
                Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                Detail_URL=detail_url,
                LastCheckedMode="INITIAL",
            ))

        logger.info(f"Alachua: {len(records)} records")
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
