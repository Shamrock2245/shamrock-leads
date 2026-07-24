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
from datetime import datetime, timezone
from typing import List, Optional
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
            raise

        session = cffi_requests.Session()
        session.headers.update(HEADERS)
        session.verify = False

        records = []
        seen = set()

        for url in [SEARCH_URL, TODAYS_URL]:
            try:
                resp = session.get(url, timeout=30, impersonate=IMPERSONATE)
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
                bond_type = ""
                bond_match = re.search(r"Bond:\s*\$?([\d,]+\.?\d*)", container_text)
                if bond_match:
                    bond_raw = bond_match.group(1)
                elif "no bond" in container_text.lower():
                    bond_raw = "0"
                    bond_type = "NO BOND"

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
                    Release_Date="",
                    Facility=FACILITY,
                    Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                    Bond_Type=bond_type,
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
                        Release_Date="",
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

    def _fetch_single_booking(self, booking_id: str, detail_url: str) -> Optional[ArrestRecord]:
        """
        Re-fetch a single Indian River County booking by booking ID.
        Uses exact detail page tables to parse demographics, charges, and bond amount/type.
        """
        import requests
        from bs4 import BeautifulSoup
        
        session = cffi_requests.Session()
        session.headers.update(HEADERS)
        session.verify = False
        
        try:
            resp = session.get(detail_url, timeout=30, impersonate=IMPERSONATE)
            if resp.status_code != 200 or len(resp.text) < 1000:
                logger.warning(f"Indian River re-fetch failed for {detail_url}: HTTP {resp.status_code}")
                return None
                
            soup = BeautifulSoup(resp.text, "html.parser")
            
            container = soup.find("div", class_="col-lg-12")
            if not container:
                logger.warning(f"Indian River re-fetch: main container div.col-lg-12 not found")
                return None
                
            tables = container.find_all("table")
            if len(tables) < 2:
                logger.warning(f"Indian River re-fetch: demographics or booking tables missing")
                return None
                
            # Demographics (Table 0)
            demographics = {}
            for row in tables[0].find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    k = cells[0].get_text(strip=True)
                    v = cells[1].get_text(strip=True)
                    demographics[k] = v
                    
            # Booking Info (Table 1)
            booking_info = {}
            for row in tables[1].find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    k = cells[0].get_text(strip=True)
                    v = cells[1].get_text(strip=True)
                    booking_info[k] = v
                    
            # Extract demographics
            full_name = demographics.get("Name", "")
            dob_raw = demographics.get("Date of Birth", "")
            dob = ""
            if dob_raw:
                dob_match = re.search(r"([A-Za-z]{3}\s+\d{1,2},\s+\d{4})", dob_raw)
                if dob_match:
                    try:
                        dt = datetime.strptime(dob_match.group(1), "%b %d, %Y")
                        dob = dt.strftime("%m/%d/%Y")
                    except Exception:
                        pass
                        
            race = demographics.get("Race", "")
            sex = demographics.get("Sex", "")
            height = demographics.get("Height", "")
            weight = demographics.get("Weight", "")
            address = demographics.get("Address", "")
            if address:
                address = " ".join(address.split())
                
            # Extract booking info
            booking_date_raw = booking_info.get("Booking Date", "")
            arrest_date_raw = booking_info.get("Arrest Date", "")
            arrest_location = booking_info.get("Arrest Location", "")
            if arrest_location:
                arrest_location = " ".join(arrest_location.split())
            agency = booking_info.get("Arresting Agency", "")
            case_number = booking_info.get("Case Number", "")
            
            # Extract and parse bond amount and type
            bond_val_str = booking_info.get("Bond", "0")
            bond_type = ""
            
            if "no bond" in bond_val_str.lower():
                bond_amount = 0.0
                bond_type = "NO BOND"
            else:
                bond_amount = self._parse_bond(bond_val_str)
                if bond_amount == 0.0 and bond_val_str:
                    cleaned = re.sub(r"[$,\s]", "", bond_val_str.upper())
                    if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
                        bond_type = "NO BOND"
                
            # Extract Charges
            charge_cards = container.find_all("div", class_="card")
            charges_list = []
            for card in charge_cards:
                header = card.find("div", class_="card-header")
                if header:
                    charge_desc = header.get_text(strip=True)
                    if charge_desc:
                        charges_list.append(charge_desc)
            charges_str = " | ".join(charges_list)
            
            f, m, l = self._pn(full_name)
            
            def parse_irc_date_time(raw_str):
                if not raw_str:
                    return "", ""
                clean_str = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw_str)
                try:
                    dt = datetime.strptime(clean_str, "%B %d, %Y at %I:%M %p")
                    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
                except Exception:
                    date_match = re.search(r"([A-Za-z]+\s+\d+,\s+\d{4})", clean_str)
                    time_match = re.search(r"(\d+:\d+\s+[ap]m)", clean_str, re.IGNORECASE)
                    d_val = ""
                    t_val = ""
                    if date_match:
                        try:
                            d_val = datetime.strptime(date_match.group(1), "%B %d, %Y").strftime("%Y-%m-%d")
                        except Exception:
                            pass
                    if time_match:
                        t_val = time_match.group(1)
                    return d_val, t_val

            b_date, b_time = parse_irc_date_time(booking_date_raw)
            a_date, a_time = parse_irc_date_time(arrest_date_raw)
            
            return ArrestRecord(
                County=self.county,
                Booking_Number=booking_id,
                Full_Name=full_name,
                First_Name=f,
                Middle_Name=m,
                Last_Name=l,
                DOB=dob,
                Arrest_Date=a_date or arrest_date_raw,
                Arrest_Time=a_time,
                Booking_Date=b_date or booking_date_raw,
                Booking_Time=b_time,
                Status="In Custody",
                Release_Date="",
                Facility=FACILITY,
                Agency=agency,
                Race=race,
                Sex=sex,
                Height=height,
                Weight=weight,
                Address=address,
                Charges=charges_str,
                Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                Bond_Paid="NO",
                Bond_Type=bond_type,
                Case_Number=case_number,
                Detail_URL=detail_url,
                LastCheckedMode="UPDATE",
                LastChecked=datetime.now(timezone.utc).isoformat()
            )
            
        except Exception as e:
            logger.error(f"Indian River _fetch_single_booking error ({booking_id}): {e}")
            return None
