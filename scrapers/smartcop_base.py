"""
SmartCOP Base Scraper — Shared logic for 12+ Florida counties using SmartCOP JMS.

SmartCOP is a standardized Jail Management System used by many small-medium
Florida counties. The inmate search portal follows a consistent pattern:
- Base URL: https://{subdomain}.smartcopsolutions.com/smart_search.php
- Search: POST with last_name, first_name parameters
- Results: HTML table with standardized column layout
- Detail: Click-through to individual inmate pages

Counties using SmartCOP (confirmed):
  Baker, Bradford, Calhoun, Columbia, Franklin, Gadsden,
  Gilchrist, Gulf, Hamilton, Holmes, Jefferson, Lafayette,
  Liberty, Madison, Suwannee, Taylor, Union, Wakulla, Washington

Usage:
    class BakerCountyScraper(SmartCOPBaseScraper):
        SMARTCOP_SUBDOMAIN = "baker"
        COUNTY_NAME = "Baker"
        FACILITY_NAME = "Baker County Jail"
"""

import logging
import re
import string
import time
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class SmartCOPBaseScraper(BaseScraper):
    """Base class for all SmartCOP JMS counties."""

    SMARTCOP_SUBDOMAIN: str = ""  # Override in subclass
    COUNTY_NAME: str = ""        # Override in subclass
    FACILITY_NAME: str = ""      # Override in subclass

    # Can override if a county uses a different URL pattern
    SMARTCOP_BASE_URL: str = ""

    @property
    def county(self) -> str:
        return self.COUNTY_NAME

    @property
    def search_url(self) -> str:
        if self.SMARTCOP_BASE_URL:
            return self.SMARTCOP_BASE_URL
        return f"https://{self.SMARTCOP_SUBDOMAIN}.smartcopsolutions.com/smart_search.php"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape SmartCOP inmate search via A-Z POST iteration."""
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error(f"requests/bs4 not installed for {self.county}")
            return []

        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            ),
        })

        seen, all_records = set(), []

        # First try loading the page to get any CSRF tokens
        try:
            resp = session.get(self.search_url, timeout=30)
            if resp.status_code != 200:
                logger.warning(f"{self.county}: initial GET returned {resp.status_code}")
        except Exception as e:
            logger.error(f"{self.county}: cannot reach {self.search_url}: {e}")
            return []

        # Iterate A-Z for last names
        for letter in string.ascii_uppercase:
            try:
                resp = session.post(
                    self.search_url,
                    data={"last_name": letter, "first_name": ""},
                    timeout=30,
                )
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                records = self._parse_results(soup)

                for r in records:
                    key = r.Booking_Number or r.Full_Name
                    if key and key not in seen:
                        seen.add(key)
                        all_records.append(r)

            except Exception as e:
                logger.debug(f"{self.county} letter {letter}: {e}")
            time.sleep(0.3)

        logger.info(f"✅ {self.county}: {len(all_records)} records")
        return all_records

    def _parse_results(self, soup) -> List[ArrestRecord]:
        """Parse SmartCOP results table."""
        from bs4 import BeautifulSoup
        records = []

        for table in soup.find_all("table"):
            header_row = table.find("tr")
            if not header_row:
                continue

            headers = [
                th.get_text(strip=True).lower()
                for th in header_row.find_all(["th", "td"])
            ]

            # Map column indices
            col_map = {}
            for i, h in enumerate(headers):
                if "name" in h:
                    col_map["name"] = i
                elif "book" in h and "num" in h:
                    col_map["booking"] = i
                elif "book" in h and "date" in h:
                    col_map["date"] = i
                elif "charge" in h or "offense" in h:
                    col_map["charge"] = i
                elif "bond" in h or "bail" in h:
                    col_map["bond"] = i
                elif "dob" in h or "birth" in h:
                    col_map["dob"] = i
                elif "race" in h:
                    col_map["race"] = i
                elif "sex" in h or "gender" in h:
                    col_map["sex"] = i
                elif "age" in h:
                    col_map["age"] = i

            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue

                texts = [c.get_text(strip=True) for c in cells]

                # Extract fields by column mapping
                full_name = texts[col_map["name"]] if "name" in col_map and col_map["name"] < len(texts) else ""
                booking_number = texts[col_map["booking"]] if "booking" in col_map and col_map["booking"] < len(texts) else ""
                booking_date = texts[col_map["date"]] if "date" in col_map and col_map["date"] < len(texts) else ""
                charges = texts[col_map["charge"]] if "charge" in col_map and col_map["charge"] < len(texts) else ""
                bond_str = texts[col_map["bond"]] if "bond" in col_map and col_map["bond"] < len(texts) else ""
                dob = texts[col_map["dob"]] if "dob" in col_map and col_map["dob"] < len(texts) else ""
                race = texts[col_map["race"]] if "race" in col_map and col_map["race"] < len(texts) else ""
                sex = texts[col_map["sex"]] if "sex" in col_map and col_map["sex"] < len(texts) else ""

                # Fallback: heuristic extraction if no column mapping
                if not full_name and not booking_number:
                    for t in texts:
                        if "," in t and not full_name and len(t) > 3:
                            full_name = t
                        elif re.match(r"^\d{4,}$", t) and not booking_number:
                            booking_number = t

                if not full_name and not booking_number:
                    continue

                # Parse bond amount
                bond_amount = "0"
                if bond_str:
                    m = re.search(r"[\$]?([\d,]+\.?\d*)", bond_str)
                    if m:
                        bond_amount = m.group(1).replace(",", "")

                first, middle, last = self._parse_name(full_name)

                # Detail URL
                link = row.find("a", href=True)
                detail_url = ""
                if link:
                    h = link["href"]
                    if not h.startswith("http"):
                        base = self.search_url.rsplit("/", 1)[0]
                        h = base + "/" + h.lstrip("/")
                    detail_url = h

                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_number,
                    Full_Name=full_name,
                    First_Name=first,
                    Middle_Name=middle,
                    Last_Name=last,
                    Booking_Date=booking_date,
                    DOB=dob,
                    Race=race,
                    Sex=sex,
                    Charges=charges,
                    Bond_Amount=bond_amount,
                    Status="In Custody",
                    Facility=self.FACILITY_NAME,
                    Detail_URL=detail_url,
                    LastCheckedMode="INITIAL",
                ))

        # If no table found, try card/div-based layout
        if not records:
            for elem in soup.find_all(
                ["div", "article", "li"],
                class_=re.compile(r"inmate|roster|card|entry|booking", re.I),
            ):
                text = elem.get_text(" ", strip=True)
                if len(text) < 10:
                    continue
                nm = re.search(
                    r"([A-Z][A-Za-z'-]+),\s*([A-Z][A-Za-z'-]+)", text
                )
                if nm:
                    full_name = f"{nm.group(1)}, {nm.group(2)}"
                    bk = re.search(r"\b(\d{4,})\b", text)
                    bd = re.search(r"\$([\d,]+)", text)
                    records.append(ArrestRecord(
                        County=self.county,
                        Booking_Number=bk.group(1) if bk else "",
                        Full_Name=full_name,
                        First_Name=nm.group(2),
                        Last_Name=nm.group(1),
                        Bond_Amount=bd.group(1).replace(",", "") if bd else "0",
                        Status="In Custody",
                        Facility=self.FACILITY_NAME,
                        LastCheckedMode="INITIAL",
                    ))

        return records

    @staticmethod
    def _parse_name(name_str: str):
        if not name_str:
            return "", "", ""
        if "," in name_str:
            parts = name_str.split(",", 1)
            last = parts[0].strip()
            fm = parts[1].strip().split()
            first = fm[0] if fm else ""
            middle = " ".join(fm[1:]) if len(fm) > 1 else ""
            return first, middle, last
        parts = name_str.split()
        return parts[0], "", parts[-1] if len(parts) >= 2 else ""
