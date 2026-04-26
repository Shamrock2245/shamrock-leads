"""
Leon County Arrest Scraper — DNN CMS A-Z Letter Iteration
Source: Leon County Sheriff's Office
URL: https://www.leoncountyso.com/About-us/Departments/Detention-Facility/Inmate-search
Method: requests POST — A-Z last name letter iteration to get all inmates
Proven pattern: swfl-arrest-scrapers/counties/leon/solver.py
"""
import logging
import re
import string
import time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.leoncountyso.com"
# Correct URL path — confirmed live 2026-04-25
SEARCH_URL = f"{BASE_URL}/About-us/Departments/Detention-Facility/Inmate-search"
FACILITY = "Leon County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": f"{BASE_URL}/About-us/Departments/Detention-Facility/Inmate-search",
}


class LeonCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Leon"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            return []

        session = requests.Session()
        session.headers.update(HEADERS)

        # GET the page first to extract ViewState/DNN tokens
        try:
            resp = session.get(SEARCH_URL, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Leon: GET failed: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract all hidden form fields (DNN uses many)
        hidden_fields = {}
        for inp in soup.find_all("input", {"type": "hidden"}):
            name = inp.get("name", "")
            value = inp.get("value", "")
            if name:
                hidden_fields[name] = value

        # Find the DNN module ID for the inmate search
        # The form field names are like: dnn_ctr{MODULE_ID}_View_Textbox_{MODULE_ID}_{FIELD_NUM}
        form = soup.find("form", id=re.compile(r"Form", re.I)) or soup.find("form")
        if not form:
            logger.warning("Leon: no form found, trying browser fallback")
            return self._browser_fallback()

        # Find last name and submit button field names
        last_name_field = None
        submit_field = None
        for inp in form.find_all("input"):
            name = inp.get("name", "")
            itype = inp.get("type", "").lower()
            if itype == "text" and ("last" in name.lower() or "lname" in name.lower() or "textbox" in name.lower()):
                if not last_name_field:
                    last_name_field = name
            elif itype == "submit" or itype == "button":
                submit_field = (name, inp.get("value", "Search"))

        if not last_name_field:
            # Try to find by placeholder or label
            for inp in form.find_all("input", {"type": "text"}):
                placeholder = inp.get("placeholder", "").lower()
                if "last" in placeholder or "name" in placeholder:
                    last_name_field = inp.get("name", "")
                    break

        records = []
        seen = set()

        if last_name_field:
            # A-Z iteration
            for letter in string.ascii_uppercase:
                post_data = dict(hidden_fields)
                post_data[last_name_field] = letter
                if submit_field:
                    post_data[submit_field[0]] = submit_field[1]

                try:
                    resp = session.post(SEARCH_URL, data=post_data, timeout=30)
                    if resp.status_code == 200:
                        batch = self._parse_html(resp.text, seen)
                        records.extend(batch)
                        if batch:
                            logger.debug(f"Leon {letter}: {len(batch)} records")
                    time.sleep(0.3)
                except Exception as e:
                    logger.warning(f"Leon {letter}: {e}")
                    continue
        else:
            # Try empty POST to get all
            post_data = dict(hidden_fields)
            try:
                resp = session.post(SEARCH_URL, data=post_data, timeout=30)
                if resp.status_code == 200:
                    records = self._parse_html(resp.text, seen)
            except Exception as e:
                logger.error(f"Leon: empty POST failed: {e}")

        if not records:
            records = self._browser_fallback()

        logger.info(f"Leon: {len(records)} records")
        return records

    def _parse_html(self, html: str, seen: set) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        records = []

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text(" ").lower()
            if not any(kw in header_text for kw in ["name", "booking", "inmate"]):
                continue
            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                texts = [c.get_text(strip=True) for c in cells]
                full_name = texts[0]
                if not full_name:
                    continue
                booking_num = texts[1] if len(texts) > 1 else ""
                booking_date = texts[2] if len(texts) > 2 else ""
                charges = texts[3] if len(texts) > 3 else ""
                bond_raw = texts[4] if len(texts) > 4 else "0"
                key = booking_num or full_name
                if key in seen:
                    continue
                seen.add(key)
                detail_url = ""
                link = row.find("a", href=True)
                if link:
                    href = link["href"]
                    detail_url = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
                f, m, l = self._pn(full_name)
                bond_amount = self._parse_bond(bond_raw)
                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_num,
                    Full_Name=full_name,
                    First_Name=f, Middle_Name=m, Last_Name=l,
                    Booking_Date=booking_date,
                    Status="In Custody",
                    Facility=FACILITY,
                    Charges=charges,
                    Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                    Detail_URL=detail_url,
                    LastCheckedMode="INITIAL",
                ))
            if records:
                break
        return records

    def _browser_fallback(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
            from bs4 import BeautifulSoup
        except ImportError:
            return []
        opts = ChromiumOptions()
        opts.headless(True)
        opts.set_argument("--no-sandbox")
        opts.set_argument("--disable-dev-shm-usage")
        opts.set_argument("--disable-gpu")
        page = None
        seen = set()
        records = []
        try:
            page = ChromiumPage(addr_or_opts=opts)
            for letter in string.ascii_uppercase[:13]:  # A-M to limit browser time
                page.get(SEARCH_URL)
                page.wait(3)
                try:
                    inputs = page.eles("tag:input")
                    for inp in inputs:
                        if inp.attr("type") == "text":
                            inp.clear()
                            inp.input(letter)
                            break
                    submit = page.ele("css:input[type='submit']") or page.ele("css:button[type='submit']")
                    if submit:
                        submit.click()
                        page.wait(3)
                except Exception:
                    pass
                batch = self._parse_html(page.html, seen)
                records.extend(batch)
        except Exception as e:
            logger.error(f"Leon browser fallback: {e}")
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass
        return records

    @staticmethod
    def _pn(n):
        if not n:
            return "", "", ""
        n = " ".join(n.strip().split())
        if "," in n:
            p = n.split(",", 1)
            l = p[0].strip()
            fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm) > 1 else ""), l
        p = n.split()
        return p[0], (" ".join(p[2:]) if len(p) > 2 else ""), (p[-1] if len(p) >= 2 else "")

    @staticmethod
    def _parse_bond(bond_str):
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
