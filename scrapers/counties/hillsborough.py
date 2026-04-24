"""
Hillsborough County Arrest Scraper — HCSO Arrest Inquiry Portal.
Source: Hillsborough County Sheriff's Office
URL: https://webapps.hcso.tampa.fl.us/arrestinquiry/
Method: DrissionPage browser automation (login + reCAPTCHA + paginated table)
Requires env vars: HCSO_EMAIL, HCSO_PASSWORD
"""
import logging, os, re, time
from datetime import datetime, timedelta, timezone
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
LOGIN_URL = "https://webapps.hcso.tampa.fl.us/arrestinquiry/Account/Login"
SEARCH_URL = "https://webapps.hcso.tampa.fl.us/arrestinquiry/Home/Search"
DAYS_BACK = 3
MAX_PAGES = 20

class HillsboroughCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Hillsborough"

    def scrape(self) -> List[ArrestRecord]:
        hcso_email = os.getenv("HCSO_EMAIL")
        hcso_password = os.getenv("HCSO_PASSWORD")
        if not hcso_email or not hcso_password:
            logger.warning("HCSO_EMAIL / HCSO_PASSWORD not set"); return []
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("DrissionPage or bs4 not installed"); return []
        page = self._setup_browser()
        all_records = []
        try:
            if not self._login(page, hcso_email, hcso_password):
                logger.error("Hillsborough: login failed"); return []
            if not self._perform_search(page):
                logger.warning("Hillsborough: no search results"); return []
            for page_num in range(1, MAX_PAGES + 1):
                soup = BeautifulSoup(page.html, "html.parser")
                page_records = self._parse_results_table(soup)
                if not page_records: break
                all_records.extend(page_records)
                try:
                    next_btn = page.ele("text:Next >", timeout=2)
                    if next_btn:
                        btn_class = next_btn.attr("class") or ""
                        if "disabled" in btn_class: break
                        next_btn.click(); time.sleep(3)
                    else: break
                except Exception: break
            logger.info(f"Hillsborough: {len(all_records)} records")
            return all_records
        except Exception as e:
            logger.error(f"Hillsborough fatal: {e}"); return []
        finally:
            try: page.quit()
            except: pass

    @staticmethod
    def _setup_browser():
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions(); co.auto_port(); co.headless(True)
        co.set_argument("--no-sandbox"); co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-gpu"); co.set_argument("--window-size=1920,1080")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36")
        return ChromiumPage(addr_or_opts=co)

    def _login(self, page, email, password):
        page.get(LOGIN_URL); time.sleep(3)
        email_field = page.ele("#Email", timeout=10)
        if not email_field: return False
        email_field.clear(); email_field.input(email)
        pwd_field = page.ele("#Password", timeout=5)
        if not pwd_field: return False
        pwd_field.clear(); pwd_field.input(password)
        try:
            remember = page.ele("#RememberMe", timeout=3)
            if remember: remember.click()
        except: pass
        try:
            recaptcha_iframe = page.ele("tag:iframe@@title=reCAPTCHA", timeout=5)
            if recaptcha_iframe:
                checkbox = recaptcha_iframe.ele("tag:div@@class:recaptcha-checkbox-border", timeout=5)
                if checkbox: checkbox.click(); time.sleep(3)
        except: pass
        time.sleep(2)
        login_btn = page.ele("tag:button@@text():Log in", timeout=5) or page.ele("tag:input@@type=submit", timeout=3)
        if login_btn: login_btn.click()
        else: pwd_field.input("\n")
        time.sleep(5)
        html = page.html
        if "Log out" in html or "Welcome" in html or "Search" in html: return True
        return "arrestinquiry" in page.url.lower()

    def _perform_search(self, page):
        page.get(SEARCH_URL); time.sleep(3)
        end_date = datetime.now(); start_date = end_date - timedelta(days=DAYS_BACK)
        start_field = page.ele("#BeginDate", timeout=5)
        if start_field: start_field.clear(); start_field.input(start_date.strftime("%m/%d/%Y"))
        end_field = page.ele("#EndDate", timeout=5)
        if end_field: end_field.clear(); end_field.input(end_date.strftime("%m/%d/%Y"))
        search_btn = page.ele("tag:button@@text():Search", timeout=5) or page.ele("#searchButton", timeout=3)
        if search_btn: search_btn.click()
        elif end_field: end_field.input("\n")
        time.sleep(5)
        return "table-striped" in page.html or "Booking Name" in page.html

    def _parse_results_table(self, soup):
        records = []
        results_table = soup.find("table", class_="table-striped")
        if not results_table: return records
        tbody = results_table.find("tbody") or results_table
        all_rows = tbody.find_all("tr", recursive=False)
        i = 0
        while i < len(all_rows):
            try:
                row = all_rows[i]; cells = row.find_all("td", recursive=False)
                if len(cells) >= 5:
                    name_link = cells[0].find("a")
                    if name_link:
                        record = self._parse_inmate_block(all_rows, i, cells, name_link)
                        if record: records.append(record)
                        i += 4; continue
                i += 1
            except Exception: i += 1
        return records

    def _parse_inmate_block(self, all_rows, i, cells, name_link):
        full_name = name_link.get_text(strip=True)
        first_name, middle_name, last_name = self._parse_name(full_name)
        href = name_link.get("href", "")
        if href and not href.startswith("http"): href = "https://webapps.hcso.tampa.fl.us" + href
        booking_number = cells[1].get_text(strip=True)
        demo = cells[4].get_text(strip=True)
        demo_parts = [p.strip() for p in demo.split("/")]
        race = demo_parts[0] if len(demo_parts) >= 1 else ""
        sex = demo_parts[1] if len(demo_parts) >= 2 else ""
        dob = demo_parts[3] if len(demo_parts) >= 4 else ""
        address = ""
        if i + 1 < len(all_rows):
            for cell in all_rows[i + 1].find_all("td"):
                text = cell.get_text(strip=True)
                if text.startswith("ADDRESS:"): address = text.replace("ADDRESS:", "").strip()
        booking_date, arrest_date, status = "", "", "In Custody"
        if i + 2 < len(all_rows):
            for cell in all_rows[i + 2].find_all("td"):
                text = cell.get_text(strip=True)
                if text.startswith("ARREST DATE:"): arrest_date = text.replace("ARREST DATE:", "").strip()
                elif text.startswith("BOOKING DATE:"): booking_date = text.replace("BOOKING DATE:", "").strip()
                elif text.startswith("RELEASE DATE:"):
                    release = text.replace("RELEASE DATE:", "").strip()
                    if release: status = "Released"
        charges_list, total_bond, case_number = [], 0.0, ""
        if i + 3 < len(all_rows):
            nested = all_rows[i + 3].find("table")
            if nested:
                for cr in nested.find_all("tr"):
                    cc = cr.find_all("td")
                    if len(cc) >= 2:
                        desc = cc[1].get_text(strip=True)
                        if desc and "Charge Type" not in desc: charges_list.append(desc)
                        if len(cc) >= 5:
                            bond_text = cc[4].get_text(strip=True)
                            try: total_bond += float(bond_text.replace("$","").replace(",",""))
                            except: pass
                        if len(cc) >= 4 and not case_number:
                            cn = cc[3].get_text(strip=True)
                            if cn and "-" in cn: case_number = cn
        if not booking_number: return None
        return ArrestRecord(County=self.county, Booking_Number=booking_number,
            Full_Name=full_name, First_Name=first_name, Middle_Name=middle_name, Last_Name=last_name,
            Booking_Date=booking_date, Arrest_Date=arrest_date, Status=status, Facility="Falkenburg Road Jail",
            Race=race, Sex=sex, DOB=dob, Address=address, Charges=" | ".join(charges_list),
            Bond_Amount=str(total_bond) if total_bond > 0 else "0", Case_Number=case_number,
            Detail_URL=href, LastCheckedMode="INITIAL")

    @staticmethod
    def _parse_name(name_str):
        if not name_str: return "", "", ""
        if "," in name_str:
            parts = name_str.split(",", 1); last_name = parts[0].strip()
            first_middle = parts[1].strip() if len(parts) > 1 else ""
            name_parts = first_middle.split()
            return name_parts[0] if name_parts else "", " ".join(name_parts[1:]) if len(name_parts) > 1 else "", last_name
        parts = name_str.split()
        return parts[0], "", parts[-1] if len(parts) >= 2 else ""
