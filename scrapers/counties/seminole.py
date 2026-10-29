"""
Seminole County Arrest Scraper — NorthPointe Custody Portal
Source: Seminole County Sheriff's Office
URL: https://seminole.northpointesuite.com/custodyportal
Method: plain requests JSON DoSearch (A–Z last-name prefixes) → detail pages

HISTORY:
- v1: Selenium roster click
- v2: nodriver / Playwright empty Search → ~500 inmates, then detail fetch
- v3 (current): Empty Search returns API error ("No response received").
  Headless Chrome is IIS-blocked ("permission denied"). curl_cffi still
  reaches DoSearch. Sweep LastName starts-with A–Z for current inmates,
  sort by personId descending (newer bookings first), then fetch details.
- v4 (2026-10-15): plain ``requests`` via ``scrapers.plain_http`` (honest
  User-Agent, TLS verified, no curl_cffi Chrome impersonation, no browser UA).
  A 401/403/429 or challenge raises EgressBlocked and a bad certificate raises
  SourceTLSInvalid; neither is swallowed per letter or per detail, so Health
  records the block instead of an empty "success". Status comes from the
  detail page only (blank when absent, never assumed "In Custody"). Bond is the
  published Total Bond only ("" when blank/NO BOND/HOLD/text, never "0"; a
  published $0 stays "0").
- 2026-10-15 (supersedes #208): Status is the detail page's published Status
  label only, recorded as extra_data.status_source = "published_detail_status";
  absent -> "" with no status_source. The DoSearch "current" filter is not used
  as custody proof (its scope could not be verified: the portal answers a
  Cloudflare 403 to the honest box probe). Detail errors never log names.
  See docs/recon/FL_SEMINOLE_HONEST_2026-10-15.md.
"""
from __future__ import annotations

import json
import logging
import re
import string
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import make_session, request
from scrapers.published_bond import charge_bond_amount, total_published_bond
from scrapers.scraper_resilience import AntiBotBlocked
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://seminole.northpointesuite.com"
PORTAL_URL = f"{BASE_URL}/custodyportal"
DOSEARCH_URL = f"{PORTAL_URL}/Home/DoSearch/"
DETAIL_URL = f"{PORTAL_URL}/Home/Details"
STATUS_SOURCE_DETAIL = "published_detail_status"
FACILITY = "John E Polk Correctional Facility"
DAYS_BACK = 14
# Detail pages are ~1.6 MB each — cap bandwidth; personId-desc prioritizes recent.
MAX_DETAIL_FETCHES = 200
LETTER_PAUSE_SEC = 0.35
DETAIL_PAUSE_SEC = 0.2


class SeminoleCountyScraper(BaseScraper):
    """Seminole County (FL) — NorthPointe Custody Portal via DoSearch API."""

    @property
    def county(self) -> str:
        return "Seminole"

    def scrape(self) -> List[ArrestRecord]:
        session = make_session()
        request(session, "GET", PORTAL_URL + "/", timeout=45, county="Seminole")

        inmates = self._roster_az(session)
        logger.info("Seminole: %d unique current inmates from A–Z DoSearch", len(inmates))
        if not inmates:
            return []

        # Newer personIds track newer bookings — fetch those first.
        inmates.sort(key=lambda x: x.get("personId") or 0, reverse=True)

        cutoff = datetime.now() - timedelta(days=DAYS_BACK)
        records: List[ArrestRecord] = []
        seen_people: set = set()
        fetched = 0

        for inmate in inmates:
            if fetched >= MAX_DETAIL_FETCHES:
                break
            person_id = inmate.get("personId")
            if not person_id or person_id in seen_people:
                continue
            try:
                record = self._fetch_detail(session, inmate, cutoff)
                fetched += 1
                seen_people.add(person_id)
                if record:
                    records.append(record)
            except AntiBotBlocked:
                raise
            except Exception as e:
                logger.debug("Seminole detail error (%s)", type(e).__name__)
            time.sleep(DETAIL_PAUSE_SEC)

        logger.info(
            "Seminole: %d records within %d days (details fetched=%d)",
            len(records),
            DAYS_BACK,
            fetched,
        )
        return records

    def _roster_az(self, session) -> List[dict]:
        """A–Z LastName starts-with sweep of current inmates via DoSearch."""
        seen: set = set()
        out: List[dict] = []
        base_params = {
            "LastName": "",
            "Age": "",
            "FirstName": "",
            "Race": "",
            "MiddleName": "",
            "Gender": "",
            "BookingNumber": "",
            "SearchCriteria": "current",
        }
        for letter in string.ascii_uppercase:
            params = {**base_params, "LastName": letter}
            query = f"&filter=LastName%3Asw%3A{letter}&inmatestatus=current"
            try:
                r = request(
                    session,
                    "GET",
                    DOSEARCH_URL,
                    params={
                        "searchParams": json.dumps(params),
                        "query": query,
                    },
                    timeout=120,
                    county="Seminole",
                )
                r.raise_for_status()
                data = r.json()
                err = (data.get("error") or {}).get("errorStatus")
                sd = data.get("searchData") or {}
                arr = sd.get("resultArray") or []
                new = 0
                for row in arr:
                    pid = row.get("personId")
                    if pid and pid not in seen:
                        seen.add(pid)
                        out.append(row)
                        new += 1
                logger.info(
                    "Seminole DoSearch %s: %s (+%d unique, limited=%s)%s",
                    letter,
                    sd.get("resultCount"),
                    new,
                    sd.get("resultCountWasLimited"),
                    f" err={err}" if err else "",
                )
            except AntiBotBlocked:
                raise
            except Exception as e:
                logger.warning("Seminole DoSearch %s failed: %s", letter, e)
            time.sleep(LETTER_PAUSE_SEC)
        return out

    def _fetch_detail(
        self, session, inmate: dict, cutoff: datetime
    ) -> Optional[ArrestRecord]:
        """Fetch the detail page for one inmate and extract booking info."""
        from bs4 import BeautifulSoup

        data_param = json.dumps(inmate)
        try:
            r = request(
                session,
                "GET",
                DETAIL_URL,
                params={"data": data_param},
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": PORTAL_URL,
                },
                timeout=60,
                county="Seminole",
            )
            r.raise_for_status()
        except AntiBotBlocked:
            raise
        except Exception as e:
            raise RuntimeError(f"Detail fetch failed: {e}")

        soup = BeautifulSoup(r.text, "html.parser")

        booking_header = soup.find("div", class_="bookingHeader")
        if not booking_header:
            return None
        bk_match = re.search(r"Booking\s*-\s*(\d+)", booking_header.get_text())
        booking_num = bk_match.group(1) if bk_match else ""
        if not booking_num:
            return None

        booking_date = self._label_next(soup, r"^Booking Date$")
        if booking_date:
            try:
                bd = datetime.strptime(booking_date, "%m/%d/%Y")
                if bd < cutoff:
                    return None
            except ValueError:
                pass

        status = self._label_next(soup, r"^Status$")
        bond = self._label_next(soup, r"^Total Bond$")
        release_date = self._label_next(soup, r"^Projected Release Date$") or ""

        charges = []
        for div in soup.find_all("div", class_=re.compile(r"chargeRow|chargeData", re.I)):
            text = div.get_text(separator=" ", strip=True)
            if text:
                charges.append(text)

        first = inmate.get("firstName", "") or ""
        last = inmate.get("lastName", "") or ""
        middle = inmate.get("middleName") or ""
        full_name = f"{last}, {first}" + (f" {middle}" if middle else "")

        dob_raw = inmate.get("dateOfBirth", "") or ""
        dob = ""
        if dob_raw:
            try:
                dob = datetime.fromisoformat(
                    dob_raw.replace("T00:00:00", "")
                ).strftime("%m/%d/%Y")
            except ValueError:
                dob = dob_raw[:10]

        rec = ArrestRecord(
            County=self.county,
            State="FL",
            Facility=FACILITY,
            Full_Name=full_name.upper(),
            First_Name=first.upper(),
            Middle_Name=middle.upper(),
            Last_Name=last.upper(),
            DOB=dob,
            Booking_Number=booking_num,
            Booking_Date=booking_date,
            Arrest_Date=booking_date,
            Status=status,
            Release_Date=release_date,
            Charges=" | ".join(charges),
            Bond_Amount=self._parse_bond(bond),
            Race=inmate.get("race", "") or "",
            Sex=inmate.get("gender", "") or "",
            Height=inmate.get("height", "") or "",
            Weight=inmate.get("weight", "") or "",
            Person_ID=str(inmate.get("personId") or ""),
            Detail_URL=f"{DETAIL_URL}?data={data_param}",
            Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
            LastChecked=datetime.now(timezone.utc).isoformat(),
            LastCheckedMode="INITIAL",
        )
        if status:
            extra = dict(getattr(rec, "extra_data", None) or {})
            extra["status_source"] = STATUS_SOURCE_DETAIL
            rec.extra_data = extra
        return rec

    @staticmethod
    def _label_next(soup, label_pattern: str) -> str:
        """Find a label by regex and return the text of the next sibling element."""
        el = soup.find(string=re.compile(label_pattern))
        if not el:
            return ""
        nxt = el.parent.find_next_sibling()
        return nxt.get_text(strip=True) if nxt else ""

    @staticmethod
    def _parse_bond(bond_str: str) -> str:
        """Published Total Bond only: "" when unpublished, never an invented "0"."""
        return total_published_bond([charge_bond_amount(bond_str)])
