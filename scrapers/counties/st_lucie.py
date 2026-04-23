"""
St. Lucie County Arrest Scraper — JailTracker HTML Table.
Source: St. Lucie County Sheriff's Office
URL: https://www.stluciesheriff.com/215/Inmate-Lookup
Method: DrissionPage — JavaScript-rendered JailTracker table
"""
import logging
import re
import time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.stluciesheriff.com"
SEARCH_URL = f"{BASE_URL}/215/Inmate-Lookup"
FACILITY = "St. Lucie County Jail"


class StLucieCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "St. Lucie"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error("DrissionPage not installed"); return []

        page = self._setup_browser()
        records = []

        try:
            page.get(SEARCH_URL)
            time.sleep(4)

            # Wait for table to load
            for _ in range(10):
                if page.ele("tag:table", timeout=2):
                    break
                time.sleep(2)

            # Try to find and click "Search All" or submit empty form
            try:
                search_btn = (
                    page.ele("text:Search", timeout=3) or
                    page.ele("css:input[type='submit']", timeout=2) or
                    page.ele("css:button[type='submit']", timeout=2)
                )
                if search_btn:
                    search_btn.click()
                    time.sleep(3)
            except Exception:
                pass

            records = self._parse_dom(page)
            logger.info(f"St. Lucie: {len(records)} records")
            return records

        except Exception as e:
            logger.error(f"St. Lucie fatal: {e}"); return []
        finally:
            try:
                page.quit()
            except Exception:
                pass

    def _parse_dom(self, page) -> List[ArrestRecord]:
        records = []
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(page.html, "html.parser")
            table = None
            for t in soup.find_all("table"):
                text = t.get_text(" ").lower()
                if any(kw in text for kw in ["name", "booking", "inmate"]):
                    rows = t.find_all("tr")
                    if len(rows) > 1:
                        table = t
                        break

            if not table:
                return []

            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                texts = [c.get_text(strip=True) for c in cells]
                if not any(texts):
                    continue

                full_name = texts[0] if len(texts) > 0 else ""
                booking_num = texts[1] if len(texts) > 1 else ""
                booking_date = texts[2] if len(texts) > 2 else ""
                charges = texts[3] if len(texts) > 3 else ""
                bond_raw = texts[4] if len(texts) > 4 else "0"

                detail_url = ""
                link = row.find("a", href=True)
                if link:
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"{BASE_URL}/{href.lstrip('/')}"
                    detail_url = href

                f, m, l = self._pn(full_name)
                bond_amount = self._parse_bond(bond_raw)

                if not full_name:
                    continue

                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=self._clean(booking_num),
                    Full_Name=full_name,
                    First_Name=f,
                    Middle_Name=m,
                    Last_Name=l,
                    Booking_Date=self._clean(booking_date),
                    Status="In Custody",
                    Facility=FACILITY,
                    Charges=self._clean(charges),
                    Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                    Detail_URL=detail_url,
                    LastCheckedMode="INITIAL",
                ))
        except Exception as e:
            logger.error(f"St. Lucie DOM parse: {e}")
        return records

    @staticmethod
    def _setup_browser():
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions()
        co.auto_port()
        co.headless(True)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--window-size=1920,1080")
        co.set_argument("--disable-gpu")
        co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36")
        return ChromiumPage(addr_or_opts=co)

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
