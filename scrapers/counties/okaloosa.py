"""
Okaloosa County (FL) Arrest Scraper — Inmate Locator (ProPhoenix / Infragistics).

Source contract (recon 2026-10-07, docs/recon/FL_IDLE_EIGHT_2026-10-07.md):
  * URL: https://okaloosacountyjail.myokaloosa.com/InmateLocator/Default.aspx
    (linked from https://www.sheriff-okaloosa.org/)
  * Plain HTTPS ASP.NET WebForms search; A–Z last-name sweeps cover the roster.
  * Source-issued Booking# is a 10-digit value (YYYY + sequence, e.g. 2026005082).
  * Rows without Booking# are dropped — no invented keys.
  * The root ``/InmateLocator/`` path is an Angular shell; the public roster is the
    legacy Default.aspx form, not the SPA.
"""
from __future__ import annotations

import logging
import re
import string
import time
from typing import List

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://okaloosacountyjail.myokaloosa.com"
SEARCH_URL = f"{BASE_URL}/InmateLocator/Default.aspx"
FACILITY = "Okaloosa County Jail"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": SEARCH_URL,
}

LAST_NAME_FIELD = "_ctl0:CpnlMain:ctrlUsrSrchTools:txtLastName"
FIRST_NAME_FIELD = "_ctl0:CpnlMain:ctrlUsrSrchTools:txtFirstName"
DOB_FIELD = "_ctl0:CpnlMain:ctrlUsrSrchTools:txtDOB"
SEARCH_BTN = "_ctl0:CpnlMain:ctrlUsrSrchTools:cmdSearch"
_BOOKING_RE = re.compile(r"^\d{8,12}$")


class OkaloosaCountyScraper(BaseScraper):
    """Okaloosa County (FL) — Inmate Locator Default.aspx (Crestview)."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Okaloosa"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        session = requests.Session()
        session.headers.update(HEADERS)
        resp = session.get(SEARCH_URL, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        base_post = self._hidden_fields(soup)
        if "__VIEWSTATE" not in base_post:
            raise RuntimeError("Okaloosa: Default.aspx search form missing __VIEWSTATE")

        all_records: List[ArrestRecord] = []
        seen: set[str] = set()
        for letter in string.ascii_uppercase:
            post = dict(base_post)
            post[LAST_NAME_FIELD] = letter
            post[FIRST_NAME_FIELD] = ""
            post[DOB_FIELD] = ""
            post[SEARCH_BTN] = "Search"
            try:
                resp = session.post(SEARCH_URL, data=post, timeout=45)
                if resp.status_code != 200:
                    logger.warning("Okaloosa letter %s HTTP %s", letter, resp.status_code)
                    continue
                soup_post = BeautifulSoup(resp.text, "html.parser")
                base_post.update(self._hidden_fields(soup_post))
                for rec in self._parse_soup(soup_post):
                    key = rec.Booking_Number
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    all_records.append(rec)
            except Exception as exc:
                logger.warning("Okaloosa letter %s failed: %s", letter, exc)
            time.sleep(0.25)

        logger.info("Okaloosa: %d total records from A-Z search", len(all_records))
        return all_records

    @staticmethod
    def _hidden_fields(soup) -> dict:
        out = {}
        for inp in soup.find_all("input"):
            name = inp.get("name") or ""
            if not name:
                continue
            typ = (inp.get("type") or "").lower()
            if typ == "hidden" or name.startswith("__"):
                out[name] = inp.get("value") or ""
        return out

    def _parse_soup(self, soup) -> List[ArrestRecord]:
        """Parse Infragistics search-results tables.

        The grid flattens header captions and inmate fields into long ``<td>``
        streams. Each inmate exposes a source Booking# (10-digit ``20YY######``),
        often duplicated a few cells later. We anchor on Booking# and walk
        backward for name parts — never invent keys.
        """
        records: List[ArrestRecord] = []
        seen: set[str] = set()

        best: list[str] = []
        for table in soup.find_all("table"):
            cells = [c.get_text(strip=True) for c in table.find_all(["th", "td"])]
            if "Booking#" in cells and "LastName" in cells and len(cells) > len(best):
                best = cells
        if not best:
            return []

        # Data begins after the last header caption block.
        try:
            booking_caption = len(best) - 1 - best[::-1].index("Booking#")
        except ValueError:
            return []
        header_end = booking_caption
        for label in ("EligReleaseDate", "Weight", "Height", "Race", "Sex", "Age", "DOB", "Name", "SPN#"):
            if label in best[booking_caption : booking_caption + 12]:
                header_end = best.index(label, booking_caption)
                break
        data = best[header_end + 1 :]

        booking_idxs = [i for i, c in enumerate(data) if _BOOKING_RE.match(c)]
        # Prefer the first of each duplicate pair (same value ~7 cells later).
        primaries: list[int] = []
        skip: set[int] = set()
        for i in booking_idxs:
            if i in skip:
                continue
            if i + 7 < len(data) and data[i + 7] == data[i]:
                primaries.append(i)
                skip.add(i + 7)
            elif i - 7 >= 0 and data[i - 7] == data[i]:
                continue
            else:
                primaries.append(i)

        captions = {
            "nametypeid", "nametype", "nametitle", "lastname", "firstname", "middlename",
            "namesuffix", "rtc", "eye", "hair", "skin", "booking#", "booking", "spn#",
            "spn", "name", "dob", "age", "sex", "race", "height", "weight", "eligreleasedate",
        }

        for bi in primaries:
            booking_num = data[bi]
            if booking_num in seen:
                continue
            # Walk backward for LastName / FirstName / MiddleName.
            window = data[max(0, bi - 16) : bi]
            # Prefer a LAST,FIRST display name after the booking when present.
            full_name = ""
            for c in data[bi + 1 : bi + 6]:
                if "," in c and re.search(r"[A-Za-z]", c) and len(c) < 60:
                    full_name = c
                    break
            last_name = first_name = middle_name = ""
            # Candidate name tokens: alphabetic, not captions, not dates/colors.
            name_tokens = []
            for c in window:
                cl = c.lower()
                if not c or cl in captions or _BOOKING_RE.match(c):
                    continue
                if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", c):
                    continue
                if re.match(r"^[A-Z]{3}$", c):  # eye/hair codes
                    continue
                if re.fullmatch(r"\d+", c):
                    continue
                if re.search(r"[A-Za-z]", c) and len(c) <= 40:
                    name_tokens.append(c)
            if full_name:
                first_name, middle_name, last_name = self._pn(full_name)
            elif len(name_tokens) >= 2:
                last_name, first_name = name_tokens[0], name_tokens[1]
                middle_name = name_tokens[2] if len(name_tokens) > 2 else ""
            else:
                continue
            if not last_name or not first_name:
                continue
            if not full_name:
                full_name = f"{last_name}, {first_name}"
                if middle_name:
                    full_name += f" {middle_name}"

            dob = ""
            for c in data[bi + 1 : bi + 8]:
                if re.match(r"^\d{1,2}/\d{2,4}$", c) or re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", c):
                    dob = c
                    break
            sex = next((c for c in data[bi + 1 : bi + 10] if c in ("M", "F")), "")
            race = ""
            for c in data[bi + 1 : bi + 12]:
                if c in ("W", "B", "H", "A", "I", "O", "U") and c != sex:
                    race = c
                    break

            seen.add(booking_num)
            records.append(
                ArrestRecord(
                    County=self.county,
                    State="FL",
                    Booking_Number=booking_num,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    DOB=dob,
                    Sex=sex,
                    Race=race,
                    Status="In Custody",
                    Facility=FACILITY,
                    Detail_URL=SEARCH_URL,
                    LastCheckedMode="INITIAL",
                )
            )
        return records

    @staticmethod
    def _pn(n: str):
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
