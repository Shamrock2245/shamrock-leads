"""
Pinellas County Arrest Scraper — Sheriff's Inmate Booking via DrissionPage.
Source: Pinellas County Sheriff's Office
URL: https://www.pinellassheriff.gov/InmateBooking/
Method: DrissionPage browser automation (date search + table parsing)
"""
import logging, re, time
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
BASE_URL = "https://www.pinellassheriff.gov"
SEARCH_URL = f"{BASE_URL}/InmateBooking/"
DAYS_BACK = 14  # Extended from 3 to 14 (date-search based; 90 days would be too slow)

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

    def _setup_browser(self):
        from DrissionPage import ChromiumPage
        co = self._get_browser_options()
        return ChromiumPage(addr_or_opts=co)

    def _scrape_date(self, page, date_str):
        """Use requests+BeautifulSoup against the ASP.NET InmateBooking endpoint."""
        import requests
        from bs4 import BeautifulSoup
        records = []
        s = requests.Session()
        s.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36',
            'Referer': SEARCH_URL,
        })
        # GET to harvest ViewState tokens
        try:
            r0 = s.get(SEARCH_URL, timeout=20)
            r0.raise_for_status()
        except Exception as e:
            logger.error(f'Pinellas GET failed: {e}'); return records
        soup0 = BeautifulSoup(r0.text, 'html.parser')
        def _gh(n):
            el = soup0.find('input', {'name': n})
            return el['value'] if el and el.get('value') else ''
        post_data = {
            '_TSM_HiddenField_': _gh('_TSM_HiddenField_'),
            '__EVENTTARGET': '', '__EVENTARGUMENT': '', '__LASTFOCUS': '',
            '__VIEWSTATE': _gh('__VIEWSTATE'),
            '__VIEWSTATEGENERATOR': _gh('__VIEWSTATEGENERATOR'),
            '__EVENTVALIDATION': _gh('__EVENTVALIDATION'),
            '__ncforminfo': _gh('__ncforminfo'),
            'txtLastName': '', 'txtFirstName': '', 'drpRace': 'Any', 'drpSex': 'Any',
            'txtDocketNumber': '', 'txtBookingDate': date_str,
            'drpAgencies': '', 'drpCharge': '', 'drpChargeType': '',
            'drpSortBy': 'Name', 'drpPageSize': '100',
            'btnSearch': 'Search', 'hdnType': '',
        }
        try:
            r1 = s.post(SEARCH_URL, data=post_data, timeout=30)
            r1.raise_for_status()
        except Exception as e:
            logger.error(f'Pinellas POST failed for {date_str}: {e}'); return records
        soup1 = BeautifulSoup(r1.text, 'html.parser')
        # Find the results table — it has name/docket/booking columns
        result_table = None
        for table in soup1.find_all('table'):
            trows = table.find_all('tr')
            if len(trows) > 2:
                hdrs = [th.get_text(strip=True).lower() for th in trows[0].find_all(['th', 'td'])]
                if any(k in ' '.join(hdrs) for k in ['name', 'docket', 'booking']):
                    result_table = table
                    break
        if not result_table:
            logger.debug(f'Pinellas: no results table for {date_str}')
            return records
        trows = result_table.find_all('tr')
        hdrs = [th.get_text(strip=True).lower() for th in trows[0].find_all(['th', 'td'])]
        col = {h: i for i, h in enumerate(hdrs)}
        def _gc(cells, keys):
            for k in keys:
                for h, i in col.items():
                    if k in h and i < len(cells):
                        return cells[i].get_text(strip=True)
            return ''
        for row in trows[1:]:
            cells = row.find_all(['td', 'th'])
            if not cells or len(cells) < 2: continue
            name = _gc(cells, ['name'])
            if not name: continue
            docket = _gc(cells, ['docket'])
            race = _gc(cells, ['race'])
            sex = _gc(cells, ['sex', 'gender'])
            charge = _gc(cells, ['charge', 'offense'])
            agency = _gc(cells, ['agency'])
            arrest_type = _gc(cells, ['arrest type', 'type'])
            name_parts = name.split(',', 1)
            last = name_parts[0].strip()
            first = name_parts[1].strip() if len(name_parts) > 1 else ''
            records.append(ArrestRecord(
                County=self.county, Booking_Number=docket, Full_Name=name,
                First_Name=first, Last_Name=last, Booking_Date=date_str,
                Status='In Custody', Facility='Pinellas County Jail',
                Race=race, Sex=sex, Charges=charge,
                Bond_Amount='0', Detail_URL=SEARCH_URL, LastCheckedMode='INITIAL'
            ))
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
                        DOB="",
            Booking_Date=booking_date, Status="In Custody",
                        Release_Date="", Facility="Pinellas County Jail",
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
