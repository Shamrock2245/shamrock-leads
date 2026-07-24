"""
Nassau County Arrest Scraper — New World InmateInquiry (Server-Rendered HTML)
Source: Nassau County Sheriff's Office
URL: https://dssinmate.nassauso.com/NewWorld.InmateInquiry/nassau
Method: requests GET — server-rendered HTML listing + detail pages
Fields: Name, Subject Number, DOB, Gender, Height, Weight, Booking Date, Bond Amount
"""

import logging
import re
import time
from typing import List
from urllib.parse import urljoin, urlparse, parse_qs

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

from curl_cffi import requests as cffi_requests
logger = logging.getLogger(__name__)

BASE_URL = "https://dssinmate.nassauso.com/NewWorld.InmateInquiry/nassau"
FACILITY = "Nassau County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
IMPERSONATE = "chrome131"


MAX_PAGES = 10
MAX_DETAILS = 200

class NassauCountyScraper(BaseScraper):
    """Nassau County (FL) — New World InmateInquiry (Fernandina Beach area)"""

    @property
    def county(self) -> str:
        return "Nassau"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            raise

        session = cffi_requests.Session()
        session.verify = False
        session.headers.update(HEADERS)
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Phase 1: Collect inmate links from listing pages
        inmate_links = []
        params = {"InCustody": "True"}

        for page_num in range(1, MAX_PAGES + 1):
            try:
                if page_num > 1:
                    params["Page"] = str(page_num)

                resp = session.get(BASE_URL, params=params, timeout=30, impersonate=IMPERSONATE, verify=False)
                if resp.status_code != 200:
                    logger.warning(f"Nassau page {page_num}: HTTP {resp.status_code}")
                    break

                soup = BeautifulSoup(resp.text, "html.parser")

                page_links = []
                for a_tag in soup.find_all("a", href=True):
                    href = a_tag["href"]
                    if "/Inmate/Detail/" in href:
                        name = a_tag.get_text(strip=True)
                        if name and name not in ("Back to Search",):
                            full_url = href if href.startswith("http") else urljoin(BASE_URL + "/", href)
                            page_links.append((name, full_url))

                if not page_links:
                    break

                inmate_links.extend(page_links)
                logger.debug(f"Nassau page {page_num}: {len(page_links)} inmates")

                next_link = soup.find("a", string=re.compile(r"Next", re.I))
                if not next_link or not next_link.get("href"):
                    break

                next_href = next_link["href"]
                if next_href.startswith("http"):
                    parsed = urlparse(next_href)
                    next_params = parse_qs(parsed.query)
                    params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v
                             for k, v in next_params.items()}
                else:
                    break

                time.sleep(0.5)

            except Exception as e:
                logger.warning(f"Nassau page {page_num} error: {e}")
                break

        if not inmate_links:
            logger.warning("Nassau: no inmate links found")
            return []

        logger.info(f"Nassau: found {len(inmate_links)} inmate links, fetching details...")

        # Phase 2: Fetch detail pages
        records = []
        seen = set()
        detail_count = 0

        for name, detail_url in inmate_links:
            if detail_count >= MAX_DETAILS:
                break
            if detail_url in seen:
                continue
            seen.add(detail_url)

            try:
                detail_count += 1
                resp = session.get(detail_url, timeout=20, impersonate=IMPERSONATE, verify=False)
                if resp.status_code != 200:
                    continue

                record = self._parse_detail_page(resp.text, name)
                if record:
                    records.append(record)

                if detail_count % 10 == 0:
                    time.sleep(1)
                else:
                    time.sleep(0.3)

            except Exception as e:
                logger.debug(f"Nassau detail error for {name}: {e}")

        logger.info(f"Nassau: {len(records)} records from {detail_count} detail pages")
        return records

    def _parse_detail_page(self, html: str, fallback_name: str) -> ArrestRecord | None:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return None

        soup = BeautifulSoup(html, "html.parser")
        fields = {}

        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                fields[dt.get_text(strip=True).lower()] = dd.get_text(strip=True)

        for li in soup.find_all("li"):
            text = li.get_text(separator="\n", strip=True)
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if len(lines) >= 2:
                key = lines[0].lower()
                if key not in fields:
                    fields[key] = lines[1]

        title_tag = soup.find("title")
        title_name = ""
        if title_tag:
            m = re.search(r"Inmate Detail\s*-\s*(.+)", title_tag.get_text(strip=True))
            if m:
                title_name = m.group(1).strip()

        full_name = fields.get("name", title_name or fallback_name)
        if not full_name:
            return None

        first, middle, last = self._parse_name(full_name)

        booking_num = fields.get("subject number", "")
        for h_tag in soup.find_all(["h2", "h3"]):
            m = re.match(r"Booking\s+(.+)", h_tag.get_text(strip=True))
            if m:
                booking_num = m.group(1).strip()
                break

        dob = fields.get("date of birth", "")
        gender = fields.get("gender", fields.get("sex", ""))
        height = fields.get("height", "")
        weight = fields.get("weight", "").replace(" lbs", "").replace(" lb", "")
        booking_date = fields.get("booking date", "")
        release_date = fields.get("release date", "")
        bond_str = fields.get("total bond amount", fields.get("bond amount", "0"))
        race = fields.get("race", "")

        status = "In Custody"
        if release_date and release_date.strip():
            status = "Released"

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_num,
            Full_Name=full_name,
            First_Name=first, Middle_Name=middle, Last_Name=last,
            DOB=dob,
            Booking_Date=booking_date,
            Status=status,
                        Release_Date="",
            Facility=FACILITY,
            Race=race,
            Sex=gender,
            Height=height,
            Weight=weight,
            Bond_Amount=str(self._parse_bond(bond_str)),
            Detail_URL=BASE_URL,
                        LastCheckedMode="INITIAL",
        )

    @staticmethod
    def _parse_name(name: str):
        if not name:
            return "", "", ""
        name = re.sub(r"\s+(Junior|Senior|Second|Third|Fourth|Jr|Sr|II|III|IV)\s*$", "", name, flags=re.I)
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            fm = parts[1].strip().split()
            first = fm[0] if fm else ""
            middle = " ".join(fm[1:]) if len(fm) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) == 1:
            return parts[0], "", ""
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return parts[0], " ".join(parts[1:-1]), parts[-1]

    @staticmethod
    def _parse_bond(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\\s]", "", str(bond_str).strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
