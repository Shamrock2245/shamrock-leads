"""
P2C (Police-to-Citizen) Base Scraper — Shared logic for P2C platform counties.

P2C by Superion/CentralSquare is used by many Florida counties. Standard pattern:
- URL: http://{subdomain}.{domain}/jailinmates.aspx
- Search: GET page → parse table or POST form with last name
- Results: HTML table with inmate data

Counties using P2C: Clay, Marion, Alachua, Putnam, others
"""

import logging, re, string, time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class P2CBaseScraper(BaseScraper):
    """Base class for P2C (Police-to-Citizen) platform counties."""

    P2C_URL: str = ""           # Override: full URL to jail inmates page
    COUNTY_NAME: str = ""       # Override
    FACILITY_NAME: str = ""     # Override

    @property
    def county(self) -> str:
        return self.COUNTY_NAME

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error(f"requests/bs4 not installed"); return []

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        })

        try:
            resp = session.get(self.P2C_URL, timeout=30, allow_redirects=True)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"{self.county}: {e}"); return []

        soup = BeautifulSoup(resp.text, "html.parser")
        records = self._parse(soup)

        # Try form-based A-Z search if direct parse yielded nothing
        if not records:
            form = soup.find("form")
            if form:
                records = self._az_search(session, form)

        logger.info(f"✅ {self.county}: {len(records)} records")
        return records

    def _az_search(self, session, form) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        action = form.get("action", self.P2C_URL)
        if not action.startswith("http"):
            action = self.P2C_URL.rsplit("/",1)[0] + "/" + action.lstrip("/")
        hf = {}
        for inp in form.find_all("input", type="hidden"):
            n, v = inp.get("name",""), inp.get("value","")
            if n: hf[n] = v
        seen, all_r = set(), []
        for letter in string.ascii_uppercase:
            try:
                r = session.post(action, data={**hf, "LastName": letter, "FirstName": ""}, timeout=30)
                if r.status_code == 200:
                    for rec in self._parse(BeautifulSoup(r.text, "html.parser")):
                        k = rec.Booking_Number or rec.Full_Name
                        if k and k not in seen: seen.add(k); all_r.append(rec)
            except Exception: pass
            time.sleep(0.3)
        return all_r

    def _parse(self, soup) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        records = []
        for table in soup.find_all("table"):
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) < 2: continue
                rec_name, rec_bk, rec_date, rec_bond = "", "", "", "0"
                for c in cells:
                    t = c.get_text(strip=True)
                    if "," in t and not rec_name and len(t) > 3:
                        rec_name = t
                    elif re.match(r"^\d{4,}$", t) and not rec_bk:
                        rec_bk = t
                    elif re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", t) and not rec_date:
                        rec_date = t
                rt = row.get_text(" ", strip=True)
                bm = re.search(r"\$([\d,]+\.?\d*)", rt)
                if bm: rec_bond = bm.group(1).replace(",","")
                if not rec_name and not rec_bk: continue
                f, m, l = self._pn(rec_name)
                lnk = row.find("a", href=True)
                detail = ""
                if lnk:
                    h = lnk["href"]
                    if not h.startswith("http"): h = self.P2C_URL.rsplit("/",1)[0]+"/"+h.lstrip("/")
                    detail = h
                records.append(ArrestRecord(
                    County=self.county,
                    State=getattr(self, "state", None) or "FL",
                    Booking_Number=rec_bk or f"P2C_{re.sub(r'[^A-Za-z0-9]', '', rec_name)[:16]}",
                    Full_Name=rec_name, First_Name=f, Middle_Name=m, Last_Name=l,
                    Booking_Date=rec_date, Bond_Amount=rec_bond, Status="In Custody",
                    Facility=self.FACILITY_NAME, Detail_URL=detail or self.P2C_URL,
                    Charges="Unknown", LastCheckedMode="INITIAL",
                ))
        return records

    @staticmethod
    def _pn(n):
        if not n: return "","",""
        if "," in n:
            p = n.split(",",1); l = p[0].strip(); fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm)>1 else ""), l
        p = n.split(); return p[0], "", p[-1] if len(p)>=2 else ""
