"""
Glades County Arrest Scraper — SmartWeb ASP.NET
Source: Glades County Sheriff's Office
URL: https://smartweb.gladessheriff.org/smartwebclient/Jail.aspx
Method: requests POST — SmartWeb form with txbLastName/txbFirstName/btnSumit (typo is correct)
Returns card-style HTML with inmate blocks
"""
import logging
import re
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URL = "https://smartweb.gladessheriff.org/smartwebclient/Jail.aspx"
FACILITY = "Glades County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": SEARCH_URL,
}


class GladesCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Glades"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            return []

        session = requests.Session()
        session.headers.update(HEADERS)

        # GET first to get ViewState tokens
        try:
            resp = session.get(SEARCH_URL, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Glades: GET failed: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        viewstate = soup.find("input", {"name": "__VIEWSTATE"})
        viewstate_gen = soup.find("input", {"name": "__VIEWSTATEGENERATOR"})
        event_val = soup.find("input", {"name": "__EVENTVALIDATION"})

        post_data = {
            "__VIEWSTATE": viewstate["value"] if viewstate else "",
            "__VIEWSTATEGENERATOR": viewstate_gen["value"] if viewstate_gen else "",
            "__EVENTVALIDATION": event_val["value"] if event_val else "",
            "txbLastName": "",
            "txbFirstName": "",
            "btnSumit": "Search",  # Note: typo in SmartWeb — btnSumit not btnSubmit
        }

        try:
            resp = session.post(SEARCH_URL, data=post_data, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Glades: POST failed: {e}")
            return []

        return self._parse(resp.text)

    def _parse(self, html: str) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        records = []
        seen = set()

        # SmartWeb returns card-style divs OR a table
        # Try table first
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text(" ").lower()
            if "name" in header_text and ("booking" in header_text or "inmate" in header_text):
                for row in rows[1:]:
                    cells = row.find_all("td")
                    if len(cells) < 2:
                        continue
                    texts = [c.get_text(strip=True) for c in cells]
                    full_name = texts[0]
                    if not full_name:
                        continue
                    booking_num = texts[1] if len(texts) > 1 else ""
                    booking_date = texts[2] if len(texts) > 2 else ""
                    charges = texts[3] if len(texts) > 3 else ""
                    bond_raw = texts[4] if len(texts) > 4 else "0"
                    key = booking_num or full_name
                    if key in seen:
                        continue
                    seen.add(key)
                    f, m, l = self._pn(full_name)
                    bond_amount = self._parse_bond(bond_raw)
                    records.append(ArrestRecord(
                        County=self.county,
                        Booking_Number=booking_num,
                        Full_Name=full_name,
                        First_Name=f, Middle_Name=m, Last_Name=l,
                        Booking_Date=booking_date,
                        Status="In Custody",
                        Facility=FACILITY,
                        Charges=charges,
                        Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                        LastCheckedMode="INITIAL",
                    ))
                if records:
                    break

        # Card-style fallback
        if not records:
            for block in soup.find_all(class_=re.compile(r"inmate|record|card|row", re.I)):
                text = block.get_text(" ", strip=True)
                if not text or len(text) < 10:
                    continue
                name_m = re.search(r"^([A-Z][A-Z ,'-]+)", text)
                booking_m = re.search(r"Booking\s*No[.:]?\s*([A-Z0-9]+)", text, re.I)
                date_m = re.search(r"Booking\s*Date[.:]?\s*(\d{2}/\d{2}/\d{4}(?:\s+\d{2}:\d{2})?)", text, re.I)
                bond_m = re.search(r"Bond[.:]?\s*\$?([\d,]+\.?\d*)", text, re.I)
                full_name = name_m.group(1).strip() if name_m else ""
                booking_num = booking_m.group(1) if booking_m else ""
                booking_date = date_m.group(1) if date_m else ""
                bond_raw = bond_m.group(1) if bond_m else "0"
                key = booking_num or full_name
                if not key or key in seen:
                    continue
                seen.add(key)
                f, m, l = self._pn(full_name)
                bond_amount = self._parse_bond(bond_raw)
                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_num,
                    Full_Name=full_name,
                    First_Name=f, Middle_Name=m, Last_Name=l,
                    Booking_Date=booking_date,
                    Status="In Custody",
                    Facility=FACILITY,
                    Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                    LastCheckedMode="INITIAL",
                ))

        logger.info(f"Glades: {len(records)} records")
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

    @staticmethod
    def _parse_bond(bond_str):
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
