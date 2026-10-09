"""
Hernando County Arrest Scraper — ASP.NET JailSearch
Source: Hernando County Sheriff's Office
URL: https://www.hernandosheriff.org/jail/Applications/JailSearch/
Method: plain requests POST — ASP.NET WebForms with ViewState
Fields: Name, Race, Sex, DOB, Booking Number, Booking Date, Offenses

2026-10-08: the search results publish no bond, so Bond_Amount is "" (unknown,
never $0); old Hernando "0" rows hydrate as unknown. Rows without a source
booking number (HCSO<YY>JBN<NNNNNN>) are skipped, never keyed on the name. A
response without the results table raises (a 7-day window always has
bookings) instead of returning an empty success. TLS impersonation is
retired; the site answers plain HTTPS.

2026-10-08 (detail pages): every keyed row is enriched from
JailSearchDetails.aspx?BookNo=<booking #>, the source of truth for custody and
bond. ``Release Date/Time: -`` is In Custody; a date is Released with
Release_Date. Anything else is unreadable, and the booking is skipped rather
than defaulted to In Custody. Per-case charges carry their Bond Amount; the
total is set only when every charge publishes a positive amount. A $0.00 cell
is the jail's "no bond set" placeholder (live: 29 of 30 all-$0.00 bookings were
still in custody, on VOP 948.06 and hold 00.00 charges), so it is unknown,
except where the charge row says ROR, which is a real $0. A detail fetch failure
or a drifted page skips that booking (nothing blank is written); the run
raises when every detail fails or when no detail page has a charge grid.

Fix 2026-05-18: Replaced DrissionPage with curl_cffi POST.
                Results are in Table 5 (last large table, 100+ rows).
                Cell 1 contains: "LAST, FIRST RACE/SEX- DOB BOOKING_NO"
                Cell 2 = Booking Date, Cell 3 = Offenses
"""

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.hernandosheriff.org"
SEARCH_URL = f"{BASE_URL}/jail/Applications/JailSearch/"
FACILITY = "Hernando County Jail"
BOOKING_RE = re.compile(r"\b(HCSO\d{2}JBN\d{6})\b")


class HernandoContractError(RuntimeError):
    """The JailSearch results page no longer matches the verified contract."""

class HernandoDetailError(HernandoContractError):
    """A JailSearchDetails page no longer matches the verified contract."""


DAYS_BACK = 7
DETAIL_URL = f"{SEARCH_URL}JailSearchDetails.aspx"
REQUEST_PAUSE_S = 0.4
# Detail GETs per run. Live 7-day windows hold ~82 bookings (2026-10-08), so
# this is headroom, not a throttle; rows past the cap are skipped (not written,
# so nothing stored is blanked) and picked up on a later run.
MAX_DETAILS_PER_RUN = 200
RELEASE_SPAN_ID = "ctl00_ContentPlaceHolder1_fvBook_lblReleaseDateTime"
BOOK_TABLE_ID = "ctl00_ContentPlaceHolder1_fvBook"
_MONEY_RE = re.compile(r"^\$\s*([0-9][0-9,]*\.\d{2})$")
_RELEASE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})(?:\s+(\d{1,2}:\d{2}))?$")


def _charge_bond(cell: str, other_info: str) -> Optional[float]:
    """A charge's published bond, or None (unknown).

    Positive "$N.NN" is published. "$0.00" is the "no bond set" placeholder
    unless the row's Other Information says ROR (released on recognizance),
    which is a real $0. Blank or any other text is unknown."""
    m = _MONEY_RE.match((cell or "").strip())
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    if value > 0:
        return value
    if re.search(r"\bROR\b", other_info or "", re.I):
        return 0.0
    return None


def parse_detail(html: str, booking_number: str) -> Dict[str, Any]:
    """Parse one JailSearchDetails page. Raises HernandoDetailError on drift."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    book = soup.find(id=BOOK_TABLE_ID)
    if book is None:
        raise HernandoDetailError(f"{booking_number}: booking table missing")
    labels: Dict[str, str] = {}
    for tr in book.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) >= 2:
            label = tds[0].get_text(" ", strip=True)
            if label.endswith(":"):
                labels[label.rstrip(":").strip().lower()] = tds[1].get_text(" ", strip=True)
    if labels.get("booking #") != booking_number:
        raise HernandoDetailError(f"{booking_number}: detail page is for another booking")

    span = soup.find(id=RELEASE_SPAN_ID)
    release_raw = (span.get_text(" ", strip=True) if span else labels.get("release date/time", "")).strip()
    release_date = release_time = ""
    if release_raw == "-":
        status: Optional[str] = "In Custody"
    else:
        rm = _RELEASE_RE.match(release_raw)
        if rm:
            status = "Released"
            release_date, release_time = rm.group(1), rm.group(2) or ""
        else:
            status = None  # unreadable: never default to In Custody

    charges: List[Dict[str, Any]] = []
    grids = 0
    for case_table in soup.find_all("table"):
        first = case_table.find("tr")
        if not first or not first.get_text(" | ", strip=True).startswith("Case Seq."):
            continue
        data_row = first.find_next_sibling("tr")
        case_cells = [td.get_text(" ", strip=True) for td in data_row.find_all("td", recursive=False)] if data_row else []
        court_case = case_cells[1] if len(case_cells) > 1 and case_cells[1].upper() not in ("N/A", "") else ""
        for grid in case_table.find_all("table"):
            head = grid.find("tr")
            if not head or not head.get_text(" | ", strip=True).startswith("Statute |"):
                continue
            grids += 1
            for row in grid.find_all("tr")[1:]:
                tds = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(tds) < 4:
                    raise HernandoDetailError(f"{booking_number}: charge row has {len(tds)} cells")
                other = tds[4] if len(tds) > 4 else ""
                charges.append(
                    {
                        "case_number": court_case,
                        "statute": tds[0],
                        "description": tds[1],
                        "bond_raw": tds[3],
                        "bond": _charge_bond(tds[3], other),
                    }
                )

    if charges and all(c["bond"] is not None for c in charges):
        total = sum(c["bond"] for c in charges)
        bond_amount = str(int(total)) if float(total).is_integer() else f"{total:.2f}"
    else:
        bond_amount = ""  # any unpublished charge bond makes the total unknown
    return {
        "status": status,
        "release_date": release_date,
        "release_time": release_time,
        "charges": charges,
        "charge_grids": grids,
        "bond_amount": bond_amount,
    }

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SEARCH_URL,
}


class HernandoCountyScraper(BaseScraper):
    """Hernando County (FL) — ASP.NET JailSearch (plain requests POST)"""

    @property
    def county(self) -> str:
        return "Hernando"

    def scrape(self) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup

        session = requests.Session()

        # Step 1: GET to retrieve ASP.NET ViewState tokens
        try:
            r = session.get(SEARCH_URL, headers=HEADERS, timeout=20)
            r.raise_for_status()
        except Exception as e:
            logger.error(f"Hernando GET failed: {e}")
            raise

        soup = BeautifulSoup(r.text, "html.parser")

        def _val(name):
            tag = soup.find("input", {"name": name})
            return tag["value"] if tag and tag.get("value") else ""

        # Step 2: POST with date range (last DAYS_BACK days)
        today = datetime.now()
        from_date = today - timedelta(days=DAYS_BACK)

        payload = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            "__VIEWSTATE": _val("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _val("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _val("__EVENTVALIDATION"),
            "ctl00$ContentPlaceHolder1$tbFirstName": "",
            "ctl00$ContentPlaceHolder1$tbLastName": "",
            "ctl00$ContentPlaceHolder1$tbBookingDateFrom": from_date.strftime("%m/%d/%Y"),
            "ctl00$ContentPlaceHolder1$tbBookingDateTo": today.strftime("%m/%d/%Y"),
            "ctl00$ContentPlaceHolder1$tbReleaseDate": "",
            "ctl00$ContentPlaceHolder1$cbShowReleased": "on",
            "ctl00$ContentPlaceHolder1$btnSearch": "Search...",
        }

        try:
            r2 = session.post(SEARCH_URL, data=payload, headers=HEADERS, timeout=30)
            r2.raise_for_status()
        except Exception as e:
            logger.error(f"Hernando POST failed: {e}")
            raise

        rows = self._parse(r2.text)
        return self._enrich_from_details(session, rows)

    def _enrich_from_details(self, session, rows: List[ArrestRecord]) -> List[ArrestRecord]:
        """Detail page per booking; skip (never blank) on fetch or shape failure."""
        out: List[ArrestRecord] = []
        fetch_failures = drift = unreadable = no_charges = 0
        grids_seen = 0
        if len(rows) > MAX_DETAILS_PER_RUN:
            logger.warning(
                "Hernando: %d keyed rows; detail cap %d, %d rows skipped this run",
                len(rows), MAX_DETAILS_PER_RUN, len(rows) - MAX_DETAILS_PER_RUN,
            )
            rows = rows[:MAX_DETAILS_PER_RUN]
        for rec in rows:
            time.sleep(REQUEST_PAUSE_S)
            url = f"{DETAIL_URL}?BookNo={rec.Booking_Number}"
            try:
                resp = session.get(url, headers=HEADERS, timeout=20)
                resp.raise_for_status()
            except Exception as exc:
                fetch_failures += 1
                logger.warning("Hernando: detail fetch failed, booking skipped (%s)", type(exc).__name__)
                continue
            try:
                detail = parse_detail(resp.text, rec.Booking_Number)
            except HernandoDetailError as exc:
                drift += 1
                logger.warning("Hernando: detail page drift, booking skipped (%s)", exc)
                continue
            grids_seen += detail["charge_grids"]
            if detail["status"] is None:
                unreadable += 1  # unknown custody: skip, never write "In Custody"
                continue
            rec.Status = detail["status"]
            rec.Release_Date = detail["release_date"]
            rec.Detail_URL = url
            if not detail["charges"] and not (rec.Charges or "").strip():
                # Blank roster Offenses cell and no detail charge grid: writing
                # this row would $set blank charges over stored ones. Skip it.
                no_charges += 1
                continue
            if detail["charges"]:
                rec.Charges = " | ".join(
                    " - ".join(x for x in (c["statute"], c["description"]) if x) for c in detail["charges"]
                )
            case_numbers = list(dict.fromkeys(c["case_number"] for c in detail["charges"] if c["case_number"]))
            if case_numbers:
                rec.Case_Number = " | ".join(case_numbers)
            rec.Bond_Amount = detail["bond_amount"]
            rec.extra_data = {
                "bond_published": detail["bond_amount"] != "",
                "release_time": detail["release_time"],
                "charge_details": [
                    {k: c[k] for k in ("case_number", "statute", "description", "bond_raw")}
                    for c in detail["charges"]
                ],
            }
            out.append(rec)
        attempts = len(rows)
        if attempts and fetch_failures == attempts:
            raise HernandoContractError(f"Hernando: all {attempts} detail fetches failed")
        if attempts and not out:
            raise HernandoDetailError(
                f"Hernando: no usable detail page ({drift} drifted, {unreadable} unreadable custody)"
            )
        if out and grids_seen == 0:
            raise HernandoDetailError("Hernando: no detail page in the run has a charge grid (markup drift)")
        logger.info(
            "Hernando: %d records (%d fetch failures, %d drifted, %d unreadable custody, %d without charges skipped)",
            len(out), fetch_failures, drift, unreadable, no_charges,
        )
        return out

    def fetch_bond_recheck(self, booking_id: str, detail_url: str = "") -> Optional[ArrestRecord]:
        """Pending-bond re-check: re-read one booking's JailSearchDetails page.

        Same detail path, headers and parser as the scrape (plain requests). The
        record carries only what the detail page publishes (custody, release,
        charges, case numbers, bond); core/pending_bond_recheck.py fills the
        rest from the stored doc. None (nothing written) when the key is not a
        source booking number, the fetch fails, the page drifted, custody is
        unreadable, or no charge grid is published."""
        booking = str(booking_id or "").strip()
        if not BOOKING_RE.fullmatch(booking):
            return None
        url = f"{DETAIL_URL}?BookNo={booking}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            detail = parse_detail(resp.text, booking)
        except HernandoDetailError as exc:
            logger.warning("Hernando re-check: detail page drift, booking skipped (%s)", exc)
            return None
        except Exception as exc:
            logger.warning("Hernando re-check: detail fetch failed for %s (%s)", booking, type(exc).__name__)
            return None
        if detail["status"] is None or not detail["charges"]:
            return None
        case_numbers = list(dict.fromkeys(c["case_number"] for c in detail["charges"] if c["case_number"]))
        rec = ArrestRecord(
            County=self.county,
            State="FL",
            Booking_Number=booking,
            Status=detail["status"],
            Release_Date=detail["release_date"],
            Charges=" | ".join(
                " - ".join(x for x in (c["statute"], c["description"]) if x) for c in detail["charges"]
            ),
            Case_Number=" | ".join(case_numbers),
            Bond_Amount=detail["bond_amount"],
            Detail_URL=url,
            Facility=FACILITY,
            LastCheckedMode="RECHECK",
        )
        rec.extra_data = {
            "bond_published": detail["bond_amount"] != "",
            "release_time": detail["release_time"],
            "charge_details": [
                {k: c[k] for k in ("case_number", "statute", "description", "bond_raw")}
                for c in detail["charges"]
            ],
        }
        return rec

    def _parse(self, html: str) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        records = []
        seen = set()

        # Results are in the LAST large table (Table 5, 100+ rows)
        # Header row: ['', 'Inmate Name Race/Sex/DOB Booking Number', 'Booking Date', 'Offenses', 'Image']
        result_table = None
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 5:
                continue
            # Check header row for "Inmate Name" or "Booking Number"
            header_text = rows[0].get_text(" ").lower() if rows else ""
            if "inmate" in header_text or "booking number" in header_text or "offenses" in header_text:
                result_table = table
                # Keep going — we want the LAST matching table (the results, not the form)
        
        if not result_table:
            raise HernandoContractError("Hernando: no results table in the search response")
        skipped_no_key = 0

        rows = result_table.find_all("tr")
        for row in rows[1:]:  # Skip header
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            # Cell 1: "LAST, FIRST RACE/SEX- DOB BOOKING_NO"
            cell1 = cells[1].get_text(separator=" ", strip=True) if len(cells) > 1 else ""
            cell2 = cells[2].get_text(strip=True) if len(cells) > 2 else ""  # Booking Date
            cell3 = cells[3].get_text(separator=" | ", strip=True) if len(cells) > 3 else ""  # Offenses

            if not cell1 or len(cell1) < 5:
                continue

            # Parse cell1: "CURL, CODY DEAN W/M- 08/31/1990 HCSO26JBN002500"
            # Booking number pattern: letters+digits
            bn_match = BOOKING_RE.search(cell1)
            if not bn_match:
                skipped_no_key += 1  # never key a record on the name
                continue
            booking_num = bn_match.group(1)

            # DOB pattern
            dob_match = re.search(r'(\d{2}/\d{2}/\d{4})', cell1)
            dob = dob_match.group(1) if dob_match else ""

            # Race/Sex: W/M, B/F, H/M etc.
            rs_match = re.search(r'\b([A-Z])/([MF])\b', cell1)
            race = rs_match.group(1) if rs_match else ""
            sex = rs_match.group(2) if rs_match else ""

            # Name: everything before the race/sex marker
            name_part = cell1
            if rs_match:
                name_part = cell1[:rs_match.start()].strip()
            elif dob_match:
                name_part = cell1[:dob_match.start()].strip()
            elif booking_num:
                name_part = cell1[:cell1.find(booking_num)].strip()

            full_name = " ".join(name_part.split())
            if not full_name or len(full_name) < 3:
                continue

            # Booking date: "05/11/202601:25" → normalize
            booking_date = cell2
            if booking_date:
                # Remove time component if concatenated without space
                bd_match = re.match(r'(\d{2}/\d{2}/\d{4})', booking_date)
                if bd_match:
                    booking_date = bd_match.group(1)

            if booking_num in seen:
                continue
            seen.add(booking_num)

            f, m, l = self._parse_name(full_name)

            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=booking_num,
                Full_Name=full_name,
                First_Name=f, Middle_Name=m, Last_Name=l,
                DOB=dob,
                Booking_Date=booking_date,
                Status="In Custody",
                Release_Date="",
                Facility=FACILITY,
                Race=race,
                Sex=sex,
                Charges=cell3,  # may be blank; _enrich_from_details skips a row with no charges anywhere
                Bond_Amount="",  # the results grid publishes no bond: unknown, never $0
                Detail_URL=SEARCH_URL,
                LastCheckedMode="INITIAL",
                extra_data={"bond_published": False},
            ))

        if skipped_no_key:
            logger.warning("Hernando: skipped %d rows without a source booking number", skipped_no_key)
        if not records:
            raise HernandoContractError("Hernando: results table had no row with a source booking number")
        return records

    @staticmethod
    def _parse_name(name: str):
        if not name:
            return "", "", ""
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            fm = parts[1].strip().split()
            first = fm[0] if fm else ""
            middle = " ".join(fm[1:]) if len(fm) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) == 1:
            return parts[0], "", ""
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return parts[0], " ".join(parts[1:-1]), parts[-1]
