"""
Generic Adaptive Scraper — For counties with unknown/custom platforms.

This base handles the common pattern of:
1. Load the county's jail/inmate page
2. Try to find and parse HTML tables, card layouts, or list elements
3. If a search form exists, iterate A-Z
4. Extract names, booking numbers, bonds, charges via heuristics
"""

import logging, re, string, time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class GenericAdaptiveScraper(BaseScraper):
    """Adaptive scraper for counties with unknown/custom inmate search platforms."""

    SEARCH_URL: str = ""
    COUNTY_NAME: str = ""
    FACILITY_NAME: str = ""

    @property
    def county(self) -> str:
        return self.COUNTY_NAME

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed"); return []

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        try:
            resp = session.get(self.SEARCH_URL, timeout=30, allow_redirects=True)
            if resp.status_code != 200:
                logger.warning(f"{self.county}: HTTP {resp.status_code}")
                return []
        except Exception as e:
            logger.error(f"{self.county}: {e}"); return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # Try iframe
        iframe = soup.find("iframe")
        if iframe and iframe.get("src"):
            iurl = iframe["src"]
            if not iurl.startswith("http"):
                iurl = self.SEARCH_URL.rsplit("/",1)[0] + "/" + iurl.lstrip("/")
            try:
                r2 = session.get(iurl, timeout=30)
                if r2.status_code == 200:
                    soup = BeautifulSoup(r2.text, "html.parser")
            except Exception: pass

        records = self._parse_tables(soup)

        # Try form A-Z
        if not records:
            form = soup.find("form")
            if form:
                records = self._form_az(session, soup, form)

        # Try cards/divs
        if not records:
            records = self._parse_cards(soup)

        logger.info(f"✅ {self.county}: {len(records)} records")
        return records

    def _form_az(self, session, soup, form) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        action = form.get("action", self.SEARCH_URL)
        if not action.startswith("http"):
            action = self.SEARCH_URL.rsplit("/",1)[0] + "/" + action.lstrip("/")
        hf = {}
        for inp in form.find_all("input", type="hidden"):
            n, v = inp.get("name",""), inp.get("value","")
            if n: hf[n] = v
        # Guess name field
        name_fields = ["LastName","last_name","lastName","lname","searchLastName","name"]
        name_field = "LastName"
        for inp in form.find_all("input", type="text"):
            n = inp.get("name","")
            if n: name_field = n; break

        seen, all_r = set(), []
        for letter in string.ascii_uppercase:
            try:
                r = session.post(action, data={**hf, name_field: letter}, timeout=30)
                if r.status_code == 200:
                    for rec in self._parse_tables(BeautifulSoup(r.text, "html.parser")):
                        k = rec.Booking_Number or rec.Full_Name
                        if k and k not in seen: seen.add(k); all_r.append(rec)
            except Exception: pass
            time.sleep(0.3)
        return all_r

    def _parse_tables(self, soup) -> List[ArrestRecord]:
        records = []
        for table in soup.find_all("table"):
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) < 2: continue
                name, bk, dt = "", "", ""
                for c in cells:
                    t = c.get_text(strip=True)
                    if "," in t and not name and len(t) > 3 and not t.replace(",","").replace(" ","").isdigit():
                        name = t
                    elif re.match(r"^\d{4,}$", t) and not bk: bk = t
                    elif re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", t) and not dt: dt = t
                if not name and not bk: continue
                rt = row.get_text(" ", strip=True)
                bm = re.search(r"\$([\d,]+\.?\d*)", rt)
                f, m, l = self._pn(name)
                records.append(ArrestRecord(County=self.county, Booking_Number=bk,
                    Full_Name=name, First_Name=f, Middle_Name=m, Last_Name=l,
                    Booking_Date=dt, Bond_Amount=bm.group(1).replace(",","") if bm else "0",
                    Status="In Custody", Facility=self.FACILITY_NAME, LastCheckedMode="INITIAL"))
        return records

    def _parse_cards(self, soup) -> List[ArrestRecord]:
        records = []
        for elem in soup.find_all(["div","article","li","section"],
                class_=re.compile(r"inmate|roster|card|entry|booking|arrest|result", re.I)):
            text = elem.get_text(" ", strip=True)
            if len(text) < 10: continue
            nm = re.search(r"([A-Z][A-Za-z'-]+),\s*([A-Z][A-Za-z'-]+)", text)
            if nm:
                bk = re.search(r"\b(\d{4,})\b", text)
                bd = re.search(r"\$([\d,]+)", text)
                records.append(ArrestRecord(County=self.county,
                    Booking_Number=bk.group(1) if bk else "",
                    Full_Name=f"{nm.group(1)}, {nm.group(2)}",
                    First_Name=nm.group(2), Last_Name=nm.group(1),
                    Bond_Amount=bd.group(1).replace(",","") if bd else "0",
                    Status="In Custody", Facility=self.FACILITY_NAME, LastCheckedMode="INITIAL"))
        return records

    @staticmethod
    def _pn(n):
        if not n: return "","",""
        if "," in n:
            p = n.split(",",1); l = p[0].strip(); fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm)>1 else ""), l
        p = n.split(); return p[0], "", p[-1] if len(p)>=2 else ""
