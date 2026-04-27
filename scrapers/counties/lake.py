"""
Lake County Arrest Scraper — JavaScript SPA via DrissionPage
Source: Lake County Sheriff's Office
URL: https://www.lcso.org/inmates/
Method: DrissionPage browser automation — site uses JS SPA (template vars like
        {{search_title}} in static HTML, data loaded dynamically)
Updated: 2026-04-25 — switched from requests to DrissionPage because the site
         is a JavaScript SPA that renders inmate data client-side.
"""
import logging
import re
import time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.lcso.org"
SEARCH_URL = f"{BASE_URL}/inmates/"
FACILITY = "Lake County Jail"


class LakeCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Lake"

    def scrape(self) -> List[ArrestRecord]:
        """Primary: DrissionPage browser to render the JS SPA."""
        records = self._browser_scrape()
        if not records:
            # Fallback: try plain requests in case site changes back
            records = self._requests_fallback()
        logger.info(f"Lake: {len(records)} records")
        return records

    def _browser_scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("DrissionPage not installed")
            return []

        opts = self._get_browser_options()

        page = None
        records = []
        seen = set()
        try:
            page = ChromiumPage(addr_or_opts=opts)
            page.get(SEARCH_URL)
            page.wait(5)

            # Wait for dynamic content to load
            for _ in range(10):
                html = page.html
                if "{{search_title}}" not in html and (
                    "inmate" in html.lower() or "booking" in html.lower()
                ):
                    break
                page.wait(1)

            soup = BeautifulSoup(page.html, "html.parser")
            records = self._parse_content(soup, seen)

            # Try pagination if available
            page_num = 2
            while records and page_num <= 10:
                next_btn = None
                try:
                    next_btn = page.ele("css:a.next, a[rel='next'], .pagination a:last-child")
                except Exception:
                    break
                if not next_btn:
                    break
                try:
                    next_btn.click()
                    page.wait(3)
                    soup = BeautifulSoup(page.html, "html.parser")
                    batch = self._parse_content(soup, seen)
                    if not batch:
                        break
                    records.extend(batch)
                    page_num += 1
                except Exception:
                    break

        except Exception as e:
            logger.error(f"Lake browser scrape: {e}")
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass
        return records

    def _requests_fallback(self) -> List[ArrestRecord]:
        """Fallback: try requests in case site reverts to server-rendered."""
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            return []

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Referer": BASE_URL,
        })

        seen = set()
        try:
            resp = session.get(SEARCH_URL, timeout=30)
            resp.raise_for_status()
            if "{{search_title}}" in resp.text:
                logger.debug("Lake: SPA template detected, requests won't work")
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            return self._parse_content(soup, seen)
        except Exception as e:
            logger.warning(f"Lake requests fallback: {e}")
            return []

    def _parse_content(self, soup, seen: set) -> List[ArrestRecord]:
        """Parse inmate data from rendered HTML — tables or card layouts."""
        records = []

        # Try table-based parsing
        for table in soup.find_all("table"):
            text = table.get_text(" ").lower()
            if not any(kw in text for kw in ["name", "booking", "inmate", "arrest"]):
                continue
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                texts = [c.get_text(strip=True) for c in cells]
                if not any(texts):
                    continue

                full_name = texts[0] if texts else ""
                booking_num = texts[1] if len(texts) > 1 else ""
                booking_date = texts[2] if len(texts) > 2 else ""
                charges = texts[3] if len(texts) > 3 else ""
                bond_raw = texts[4] if len(texts) > 4 else "0"

                key = (full_name, booking_num)
                if key in seen or not full_name:
                    continue
                seen.add(key)

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
                    First_Name=f, Middle_Name=m, Last_Name=l,
                        DOB="",
                    Booking_Date=self._clean(booking_date),
                    Status="In Custody",
                        Release_Date="",
                    Facility=FACILITY,
                    Charges=self._clean(charges),
                    Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                    Detail_URL=detail_url,
                    LastCheckedMode="INITIAL",
                ))
            if records:
                break

        # Try card/div-based parsing if no tables
        if not records:
            cards = soup.find_all("div", class_=re.compile(
                r"inmate|booking|card|result", re.I
            ))
            for card in cards:
                name_el = card.find(["a", "h3", "h4", "strong"])
                if not name_el:
                    continue
                full_name = name_el.get_text(strip=True)
                if not full_name or len(full_name) < 3:
                    continue
                key = full_name
                if key in seen:
                    continue
                seen.add(key)

                card_text = card.get_text(" ", strip=True)
                booking_match = re.search(
                    r"(?:Booking|Book)\s*#?\s*:?\s*(\S+)", card_text, re.I
                )
                bond_match = re.search(r"\$[\d,]+\.?\d*", card_text)

                detail_url = ""
                link = card.find("a", href=True)
                if link:
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"{BASE_URL}/{href.lstrip('/')}"
                    detail_url = href

                f, m, l = self._pn(full_name)

                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_match.group(1) if booking_match else "",
                    Full_Name=full_name,
                    First_Name=f, Middle_Name=m, Last_Name=l,
                        DOB="",
                    Status="In Custody",
                        Release_Date="",
                    Facility=FACILITY,
                    Bond_Amount=bond_match.group(0) if bond_match else "0",
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
        cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
