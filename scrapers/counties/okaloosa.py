"""
Okaloosa County Arrest Scraper — Okaloosa County Jail Locator
Source: Okaloosa County Sheriff's Office
URL: https://okaloosacountyjail.myokaloosa.com/inmatelocator/
Method: requests POST — HTML table with all current inmates
Columns: NameTypeID, NameType, NameTitle, LastName, FirstName, MiddleName, NameSuffix,
         RTC, Eye, Hair, Skin, Booking#, SPN#, Name, DOB, Sex, Race, Height, Weight, EligReleaseDate
"""
import logging
import re
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://okaloosacountyjail.myokaloosa.com"
SEARCH_URL = f"{BASE_URL}/inmatelocator/"
FACILITY = "Okaloosa County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": SEARCH_URL,
}


class OkaloosaCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Okaloosa"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            raise

        session = requests.Session()
        session.headers.update(HEADERS)

        # GET to get ViewState/CSRF tokens
        try:
            resp = session.get(SEARCH_URL, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Okaloosa: GET failed: {e}")
            raise

        soup = BeautifulSoup(resp.text, "html.parser")

        # Build POST data with all hidden fields
        post_data = {}
        for inp in soup.find_all("input", {"type": "hidden"}):
            name = inp.get("name", "")
            value = inp.get("value", "")
            if name:
                post_data[name] = value

        # Add search fields — empty search to get all inmates
        post_data["LastName"] = ""
        post_data["FirstName"] = ""

        # Find the submit button
        submit_btn = soup.find("input", {"type": "submit"}) or soup.find("button", {"type": "submit"})
        if submit_btn:
            btn_name = submit_btn.get("name", "")
            btn_value = submit_btn.get("value", "Search")
            if btn_name:
                post_data[btn_name] = btn_value

        try:
            resp = session.post(SEARCH_URL, data=post_data, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Okaloosa: POST failed: {e}")
            raise

        return self._parse(resp.text)

    def _parse(self, html: str) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        records = []
        seen = set()

        # Find the inmate table — has columns: LastName, FirstName, MiddleName, Booking#, DOB, Sex, Race, etc.
        target_table = None
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text(" ").lower()
            if any(kw in header_text for kw in ["lastname", "last name", "booking", "name"]):
                target_table = table
                break

        if not target_table:
            logger.warning("Okaloosa: no inmate table found")
            return []

        # Parse header to find column indices
        header_row = target_table.find("tr")
        headers = [th.get_text(strip=True).lower().replace(" ", "") for th in header_row.find_all(["th", "td"])]

        def col(name):
            for i, h in enumerate(headers):
                if name.lower() in h:
                    return i
            return -1

        last_idx = col("lastname")
        first_idx = col("firstname")
        mid_idx = col("middlename")
        booking_idx = col("booking")
        dob_idx = col("dob")
        sex_idx = col("sex")
        race_idx = col("race")
        height_idx = col("height")
        weight_idx = col("weight")
        name_idx = col("name")  # Full name column

        for row in target_table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if not cells:
                continue

            def get(idx):
                if idx < 0 or idx >= len(cells):
                    return ""
                return cells[idx].get_text(strip=True)

            last_name = get(last_idx)
            first_name = get(first_idx)
            middle_name = get(mid_idx)
            booking_num = get(booking_idx)
            dob = get(dob_idx)
            sex = get(sex_idx)
            race = get(race_idx)
            height = get(height_idx)
            weight = get(weight_idx)
            full_name_cell = get(name_idx)

            # Build full name
            if last_name and first_name:
                full_name = f"{last_name}, {first_name}"
                if middle_name:
                    full_name += f" {middle_name}"
            elif full_name_cell:
                full_name = full_name_cell
                if not last_name:
                    _, _, last_name = self._pn(full_name)
                    first_name = self._pn(full_name)[0]
            else:
                continue

            key = booking_num or full_name
            if not key or key in seen:
                continue
            seen.add(key)

            # Get detail URL
            detail_url = ""
            link = row.find("a", href=True)
            if link:
                href = link["href"]
                detail_url = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"

            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=booking_num,
                Full_Name=full_name,
                First_Name=first_name,
                Middle_Name=middle_name,
                Last_Name=last_name,
                DOB=dob,
                Sex=sex,
                Race=race,
                Height=height,
                Weight=weight,
                Status="In Custody",
                        Release_Date="",
                Facility=FACILITY,
                Detail_URL=detail_url,
                LastCheckedMode="INITIAL",
            ))

        logger.info(f"Okaloosa: {len(records)} records")
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
