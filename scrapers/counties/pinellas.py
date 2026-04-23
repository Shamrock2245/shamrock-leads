"""
Pinellas County Arrest Scraper — Sheriff's Inmate Booking via DrissionPage.
Source: Pinellas County Sheriff's Office
URL: https://www.pinellassheriff.gov/InmateBooking
Method: DrissionPage browser automation (date search + table parsing)
"""
import logging, re, time
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://www.pinellassheriff.gov"
SEARCH_URL = f"{BASE_URL}/InmateBooking"
DAYS_BACK = 3

class PinellasCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Pinellas"
    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error("DrissionPage not installed"); return []
        page = self._setup_browser()
        all_records = []
        try:
            for days_ago in range(DAYS_BACK):
                target_date = datetime.now() - timedelta(days=days_ago)
                date_str = target_date.strftime("%m/%d/%Y")
                try:
                    daily = self._scrape_date(page, date_str)
                    all_records.extend(daily)
                    logger.info(f"Pinellas {date_str}: {len(daily)} records")
                except Exception as e:
                    logger.warning(f"Pinellas {date_str}: {e}")
                time.sleep(2)
            logger.info(f"Pinellas: {len(all_records)} total records")
            return all_records
        except Exception as e:
            logger.error(f"Pinellas fatal: {e}"); return []
        finally:
            try: page.quit()
            except: pass

    @staticmethod
    def _setup_browser():
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions(); co.auto_port(); co.headless(True)
        co.set_argument("--no-sandbox"); co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--window-size=1920,1080"); co.set_argument("--disable-gpu")
        co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36")
        return ChromiumPage(addr_or_opts=co)

    def _scrape_date(self, page, date_str):
        records = []
        page.get(SEARCH_URL); time.sleep(3)
        try:
            date_input = page.ele('css:input[type="date"]') or page.ele('css:input[name*="date"]') or page.ele('css:#BookingDate')
            if date_input: date_input.clear(); date_input.input(date_str); time.sleep(1)
            search_btn = page.ele('css:button[type="submit"]') or page.ele('css:input[type="submit"]') or page.ele('text:Search')
            if search_btn: search_btn.click(); time.sleep(3)
        except Exception as e:
            logger.warning(f"Form error: {e}"); return records
        rows = page.eles('xpath://table//tr[td]')
        if not rows: rows = page.eles('css:.inmate-row') or page.eles('css:.booking-row')
        for row in rows:
            try:
                record = self._parse_row(row, date_str)
                if record and record.Full_Name and record.Booking_Number: records.append(record)
            except: continue
        return records

    def _parse_row(self, row, date_str):
        cells = row.eles('tag:td')
        if len(cells) < 3: return None
        ct = [self._clean(c.text) for c in cells]
        name = ct[0] if len(ct) > 0 else ""
        booking_num = ct[1] if len(ct) > 1 else ""
        booking_date = ct[2] if len(ct) > 2 else date_str
        charges = ct[3] if len(ct) > 3 else ""
        bond = ct[4] if len(ct) > 4 else "0"
        race = ct[5] if len(ct) > 5 else ""
        sex = ct[6] if len(ct) > 6 else ""
        f, m, l = self._pn(name)
        bond_amount = self._parse_bond(bond)
        detail_url = ""
        try:
            link = row.ele('tag:a')
            if link:
                href = link.attr("href") or ""
                if href and not href.startswith("http"): href = f"{BASE_URL}{href}"
                detail_url = href
        except: pass
        return ArrestRecord(County=self.county, Booking_Number=self._clean(booking_num),
            Full_Name=name, First_Name=f, Middle_Name=m, Last_Name=l,
            Booking_Date=booking_date, Status="In Custody", Facility="Pinellas County Jail",
            Race=self._clean(race), Sex=self._clean(sex), Charges=self._clean(charges),
            Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
            Detail_URL=detail_url, LastCheckedMode="INITIAL")

    @staticmethod
    def _clean(text):
        if not text: return ""
        return " ".join(str(text).strip().split())
    @staticmethod
    def _pn(n):
        if not n: return "","",""
        n = " ".join(n.strip().split())
        if "," in n:
            p = n.split(",",1); l = p[0].strip(); fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm)>1 else ""), l
        p = n.split()
        return p[0], (" ".join(p[2:]) if len(p)>2 else ""), p[-1] if len(p)>=2 else ""
    @staticmethod
    def _parse_bond(bond_str):
        if not bond_str: return 0.0
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND","NONE","N/A","HOLD"]): return 0.0
        try: return float(cleaned)
        except: return 0.0
