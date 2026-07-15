"""
Dallas County (TX) Arrest Scraper — Official Jail Lookup System.

Portal: https://www.dallascounty.org/jaillookup/
Search requires last name + first name + race + sex.
Strategy: single-letter prefix grid (A–Z × A–Z × race × sex) with dedup.

Dallas County is the 2nd-largest TX county (~2.6M). Full grid is rate-limited
and section-rotated so a 60–90m interval stays respectful.
"""
from __future__ import annotations

import hashlib
import logging
import string
import time
from typing import List, Set, Tuple

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.dallascounty.org/jaillookup/searchByName"
LANDING_URL = "https://www.dallascounty.org/jaillookup/search.jsp"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Origin": "https://www.dallascounty.org",
    "Referer": LANDING_URL,
}

RACES = ("White", "Black", "Hispanic", "Asian")
SEXES = ("Male", "Female")
REQUEST_PAUSE = 0.12
# Hard cap per run (~10–12 min worst case)
MAX_REQUESTS = 900


class DallasScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Dallas"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)
        session.verify = False

        records: List[ArrestRecord] = []
        seen: Set[str] = set()
        reqs = 0

        # Rotate last-name letter block by hour so multi-run covers the alphabet.
        hour = time.localtime().tm_hour
        letters = list(string.ascii_uppercase)
        # 26 letters / ~8 blocks → ~4 letters per hour-block, shift by hour
        block_size = 4
        offset = (hour * block_size) % 26
        last_letters = letters[offset:] + letters[:offset]

        try:
            session.get(LANDING_URL, timeout=20)
        except Exception as e:
            logger.warning(f"Dallas landing failed: {e}")

        for last_l in last_letters:
            if reqs >= MAX_REQUESTS:
                break
            for first_l in letters:
                if reqs >= MAX_REQUESTS:
                    break
                for race in RACES:
                    if reqs >= MAX_REQUESTS:
                        break
                    for sex in SEXES:
                        if reqs >= MAX_REQUESTS:
                            break
                        try:
                            batch = self._search(
                                session,
                                last_name=last_l,
                                first_name=first_l,
                                race=race,
                                sex=sex,
                            )
                            reqs += 1
                        except Exception as e:
                            logger.debug(
                                f"Dallas {last_l}/{first_l}/{race}/{sex}: {e}"
                            )
                            reqs += 1
                            time.sleep(REQUEST_PAUSE)
                            continue

                        for rec in batch:
                            key = rec.Booking_Number
                            if not key or key in seen:
                                continue
                            seen.add(key)
                            records.append(rec)
                        time.sleep(REQUEST_PAUSE)

        logger.info(
            f"✅ Dallas (TX): {len(records)} records "
            f"({reqs} requests) in {time.time() - start:.1f}s"
        )
        return records

    def _search(
        self,
        session: requests.Session,
        last_name: str,
        first_name: str,
        race: str,
        sex: str,
    ) -> List[ArrestRecord]:
        data = {
            "lastName": last_name,
            "firstName": first_name,
            "race": race,
            "sex": sex,
            "dobYear": "",
        }
        resp = session.post(SEARCH_URL, data=data, timeout=25)
        if resp.status_code != 200:
            return []
        return self._parse_results(resp.text)

    def _parse_results(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        table = None
        for t in soup.find_all("table"):
            headers = [
                th.get_text(" ", strip=True).lower()
                for th in t.find_all("tr")[0].find_all(["th", "td"])
            ] if t.find_all("tr") else []
            joined = " ".join(headers)
            if "defendant" in joined or "bookin" in joined:
                table = t
                break
        if table is None:
            return []

        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        headers = [
            th.get_text(" ", strip=True).lower()
            for th in rows[0].find_all(["th", "td"])
        ]
        out: List[ArrestRecord] = []

        for row in rows[1:]:
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if len(cells) < 3:
                continue

            # Map by header when possible
            name = ""
            race_sex = ""
            dob = ""
            book_date = ""
            book_num = ""
            for i, h in enumerate(headers):
                if i >= len(cells):
                    break
                val = cells[i]
                if "defendant" in h or h == "name":
                    name = val
                elif "race" in h or "sex" in h:
                    race_sex = val
                elif "dob" in h or "birth" in h:
                    dob = val
                elif "bookin date" in h or "book" in h and "date" in h:
                    book_date = val
                elif "bookin number" in h or ("book" in h and "number" in h):
                    book_num = val

            # Positional fallback: ['', name, race/sex, dob, date, number]
            if not name and len(cells) >= 2:
                name = cells[1] if cells[0] == "" else cells[0]
            if not race_sex and len(cells) >= 3:
                race_sex = cells[2] if cells[0] == "" else cells[1]
            if not dob and len(cells) >= 4:
                dob = cells[3] if cells[0] == "" else cells[2]
            if not book_date and len(cells) >= 5:
                book_date = cells[4] if cells[0] == "" else cells[3]
            if not book_num and len(cells) >= 6:
                book_num = cells[5] if cells[0] == "" else cells[4]
            elif not book_num and len(cells) >= 5:
                # sometimes book number is last cell
                book_num = cells[-1]

            name = (name or "").replace("\xa0", " ").strip()
            if not name or len(name) < 2:
                continue

            if not book_num:
                book_num = (
                    f"DAL_{hashlib.md5(f'{name}|{book_date}|DALLAS_TX'.encode()).hexdigest()[:10]}"
                )

            race, sex = self._parse_race_sex(race_sex)
            first, last = self._split_name(name)

            out.append(
                ArrestRecord(
                    County=self.county,
                    State="TX",
                    Full_Name=name.title() if name.isupper() else name,
                    First_Name=first,
                    Last_Name=last,
                    Booking_Number=str(book_num).strip(),
                    DOB=dob,
                    Race=race,
                    Sex=sex,
                    Booking_Date=book_date,
                    Arrest_Date=book_date,
                    Charges="Unknown",
                    Bond_Amount="0",
                    Status="In Custody",
                    Facility="Dallas County Jail",
                    Agency="Dallas County Sheriff's Office",
                    Detail_URL=LANDING_URL,
                )
            )
        return out

    @staticmethod
    def _parse_race_sex(raw: str) -> Tuple[str, str]:
        raw = (raw or "").strip()
        # Formats: "W/M", "B/F"
        if "/" in raw:
            parts = raw.split("/", 1)
            return parts[0].strip(), parts[1].strip()[:1].upper()
        return raw, ""

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
