"""
Highlands County Arrest Scraper — OCV Next.js SPA via DrissionPage
Source: Highlands County Sheriff's Office
URL: https://www.highlandssheriff.org/inmateSearch
Method: DrissionPage — intercepts OCV API calls to get inmate JSON
Note: The S3 JSON URL (a999041447) returns 403 — app ID is wrong or access restricted.
      DrissionPage loads the page as a real browser and intercepts the authenticated API calls.
Proven pattern: swfl-arrest-scrapers/counties/highlands/solver.py
"""
import logging
import re
import json
import time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.highlandssheriff.org/inmateSearch"
FACILITY = "Highlands County Jail"


class HighlandsCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Highlands"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage
        except ImportError:
            logger.error("DrissionPage not installed")
            return []

        opts = self._get_browser_options()

        page = None
        all_records = []
        seen = set()

        try:
            page = ChromiumPage(addr_or_opts=opts)
            page.listen.start("api")
            page.get(SEARCH_URL)
            page.wait(6)

            # Collect API responses
            api_data = []
            for pkt in page.listen.steps(timeout=10):
                try:
                    if pkt.response and pkt.response.body:
                        body = pkt.response.body
                        if isinstance(body, (dict, list)):
                            api_data.append(body)
                        elif isinstance(body, str) and (body.startswith("{") or body.startswith("[")):
                            api_data.append(json.loads(body))
                except Exception:
                    pass

            # Extract records from API responses
            for data in api_data:
                batch = self._extract_from_api(data, seen)
                all_records.extend(batch)

            # If no API data, try scrolling to load more and parse HTML
            if not all_records:
                # Try clicking "View All" or scrolling
                try:
                    btns = page.eles("tag:button")
                    for btn in btns:
                        txt = (btn.text or "").lower()
                        if any(w in txt for w in ["all", "view all", "show all", "load all"]):
                            btn.click()
                            page.wait(4)
                            break
                except Exception:
                    pass

                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page.html, "html.parser")
                all_records = self._parse_html(soup, seen)

        except Exception as e:
            logger.error(f"Highlands: DrissionPage error: {e}")
        finally:
            if page:
                try:
                    page.listen.stop()
                    page.quit()
                except Exception:
                    pass

        logger.info(f"Highlands: {len(all_records)} records")
        return all_records

    def _extract_from_api(self, data, seen: set) -> List[ArrestRecord]:
        """Extract records from intercepted OCV/API JSON responses."""
        records = []

        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ["inmates", "records", "data", "results", "items", "bookings", "docs"]:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    break
            if not items:
                items = [data] if any(k in data for k in ["lastName", "last_name", "inmateID", "bookingNumber"]) else []

        for item in items:
            if not isinstance(item, dict):
                continue

            def _get(*keys):
                for k in keys:
                    for dk in item.keys():
                        if dk.lower() == k.lower():
                            v = item[dk]
                            return str(v).strip() if v is not None else ""
                return ""

            last_name = _get("lastName", "last_name", "lname")
            first_name = _get("firstName", "first_name", "fname")
            middle_name = _get("middleName", "middle_name", "mname")
            full_name = _get("name", "fullname", "full_name", "titleWithFirst", "title")
            if not full_name and last_name:
                full_name = f"{last_name}, {first_name}"
                if middle_name:
                    full_name += f" {middle_name}"

            booking_num = _get("inmateID", "bookingNumber", "booking_number", "bookingNo", "id")
            booking_date = _get("bookingDate", "booking_date", "arrestDate", "bookedDate")
            charges = _get("charges", "charge", "offenses")
            bond_raw = _get("bondAmount", "bond_amount", "bond", "totalBond")
            race = _get("race")
            sex = _get("sex", "gender")
            status = _get("status", "custodyStatus", "custody_status_cd") or "In Custody"

            # Parse OCV content HTML for demographics
            content = item.get("content", "")
            if content:
                demo = self._parse_content(content)
                if not booking_date:
                    booking_date = demo.get("booking_date", "")
                if not race:
                    race = demo.get("race", "")
                if not sex:
                    sex = demo.get("gender", "")

            key = booking_num or full_name
            if not key or key in seen:
                continue
            seen.add(key)

            if not full_name and not booking_num:
                continue

            f, m, l = self._pn(full_name) if full_name else (first_name, middle_name, last_name)
            bond_amount = self._parse_bond(bond_raw)

            # Get mugshot URL
            mugshot_url = ""
            images = item.get("images", [])
            if images and isinstance(images, list):
                img = images[0]
                if isinstance(img, dict):
                    mugshot_url = img.get("large", img.get("small", ""))
                elif isinstance(img, str):
                    mugshot_url = img

            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=booking_num,
                Full_Name=full_name,
                First_Name=f or first_name,
                Middle_Name=m or middle_name,
                Last_Name=l or last_name,
                        DOB="",
                Booking_Date=booking_date,
                Status="In Custody",
                        Release_Date="",
                Facility=FACILITY,
                Race=race,
                Sex=sex,
                Charges=charges,
                Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                Mugshot_URL=mugshot_url,
                Detail_URL=SEARCH_URL,

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
                        DOB="",
                    Booking_Date=booking_date,
                    Status="In Custody",
                        Release_Date="",
                    Facility=FACILITY,
                    Charges=charges,
                    Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                    Detail_URL=SEARCH_URL,

                    LastCheckedMode="INITIAL",
                ))
            break

        return records

    @staticmethod
    def _parse_content(html: str) -> dict:
        """Parse OCV content HTML for demographics."""
        if not html:
            return {}
        text = re.sub(r"<[^>]+>", "\n", html)
        text = re.sub(r"\n+", "\n", text).strip()
        result = {}
        m = re.search(r"Gender:\s*([A-Z])", text)
        if m:
            result["gender"] = m.group(1)
        m = re.search(r"Race:\s*([A-Z]+)", text)
        if m:
            result["race"] = m.group(1)
        m = re.search(r"Booked Date:\s*(\d{1,2}/\d{1,2}/\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)", text)
        if m:
            result["booking_date"] = m.group(1)
        return result

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
