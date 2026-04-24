"""
Escambia County Arrest Scraper — OCV Next.js SPA via DrissionPage
Source: Escambia County Sheriff's Office
URL: https://www.escambiaso.com/inmate-lookup
Method: DrissionPage — loads Next.js SPA, intercepts OCV API calls for inmate data
Note: myescambia.com is DEAD. New URL is escambiaso.com/inmate-lookup (OCV platform)
Proven pattern: swfl-arrest-scrapers/counties/escambia/solver.py (updated URL)
"""
import logging
import re
import json
import time
import string
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.escambiaso.com/inmate-lookup"
FACILITY = "Escambia County Jail"


class EscambiaCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Escambia"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error("DrissionPage not installed")
            return []

        opts = ChromiumOptions()
        opts.headless(True)
        opts.set_argument("--no-sandbox")
        opts.set_argument("--disable-dev-shm-usage")
        opts.set_argument("--disable-gpu")

        page = None
        all_records = []
        seen = set()

        try:
            page = ChromiumPage(addr_or_opts=opts)

            # Start network interception to catch OCV API calls
            page.listen.start("api")
            page.get(SEARCH_URL)
            page.wait(5)

            # Collect API responses
            api_responses = []
            for pkt in page.listen.steps(timeout=8):
                try:
                    if pkt.response and pkt.response.body:
                        body = pkt.response.body
                        if isinstance(body, (dict, list)):
                            api_responses.append(body)
                        elif isinstance(body, str) and (body.startswith("{") or body.startswith("[")):
                            api_responses.append(json.loads(body))
                except Exception:
                    pass

            # Process intercepted API responses
            for data in api_responses:
                batch = self._extract_from_api(data, seen)
                all_records.extend(batch)

            # If no API data, try parsing the rendered HTML
            if not all_records:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page.html, "html.parser")
                all_records = self._parse_html(soup, seen)

            # Try clicking through A-Z if still no results
            if not all_records:
                all_records = self._scrape_az(page, seen)

        except Exception as e:
            logger.error(f"Escambia: DrissionPage error: {e}")
        finally:
            if page:
                try:
                    page.listen.stop()
                    page.quit()
                except Exception:
                    pass

        logger.info(f"Escambia: {len(all_records)} records")
        return all_records

    def _scrape_az(self, page, seen: set) -> List[ArrestRecord]:
        """Try A-Z letter search if API interception didn't work."""
        from bs4 import BeautifulSoup
        records = []

        for letter in string.ascii_uppercase:
            try:
                # Look for a search input
                inputs = page.eles("tag:input")
                for inp in inputs:
                    t = inp.attr("type") or ""
                    placeholder = (inp.attr("placeholder") or "").lower()
                    if t == "text" or "name" in placeholder or "search" in placeholder:
                        inp.clear()
                        inp.input(letter)
                        break

                # Submit
                btns = page.eles("tag:button")
                for btn in btns:
                    txt = (btn.text or "").lower()
                    if any(w in txt for w in ["search", "find", "go", "submit"]):
                        btn.click()
                        break

                page.wait(3)
                soup = BeautifulSoup(page.html, "html.parser")
                batch = self._parse_html(soup, seen)
                records.extend(batch)
                time.sleep(0.5)

            except Exception as e:
                logger.debug(f"Escambia A-Z {letter}: {e}")
                continue

        return records

    def _extract_from_api(self, data, seen: set) -> List[ArrestRecord]:
        """Extract records from intercepted OCV API JSON."""
        records = []

        # Handle list of inmates
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ["inmates", "records", "data", "results", "items", "bookings"]:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    break
            if not items and "total" in data:
                items = [data]

        for item in items:
            if not isinstance(item, dict):
                continue

            def _get(*keys):
                for k in keys:
                    for dk in item.keys():
                        if dk.lower() == k.lower():
                            return str(item[dk]).strip()
                return ""

            full_name = _get("name", "fullname", "full_name", "inmateFullName")
            last_name = _get("lastName", "last_name", "lname", "surname")
            first_name = _get("firstName", "first_name", "fname")
            middle_name = _get("middleName", "middle_name", "mname")
            booking_num = _get("bookingNumber", "booking_number", "bookingNo", "id")
            booking_date = _get("bookingDate", "booking_date", "arrestDate")
            charges = _get("charges", "charge", "offenses")
            bond_raw = _get("bondAmount", "bond_amount", "bond", "totalBond")
            race = _get("race")
            sex = _get("sex", "gender")
            status = _get("status", "inmateStatus") or "In Custody"

            if not full_name and last_name:
                full_name = f"{last_name}, {first_name}"
                if middle_name:
                    full_name += f" {middle_name}"

            key = booking_num or full_name
            if not key or key in seen:
                continue
            seen.add(key)

            if not full_name and not booking_num:
                continue

            f, m, l = self._pn(full_name) if full_name else (first_name, middle_name, last_name)
            bond_amount = self._parse_bond(bond_raw)

            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=booking_num,
                Full_Name=full_name,
                First_Name=f or first_name,
                Middle_Name=m or middle_name,
                Last_Name=l or last_name,
                Booking_Date=booking_date,
                Status=status,
                Facility=FACILITY,
                Race=race,
                Sex=sex,
                Charges=charges,
                Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                LastCheckedMode="INITIAL",
            ))

        return records

    def _parse_html(self, soup, seen: set) -> List[ArrestRecord]:
        """Parse rendered HTML for inmate data."""
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
                booking_num = texts[1] if len(texts) > 1 else ""
                booking_date = texts[2] if len(texts) > 2 else ""
                charges = texts[3] if len(texts) > 3 else ""
                bond_raw = texts[4] if len(texts) > 4 else "0"

                key = booking_num or full_name
                if not key or key in seen:
                    continue
                seen.add(key)

                f, m, l = self._pn(full_name)
                bond_amount = self._parse_bond(bond_raw)

                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_num,
                    Full_Name=full_name,
                    First_Name=f,
                    Middle_Name=m,
                    Last_Name=l,
                    Booking_Date=booking_date,
                    Status="In Custody",
                    Facility=FACILITY,
                    Charges=charges,
                    Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                    LastCheckedMode="INITIAL",
                ))

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
    def _parse_bond(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
