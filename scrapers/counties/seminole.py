"""
Seminole County Arrest Scraper — Seminole County Inmate Search.
Source: Seminole County Sheriff's Office
URL: https://www.seminolesheriff.org/inmate-search/
Method: DrissionPage browser (API interception + HTML fallback)
"""
import logging, json, re, time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
SEARCH_URL = "https://www.seminolesheriff.org/inmate-search/"
ALT_URL = "https://apps.seminolesheriff.org/InmateSearch/"
FACILITY = "John E. Polk Correctional Facility"

class SeminoleCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Seminole"
    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error("DrissionPage not installed"); return []
        co = ChromiumOptions(); co.auto_port(); co.headless(True)
        co.set_argument("--no-sandbox"); co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36")
        page = ChromiumPage(addr_or_opts=co)
        records = []
        try:
            for url in [SEARCH_URL, ALT_URL]:
                try:
                    page.listen.start("json")
                    page.get(url); time.sleep(4)
                    try:
                        inp = page.ele("tag:input@@type=text", timeout=3)
                        if inp: inp.input("a"); time.sleep(1)
                        btn = page.ele("tag:button@@text():Search", timeout=3) or page.ele("tag:input@@type=submit", timeout=3)
                        if btn: btn.click(); time.sleep(5)
                    except: pass
                    for pkt in page.listen.steps(timeout=15):
                        try:
                            body = pkt.response.body if hasattr(pkt, "response") and pkt.response else None
                            if isinstance(body, str) and body.strip().startswith(("{","[")): body = json.loads(body)
                            if isinstance(body, (dict, list)): records.extend(self._parse_api(body))
                        except: pass
                    page.listen.stop()
                    if not records:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(page.html, "html.parser")
                        for row in soup.select("table tbody tr"):
                            cells = row.find_all("td")
                            if len(cells) < 2: continue
                            name = cells[0].get_text(strip=True)
                            bk = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                            f, m, l = self._pn(name)
                            bd = re.search(r"\$([\d,]+)", row.get_text())
                            records.append(ArrestRecord(County=self.county, Booking_Number=bk,
                                Full_Name=name, First_Name=f, Middle_Name=m, Last_Name=l,
                                Bond_Amount=bd.group(1).replace(",","") if bd else "0",
                                Status="In Custody", Facility=FACILITY, LastCheckedMode="INITIAL"))
                    if records: break
                except Exception as e:
                    logger.warning(f"Seminole {url}: {e}")
                    try: page.listen.stop()
                    except: pass
            logger.info(f"Seminole: {len(records)} records")
            return records
        except Exception as e:
            logger.error(f"Seminole fatal: {e}"); return []
        finally:
            try: page.quit()
            except: pass

    def _parse_api(self, data) -> List[ArrestRecord]:
        entries = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for k in ["data","results","inmates","entries","items","rows"]:
                if k in data and isinstance(data[k], list): entries = data[k]; break
        out = []
        for e in entries:
            if not isinstance(e, dict): continue
            name = e.get("name", e.get("full_name", e.get("fullName", "")))
            if not name:
                fn, ln = e.get("firstName",""), e.get("lastName","")
                if fn and ln: name = f"{ln}, {fn}"
            bk = str(e.get("bookingNumber", e.get("booking_number", e.get("id", ""))))
            if not name and not bk: continue
            f, m, l = self._pn(name)
            ch = e.get("charges", e.get("charge", ""))
            charges = " | ".join(str(c) for c in ch) if isinstance(ch, list) else str(ch) if ch else ""
            out.append(ArrestRecord(County=self.county, Booking_Number=bk, Full_Name=name,
                First_Name=f, Middle_Name=m, Last_Name=l, DOB=str(e.get("dob","")),
                Booking_Date=str(e.get("bookingDate","")), Race=str(e.get("race","")),
                Sex=str(e.get("sex","")), Charges=charges,
                Bond_Amount=str(e.get("bond", e.get("bondAmount","0"))),
                Status="In Custody", Facility=FACILITY, LastCheckedMode="INITIAL"))
        return out
    @staticmethod
    def _pn(n):
        if not n: return "","",""
        if "," in n:
            p = n.split(",",1); l = p[0].strip(); fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm)>1 else ""), l
        p = n.split(); return p[0], "", p[-1] if len(p)>=2 else ""
