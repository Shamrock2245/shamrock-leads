"""
Brazoria County (TX) Arrest Scraper — Tyler Odyssey JailAccess.

Portal: https://portal-txbrazoria.tylertech.cloud/JailAccess/
Search: JailingSearch.aspx?ID=400

Requires:
  1) GET default.aspx for ASP.NET session + .ASPXFORMSPUBLICACCESS
  2) GET search form for ViewState
  3) POST with SearchType=PARTYNAME, NameTypeKy=ALIAS, RadioSearchType=1
"""
from __future__ import annotations

import hashlib
import logging
import re
import string
import time
from typing import List, Set
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PORTAL = "https://portal-txbrazoria.tylertech.cloud/JailAccess/"
DEFAULT = PORTAL + "default.aspx"
SEARCH = PORTAL + "JailingSearch.aspx?ID=400"
FACILITY = "Brazoria County Detention Center"
AGENCY = "Brazoria County Sheriff's Office"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

NAV_NOISE = re.compile(
    r"skip to|logout|my account|help|public access|search|no matches|"
    r"tyler|brazoria county$|configured|odyssey",
    re.I,
)


class BrazoriaScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Brazoria"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen: Set[str] = set()

        # Odyssey requires BOTH first + last name. Wildcards */* or _/_ return
        # the public jailing roster (capped ~200 rows per query).
        for last, first in (("*", "*"), ("_", "_")):
            try:
                batch = self._search_name(last, first, seen)
                records.extend(batch)
                logger.info(f"Brazoria {last}/{first}: +{len(batch)} (total {len(records)})")
                if len(records) >= 50:
                    break
                time.sleep(0.4)
            except Exception as e:
                logger.warning(f"Brazoria wildcard {last}/{first}: {e}")

        # Light letter-pair supplement only if wildcards empty
        if not records:
            for last in ("S", "M", "B", "H", "J", "W", "C", "A"):
                for first in ("A", "J", "M", "R", "S", "D", "L", "C"):
                    try:
                        batch = self._search_name(last, first, seen)
                        records.extend(batch)
                        time.sleep(0.2)
                    except Exception as e:
                        logger.debug(f"Brazoria {last}/{first}: {e}")

        if not records:
            try:
                records = self._scrape_browser(seen)
            except Exception as e:
                logger.warning(f"Brazoria browser failed: {e}")

        logger.info(f"✅ Brazoria (TX): {len(records)} records in {time.time() - start:.1f}s")
        return records

    def _search_name(self, last: str, first: str, seen: Set[str]) -> List[ArrestRecord]:
        session = requests.Session()
        session.headers.update(HEADERS)
        session.get(DEFAULT, timeout=30)
        r = session.get(SEARCH, timeout=30)
        r.raise_for_status()
        html = r.text
        vs = self._field(html, "__VIEWSTATE")
        vsg = self._field(html, "__VIEWSTATEGENERATOR")
        ev = self._field(html, "__EVENTVALIDATION")
        if not vs:
            return []

        data = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": vs,
            "__VIEWSTATEGENERATOR": vsg or "",
            "__EVENTVALIDATION": ev or "",
            "RadioSearchType": "1",  # Party name
            "LastName": last,
            "FirstName": first,
            "MiddleName": "",
            "BookingNumber": "",
            "DateOfBirth": "",
            "DateBookingOnAfter": "",
            "DateBookingOnBefore": "",
            "DateReleasedOnAfter": "",
            "DateReleasedOnBefore": "",
            "BondStatusType": "0",  # All
            "DatePostedOnAfter": "",
            "DatePostedOnBefore": "",
            "SearchSubmit": "Search",
            "SearchType": "PARTYNAME",
            "NameTypeKy": "ALIAS",
            "BaseConnKy": "",
            "ShowInactive": "",
            "StatusType": "",
            "AllStatusTypes": "",
            "BondCompany": "",
            "NodeID": "98,3100",
            "ProductType": "",
            "SearchParams": "",
        }
        resp = session.post(
            SEARCH,
            data=data,
            timeout=60,
            headers={
                "Referer": SEARCH,
                "Origin": "https://portal-txbrazoria.tylertech.cloud",
            },
        )
        if "ErrorOccured" in resp.url or resp.status_code != 200:
            logger.debug(f"Brazoria {last}/{first}: error page {resp.url}")
            return []
        return self._parse_results(resp.text, seen)

    def _parse_results(self, html: str, seen: Set[str]) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[ArrestRecord] = []

        # Prefer JailingDetail links (authoritative Odyssey rows).
        # Link text is often the booking #; party name is usually a sibling cell.
        links = soup.find_all("a", href=re.compile(r"JailingDetail\.aspx", re.I))
        if links:
            for link in links:
                row = link.find_parent("tr")
                if not row:
                    continue
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                link_text = (link.get_text(" ", strip=True) or "").strip()

                name = next((c for c in cells if self._looks_like_name(c)), "")
                if not name and self._looks_like_name(link_text):
                    name = link_text
                if not name:
                    continue

                booking = ""
                book_date = ""
                charges = "Unknown"
                # Booking often equals link text when it's numeric
                if re.match(r"^\d{4,}$", link_text):
                    booking = link_text
                for c in cells:
                    if re.match(r"^\d{4,}$", c) and not booking:
                        booking = c
                    elif re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", c) and not book_date:
                        book_date = c
                    elif self._looks_like_charge(c) and charges == "Unknown":
                        charges = c
                if not booking:
                    href = link.get("href") or ""
                    m = re.search(
                        r"(?:CaseID|JailingID|ID)=([A-Za-z0-9\-]+)", href, re.I
                    )
                    booking = (
                        m.group(1)
                        if m
                        else hashlib.sha1(
                            f"brazoria|{name}|{book_date}".encode()
                        ).hexdigest()[:12]
                    )
                if booking in seen:
                    continue
                seen.add(booking)
                first, middle, last = self._pn(name)
                detail = urljoin(SEARCH, link.get("href") or "")
                out.append(
                    ArrestRecord(
                        County=self.county,
                        State="TX",
                        Full_Name=name,
                        First_Name=first,
                        Middle_Name=middle,
                        Last_Name=last,
                        Booking_Number=str(booking),
                        Booking_Date=book_date,
                        Charges=charges,
                        Bond_Amount="0",
                        Status="In Custody",
                        Facility=FACILITY,
                        Agency=AGENCY,
                        Detail_URL=detail,
                        LastCheckedMode="INITIAL",
                    )
                )
            return out

        # Fallback: table scan with strict name filter
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            for tr in rows[1:]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
                if len(cells) < 2:
                    continue
                name = next((c for c in cells if self._looks_like_name(c)), "")
                if not name:
                    continue
                booking = next((c for c in cells if re.match(r"^\d{5,}$", c)), "")
                if not booking:
                    booking = hashlib.sha1(f"brazoria|{name}".encode()).hexdigest()[:12]
                if booking in seen:
                    continue
                seen.add(booking)
                first, middle, last = self._pn(name)
                out.append(
                    ArrestRecord(
                        County=self.county,
                        State="TX",
                        Full_Name=name,
                        First_Name=first,
                        Middle_Name=middle,
                        Last_Name=last,
                        Booking_Number=str(booking),
                        Charges="Unknown",
                        Bond_Amount="0",
                        Status="In Custody",
                        Facility=FACILITY,
                        Agency=AGENCY,
                        Detail_URL=SEARCH,
                        LastCheckedMode="INITIAL",
                    )
                )
        return out

    def _scrape_browser(self, seen: Set[str]) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage
        except Exception as e:
            logger.error(f"Brazoria browser unavailable: {e}")
            return []
        page = None
        out: List[ArrestRecord] = []
        try:
            co = self._get_browser_options() if hasattr(self, "_get_browser_options") else None
            page = ChromiumPage(co) if co else ChromiumPage()
            page.get(DEFAULT)
            time.sleep(1)
            page.get(SEARCH)
            time.sleep(1.5)
            for letter in list(string.ascii_uppercase)[:10]:
                try:
                    el = page.ele("#LastName", timeout=2) or page.ele(
                        "css:input[name=LastName]", timeout=1
                    )
                    if el:
                        el.clear()
                        el.input(letter)
                    btn = page.ele("#SearchSubmit", timeout=1)
                    if btn:
                        btn.click()
                        time.sleep(1.5)
                    batch = self._parse_results(page.html or "", seen)
                    out.extend(batch)
                except Exception as e:
                    logger.debug(f"Brazoria browser {letter}: {e}")
        finally:
            try:
                if page:
                    page.quit()
            except Exception:
                pass
        return out

    @staticmethod
    def _field(html: str, name: str) -> str:
        m = re.search(rf'id="{name}" value="([^"]*)"', html)
        return m.group(1) if m else ""

    @staticmethod
    def _looks_like_charge(s: str) -> bool:
        if not s or len(s) < 4 or len(s) > 200:
            return False
        if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}", s):
            return False
        if re.match(r"^\d+$", s):
            return False
        # charge-ish tokens
        return bool(
            re.search(
                r"assault|theft|poss|dwi|dwls|warrant|bond|drug|burgl|robbery|"
                r"firearm|weapon|traffic|violate|other agency|felony|misd",
                s,
                re.I,
            )
        )

    @staticmethod
    def _looks_like_name(s: str) -> bool:
        if not s or len(s) < 3 or len(s) > 80:
            return False
        if NAV_NOISE.search(s):
            return False
        if re.match(r"^[\d\$\.,\s/]+$", s):
            return False
        if not re.search(r"[A-Za-z]{2,}", s):
            return False
        # Prefer Last, First pattern or multi-word alpha
        if "," in s:
            return True
        words = [w for w in re.split(r"\s+", s) if re.search(r"[A-Za-z]", w)]
        return len(words) >= 2

    @staticmethod
    def _pn(n: str):
        n = " ".join((n or "").strip().split())
        if "," in n:
            last, rest = n.split(",", 1)
            p = rest.strip().split()
            return (p[0] if p else ""), (" ".join(p[1:]) if len(p) > 1 else ""), last.strip()
        p = n.split()
        return (
            (p[0] if p else ""),
            (" ".join(p[1:-1]) if len(p) > 2 else ""),
            (p[-1] if len(p) > 1 else n),
        )
