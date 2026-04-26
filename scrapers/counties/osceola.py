"""
Osceola County Arrest Scraper — Daily Reports via DrissionPage.
Source: Osceola County Corrections
URL: https://apps.osceola.org/Apps/CorrectionsReports/Report/Daily/
Method: DrissionPage browser automation (date dropdown + detail pages)
"""
import logging, re, time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://apps.osceola.org/Apps/CorrectionsReports"
DAILY_URL = f"{BASE_URL}/Report/Daily/"
DETAIL_URL_TPL = f"{BASE_URL}/Report/Details/{{}}"
DAYS_BACK = 3

class OsceolaCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Osceola"
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
                    daily = self._scrape_daily_report(page, target_date)
                    all_records.extend(daily)
                    logger.info(f"Osceola {date_str}: {len(daily)} records")
                except Exception as e:
                    logger.warning(f"Osceola {date_str}: {e}")
                time.sleep(1)
            logger.info(f"Osceola: {len(all_records)} total records")
            return all_records
        except Exception as e:
            logger.error(f"Osceola fatal: {e}"); return []
        finally:
            try: page.quit()
            except: pass

    def _setup_browser(self):
        from DrissionPage import ChromiumPage
        co = self._get_browser_options()
        return ChromiumPage(addr_or_opts=co)

    def _scrape_daily_report(self, page, target_date):
        records = []
        date_str = target_date.strftime("%m/%d/%Y")
        page.get(DAILY_URL); time.sleep(2)
        try:
            date_select = page.ele('css:#date')
            if date_select:
                options = page.eles('css:#date option')
                date_found = False
                for opt in options:
                    if self._clean(opt.text) == date_str: date_found = True; break
                if not date_found: return records
                date_select.select(date_str); time.sleep(2)
        except Exception as e:
            logger.warning(f"Date select error: {e}"); return records
        detail_links = page.eles('xpath://table//a[contains(@href, "Details")]')
        inmates_basic = []
        for link in detail_links:
            try:
                href = link.attr("href") or ""
                id_match = re.search(r'/Details/(\d+)', href)
                if not id_match: continue
                inmate_id = id_match.group(1)
                name_text = self._clean(link.text)
                row = link.parent('tag:tr')
                booking_num, dob, agency, charges_summary = "", "", "OCSO", ""
                if row:
                    row_text = self._clean(row.text)
                    bm = re.search(r'Booking #:\s*(\d+)', row_text)
                    if bm: booking_num = bm.group(1)
                    dm = re.search(r'Birthdate:\s*([A-Za-z]+ \d+, \d{4})', row_text)
                    if dm: dob = self._parse_date(dm.group(1))
                    am = re.search(r'By Agency:\s*(\w+)', row_text)
                    if am: agency = am.group(1)
                    cells = row.eles('tag:td')
                    if cells and len(cells) >= 3: charges_summary = self._clean(cells[-1].text)
                inmates_basic.append({"inmate_id": inmate_id, "name": name_text,
                    "booking_number": booking_num, "dob": dob, "agency": agency,
                    "charges_summary": charges_summary, "arrest_date": date_str})
            except: continue
        for idx, basic in enumerate(inmates_basic):
            detail = self._scrape_detail(page, basic["inmate_id"])
            f, m, l = self._pn(basic["name"])
            record = ArrestRecord(County=self.county, Booking_Number=basic["booking_number"],
                Full_Name=basic["name"], First_Name=f, Middle_Name=m, Last_Name=l,
                DOB=detail.get("dob") or basic["dob"], Arrest_Date=basic["arrest_date"],
                Booking_Date=basic["arrest_date"], Agency=basic["agency"], Status="In Custody",
                Facility="Osceola County Jail", Race=detail.get("race",""), Sex=detail.get("sex",""),
                Height=detail.get("height",""), Weight=detail.get("weight",""),
                Charges=basic["charges_summary"], Bond_Amount=detail.get("bond_amount","0"),
                Case_Number=", ".join(detail.get("case_numbers",[])),
                Mugshot_URL=detail.get("mugshot_url",""),
                Detail_URL=DETAIL_URL_TPL.format(basic["inmate_id"]), LastCheckedMode="INITIAL")
            if record.Full_Name and record.Booking_Number: records.append(record)
            time.sleep(0.5)
        return records

    def _scrape_detail(self, page, inmate_id):
        result = {"bond_amount":"0","mugshot_url":"","race":"","sex":"","dob":"","height":"","weight":"","case_numbers":[]}
        try:
            page.get(DETAIL_URL_TPL.format(inmate_id)); time.sleep(1)
            html = page.html or ""
            bm = re.search(r'Total Bond:\s*\$?([\d,]+(?:\.\d{2})?)', html)
            if bm: result["bond_amount"] = bm.group(1).replace(",","")
            for field, pattern in [("race",r'Race:\s*</td>\s*<td[^>]*>\s*(\w+)'),("sex",r'Sex:\s*</td>\s*<td[^>]*>\s*(\w+)'),
                ("dob",r'DOB:\s*</td>\s*<td[^>]*>\s*([\d/]+)'),("height",r'Height:\s*</td>\s*<td[^>]*>\s*([^<]+)'),
                ("weight",r'Weight:\s*</td>\s*<td[^>]*>\s*(\d+)')]:
                match = re.search(pattern, html)
                if match:
                    val = self._clean(match.group(1))
                    if field == "dob": val = self._parse_date(val)
                    result[field] = val
            result["case_numbers"] = list(set(re.findall(r'(\d{4}\s*(?:CF|CT|MM|TR)\s*\d+)', html)))
            for img in page.eles('tag:img'):
                src = img.attr("src") or ""
                if any(kw in src.lower() for kw in ["inmate","photo","image","mugshot"]):
                    if src.startswith("/"): src = f"https://apps.osceola.org{src}"
                    result["mugshot_url"] = src; break
        except Exception as e:
            logger.debug(f"Detail error {inmate_id}: {e}")
        return result

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
    def _parse_date(date_str):
        if not date_str: return ""
        for fmt in ["%m/%d/%Y","%m/%d/%y","%Y-%m-%d","%b %d, %Y"]:
            try: return datetime.strptime(date_str.strip(), fmt).strftime("%m/%d/%Y")
            except: continue
        return date_str
