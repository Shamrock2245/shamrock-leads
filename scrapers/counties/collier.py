"""
Collier County Arrest Scraper — ASP.NET ViewState Approach.

Source: Collier County Sheriff's Office public arrest search
URL: https://www2.colliersheriff.org/arrestsearch/Report.aspx
Method: plain requests (scrapers.plain_http): honest User-Agent, TLS verified,
no browser fingerprint impersonation.

Custody: this is an arrest report, not a custody roster, and it publishes no
custody / release field. Status is left "" (unknown), never assumed.

Architecture:
1. GET initial page → extract __VIEWSTATE + __VIEWSTATEGENERATOR
2. AJAX POST (simulate timerLoad UpdatePanel) → get arrest grid HTML
3. Parse nested tables for name, demographics, charges, bond, mugshot
"""

import logging
import re
import time
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin

from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import make_session, request
from scrapers.scraper_resilience import AntiBotBlocked, EgressBlocked
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
BASE_URL = "https://www2.colliersheriff.org"
SEARCH_URL = f"{BASE_URL}/arrestsearch/Report.aspx"

HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
}

RETRY_LIMIT = 3
BACKOFF_BASE_S = 2.0


class CollierCountyScraper(BaseScraper):
    """Collier County (FL) arrest scraper — ASP.NET ViewState + AJAX POST.

    Stays unverified (CoS 2026-10-14). Without the Chrome disguise the plain
    client's grid postback was redirected to errorpage.aspx in two reads; that
    now raises EgressBlocked (``egress_block:``) so the run fails visibly
    instead of parsing an empty roster. No workaround.
    """

    @property
    def county(self) -> str:
        return "Collier"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape current inmate roster via ASP.NET ViewState postback."""
        from bs4 import BeautifulSoup

        session = make_session()

        # ── Step 1: GET the initial page to extract ViewState ──
        logger.info(f"📡 Loading {SEARCH_URL}...")
        resp = self._fetch_with_retry(
            session, "GET", SEARCH_URL, headers=HEADERS
        )
        if resp is None or resp.status_code != 200:
            logger.error(f"❌ Initial page fetch failed")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        viewstate = self._extract_field(soup, "__VIEWSTATE")
        viewstategen = self._extract_field(soup, "__VIEWSTATEGENERATOR")

        if not viewstate or not viewstategen:
            logger.error("❌ Could not extract VIEWSTATE tokens")
            return []

        logger.info("✅ ViewState tokens extracted")

        # ── Step 2: AJAX POST to trigger the UpdatePanel grid load ──
        post_headers = {
            **HEADERS,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-MicrosoftAjax": "Delta=true",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL,
            "Referer": SEARCH_URL,
        }

        data = {
            "ScriptManager1": "UpdatePanel1|timerLoad",
            "__EVENTTARGET": "timerLoad",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": viewstategen,
            "__ASYNCPOST": "true",
        }

        logger.info("📡 Sending AJAX POST to load arrest grid...")
        resp = self._fetch_with_retry(
            session, "POST", SEARCH_URL, headers=post_headers, data=data
        )
        if resp is None or resp.status_code != 200:
            logger.error(f"❌ AJAX POST failed")
            return []
        self._raise_if_error_redirect(resp.text)

        # ── Step 3: Parse the HTML grid ──
        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.find_all("table")
        logger.info(f"📊 Found {len(tables)} tables in response")

        records = self._parse_arrest_tables(tables, soup)
        logger.info(f"✅ Parsed {len(records)} arrest records")

        return records

    def _parse_arrest_tables(
        self, tables: list, soup: Any
    ) -> List[ArrestRecord]:
        """
        Parse the nested table structure of Collier's ASP.NET grid.

        The grid uses a pattern where:
        - A "header table" has cells: [Name, Date of Birth, Residence, <values>]
        - Following tables contain demographics, charges, bond info
        """
        # Pass 1: Find all "name header" tables
        name_entries = []
        for i, table in enumerate(tables):
            cells = [td.get_text(strip=True) for td in table.find_all("td")]
            if (
                len(cells) == 6
                and cells[0] == "Name"
                and cells[1] == "Date of Birth"
                and cells[2] == "Residence"
                and "," in cells[3]  # Name format: LAST, FIRST
            ):
                table_id = table.get("id", "") or ""
                name_entries.append({
                    "index": i,
                    "name": cells[3],
                    "dob": cells[4],
                    "address": cells[5],
                    # "gvReport_ctl04_ReportUC_" scopes this person's spans.
                    "prefix": table_id[: -len("Table1")] if table_id.endswith("Table1") else "",
                })

        logger.info(f"🔍 Found {len(name_entries)} name headers")

        # Pass 2: For each name entry, look ahead for details & charges
        records: List[ArrestRecord] = []
        all_mugshots = soup.select('img[src*="PicThumb"]')

        for entry_idx, entry in enumerate(name_entries):
            try:
                record_data = self._extract_record_from_tables(
                    tables, entry, all_mugshots, entry_idx, len(records), soup=soup
                )
                if record_data and record_data.Booking_Number:
                    records.append(record_data)
            except Exception as e:
                logger.warning(
                    f"⚠️ Error parsing record at index {entry['index']}: {type(e).__name__}"
                )

        return records

    def _extract_record_from_tables(
        self,
        tables: list,
        entry: dict,
        all_mugshots: list,
        entry_idx: int,
        records_count: int,
        soup: Any = None,
    ) -> Optional[ArrestRecord]:
        """Extract a single ArrestRecord from the table sequence following a name header.

        Bond: the daily report publishes no bond amount, only ``lblBondSummary``
        ("No information available." or "<date> BONDED"). A bond counts only
        when this person's own bond span prints a ``$`` figure. Dollar figures
        in charge text ("GRAND THEFT ... $750-$5K") are never a bond, and an
        unknown bond stays ``""`` (never ``"0"``).
        """

        # Parse the name
        raw_name = entry["name"]
        first_name = ""
        last_name = ""
        if "," in raw_name:
            parts = raw_name.split(",", 1)
            last_name = parts[0].strip().title()
            first_name = parts[1].strip().title()

        full_name = f"{last_name}, {first_name}" if last_name else raw_name

        # Parse address
        address = entry.get("address", "")
        city = ""
        state = "FL"
        zip_code = ""
        if address:
            # Try to parse "123 Main St, Naples, FL 34102"
            zip_match = re.search(r"\b(\d{5})\b", address)
            if zip_match:
                zip_code = zip_match.group(1)

        # Initialize record fields
        booking_number = ""
        booking_date = ""
        race = ""
        sex = ""
        height = ""
        weight = ""
        agency = ""
        age = ""
        # The arrest report publishes no custody/release field: unknown, never assumed.
        status = ""
        charges_list = []
        bond_paid = "NO"
        bond_amount = ""  # published bond-span figure only; "" when unpublished
        mugshot_url = ""
        hair_color = ""
        eye_color = ""

        # Look ahead in tables for detail data
        start_idx = entry["index"] + 1
        end_idx = min(start_idx + 15, len(tables))

        for j in range(start_idx, end_idx):
            table = tables[j]
            cells = [td.get_text(strip=True) for td in table.find_all("td")]

            # Extract key-value pairs from cells
            for k in range(len(cells) - 1):
                label = cells[k]
                value = cells[k + 1]

                if label == "A#" and value and len(value) > 3:
                    # Arrest number — use as booking number fallback
                    if not booking_number:
                        booking_number = value
                elif label == "PIN" and value and len(value) > 3:
                    pass  # Person ID — not currently mapped
                elif label == "Race" and value:
                    race = value
                elif label == "Sex" and value:
                    sex = value
                elif label == "Height" and value:
                    height = value
                elif label == "Weight" and value:
                    weight = value
                elif label == "Hair Color" and value:
                    hair_color = value
                elif label == "Eye Color" and value:
                    eye_color = value
                elif label == "Booking Date" and value:
                    booking_date = value
                elif label == "Booking Number" and value and len(value) > 5:
                    booking_number = value
                elif label == "Agency" and value:
                    agency = value
                elif label == "Age at Arrest" and value:
                    age = value

            # Extract charges from gvCharge tables
            table_id = table.get("id", "")
            if "gvCharge" in table_id:
                charge_rows = table.find_all("tr")[1:]  # Skip header
                for row in charge_rows:
                    row_cells = [
                        td.get_text(strip=True)
                        for td in row.find_all("td", recursive=False)
                    ]
                    if len(row_cells) >= 3:
                        offense = row_cells[2]
                        if offense and offense != "\xa0":
                            charges_list.append(offense)

            # Stop looking if we have the booking number and checked enough tables
            if booking_number and j > start_idx + 5:
                break

        if not booking_number:
            return None

        for bond_text in self._bond_span_texts(tables, entry, soup):
            if "BONDED" in bond_text.upper():
                bond_paid = "BONDED"
            elif bond_text and bond_text != "No information available.":
                bond_paid = bond_text
            amount = self._published_bond_amount(bond_text)
            if amount and not bond_amount:
                bond_amount = amount

        # Mugshot
        if len(all_mugshots) > records_count:
            img = all_mugshots[records_count]
            src = img.get("src", "")
            if src:
                mugshot_url = urljoin(SEARCH_URL, src)

        charges_str = " | ".join(charges_list) if charges_list else ""

        # Build extra_data for fields not in core schema
        extra = {}
        if hair_color:
            extra["hair_color"] = hair_color
        if eye_color:
            extra["eye_color"] = eye_color

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_number,
            Full_Name=full_name,
            First_Name=first_name,
            Last_Name=last_name,
            DOB=entry.get("dob", ""),
            Booking_Date=booking_date,
            Status=status,
                        Release_Date="",
            Facility="Collier County Jail",
            Agency=agency,
            Race=race,
            Sex=sex,
            Height=height,
            Weight=weight,
            Age_At_Arrest=age,
            Address=address,
            City=city,
            State=state,
            ZIP=zip_code,
            Mugshot_URL=mugshot_url,
            Charges=charges_str,
            Bond_Amount=bond_amount,
            Bond_Paid=bond_paid,
            Detail_URL=SEARCH_URL,
            LastCheckedMode="INITIAL",
            extra_data=extra,
        )

    # ── Bond helpers ──

    _BOND_SPANS = ("lblBondAmount", "lblBondSummary")

    @classmethod
    def _bond_span_texts(cls, tables: list, entry: dict, soup: Any) -> List[str]:
        """This person's bond span texts, never a neighbour's.

        The report nests each person's tables inside wrapper tables that also
        hold the next person, so a look-ahead ``find`` can land on the wrong
        span. The span ids share the name-header prefix
        (``gvReport_ctl04_ReportUC_``). Without that prefix there is no safe
        way to tell this person's span from the next person's, so no bond
        span is read and the bond stays "" (unknown).
        """
        prefix = entry.get("prefix") or ""
        texts: List[str] = []
        if not prefix or soup is None:
            return texts
        for name in cls._BOND_SPANS:
            span = soup.find("span", id=prefix + name)
            if span is not None:
                texts.append(span.get_text(" ", strip=True))
        return texts

    @staticmethod
    def _published_bond_amount(text: str) -> str:
        """The ``$`` figure printed in a bond span ("1500"; a published $0 is "0"), else "" (unknown)."""
        match = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)", text or "")
        if not match:
            return ""
        try:
            amount = float(match.group(1).replace(",", ""))
        except ValueError:
            return ""
        return f"{amount:.2f}".rstrip("0").rstrip(".") or "0"

    # ── HTTP Helpers ──

    @staticmethod
    def _extract_field(soup: Any, field_id: str) -> Optional[str]:
        """Extract a hidden form field value by ID."""
        elem = soup.find("input", {"id": field_id})
        return elem["value"] if elem else None

    def _fetch_with_retry(
        self, session, method: str, url: str, **kwargs
    ) -> Optional[Any]:
        """Verified plain HTTP with retries for 5xx / network errors only.

        A bad certificate (``SourceTLSInvalid``) or a 401/403/429 / challenge
        (``EgressBlocked``) is raised at once: never retried, never worked around.
        """
        for attempt in range(RETRY_LIMIT):
            try:
                resp = request(session, method.upper(), url, county="Collier", **kwargs)

                if resp.status_code == 200:
                    return resp

                if resp.status_code in (500, 502, 503):
                    sleep_s = BACKOFF_BASE_S * (2 ** attempt)
                    logger.warning(
                        f"⏳ HTTP {resp.status_code}, retry in {sleep_s:.1f}s"
                    )
                    time.sleep(sleep_s)
                    continue

                return resp

            except AntiBotBlocked:
                raise
            except Exception as e:
                sleep_s = BACKOFF_BASE_S * (2 ** attempt)
                if attempt < RETRY_LIMIT - 1:
                    logger.warning(
                        f"⚠️ HTTP error, retrying in {sleep_s:.1f}s: {type(e).__name__}"
                    )
                    time.sleep(sleep_s)
                else:
                    logger.error(f"❌ HTTP failed after {RETRY_LIMIT} retries")
                    return None

        return None

    @staticmethod
    def _raise_if_error_redirect(body: str) -> None:
        """The UpdatePanel answers a refused postback with ``pageRedirect`` to
        ``errorpage.aspx``. Treat it as a block, not as an empty roster."""
        text = (body or "")[:2000].lower()
        if "pageredirect" in text and "errorpage" in text:
            raise EgressBlocked("egress_block: Collier UpdatePanel redirected to errorpage.aspx")

    # ── FirstAppearanceWatcher hook ───────────────────────────────────────────
    def _fetch_single_booking(
        self, booking_id: str, detail_url: str
    ) -> "Optional[ArrestRecord]":
        """
        Re-fetch a single Collier County booking by booking number.

        Collier uses an ASP.NET ViewState grid — there is no per-booking
        detail URL, so we re-run the full AJAX POST and filter the parsed
        records for the matching booking number.

        Returns None on any failure (watcher falls back to generic HTTP).
        """
        if not booking_id:
            return None
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return None
        try:
            session = make_session()
            resp = self._fetch_with_retry(session, "GET", SEARCH_URL, headers=HEADERS)
            if resp is None or resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.text, "html.parser")
            viewstate = self._extract_field(soup, "__VIEWSTATE")
            viewstategen = self._extract_field(soup, "__VIEWSTATEGENERATOR")
            if not viewstate or not viewstategen:
                return None
            post_headers = {
                **HEADERS,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-MicrosoftAjax": "Delta=true",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": BASE_URL,
                "Referer": SEARCH_URL,
            }
            data = {
                "ScriptManager1": "UpdatePanel1|timerLoad",
                "__EVENTTARGET": "timerLoad",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": viewstate,
                "__VIEWSTATEGENERATOR": viewstategen,
                "__ASYNCPOST": "true",
            }
            resp = self._fetch_with_retry(
                session, "POST", SEARCH_URL, headers=post_headers, data=data
            )
            if resp is None or resp.status_code != 200:
                return None
            self._raise_if_error_redirect(resp.text)
            soup = BeautifulSoup(resp.text, "html.parser")
            tables = soup.find_all("table")
            records = self._parse_arrest_tables(tables, soup)
            # Find the matching record by booking number
            for record in records:
                if record.Booking_Number == booking_id:
                    record.LastCheckedMode = "UPDATE"
                    return record
            logger.debug(
                f"Collier _fetch_single_booking: booking {booking_id} not found in current roster"
            )
            return None
        except AntiBotBlocked:
            raise  # the watcher stops; it must not retry through a generic fetch
        except Exception as e:
            logger.warning(f"Collier _fetch_single_booking error ({booking_id}): {type(e).__name__}")
            return None
