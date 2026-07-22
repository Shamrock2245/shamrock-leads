"""
Collin County (TX) Arrest Scraper — Sheriff's Office Inmate Lookup.

Portal: https://www.collincountytx.gov/sheriff/inmate-information
        https://apps.collincountytx.gov/JailInmates/
Collin County is the 6th-largest TX county (~1.1M pop) in the DFW metro.
The portal is protected by Incapsula WAF; uses stealth stack with residential
proxy rotation (APE) and curl_cffi TLS impersonation to access the inmate list.
Fallback: Tyler/Odyssey PublicAccess JailingSearch on cijspub.co.collin.tx.us.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import List, Set, Tuple

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from scrapers.stealth_utils import make_stealth_request
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# Primary: Collin County Sheriff inmate lookup (behind Incapsula)
INMATE_URL = "https://apps.collincountytx.gov/JailInmates/"
# Fallback: Tyler/Odyssey PublicAccess
ODYSSEY_BASE = "https://cijspub.co.collin.tx.us/PublicAccess/"
ODYSSEY_JAILING = f"{ODYSSEY_BASE}JailingSearch.aspx?ID=400"
PORTAL_URL = "https://www.collincountytx.gov/sheriff/inmate-information"


class CollinScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Collin"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []

        # Strategy 1: Try primary inmate portal (apps.collincountytx.gov)
        try:
            records = self._scrape_inmate_portal()
        except Exception as e:
            logger.warning(f"Collin primary portal failed: {e}")

        # Strategy 2: Fallback to Odyssey PublicAccess JailingSearch
        if not records:
            try:
                records = self._scrape_odyssey_jailing()
            except Exception as e:
                logger.warning(f"Collin Odyssey fallback failed: {e}")

        logger.info(
            f"✅ Collin (TX): {len(records)} records in {time.time() - start:.1f}s"
        )
        return records

    def _scrape_inmate_portal(self) -> List[ArrestRecord]:
        """Scrape the Collin County Sheriff inmate portal (ASP.NET)."""
        out: List[ArrestRecord] = []
        seen: Set[str] = set()

        # The portal serves a paginated inmate list; try landing page first
        resp = make_stealth_request(INMATE_URL, method="GET", timeout=30)
        if not resp or resp.status_code != 200:
            logger.debug(
                f"Collin inmate portal returned {resp.status_code if resp else 'None'}"
            )
            return []

        html = resp.text
        if not html or len(html) < 500:
            return []

        # Check if we got blocked by Incapsula
        if "Incapsula" in html or "_Incapsula_Resource" in html:
            logger.debug("Collin: Incapsula challenge detected, will retry with APE")
            return []

        out = self._parse_inmate_table(html, seen)

        # Try pagination if available (ASP.NET postback or page links)
        page = 2
        while page <= 10:
            next_url = f"{INMATE_URL}?page={page}"
            try:
                resp = make_stealth_request(next_url, method="GET", timeout=20)
                if not resp or resp.status_code != 200:
                    break
                batch = self._parse_inmate_table(resp.text, seen)
                if not batch:
                    break
                out.extend(batch)
                page += 1
            except Exception:
                break

        return out

    def _scrape_odyssey_jailing(self) -> List[ArrestRecord]:
        """Fallback: Tyler/Odyssey PublicAccess JailingSearch."""
        out: List[ArrestRecord] = []
        seen: Set[str] = set()

        # Step 1: Get session cookie from default.aspx
        try:
            make_stealth_request(
                f"{ODYSSEY_BASE}default.aspx", method="GET", timeout=20
            )
        except Exception:
            pass

        # Step 2: GET JailingSearch page for ViewState
        try:
            resp = make_stealth_request(ODYSSEY_JAILING, method="GET", timeout=20)
            if not resp or resp.status_code != 200:
                return []
        except Exception:
            return []

        html = resp.text
        vs = re.search(
            r'name="__VIEWSTATE" id="__VIEWSTATE" value="([^"]*)"', html
        )
        vsg = re.search(
            r'name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="([^"]*)"',
            html,
        )
        ev = re.search(
            r'name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="([^"]*)"', html
        )

        if not vs:
            logger.debug("Collin Odyssey: no ViewState found")
            return []

        # Step 3: Search A-Z last-name prefixes
        for prefix in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            try:
                data = {
                    "__EVENTTARGET": "",
                    "__EVENTARGUMENT": "",
                    "__VIEWSTATE": vs.group(1),
                    "__VIEWSTATEGENERATOR": vsg.group(1) if vsg else "",
                    "__EVENTVALIDATION": ev.group(1) if ev else "",
                    "LastName": prefix,
                    "FirstName": "",
                    "MiddleName": "",
                    "DateOfBirth": "",
                    "BookingNumber": "",
                    "SearchSubmit": "Search",
                    "SearchType": "JailingSearch",
                    "NameTypeKy": "0",
                    "BaseConnKy": "1",
                    "BondStatusType": "All",
                }
                resp = make_stealth_request(
                    ODYSSEY_JAILING, method="POST", data=data, timeout=25
                )
                if not resp or resp.status_code != 200:
                    continue

                batch = self._parse_odyssey_results(resp.text, seen)
                out.extend(batch)

                # Update ViewState for next POST
                new_vs = re.search(
                    r'name="__VIEWSTATE" id="__VIEWSTATE" value="([^"]*)"',
                    resp.text,
                )
                if new_vs:
                    vs = new_vs
                new_ev = re.search(
                    r'name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="([^"]*)"',
                    resp.text,
                )
                if new_ev:
                    ev = new_ev

            except Exception as e:
                logger.debug(f"Collin Odyssey prefix={prefix} failed: {e}")

        return out

    def _parse_inmate_table(self, html: str, seen: Set[str]) -> List[ArrestRecord]:
        """Parse inmate records from the Sheriff portal HTML table."""
        soup = BeautifulSoup(html, "html.parser")
        out: List[ArrestRecord] = []

        # Find the main inmate table
        table = None
        for t in soup.find_all("table"):
            rows = t.find_all("tr")
            if not rows:
                continue
            headers = [
                th.get_text(" ", strip=True).lower()
                for th in rows[0].find_all(["th", "td"])
            ]
            joined = " ".join(headers)
            if "name" in joined and ("book" in joined or "charge" in joined or "bond" in joined):
                table = t
                break

        if table is None:
            # Fallback: largest table with > 5 rows
            tables = soup.find_all("table")
            candidates = [t for t in tables if len(t.find_all("tr")) > 5]
            if not candidates:
                return []
            table = max(candidates, key=lambda t: len(t.find_all("tr")))

        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        headers = [
            th.get_text(" ", strip=True).lower()
            for th in rows[0].find_all(["th", "td"])
        ]

        for row in rows[1:]:
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if len(cells) < 3:
                continue

            name = ""
            booking = ""
            charges = ""
            bond = ""
            book_date = ""

            for i, h in enumerate(headers):
                if i >= len(cells):
                    break
                val = cells[i]
                if "name" in h and not name:
                    name = val
                elif "book" in h and "number" in h:
                    booking = val
                elif "book" in h and "date" in h:
                    book_date = val
                elif "charge" in h:
                    charges = val
                elif "bond" in h or "bail" in h:
                    bond = val

            # Positional fallback
            if not name and cells:
                name = cells[0]

            name = (name or "").strip()
            if not name or len(name) < 2:
                continue

            if not booking:
                booking = f"COL_{hashlib.md5(f'{name}|{book_date}|COLLIN'.encode()).hexdigest()[:10]}"

            if booking in seen:
                continue
            seen.add(booking)

            first, last = self._split_name(name)
            bond_clean = re.sub(r"[^0-9.]", "", bond) if bond else "0"

            out.append(
                ArrestRecord(
                    County=self.county,
                    State=self.state,
                    Full_Name=name.title() if name.isupper() else name,
                    First_Name=first,
                    Last_Name=last,
                    Booking_Number=booking,
                    Booking_Date=book_date,
                    Arrest_Date=book_date,
                    Charges=charges or "Unknown",
                    Bond_Amount=bond_clean or "0",
                    Status="In Custody",
                    Facility="Collin County Detention Facility",
                    Agency="Collin County Sheriff's Office",
                    Detail_URL=PORTAL_URL,
                )
            )

        return out

    def _parse_odyssey_results(
        self, html: str, seen: Set[str]
    ) -> List[ArrestRecord]:
        """Parse Tyler/Odyssey JailingSearch result rows."""
        soup = BeautifulSoup(html, "html.parser")
        out: List[ArrestRecord] = []

        # Odyssey results use <a> links to JailingDetail.aspx
        links = soup.find_all("a", href=re.compile(r"JailingDetail\.aspx"))
        for link in links:
            row = link.find_parent("tr")
            if not row:
                continue

            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if len(cells) < 3:
                continue

            # Typical Odyssey columns: Case#, Name, BookingDate, Charges, BondStatus
            name = ""
            case_num = ""
            book_date = ""
            charges = ""

            if len(cells) >= 5:
                case_num = cells[0]
                name = cells[1]
                book_date = cells[2]
                charges = cells[3]
            elif len(cells) >= 3:
                name = cells[0]
                book_date = cells[1]
                charges = cells[2]

            name = (name or "").strip()
            if not name or len(name) < 2:
                continue

            booking = case_num or f"COL_{hashlib.md5(f'{name}|{book_date}'.encode()).hexdigest()[:10]}"
            if booking in seen:
                continue
            seen.add(booking)

            first, last = self._split_name(name)

            out.append(
                ArrestRecord(
                    County=self.county,
                    State=self.state,
                    Full_Name=name.title() if name.isupper() else name,
                    First_Name=first,
                    Last_Name=last,
                    Booking_Number=booking,
                    Booking_Date=book_date,
                    Arrest_Date=book_date,
                    Charges=charges or "Unknown",
                    Bond_Amount="0",
                    Status="In Custody",
                    Facility="Collin County Detention Facility",
                    Agency="Collin County Sheriff's Office",
                    Detail_URL=PORTAL_URL,
                )
            )

        return out

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        name = name.replace("\xa0", " ").strip()
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip().title()
            first = parts[1].strip().title()
            return first, last
        bits = name.split()
        if len(bits) >= 2:
            return bits[0].title(), bits[-1].title()
        return name.title(), ""
