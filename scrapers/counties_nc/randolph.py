"""
Randolph County (NC) Arrest Scraper — Confined Inmates by Name (ASP.NET HTML).

URL: https://legacyweb.randolphcountync.gov/sheriff/ConfinedInmatesByName.aspx

Server-renders full roster (~360) with expandable detail rows (charges + bail).
No form POST required.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = (
    "https://legacyweb.randolphcountync.gov/sheriff/ConfinedInmatesByName.aspx"
)


class RandolphScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Randolph"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        })

        try:
            resp = session.get(PORTAL_URL, timeout=120, verify=False)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Randolph GET failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        summary_rows = soup.select("tr.FontNormal")
        records: List[ArrestRecord] = []

        for tr in summary_rows:
            try:
                rec = self._parse_inmate_row(tr)
                if rec:
                    records.append(rec)
            except Exception as e:
                logger.debug("Randolph row parse fail: %s", e)

        logger.info(
            "Randolph: %d confined inmates in %.1fs",
            len(records),
            time.time() - start,
        )
        return records

    def _parse_inmate_row(self, tr: Tag) -> Optional[ArrestRecord]:
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        # Last | First | Middle | Suffix | Age | Race | Gender | Date Confined
        if len(cells) < 5:
            return None
        last, first = cells[0], cells[1]
        if not last or last.lower() == "last name":
            return None
        middle = cells[2] if len(cells) > 2 else ""
        suffix = cells[3] if len(cells) > 3 else ""
        age = cells[4] if len(cells) > 4 else ""
        race = cells[5] if len(cells) > 5 else ""
        gender = cells[6] if len(cells) > 6 else ""
        confined = cells[7] if len(cells) > 7 else ""

        given = " ".join(p for p in (first, middle, suffix) if p).strip()
        full = f"{last}, {given}".strip(", ")

        detail_tr = tr.find_next_sibling("tr")
        address = city = zipcode = height = weight = facility = ""
        charges: List[str] = []
        total_bond = 0.0
        case_numbers: List[str] = []

        if detail_tr and "DetailOff" in (detail_tr.get("class") or []):
            address, city, zipcode, height, weight, facility, charges, total_bond, case_numbers = (
                self._parse_detail(detail_tr)
            )

        booking = ""
        if case_numbers:
            booking = case_numbers[0]
        if not booking:
            key = f"{last}|{first}|{confined}|{age}".upper()
            booking = "RAN_" + hashlib.md5(key.encode()).hexdigest()[:12]

        return ArrestRecord(
            County=self.county,
            State="NC",
            Full_Name=full,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=str(booking),
            Case_Number=" | ".join(case_numbers[:5]) if case_numbers else "",
            Age_At_Arrest=str(age),
            Race=race,
            Sex=(gender or "")[:1].upper(),
            Booking_Date=confined,
            Address=address,
            City=city,
            ZIP=zipcode,
            Height=height,
            Weight=weight,
            Charges=" | ".join(charges) if charges else "Unknown",
            Bond_Amount=f"{total_bond:.2f}" if total_bond else "0",
            Status="In Custody",
            Facility=facility or "Randolph County Detention",
            Detail_URL=PORTAL_URL,
            Agency="Randolph County Sheriff",
        )

    def _parse_detail(
        self, detail_tr: Tag
    ) -> Tuple[str, str, str, str, str, str, List[str], float, List[str]]:
        address = city = zipcode = height = weight = facility = ""
        charges: List[str] = []
        total_bond = 0.0
        case_numbers: List[str] = []

        # Label/value pairs in nested tables
        for nested_tr in detail_tr.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in nested_tr.find_all("td")]
            if len(cells) == 2:
                label, val = cells[0].rstrip(":").strip().lower(), cells[1].strip()
                if "street address" in label:
                    address = val
                elif "city/state/zip" in label:
                    m = re.search(r"([^,]+),\s*([A-Z]{2})\s+(\d{5})", val)
                    if m:
                        city, zipcode = m.group(1).strip(), m.group(3)
                    else:
                        city = val
                elif "height" in label:
                    # e.g. 5'04"  / 115 lbs.
                    hm = re.search(r"([\d'\"]+)\s*/\s*(\d+)", val)
                    if hm:
                        height, weight = hm.group(1).strip(), hm.group(2)
                    else:
                        height = val
                elif "jail facility" in label:
                    facility = val
            # Charge rows: date, description, incident, court ref, bail, type
            if len(cells) >= 6:
                # Skip header
                if "offense description" in " ".join(cells).lower():
                    continue
                # Often leading empty cell
                rest = cells[1:] if cells[0] == "" else cells
                if len(rest) >= 5 and re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", rest[0] or ""):
                    desc = rest[1]
                    court_ref = rest[3] if len(rest) > 3 else ""
                    bail = rest[4] if len(rest) > 4 else ""
                    bail_type = rest[5] if len(rest) > 5 else ""
                    if desc and desc.lower() not in ("offense description",):
                        if desc not in charges:
                            charges.append(desc)
                    if court_ref and re.search(r"\d", court_ref):
                        if court_ref not in case_numbers:
                            case_numbers.append(court_ref)
                    if bail and re.search(r"\d", bail) and "bond" in (bail_type or "").lower():
                        try:
                            total_bond += float(re.sub(r"[^\d.]", "", bail) or 0)
                        except ValueError:
                            pass
                    elif bail and bail.isdigit():
                        try:
                            total_bond += float(bail)
                        except ValueError:
                            pass

        return address, city, zipcode, height, weight, facility, charges, total_bond, case_numbers
