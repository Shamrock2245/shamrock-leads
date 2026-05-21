"""
Marion County Arrest Scraper — ASP.NET HTML Form (Recent Bookings).
Source: Marion County Sheriff's Office
URL: https://jail.marionso.com/
Method: requests + BeautifulSoup — POST empty search to get recent bookings
"""
import logging
import re
import time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://jail.marionso.com"
SEARCH_URL = f"{BASE_URL}/"
FACILITY = "Marion County Jail"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": BASE_URL,
    "DNT": "1",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}
IMPERSONATE = "chrome131"


class MarionCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Marion"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed"); raise

        session = cffi_requests.Session()

        # Step 1: GET the search page
        try:
            resp = session.get(SEARCH_URL, headers=HEADERS, timeout=30, impersonate=IMPERSONATE)
            time.sleep(1)  # Rate limit
            if resp.status_code != 200:
                raise Exception(f"{resp.status_code} Client Error")
        except Exception as e:
            logger.error(f"Marion: failed to load page: {e}"); raise

        soup = BeautifulSoup(resp.text, "html.parser")

        def _get_hidden(name):
            el = soup.find("input", {"name": name})
            return el["value"] if el and el.get("value") else ""

        # Step 2: POST empty search (shows recent bookings)
        # Confirmed field names from live form inspection: txtLname, txtFName, btnSearch
        post_data = {
            "__VIEWSTATE": _get_hidden("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _get_hidden("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _get_hidden("__EVENTVALIDATION"),
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "txtLname": "",
            "txtFName": "",
            "btnSearch": "Search",
        }

        try:
            resp2 = session.post(SEARCH_URL, data=post_data, headers=HEADERS, timeout=60, impersonate=IMPERSONATE)
            if resp2.status_code != 200:
                raise Exception(f"{resp2.status_code} Server Error")
        except Exception as e:
            logger.error(f"Marion: POST failed: {e}"); raise

        soup2 = BeautifulSoup(resp2.text, "html.parser")
        records = []

        # Find results table
        table = None
        for t in soup2.find_all("table"):
            header_text = t.get_text(" ").lower()
            if any(kw in header_text for kw in ["name", "booking", "inmate", "arrest"]):
                rows = t.find_all("tr")
                if len(rows) > 1:
                    table = t
                    break

        if not table:
            logger.warning("Marion: no data table found")
            return []

        rows = table.find_all("tr")[1:]
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if not any(texts):
                continue

            # Marion columns: Booking#, Photo, InmateID, LastName, FirstName, Middle, Suffix, DOB, Sex, Race, BookingDate, ReleaseDate, InCustody
            booking_num = texts[0] if len(texts) > 0 else ""
            inmate_id = texts[2] if len(texts) > 2 else ""
            last_name = texts[3] if len(texts) > 3 else ""
            first_name = texts[4] if len(texts) > 4 else ""
            middle_name = texts[5] if len(texts) > 5 else ""
            dob = texts[7] if len(texts) > 7 else ""
            sex = texts[8] if len(texts) > 8 else ""
            race = texts[9] if len(texts) > 9 else ""
            booking_date = texts[10] if len(texts) > 10 else ""
            in_custody = texts[12] if len(texts) > 12 else "Y"
            full_name = f"{last_name}, {first_name} {middle_name}".strip().rstrip(",")
            charges = ""
            bond_raw = "0"

            detail_url = ""
            link = row.find("a", href=True)
            if link:
                href = link["href"]
                if not href.startswith("http"):
                    href = f"{BASE_URL}/{href.lstrip('/')}"
                detail_url = href

            status = "In Custody" if in_custody.upper() in ("Y", "YES", "1") else "Released"

            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=self._clean(booking_num),
                Person_ID=inmate_id,
                Full_Name=full_name,
                First_Name=first_name,
                Middle_Name=middle_name,
                Last_Name=last_name,
                DOB=self._clean(dob),
                Booking_Date=self._clean(booking_date),
                Status=status,
                        Release_Date="",
                Facility=FACILITY,
                Race=self._clean(race),
                Sex=self._clean(sex)[:1].upper() if sex else "",
                Charges=charges,
                Bond_Amount="0",
                Detail_URL=detail_url,
                LastCheckedMode="INITIAL",
            ))

        logger.info(f"Marion: {len(records)} records")
        return records

    @staticmethod
    def _clean(text):
        if not text:
            return ""
        return " ".join(str(text).strip().split())

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
        return p[0], (" ".join(p[2:]) if len(p) > 2 else ""), p[-1] if len(p) >= 2 else ""

    @staticmethod
    def _parse_bond(bond_str):
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
