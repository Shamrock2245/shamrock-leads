"""
Glades County Arrest Scraper — SmartCop AJAX (AddMoreResults)
Source: Glades County Sheriff's Office
URL: https://smartweb.gladessheriff.org/smartwebclient/Jail.aspx
Method: curl_cffi POST to Jail.aspx/AddMoreResults (ASP.NET PageMethods AJAX)
Fix 2026-05-18: Replaced broken form POST with AJAX endpoint (same pattern as Suwannee/Putnam)
Fix 2026-10-08: bond comes only from this card's own charge grid BOND column
(or the card's own "Bond Amount:" cell when it prints a positive figure). Any
"NO BOND" charge (a hold) or a missing bond makes the total "" (unknown); a
printed charge "$0.00" is a real 0. A card never takes the next card's bond.
"""
import logging
import re
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://smartweb.gladessheriff.org/smartwebclient"
AJAX_URL = f"{BASE_URL}/Jail.aspx/AddMoreResults"
FACILITY = "Glades County Jail"
IMPERSONATE = "chrome131"
PAGE_SIZE = 185

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Content-Type": "application/json; charset=utf-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/Jail.aspx",
    "Origin": "https://smartweb.gladessheriff.org",
}


class GladesCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Glades"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cf
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed")
            raise

        session = cf.Session()
        records = []
        seen = set()
        offset = 0

        while True:
            payload = {
                "FirstName": "",
                "MiddleName": "",
                "LastName": "",
                "BeginBookDate": "",
                "EndBookDate": "",
                "BeginReleaseDate": "",
                "EndReleaseDate": "",
                "TypeJailSearch": 0,
                "RecordsLoaded": offset,
                "SortOption": 1,
                "SortOrder": 1,
                "IsDefault": False,
            }

            try:
                r = session.post(
                    AJAX_URL,
                    json=payload,
                    headers=HEADERS,
                    timeout=30,
                    impersonate=IMPERSONATE,
                )
                r.raise_for_status()
            except Exception as e:
                logger.error(f"Glades AJAX failed (offset={offset}): {e}")
                break

            try:
                data = r.json()
                html_rows = data["d"]["Data"]["data"]
            except Exception as e:
                logger.error(f"Glades JSON parse failed: {e}")
                break

            if not html_rows or len(html_rows) < 1:
                break

            batch = self._parse_html(html_rows, seen)
            if not batch:
                break

            records.extend(batch)
            offset += PAGE_SIZE

            if offset >= PAGE_SIZE * 3:
                break

        logger.info(f"Glades: {len(records)} records")
        return records

    def _parse_html(self, html: str, seen: set) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        from datetime import datetime, timezone
        soup = BeautifulSoup(html, "html.parser")
        records = []

        for img in soup.find_all("img", src=re.compile(r"bookno=")):
            src = img.get("src", "")
            bk_m = re.search(r"bookno=([A-Z0-9]+)", src)
            if not bk_m:
                continue
            booking_num = bk_m.group(1)
            if booking_num in seen:
                continue
            seen.add(booking_num)

            # Collect text from this row and next 15 siblings
            block_text = ""
            try:
                row = img.find_parent("tr")
                current = row
                for _ in range(15):
                    if current:
                        block_text += " " + current.get_text(" ", strip=True)
                        current = current.find_next_sibling("tr")
            except Exception:
                pass

            # Name: "LAST, FIRST MIDDLE  (RACE/\nSEX\n/ DOB: ...)"
            # SmartCop format has race/sex split across lines with whitespace
            name_m = re.search(
                r"([A-Z][A-Z\s\-\',]+,\s*[A-Z][A-Z\s\-\']+)\s*\(([A-Z])/\s*([A-Z]+)",
                block_text,
                re.IGNORECASE
            )
            full_name = name_m.group(1).strip() if name_m else ""
            race = name_m.group(2) if name_m else ""
            sex = name_m.group(3) if name_m else ""

            # Clean up: remove "Enlarge Photo" prefix from name
            full_name = re.sub(r"^(?:Enlarge\s+Photo\s+)", "", full_name, flags=re.IGNORECASE).strip()
            # Normalize sex: MALE→M, FEMALE→F
            if sex.upper() == "MALE": sex = "M"
            elif sex.upper() == "FEMALE": sex = "F"

            # If strict regex failed, try extracting name before the parenthesis
            if not full_name:
                # Try: "LAST, FIRST MIDDLE (anything"
                name_m2 = re.search(
                    r"(?:Enlarge Photo\s+)?([A-Z][A-Z\s\-\',]+,\s*[A-Z][A-Z\s\-\']+)\s*\(",
                    block_text, re.IGNORECASE
                )
                if name_m2:
                    full_name = name_m2.group(1).strip()
                    # Extract race/sex separately
                    rs = re.search(r"\(([A-Z])/\s*([A-Z]+)", block_text, re.IGNORECASE)
                    if rs:
                        race = rs.group(1)
                        sex = rs.group(2)

            last, first, middle = "", "", ""
            if "," in full_name:
                parts = full_name.split(",", 1)
                last = parts[0].strip()
                fm = parts[1].strip().split()
                first = fm[0] if fm else ""
                middle = " ".join(fm[1:]) if len(fm) > 1 else ""

            dob_m = re.search(r"DOB:\s*([\d/]+)", block_text)
            dob = dob_m.group(1) if dob_m else ""

            bd_m = re.search(r"Booking Date:\s*([\d/]+)", block_text)
            booking_date = bd_m.group(1) if bd_m else ""

            charges = " | ".join(re.findall(r"Charge(?:\s+\d+)?:\s*([^\n\r]+)", block_text))

            bond = self._card_bond(img)

            # Parse status from block text
            status_m = re.search(r"Status:\s*([a-zA-Z\s]+)", block_text)
            status = status_m.group(1).strip() if status_m else "In Custody"
            if "jail" in status.lower() or "custody" in status.lower():
                status = "In Custody"

            # Parse address from block text
            addr_m = re.search(r"Address Given:\s*([^\n\r\t]+)", block_text)
            address = addr_m.group(1).strip() if addr_m else ""

            if not full_name:
                continue

            records.append(ArrestRecord(
                County=self.county, State="FL", Facility=FACILITY,
                Full_Name=full_name.upper(),
                First_Name=first.upper(), Middle_Name=middle.upper(), Last_Name=last.upper(),
                DOB=dob, Race=race.upper() if race else "", Sex=sex.upper() if sex else "",
                Booking_Number=booking_num, Booking_Date=booking_date,
                Charges=charges, Bond_Amount=bond,
                Address=address, Status=status,
                Detail_URL=f"{BASE_URL}/Jail.aspx",
                Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                LastChecked=datetime.now(timezone.utc).isoformat(),
                LastCheckedMode="INITIAL",
            ))

        return records

    @staticmethod
    def _money(text: str):
        """A published dollar figure ("$5,000.00" or "5000"), else None."""
        t = (text or "").strip()
        m = re.fullmatch(r"\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", t)
        if not m:
            return None  # "NO BOND", "BOND", blank, reference ids
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None

    @classmethod
    def _card_bond(cls, img) -> str:
        """This card's bond from its own charge grid BOND column.

        Walks the document from this card's photo to the next card's photo, so
        charge tables of later inmates are never read.

        * Any charge printed "NO BOND" (a hold) makes the total unknown: "".
        * Any BOND cell that is not a dollar figure makes the total unknown: "".
        * Otherwise the sum of the printed figures; a printed "$0.00" is a
          real 0, so all-$0.00 charges give "0".
        * No charge grid: the card's own "Bond Amount:" only when positive.
          The card prints "$0.00" there even when its charges publish
          $15,000-$245,000 (live check 2026-10-08), so a card-level $0.00 is
          not a bond and stays "".
        """
        cells: List[str] = []
        for el in img.next_elements:
            name = getattr(el, "name", None)
            if name == "img" and "bookno=" in (el.get("src") or ""):
                break
            if name != "table" or "JailViewCharges" not in (el.get("class") or []):
                continue
            bond_idx = None
            for tr in el.find_all("tr"):
                row = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if bond_idx is None:
                    if "BOND" in row:
                        bond_idx = row.index("BOND")
                    continue
                if len(row) > bond_idx:
                    cells.append(row[bond_idx])
        if cells:
            if any("NO BOND" in c.upper() for c in cells):
                return ""
            values = [cls._money(c) for c in cells]
            if any(v is None for v in values):
                return ""
            return cls._fmt(sum(values))
        card = img.find_parent("tr")
        for td in card.find_all("td") if card is not None else []:
            if td.get_text(strip=True) == "Bond Amount:":
                nxt = td.find_next_sibling("td")
                val = cls._money(nxt.get_text(" ", strip=True)) if nxt is not None else None
                if val and val > 0:
                    return cls._fmt(val)
                break
        return ""

    @staticmethod
    def _fmt(total: float) -> str:
        return str(int(total)) if float(total).is_integer() else f"{total:.2f}"
