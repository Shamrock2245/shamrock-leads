"""
Indian River County Arrest Scraper — Custom HTML Table
Source: Indian River County Sheriff's Office
URL: https://www.ircsheriff.org/inmate-search (SSL cert issues — use verify=False)
Method: requests with SSL verification disabled + BeautifulSoup
Note: ircsheriff.org has an expired/invalid SSL cert — verify=False required
"""
import logging
import re
import warnings
import urllib3
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ircsheriff.org"
SEARCH_URL = f"{BASE_URL}/inmate-search"
TODAYS_URL = f"{BASE_URL}/todays-bookings"
FACILITY = "Indian River County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class IndianRiverCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Indian River"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            return []

        session = requests.Session()
        session.headers.update(HEADERS)
        session.verify = False  # SSL cert issue on ircsheriff.org

        records = []
        seen = set()

        # Try main inmate search page first
        for url in [SEARCH_URL, TODAYS_URL]:
            try:
                resp = session.get(url, timeout=30)
                if resp.status_code == 200 and len(resp.text) > 1000:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    batch = self._parse_html(soup, seen)
                    records.extend(batch)
                    if records:
                        break
            except Exception as e:
                logger.warning(f"Indian River: {url} failed: {e}")
                continue

        # If no records, try DrissionPage fallback
        if not records:
            records = self._browser_fallback(seen)

        logger.info(f"Indian River: {len(records)} records")
        return records

    def _parse_html(self, soup, seen: set) -> List[ArrestRecord]:
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

    def _browser_fallback(self, seen: set) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
            from bs4 import BeautifulSoup
        except ImportError:
            return []
        opts = ChromiumOptions()
        opts.headless(True)
        opts.set_argument("--no-sandbox")
        opts.set_argument("--disable-dev-shm-usage")
        opts.set_argument("--ignore-certificate-errors")
        opts.set_argument("--disable-gpu")
        page = None
        try:
            page = ChromiumPage(addr_or_opts=opts)
            page.get(SEARCH_URL)
            page.wait(5)
            soup = BeautifulSoup(page.html, "html.parser")
            return self._parse_html(soup, seen)
        except Exception as e:
            logger.error(f"Indian River browser fallback: {e}")
            return []
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass

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
