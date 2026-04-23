"""
Hernando County Arrest Scraper — HTML Inmate Search.
Source: Hernando County Sheriff's Office
URL: https://www.hernandosheriff.org/inmate-search
Method: DrissionPage — JavaScript-rendered page
"""
import logging
import re
import time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.hernandosheriff.org"
SEARCH_URL = f"{BASE_URL}/inmate-search"
FACILITY = "Hernando County Jail"


class HernandoCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Hernando"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed"); return []

        # Try simple HTTP first
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        records = []
        try:
            resp = session.get(SEARCH_URL, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            records = self._parse_table(soup)
        except Exception as e:
            logger.warning(f"Hernando HTTP failed ({e}), trying browser")

        if not records:
            records = self._browser_scrape()

        logger.info(f"Hernando: {len(records)} records")
        return records

    def _browser_scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
            from bs4 import BeautifulSoup
        except ImportError:
            return []

        co = ChromiumOptions()
        co.auto_port()
        co.headless(True)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--window-size=1920,1080")
        co.set_argument("--disable-gpu")
        co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36")
        page = ChromiumPage(addr_or_opts=co)

        records = []
        try:
            page.get(SEARCH_URL)
            time.sleep(4)

            # Try to submit empty search
            try:
                btn = (
                    page.ele("text:Search", timeout=3) or
                    page.ele("css:input[type='submit']", timeout=2) or
                    page.ele("css:button[type='submit']", timeout=2)
                )
                if btn:
                    btn.click()
                    time.sleep(3)
            except Exception:
                pass

            soup = BeautifulSoup(page.html, "html.parser")
            records = self._parse_table(soup)
        except Exception as e:
            logger.error(f"Hernando browser: {e}")
        finally:
            try:
                page.quit()
            except Exception:
                pass

        return records

    def _parse_table(self, soup) -> List[ArrestRecord]:
        records = []
        table = None
        for t in soup.find_all("table"):
            text = t.get_text(" ").lower()
            if any(kw in text for kw in ["name", "booking", "inmate", "arrest"]):
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

            if not full_name:
                continue

            detail_url = ""
            link = row.find("a", href=True)
            if link:
                href = link["href"]
                if not href.startswith("http"):
                    href = f"{BASE_URL}/{href.lstrip('/')}"
                detail_url = href

            f, m, l = self._pn(full_name)
            bond_amount = self._parse_bond(bond_raw)

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
