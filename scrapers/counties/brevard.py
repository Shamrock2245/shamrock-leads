"""
Brevard County Arrest Scraper — inmatesearch.brevardsheriff.org
Source: Brevard County Sheriff's Office
URL: https://inmatesearch.brevardsheriff.org/Results
Method: requests POST (date-range form) + DrissionPage fallback for JS rendering
Proven pattern from: swfl-arrest-scrapers/counties/brevard/solver.py
"""
import logging
import re
import time
import os
from datetime import datetime, timedelta
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

from curl_cffi import requests as cffi_requests
logger = logging.getLogger(__name__)

BASE_URL = "https://inmatesearch.brevardsheriff.org"
SEARCH_URL = f"{BASE_URL}/Results"
FACILITY = "Brevard County Jail Complex"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL,
}

class BrevardCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Brevard"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            raise

        session = cffi_requests.Session()
        session.headers.update(HEADERS)

        # Load home page first (sets session cookies / anti-bot tokens)
        try:
            session.get(BASE_URL, timeout=20, impersonate=IMPERSONATE, verify=False)
        except requests.RequestException:
            pass

        to_date = datetime.now()
        from_date = to_date - timedelta(days=7)
        form_data = {
            "SearchForm.FromDate": from_date.strftime("%Y-%m-%d"),
            "SearchForm.ToDate": to_date.strftime("%Y-%m-%d"),
            "SearchForm.LastName": "",
            "SearchForm.FirstName": "",
            "SearchForm.SubjectNumber": "",
            "SearchForm.BookingNumber": "",
            "SearchForm.Facility": "",
            "SearchForm.PageSize": "100",
            "SearchForm.PageNumber": "1",
        }

        records = []
        seen = set()
        page_num = 1

        while page_num <= 25:
            try:
                if page_num == 1:
                    resp = session.post(SEARCH_URL, data=form_data, timeout=30, impersonate=IMPERSONATE, verify=False)
                else:
                    form_data["SearchForm.PageNumber"] = str(page_num)
                    resp = session.post(SEARCH_URL, data=form_data, timeout=30, impersonate=IMPERSONATE, verify=False)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"Brevard page {page_num}: {e}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")

            # Check if page is JS shell (Angular/React — only ~7KB)
            if len(resp.text) < 10000 and not soup.find("table"):
                # Fall back to DrissionPage
                logger.info("Brevard: JS-rendered page detected, switching to DrissionPage")
                return self._scrape_drission()

            batch = self._parse_page(soup, seen)
            if not batch:
                break
            records.extend(batch)

            # Check for next page
            next_link = soup.find("a", string=re.compile(r"Next|›|»", re.I))
            if not next_link:
                break
            page_num += 1
            time.sleep(0.4)

        if not records:
            return self._scrape_drission()

        logger.info(f"Brevard: {len(records)} records")
        return records

    def _scrape_drission(self) -> List[ArrestRecord]:
        """DrissionPage fallback for JS-rendered content."""
        try:
            from DrissionPage import ChromiumPage
        except ImportError:
            logger.warning("Brevard: DrissionPage not available")
            return []

        co = self._get_browser_options()

        page = ChromiumPage(addr_or_opts=co)
        records = []
        seen = set()

        try:
            page.get(BASE_URL)
            time.sleep(5)

            # Fill date fields
            try:
                to_el = page.ele("#SearchForm_ToDate", timeout=5)
                max_date_str = to_el.attr("max") if to_el else None
                
                if max_date_str:
                    to_date = datetime.strptime(max_date_str, "%Y-%m-%d")
                else:
                    to_date = datetime.now() - timedelta(days=1)
                    
                from_date = to_date - timedelta(days=7)
                from_date_str = from_date.strftime("%Y-%m-%d")
                to_date_str = to_date.strftime("%Y-%m-%d")
                
                logger.info(f"Brevard: entering search dates From={from_date_str}, To={to_date_str}")
                page.run_js(f"document.getElementById('SearchForm_FromDate').value = '{from_date_str}';")
                page.run_js(f"document.getElementById('SearchForm_ToDate').value = '{to_date_str}';")
                
                submit = page.ele("tag:button@@text():Search", timeout=5)
                if submit:
                    submit.click()
                    time.sleep(5)
            except Exception as fe:
                logger.warning(f"Brevard form entry failed: {fe}")

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(page.html, "html.parser")
            records = self._parse_page(soup, seen)

        except Exception as e:
            logger.warning(f"Brevard DrissionPage: {e}")
            raise
        finally:
            try:
                page.quit()
            except Exception:
                pass

        logger.info(f"Brevard (DrissionPage): {len(records)} records")
        return records

    def _parse_page(self, soup, seen: set) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        records = []

        # Try table rows first
        for table in soup.find_all("table"):
            rows = table.find_all("tr")[1:]  # skip header
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                texts = [c.get_text(strip=True) for c in cells]
                full_name = texts[0] if texts else ""
                booking_num = texts[1] if len(texts) > 1 else ""
                booking_date = texts[2] if len(texts) > 2 else ""
                charges = texts[3] if len(texts) > 3 else ""
                bond_raw = texts[4] if len(texts) > 4 else "0"

                key = (full_name, booking_num)
                if not full_name or key in seen:
                    continue
                seen.add(key)

                link = row.find("a", href=True)
                detail_url = ""
                if link:
                    h = link["href"]
                    detail_url = h if h.startswith("http") else f"{BASE_URL}{h}"

                f, m, l = _pn(full_name)
                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_num,
                    Full_Name=full_name,
                    First_Name=f, Middle_Name=m, Last_Name=l,
                        DOB="",
                    Booking_Date=booking_date,
                    Status="In Custody",
                        Release_Date="", Facility=FACILITY,
                    Charges=charges,
                    Bond_Amount=_parse_bond(bond_raw),
                    Detail_URL=detail_url,
                    LastCheckedMode="INITIAL",
                ))

        # Try detail links if no table
        if not records:
            for link in soup.find_all("a", href=re.compile(r"/Details/|/Booking/", re.I)):
                href = link.get("href", "")
                detail_url = href if href.startswith("http") else f"{BASE_URL}{href}"
                key = detail_url
                if key in seen:
                    continue
                seen.add(key)
                row = link.find_parent("tr") or link.find_parent("div")
                cells = row.find_all("td") if row else []
                texts = [c.get_text(strip=True) for c in cells]
                full_name = texts[0] if texts else link.get_text(strip=True)
                if not full_name:
                    continue
                f, m, l = _pn(full_name)
                records.append(ArrestRecord(
                    County=self.county,
                    Full_Name=full_name,
                    First_Name=f, Middle_Name=m, Last_Name=l,
                        DOB="",
                    Status="In Custody",
                        Release_Date="", Facility=FACILITY,
                    Detail_URL=detail_url,
                    LastCheckedMode="INITIAL",
                ))

        return records

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
    return p[0], (" ".join(p[1:-1]) if len(p) > 2 else ""), (p[-1] if len(p) >= 2 else "")

def _parse_bond(bond_str):
    if not bond_str:
        return "0"
    cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
    if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD", "NO"]):
        return "0"
    try:
        return str(float(cleaned))
    except (ValueError, TypeError):
        return "0"
