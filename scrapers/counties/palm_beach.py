"""
Palm Beach County Arrest Scraper — PBSO Booking Blotter.
Source: Palm Beach County Sheriff's Office
URL: https://www.pbso.org/booking-blotter
Method: HTTP requests → JSON/HTML parsing
"""
import logging, re, time, json, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://www.pbso.org"
BLOTTER_URL = f"{BASE_URL}/booking-blotter"
API_PATTERNS = [
    f"{BASE_URL}/api/bookings",
    f"{BASE_URL}/api/v1/bookings",
    f"{BASE_URL}/api/inmates",
    f"{BASE_URL}/BookingBlotter/GetData",
    f"{BASE_URL}/BookingBlotter/Search",
]
FACILITY = "Palm Beach County Jail"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8"}

class PalmBeachCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Palm Beach"

    def scrape(self) -> List[ArrestRecord]:
        records = self._try_api_first()
        if records:
            logger.info(f"Palm Beach (API): {len(records)} records"); return records
        records = self._try_browser()
        logger.info(f"Palm Beach: {len(records)} records")
        return records

    def _http_get(self, url, accept="text/html"):
        headers = dict(HEADERS); headers["Accept"] = accept
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except: return ""

    def _try_api_first(self):
        for api_url in API_PATTERNS:
            try:
                body = self._http_get(api_url, accept="application/json")
                if not body or len(body) < 50: continue
                data = json.loads(body)
                records = self._parse_json(data)
                if records: return records
            except: continue
        html = self._http_get(BLOTTER_URL)
        if not html or len(html) < 500: return []
        return self._parse_blotter_html(html)

    def _try_browser(self):
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
            page.listen.start("json")
            page.get(BLOTTER_URL); time.sleep(5)
            for pkt in page.listen.steps(timeout=15):
                try:
                    body = pkt.response.body if hasattr(pkt, "response") and pkt.response else None
                    if isinstance(body, str) and body.strip().startswith(("{","[")):
                        data = json.loads(body)
                        parsed = self._parse_json(data)
                        if parsed: records.extend(parsed)
                except: pass
            if not records:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page.html, "html.parser")
                records = self._parse_soup(soup)
        except Exception as e:
            logger.error(f"Palm Beach browser: {e}")
        finally:
            try: page.listen.stop(); page.quit()
            except: pass
        return records

    def _parse_json(self, data) -> List[ArrestRecord]:
        entries = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for k in ["data","results","inmates","bookings","records","d","value"]:
                if k in data and isinstance(data[k], list): entries = data[k]; break
        out = []
        for e in entries:
            if not isinstance(e, dict): continue
            name = e.get("inmateFullName", e.get("name", e.get("full_name", e.get("fullName", ""))))
            if not name:
                fn = e.get("firstName", e.get("first_name", ""))
                ln = e.get("lastName", e.get("last_name", ""))
                if fn and ln: name = f"{ln}, {fn}"
            bk = str(e.get("bookingNumber", e.get("booking_number", e.get("id", e.get("bookingId", "")))))
            if not name and not bk: continue
            f, m, l = self._pn(name)
            ch = e.get("charges", e.get("chargeDescription", ""))
            charges = " | ".join(str(c) for c in ch) if isinstance(ch, list) else str(ch) if ch else ""
            bond = e.get("bondAmount", e.get("bond", e.get("totalBond", 0)))
            out.append(ArrestRecord(County=self.county, Booking_Number=bk, Full_Name=name,
                First_Name=f, Middle_Name=m, Last_Name=l,
                DOB=str(e.get("dob", e.get("dateOfBirth", ""))),
                Booking_Date=str(e.get("bookingDate", e.get("booking_date", ""))),
                Race=str(e.get("race", "")), Sex=str(e.get("sex", e.get("gender", ""))),
                Charges=charges, Bond_Amount=str(bond) if bond else "0",
                Status="In Custody", Facility=FACILITY, LastCheckedMode="INITIAL"))
        return out

    def _parse_blotter_html(self, html):
        try: from bs4 import BeautifulSoup
        except ImportError: return []
        soup = BeautifulSoup(html, "html.parser")
        return self._parse_soup(soup)

    def _parse_soup(self, soup):
        records = []
        for row in soup.select("table tbody tr, .booking-row, .inmate-card, .inmate-row"):
            try:
                text = row.get_text(" ", strip=True)
                nm = re.search(r"([A-Z][A-Za-z'-]+,\s*[A-Z][A-Za-z'-]+(?:\s+[A-Z])?)", text)
                if not nm: continue
                name = nm.group(1)
                f, m, l = self._pn(name)
                bk = re.search(r"(\d{6,})", text)
                bd = re.search(r"\$([\d,]+(?:\.\d{2})?)", text)
                records.append(ArrestRecord(County=self.county, Booking_Number=bk.group(1) if bk else "",
                    Full_Name=name, First_Name=f, Middle_Name=m, Last_Name=l,
                    Bond_Amount=bd.group(1).replace(",","") if bd else "0",
                    Status="In Custody", Facility=FACILITY, LastCheckedMode="INITIAL"))
            except: continue
        return records

    @staticmethod
    def _pn(n):
        if not n: return "","",""
        if "," in n:
            p = n.split(",",1); l = p[0].strip(); fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm)>1 else ""), l
        p = n.split(); return p[0], "", p[-1] if len(p)>=2 else ""
