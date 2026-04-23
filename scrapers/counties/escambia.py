"""
Escambia County Arrest Scraper — Revize CMS Search Form.

Source: Escambia County Corrections
URL: https://myescambia.com/our-services/corrections/inmate-lookup
Method: requests + BeautifulSoup (HTML search form)
"""

import logging, re, time, string
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
SEARCH_URL = "https://myescambia.com/our-services/corrections/inmate-lookup"
FACILITY = "Escambia County Jail"


class EscambiaCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Escambia"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed"); return []

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        })
        seen, all_records = set(), []

        # Try empty search first
        try:
            resp = session.get(SEARCH_URL, timeout=30)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                # Find the actual form/search endpoint
                form = soup.find("form")
                action = SEARCH_URL
                if form and form.get("action"):
                    a = form["action"]
                    action = a if a.startswith("http") else SEARCH_URL.rsplit("/",1)[0] + "/" + a.lstrip("/")

                # Try A-Z search
                for letter in string.ascii_uppercase:
                    try:
                        r = session.post(action, data={"LastName": letter, "FirstName": ""}, timeout=30)
                        if r.status_code == 200:
                            s = BeautifulSoup(r.text, "html.parser")
                            for rec in self._parse(s):
                                key = rec.Booking_Number or rec.Full_Name
                                if key and key not in seen:
                                    seen.add(key); all_records.append(rec)
                    except Exception: pass
                    time.sleep(0.3)
        except Exception as e:
            logger.error(f"Escambia: {e}")

        logger.info(f"✅ Escambia: {len(all_records)} records")
        return all_records

    def _parse(self, soup) -> List[ArrestRecord]:
        records = []
        for table in soup.find_all("table"):
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) < 2: continue
                texts = [c.get_text(strip=True) for c in cells]
                full_name, booking_number, booking_date = "", "", ""
                for t in texts:
                    if "," in t and not full_name and len(t) > 3:
                        full_name = t
                    elif re.match(r"^\d{4,}$", t) and not booking_number:
                        booking_number = t
                    elif re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", t) and not booking_date:
                        booking_date = t
                if not full_name and not booking_number: continue
                rt = row.get_text(" ", strip=True)
                bm = re.search(r"\$([\d,]+\.?\d*)", rt)
                f, m, l = self._pn(full_name)
                records.append(ArrestRecord(County=self.county, Booking_Number=booking_number,
                    Full_Name=full_name, First_Name=f, Middle_Name=m, Last_Name=l,
                    Booking_Date=booking_date, Bond_Amount=bm.group(1).replace(",","") if bm else "0",
                    Status="In Custody", Facility=FACILITY, LastCheckedMode="INITIAL"))
        return records

    @staticmethod
    def _pn(n):
        if not n: return "","",""
        if "," in n:
            p = n.split(",",1); l = p[0].strip(); fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm)>1 else ""), l
        p = n.split(); return p[0], "", p[-1] if len(p)>=2 else ""
