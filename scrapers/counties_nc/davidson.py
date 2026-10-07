"""
Davidson County (NC) Arrest Scraper — DCSO dhtmlxGrid inmate list + details.

Source contract (recon 2026-10-07, docs/recon/NC_DAVIDSON_DCINMATES_2026-10-07.md):
  * Portal: http://www2.co.davidson.nc.us/DCInmates/ (ordinary public HTTP;
    HTTPS EOF from box — site serves the roster over HTTP).
  * Roster: GET handler/inmate_data.ashx?dynamic=100&posStart=N&count=100
    → dhtmlx XML rows; cell[11] is source booking ``YY-######`` (e.g. 26-003404);
    cell[12] is internal person number used by details.
  * Detail: POST handler/inmate_details.ashx?number={InNum}&curbook={booking}
    → Incarceration Date, Bail Bonds Remaining totals, Offense Description.
  * Booking_Number is source booking only. Never invent ``DAV_`` keys.
  * Health stays unverified until a write smoke — do not set verified_public.
"""
from __future__ import annotations

import logging
import re
import time
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Set, Tuple
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "http://www2.co.davidson.nc.us/DCInmates/"
DATA_URL = "http://www2.co.davidson.nc.us/DCInmates/handler/inmate_data.ashx"
DETAILS_URL = "http://www2.co.davidson.nc.us/DCInmates/handler/inmate_details.ashx"
FACILITY = "Davidson County Detention"

PAGE_SIZE = 100
MAX_PAGES = 20  # hard cap (~2000)
MAX_DETAILS = 500
REQUEST_PAUSE_S = 0.12

# Source jail booking numbers observed on the public grid (YY-NNNNNN).
_BOOKING_RE = re.compile(r"^\d{2}-\d{5,8}$")


class DavidsonScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "www2.co.davidson.nc.us/DCInmates ordinary public ashx roster; "
        "Booking_Number is source cell booking YY-###### (never DAV_ invent); "
        "Incarceration Date / charges / Remaining bond from inmate_details.ashx "
        "when published."
    )

    @property
    def county(self) -> str:
        return "Davidson"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Referer": PORTAL_URL,
                "Accept": "*/*",
            }
        )

        try:
            session.get(PORTAL_URL, timeout=30).raise_for_status()
        except Exception as exc:
            logger.error("Davidson landing failed: %s", exc)
            return []

        listings = self._fetch_roster(session)
        if not listings:
            logger.warning("Davidson: empty roster")
            return []

        records: List[ArrestRecord] = []
        seen: Set[str] = set()
        enriched = 0

        for item in listings:
            booking = item["booking"]
            if booking in seen:
                continue
            seen.add(booking)

            charges = "Unknown"
            bond = "0"
            booking_date = ""
            race = ""
            extra = {
                "listing_only_for_hydrate": False,
                "hydrate_limitation": "",
            }

            if enriched < MAX_DETAILS and item["in_num"]:
                detail = self._fetch_details(session, item["in_num"], booking)
                time.sleep(REQUEST_PAUSE_S)
                if detail is not None:
                    booking_date, charges, bond, race = detail
                    enriched += 1
                else:
                    extra["listing_only_for_hydrate"] = True
                    extra["hydrate_limitation"] = (
                        "detail ashx unavailable; listing has booking # but no "
                        "published charges/bond/incarceration date"
                    )
            else:
                extra["listing_only_for_hydrate"] = True
                extra["hydrate_limitation"] = (
                    "detail enrich capped or missing InNum; listing-only fields"
                )

            records.append(
                ArrestRecord(
                    County=self.county,
                    State="NC",
                    Full_Name=item["full_name"],
                    First_Name=item["first"],
                    Middle_Name=item["middle"],
                    Last_Name=item["last"],
                    Booking_Number=booking,
                    Person_ID=item["in_num"],
                    Booking_Date=booking_date,
                    Age_At_Arrest=item["age"],
                    Sex=item["sex"],
                    Race=race,
                    Height=item["height"],
                    Weight=item["weight"],
                    Charges=charges,
                    Bond_Amount=bond,
                    Status="In Custody",
                    Detail_URL=(
                        f"{DETAILS_URL}?number={item['in_num']}&curbook={booking}"
                        if item["in_num"]
                        else PORTAL_URL
                    ),
                    Facility=FACILITY,
                    City=item["city"],
                    extra_data=extra,
                )
            )

        logger.info(
            "Davidson: %s records (%s detail-enriched) in %.1fs",
            len(records),
            enriched,
            time.time() - start,
        )
        return records

    def _fetch_roster(self, session: requests.Session) -> List[dict]:
        out: List[dict] = []
        total: Optional[int] = None
        for page in range(MAX_PAGES):
            pos = page * PAGE_SIZE
            params = {
                "dynamic": str(PAGE_SIZE),
                "posStart": str(pos),
                "count": str(PAGE_SIZE),
            }
            try:
                resp = session.get(DATA_URL, params=params, timeout=40)
                resp.raise_for_status()
            except Exception as exc:
                logger.error("Davidson roster page %s failed: %s", page, exc)
                break

            rows, page_total = self._parse_roster_xml(resp.content)
            if total is None and page_total is not None:
                total = page_total
            if not rows:
                break
            out.extend(rows)
            if total is not None and len(out) >= total:
                break
            if len(rows) < PAGE_SIZE:
                break
            time.sleep(REQUEST_PAUSE_S)
        return out

    def _parse_roster_xml(
        self, content: bytes
    ) -> Tuple[List[dict], Optional[int]]:
        """Parse dhtmlx XML; return (rows, total_count_or_None)."""
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            logger.error("Davidson roster XML parse failed: %s", exc)
            return [], None

        total: Optional[int] = None
        raw_total = root.attrib.get("total_count")
        if raw_total and str(raw_total).isdigit():
            total = int(raw_total)

        rows: List[dict] = []
        for row in root.findall("row"):
            parsed = self._parse_roster_row(row)
            if parsed:
                rows.append(parsed)
        return rows, total

    def _parse_roster_row(self, row: ET.Element) -> Optional[dict]:
        cells: List[str] = []
        for cell in row.findall("cell"):
            text = (cell.text or "").strip()
            text = re.sub(r"<[^>]+>", "", text).strip()
            cells.append(text)
        if len(cells) < 12:
            return None

        last, first, middle = cells[0], cells[1], cells[2]
        full = cells[3] or f"{last}, {first} {middle}".strip().strip(",")
        booking = cells[11].strip()
        if not _BOOKING_RE.fullmatch(booking):
            # Never invent DAV_ / name-derived keys.
            return None
        in_num = cells[12].strip() if len(cells) > 12 else ""
        if not in_num:
            in_num = (row.attrib.get("id") or "").strip()

        sex = (cells[7] if len(cells) > 7 else "")[:1].upper()
        return {
            "last": last,
            "first": first,
            "middle": middle,
            "full_name": full,
            "city": cells[5] if len(cells) > 5 else "",
            "sex": sex,
            "age": cells[8] if len(cells) > 8 else "",
            "height": cells[9] if len(cells) > 9 else "",
            "weight": cells[10] if len(cells) > 10 else "",
            "booking": booking,
            "in_num": in_num,
        }

    def _fetch_details(
        self, session: requests.Session, in_num: str, booking: str
    ) -> Optional[Tuple[str, str, str, str]]:
        try:
            resp = session.post(
                DETAILS_URL,
                params={"number": in_num, "curbook": booking},
                data="{}",
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Referer": PORTAL_URL,
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=30,
            )
            if resp.status_code != 200 or not resp.text.strip():
                return None
            return self._parse_details_html(resp.text)
        except Exception as exc:
            logger.debug("Davidson detail %s/%s failed: %s", in_num, booking, exc)
            return None

    @staticmethod
    def _parse_details_html(
        html: str,
    ) -> Optional[Tuple[str, str, str, str]]:
        """Return (booking_date, charges, bond, race) from detail HTML."""
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)

        booking_date = ""
        m = re.search(
            r"Incarceration Date:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}[^\n]*)",
            text,
            flags=re.I,
        )
        if m:
            booking_date = m.group(1).strip()

        race = ""
        m = re.search(r"Race:\s*([A-Za-z]+)", text, flags=re.I)
        if m:
            race = m.group(1).strip()

        charges: List[str] = []
        for table in soup.find_all("table"):
            headers = [
                c.get_text(" ", strip=True).lower()
                for c in (table.find("tr").find_all(["td", "th"]) if table.find("tr") else [])
            ]
            if not any("offense description" in h for h in headers):
                continue
            desc_idx = next(
                (i for i, h in enumerate(headers) if "offense description" in h),
                None,
            )
            if desc_idx is None:
                continue
            for tr in table.find_all("tr")[1:]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) <= desc_idx:
                    continue
                desc = cells[desc_idx].strip()
                if desc and desc.lower() != "offense description":
                    charges.append(desc)

        bond = "0"
        # Prefer Bail Bonds "Totals:" Remaining column when published.
        for table in soup.find_all("table"):
            headers = [
                c.get_text(" ", strip=True).lower()
                for c in (table.find("tr").find_all(["td", "th"]) if table.find("tr") else [])
            ]
            if not any("remaining" in h for h in headers):
                continue
            rem_idx = next(
                (i for i, h in enumerate(headers) if "remaining" in h), None
            )
            if rem_idx is None:
                continue
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if not cells:
                    continue
                label = cells[0].strip().lower()
                if "total" not in label:
                    continue
                if len(cells) <= rem_idx:
                    continue
                bond = DavidsonScraper._money_to_str(cells[rem_idx])
                break
            break

        if not booking_date and not charges and bond == "0":
            # Empty / error page
            if "inmate details" not in text.lower():
                return None

        return (
            booking_date,
            " | ".join(charges) if charges else "Unknown",
            bond,
            race,
        )

    @staticmethod
    def _money_to_str(raw: str) -> str:
        cleaned = re.sub(r"[^\d.]", "", (raw or "").replace(",", ""))
        if not cleaned:
            return "0"
        try:
            val = Decimal(cleaned)
        except InvalidOperation:
            return "0"
        if val == val.to_integral_value():
            return str(int(val))
        return str(val)
