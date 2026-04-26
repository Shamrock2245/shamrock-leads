"""
Pasco County Arrest Scraper — Cloudflare-Protected Inmate Search.
Source: Pasco County Sheriff's Office
URL: https://www.pascosheriff.com/inmate-search.html
Method: DrissionPage browser (Cloudflare bypass + DOM parsing)
"""
import logging, json, re, time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://www.pascosheriff.com/inmate-search.html"
FACILITY = "Pasco County Jail - Land O' Lakes"

class PascoCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Pasco"
    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("DrissionPage/bs4 not installed"); return []
        co = self._get_browser_options()
        page = ChromiumPage(addr_or_opts=co)
        records = []
        try:
            page.listen.start("json")
            page.get(BASE_URL)
            for i in range(15):
                if any(k in (page.title or "").lower() for k in ["just a moment", "security", "checking"]): time.sleep(3)
                else: break
            time.sleep(5)
            try:
                inp = page.ele("tag:input@@type=text", timeout=5)
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
            if not records:
                soup = BeautifulSoup(page.html, "html.parser")
                for row in soup.select("table tr, .inmate-card, .result-row"):
                    text = row.get_text(" ", strip=True)
                    nm = re.search(r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*[A-Z][a-z]+)", text)
                    if nm:
                        f, m, l = self._pn(nm.group(1))
                        bk = re.search(r"\b(\d{4,})\b", text)
                        bd = re.search(r"\$([\d,]+)", text)
                        records.append(ArrestRecord(County=self.county, Booking_Number=bk.group(1) if bk else "",
                            Full_Name=nm.group(1), First_Name=f, Middle_Name=m, Last_Name=l,
                            Bond_Amount=bd.group(1).replace(",","") if bd else "0",
                            Status="In Custody", Facility=FACILITY, LastCheckedMode="INITIAL"))
            logger.info(f"Pasco: {len(records)} records")
            return records
        except Exception as e:
            logger.error(f"Pasco error: {e}"); return []
        finally:
            try: page.listen.stop(); page.quit()
            except: pass
    def _parse_api(self, data) -> List[ArrestRecord]:
        entries = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for k in ["data","results","inmates","entries","items"]:
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
                Charges=charges, Bond_Amount=str(e.get("bond", e.get("bondAmount","0"))),
                Status="In Custody", Facility=FACILITY, LastCheckedMode="INITIAL"))
        return out
    @staticmethod
    def _pn(n):
        if not n: return "","",""
        if "," in n:
            p = n.split(",",1); l = p[0].strip(); fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm)>1 else ""), l
        p = n.split(); return p[0], "", p[-1] if len(p)>=2 else ""
