"""
Indian River County Arrest Scraper — Card-Based Layout Parser
Source: Indian River County Sheriff's Office
URL: https://www.ircsheriff.org/inmate-search
Method: requests + BeautifulSoup — parse card/list layout with booking-details links
Note: Site uses card layout (not tables). Each inmate is a link to /booking-details/{id}
      with DOB, bond amount, and status shown inline. SSL cert issues — verify=False.
Updated: 2026-04-25 — rewrote parser for card layout (was looking for tables, site has none)
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
        session.verify = False

        records = []
        seen = set()

        for url in [SEARCH_URL, TODAYS_URL]:
            try:
                resp = session.get(url, timeout=30)
                if resp.status_code == 200 and len(resp.text) > 1000:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    # Try card-based parsing first (current site layout)
                    batch = self._parse_cards(soup, seen)
                    # Fallback to table parsing
                    if not batch:
                        batch = self._parse_tables(soup, seen)
                    records.extend(batch)
                    if records:
                        break
            except Exception as e:
                logger.warning(f"Indian River: {url} failed: {e}")
                continue

        if not records:
            records = self._browser_fallback(seen)

        logger.info(f"Indian River: {len(records)} records")
        return records

    def _parse_cards(self, soup, seen: set) -> List[ArrestRecord]:
        """Parse card/list layout — each inmate is a link to /booking-details/{id}."""
        records = []

        # Find all links to booking details
        booking_links = soup.find_all("a", href=re.compile(r"/booking-details/\d+"))
        if not booking_links:
            return []

        for link in booking_links:
            try:
                href = link.get("href", "")
                full_name = link.get_text(strip=True)
                if not full_name:
                    continue

                detail_url = href if href.startswith("http") else f"{BASE_URL}{href}"

                # Extract booking ID from URL
                bid_match = re.search(r"/booking-details/(\d+)", href)
                booking_id = bid_match.group(1) if bid_match else ""

                key = booking_id or full_name
                if key in seen:
                    continue
                seen.add(key)

                # Get parent container for metadata
                container = link.find_parent(["li", "div", "article", "section"])
                container_text = container.get_text(" ", strip=True) if container else ""

                # Extract status
                status = "In Custody"
                if "released" in container_text.lower():
                    status = "Released"
                elif "incarcerated" in container_text.lower():
                    status = "In Custody"

                # Extract DOB
                dob = ""
                dob_match = re.search(r"DOB:\s*(\d{1,2}/\d{1,2}/\d{4})", container_text)
                if dob_match:
                    dob = dob_match.group(1)

                # Extract bond amount
                bond_raw = "0"
                bond_match = re.search(r"Bond:\s*\$?([\d,]+\.?\d*)", container_text)
                if bond_match:
                    bond_raw = bond_match.group(1)
                elif "no bond" in container_text.lower():
                    bond_raw = "0"

                f, m, l = self._pn(full_name)
                bond_amount = self._parse_bond(bond_raw)

                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_id,
                    Full_Name=full_name,
                    First_Name=f,
                    Middle_Name=m,
                    Last_Name=l,
                    DOB=dob,
                    Status=status,
                    Facility=FACILITY,
                    Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                    Detail_URL=detail_url,
                    LastCheckedMode="INITIAL",
                ))
            except Exception as e:
                logger.debug(f"Indian River card parse error: {e}")
                continue

        return records

    def _parse_tables(self, soup, seen: set) -> List[ArrestRecord]:
        """Fallback: parse HTML tables if they exist."""
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
                key = booking_num or full_name
                if key in seen:
                    continue
                seen.add(key)
                f, m, l = self._pn(full_name)
                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_num,
                    Full_Name=full_name,
                    First_Name=f, Middle_Name=m, Last_Name=l,
                    Status="In Custody",
                    Facility=FACILITY,
                    LastCheckedMode="INITIAL",
                ))
            if records:
                break
        return records

    def _browser_fallback(self, seen: set) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage
            from bs4 import BeautifulSoup
        except ImportError:
            return []
        opts = self._get_browser_options()
        opts.set_argument("--ignore-certificate-errors")  # Indian River-specific
        page = None
        try:
            page = ChromiumPage(addr_or_opts=opts)
            page.get(SEARCH_URL)
            page.wait(5)
            soup = BeautifulSoup(page.html, "html.parser")
            records = self._parse_cards(soup, seen)
            if not records:
                records = self._parse_tables(soup, seen)
            return records
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
