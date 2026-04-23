"""
Brevard County Arrest Scraper — POST-Based Inmate Search.

Source: Brevard County Sheriff's Office
URL: https://www.brevardcounty.us/JailCompliance/SubSearch
Method: requests + BeautifulSoup (POST form search)
"""

import logging, re, time, string
from datetime import datetime
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
SEARCH_URL = "https://www.brevardcounty.us/JailCompliance/SubSearch"
FACILITY = "Brevard County Jail Complex"


class BrevardCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Brevard"

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

        for letter in string.ascii_uppercase:
            try:
                resp = session.post(SEARCH_URL, data={"LastName": letter, "FirstName": ""}, timeout=30)
                if resp.status_code != 200: continue
                soup = BeautifulSoup(resp.text, "html.parser")
                records = self._parse_results(soup)
                for r in records:
                    key = r.Booking_Number or r.Full_Name
                    if key and key not in seen:
                        seen.add(key)
                        all_records.append(r)
            except Exception as e:
                logger.warning(f"Brevard letter {letter}: {e}")
            time.sleep(0.3)

        logger.info(f"✅ Brevard: {len(all_records)} records")
        return all_records

    def _parse_results(self, soup) -> List[ArrestRecord]:
        records = []
        for table in soup.find_all("table"):
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) < 3: continue
                texts = [c.get_text(strip=True) for c in cells]

                full_name = ""
                booking_number = ""
                booking_date = ""
                bond_amount = "0"
                charges = ""
                dob = ""

                for t in texts:
                    if "," in t and not full_name and len(t) > 3 and not t.replace(",","").replace(" ","").isdigit():
                        full_name = t
                    elif re.match(r"^\d{4,}$", t.strip()) and not booking_number:
                        booking_number = t.strip()
                    elif re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", t) and not booking_date:
                        booking_date = t

                rt = row.get_text(" ", strip=True)
                bm = re.search(r"\$([\d,]+\.?\d*)", rt)
                if bm: bond_amount = bm.group(1).replace(",", "")

                if not full_name and not booking_number: continue
                f, m, l = self._pn(full_name)

                link = row.find("a", href=True)
                detail_url = ""
                if link:
                    h = link["href"]
                    if not h.startswith("http"): h = "https://www.brevardcounty.us" + h
                    detail_url = h

                records.append(ArrestRecord(
                    County=self.county, Booking_Number=booking_number,
                    Full_Name=full_name, First_Name=f, Middle_Name=m, Last_Name=l,
                    Booking_Date=booking_date, Bond_Amount=bond_amount,
                    Status="In Custody", Facility=FACILITY,
                    Detail_URL=detail_url, LastCheckedMode="INITIAL"))
        return records

    @staticmethod
    def _pn(n):
        if not n: return "","",""
        if "," in n:
            p = n.split(",",1); l = p[0].strip(); fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm)>1 else ""), l
        p = n.split(); return p[0], "", p[-1] if len(p)>=2 else ""
