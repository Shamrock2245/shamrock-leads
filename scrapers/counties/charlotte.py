"""
Charlotte County Arrest Scraper — Revize CMS via curl_cffi (TLS Impersonation)
===============================================================================
Source:  Charlotte County Sheriff's Office (CCSO)
Portal:  https://ccso.org/correctional_facility/local_arrest_database.php
Backend: https://inmates.charlottecountyfl.revize.com/bookings  (Revize CMS)

APPROACH
--------
Uses curl_cffi with Chrome TLS fingerprint impersonation to bypass Cloudflare
WAF at the protocol level. No browser automation needed — Revize serves
server-rendered HTML/PHP. The session first visits ccso.org to establish
referrer cookies, then accesses the Revize CMS bookings pages.

Previous DrissionPage approach failed because Cloudflare blocks headless
Chromium from datacenter IPs via JA3 fingerprinting. curl_cffi impersonates
a real Chrome TLS fingerprint, making requests indistinguishable from a
residential browser.

Architecture (3-Phase):
1. Establish session via parent page → set Referer + cookies
2. Paginate roster → collect booking detail URLs
3. Visit each detail page → extract structured data via BeautifulSoup
"""
import logging
import re
import time
from datetime import datetime
from typing import List, Optional, Tuple

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── URLs ──────────────────────────────────────────────────────────────────────
PARENT_URL   = "https://ccso.org/correctional_facility/local_arrest_database.php"
BASE_URL     = "https://inmates.charlottecountyfl.revize.com"
BOOKINGS_URL = f"{BASE_URL}/bookings"

# ── Tuning ────────────────────────────────────────────────────────────────────
MAX_PAGES      = 50    # Safety cap — Charlotte rarely exceeds 20 pages
DETAIL_DELAY_S = 0.8   # Polite delay between detail page requests
RETRY_LIMIT    = 3
BACKOFF_BASE_S = 2.0

# ── Bond sanity cap ───────────────────────────────────────────────────────────
# No single charge in Florida realistically exceeds $5M bond.
# Values above this are data-parsing artifacts (booking IDs, agency codes, etc.)
MAX_BOND_PER_CHARGE = 5_000_000
MAX_BOND_TOTAL      = 10_000_000  # Total across all charges

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class CharlotteCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Charlotte"

    # ── Main entry point ──────────────────────────────────────────────────────
    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError as e:
            logger.error(f"[Charlotte] Missing dependency: {e}. pip install curl-cffi beautifulsoup4")
            raise

        session = cffi_requests.Session()

        # Phase 1: Establish context via parent page (bypass Cloudflare WAF)
        logger.info(f"[Charlotte] Phase 1: Loading parent page {PARENT_URL}")
        parent_resp = self._fetch(session, "GET", PARENT_URL)
        if parent_resp and parent_resp.status_code == 200:
            logger.info("[Charlotte] Parent page loaded — cookies established")
        else:
            logger.warning("[Charlotte] Parent page failed — trying roster directly")

        # Phase 2: Collect all booking detail URLs from the roster
        booking_links = self._collect_booking_links(session)
        if not booking_links:
            logger.warning("[Charlotte] No booking links found on roster")
            return []

        logger.info(f"[Charlotte] Phase 2 complete: {len(booking_links)} booking links collected")

        # Phase 3: Visit each detail page and extract data
        records = []
        for idx, (booking_id, detail_url) in enumerate(booking_links, 1):
            if idx % 10 == 0:
                logger.info(
                    f"[Charlotte] Progress: {idx}/{len(booking_links)} ({len(records)} records)"
                )
            try:
                record = self._extract_detail(session, booking_id, detail_url)
                if record and record.Full_Name and record.Booking_Number:
                    records.append(record)
            except Exception as e:
                logger.warning(f"[Charlotte] Error on detail {booking_id}: {e}")
            time.sleep(DETAIL_DELAY_S)

        logger.info(f"[Charlotte] Scraped {len(records)} in-custody records")
        return records

    # ── Phase 2: Collect booking links ────────────────────────────────────────
    def _collect_booking_links(self, session) -> List[Tuple[str, str]]:
        """Paginate through the Revize bookings roster and collect all detail URLs."""
        from bs4 import BeautifulSoup

        all_links: List[Tuple[str, str]] = []
        seen_urls: set = set()

        for current_page in range(1, MAX_PAGES + 1):
            url = BOOKINGS_URL if current_page == 1 else f"{BOOKINGS_URL}?page={current_page}"
            logger.info(f"[Charlotte] Loading roster page {current_page}: {url}")

            resp = self._fetch(session, "GET", url, extra_headers={
                "Referer": PARENT_URL if current_page == 1 else BOOKINGS_URL,
            })

            if not resp or resp.status_code != 200:
                logger.warning(f"[Charlotte] Roster page {current_page} returned {resp.status_code if resp else 'None'}")
                break

            # Check for Cloudflare block page
            if "just a moment" in resp.text.lower()[:500] or resp.status_code == 403:
                logger.warning(f"[Charlotte] Cloudflare challenge on page {current_page}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract booking links
            page_links = soup.find_all("a", href=re.compile(r"/bookings/[^?]"))
            new_count = 0
            for link in page_links:
                href = link.get("href", "")
                # Skip the bare /bookings/ link
                if re.match(r"^/bookings/?$", href):
                    continue
                # Build full URL
                if not href.startswith("http"):
                    href = f"{BASE_URL}{href}" if href.startswith("/") else f"{BASE_URL}/{href}"

                if href not in seen_urls:
                    seen_urls.add(href)
                    booking_id = href.split("/bookings/")[-1].split("?")[0].strip("/")
                    if booking_id:
                        all_links.append((booking_id, href))
                        new_count += 1

            logger.info(f"[Charlotte] Page {current_page}: +{new_count} links (total: {len(all_links)})")

            if new_count == 0:
                logger.info(f"[Charlotte] No new links on page {current_page} — end of roster")
                break

            # Check for next page
            next_link = soup.find("a", href=re.compile(rf"page={current_page + 1}"))
            if not next_link:
                # Also check for "Next" text in pagination
                next_text = soup.find("a", string=re.compile(r"next", re.I))
                if not next_text:
                    logger.info(f"[Charlotte] No next page — end of roster at page {current_page}")
                    break
                # Check if the "Next" link is disabled
                parent = next_text.parent
                if parent and "disabled" in (parent.get("class", []) or []):
                    logger.info("[Charlotte] Next page disabled — end of roster")
                    break

            time.sleep(1.0)

        return all_links

    # ── Phase 3: Extract detail page ──────────────────────────────────────────
    def _extract_detail(
        self, session, booking_id: str, detail_url: str
    ) -> Optional[ArrestRecord]:
        """Visit a booking detail page and extract all structured data with BS4."""
        from bs4 import BeautifulSoup

        resp = self._fetch(session, "GET", detail_url, extra_headers={
            "Referer": BOOKINGS_URL,
        })

        if not resp or resp.status_code != 200:
            logger.warning(f"[Charlotte] Detail page {booking_id}: HTTP {resp.status_code if resp else 'None'}")
            return None

        if "just a moment" in resp.text.lower()[:500]:
            logger.warning(f"[Charlotte] Cloudflare challenge on detail {booking_id}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        raw = {}

        # ── Extract from label/value pairs ──
        for label in soup.find_all("label"):
            text = label.get_text(strip=True).rstrip(":")
            value = None
            for_id = label.get("for")
            if for_id:
                inp = soup.find(id=for_id)
                if inp:
                    value = inp.get("value") or inp.get_text(strip=True)
            if not value:
                next_sib = label.find_next_sibling()
                if next_sib:
                    value = next_sib.get_text(strip=True) or next_sib.get("value")
            if value and value.strip():
                raw[text] = value.strip()

        # ── Extract from definition lists (dt/dd) ──
        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                raw[dt.get_text(strip=True).rstrip(":")] = dd.get_text(strip=True)

        # ── Extract from 2-column table rows (key-value) ──
        # SAFETY: Skip rows inside booking/charge tables to prevent
        # booking numbers (e.g. 202602867) from being parsed as bonds.
        for row in soup.find_all("tr"):
            # Skip rows inside tables that have 3+ column headers (booking/charge tables)
            parent_table = row.find_parent("table")
            if parent_table:
                headers = parent_table.find_all("th")
                if len(headers) >= 3:
                    continue
            cells = row.find_all(["td", "th"])
            if len(cells) == 2:
                key = cells[0].get_text(strip=True).rstrip(":")
                val = cells[1].get_text(strip=True)
                if key and val:
                    raw[key] = val

        # Charlotte detail pages list charges directly; no nested sub-pages are used.

        # ── Extract charges from main page ──
        if "__CHARGES" not in raw:
            self._extract_charges_from_soup(soup, raw)

        # ── Extract booking table data ──
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if any(h for h in headers if "book" in h or "arrest" in h):
                first_row = table.find("tbody", recursive=False)
                if first_row:
                    first_row = first_row.find("tr")
                else:
                    rows = table.find_all("tr")
                    first_row = rows[1] if len(rows) > 1 else None
                if first_row:
                    cells = first_row.find_all("td")
                    for i, h in enumerate(headers):
                        if i >= len(cells):
                            break
                        val = cells[i].get_text(strip=True)
                        if "book" in h and ("#" in h or "num" in h):
                            raw["__BookNum"] = val
                        elif "book" in h and "date" in h:
                            raw["__BookDate"] = val
                        elif "arrest" in h and "date" in h:
                            raw["__BookDate"] = raw.get("__BookDate") or val
                        elif "release" in h or "rel" in h:
                            raw["__RelDate"] = val
                        elif "agency" in h:
                            raw["__Agency"] = val

        # ── Mugshot ──
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src and not src.startswith("data:") and any(
                kw in src.lower() for kw in ["photo", "mugshot", "image", "booking", "inmate"]
            ):
                if not src.startswith("http"):
                    src = f"{BASE_URL}{src}" if src.startswith("/") else f"{BASE_URL}/{src}"
                raw["__Mugshot"] = src
                break

        # ── ICE Hold ──
        body_text = soup.get_text(" ")
        raw["__HAS_ICE"] = "ICE HOLD" in body_text or "IMMIGRATION DETAINER" in body_text

        raw["__Booking_ID"] = booking_id
        raw["__Detail_URL"] = detail_url

        return self._convert_to_record(raw)

    # ── Charge extraction helper ──────────────────────────────────────────────
    @staticmethod
    def _extract_charges_from_soup(soup, raw: dict):
        """Extract charges from table rows in a soup object."""
        charges = raw.get("__CHARGES", [])

        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            is_charge_table = any(
                kw in " ".join(headers) for kw in ["charge", "offense", "statute", "bond", "bail"]
            )
            if not is_charge_table:
                continue

            for row in table.find_all("tr")[1:]:  # skip header
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                cell_texts = [c.get_text(strip=True) for c in cells]
                row_text = " ".join(cell_texts)

                charge = {"desc": "", "degree": "", "bond": "", "agency": ""}

                # Map by header
                for i, h in enumerate(headers):
                    if i >= len(cells):
                        break
                    val = cell_texts[i]
                    if re.search(r"charge|desc|offense|statute", h):
                        charge["desc"] = val
                    elif re.search(r"degree|level|class", h):
                        charge["degree"] = val
                    elif re.search(r"bond|bail|amount", h):
                        charge["bond"] = val
                    elif re.search(r"agency", h):
                        charge["agency"] = val
                    elif re.search(r"case|docket", h):
                        charge["caseNum"] = val

                # Fallback: find dollar amounts in cells
                # STRICT: Only match values that look like real currency amounts.
                # Must have $ prefix OR decimal point with cents to avoid
                # misidentifying booking IDs (202602867) or agency codes as bonds.
                if not charge["bond"]:
                    for ct in cell_texts:
                        cleaned = ct.replace(" ", "")
                        # Require either $ prefix or a decimal portion (e.g. "150.0000")
                        if re.match(r"^\$[\d,]+\.?\d*$", cleaned):
                            bond_candidate = float(cleaned.replace("$", "").replace(",", "") or "0")
                            if 0 < bond_candidate <= MAX_BOND_PER_CHARGE:
                                charge["bond"] = ct
                                break
                        elif re.match(r"^[\d,]+\.\d{2,}$", cleaned):
                            # Matches "150.0000" format (decimal with 2+ decimal digits)
                            bond_candidate = float(cleaned.replace(",", "") or "0")
                            if 0 < bond_candidate <= MAX_BOND_PER_CHARGE:
                                charge["bond"] = ct
                                break

                # Fallback: use first long text as desc
                if not charge["desc"]:
                    for ct in cell_texts:
                        if len(ct) > 10 and not ct.startswith("$") and not re.match(r"^\d{2}[/-]\d{2}", ct):
                            charge["desc"] = ct
                            break

                if charge["desc"] or charge["bond"]:
                    charges.append(charge)

        raw["__CHARGES"] = charges

    # ── Convert raw dict → ArrestRecord ──────────────────────────────────────
    def _convert_to_record(self, raw: dict) -> Optional[ArrestRecord]:
        if not raw:
            return None

        first = self._clean(raw.get("First Name") or raw.get("FirstName") or "")
        last  = self._clean(raw.get("Last Name")  or raw.get("LastName")  or "")
        mid   = self._clean(raw.get("Middle Name") or raw.get("MiddleName") or "")

        if not first and not last:
            return None

        full_name = f"{last}, {first}" if last and first else (last or first)
        if mid:
            full_name = f"{last}, {first} {mid}"

        # Booking number — prefer explicit field, fall back to __BookNum or URL ID
        booking_number = self._clean(
            raw.get("Booking #") or raw.get("Book #") or
            raw.get("Booking Number") or raw.get("__BookNum") or
            raw.get("__Booking_ID") or ""
        )

        # Dates
        booking_date = self._clean(
            raw.get("__BookDate") or raw.get("Booking Date") or
            raw.get("Book Date") or raw.get("Arrest Date") or ""
        )
        release_date = self._clean(
            raw.get("__RelDate") or raw.get("Release Date") or ""
        )
        in_custody = not release_date or release_date.upper() in ("N/A", "NA", "")

        # Charges + bond
        charges_raw = raw.get("__CHARGES") or []
        charges_list = []
        bond_total = 0.0
        for ch in charges_raw:
            desc = ch.get("desc", "")
            bond_str = ch.get("bond", "0").replace(",", "").replace("$", "").strip()
            try:
                bond_val = float(bond_str) if bond_str else 0.0
            except ValueError:
                bond_val = 0.0
            # Per-charge sanity cap — rejects booking IDs parsed as bonds
            if bond_val > MAX_BOND_PER_CHARGE:
                logger.warning(
                    f"[Charlotte] Bond sanity cap triggered: ${bond_val:,.0f} on "
                    f"charge '{desc}' — capping to $0 (likely a parsing artifact)"
                )
                bond_val = 0.0
            bond_total += bond_val
            if desc:
                charges_list.append(
                    f"{desc} [{ch.get('degree', '')}]"
                    + (f" Bond: ${bond_val:,.0f}" if bond_val else "")
                )
        # Total bond sanity cap
        if bond_total > MAX_BOND_TOTAL:
            logger.warning(
                f"[Charlotte] Total bond ${bond_total:,.0f} exceeds ${MAX_BOND_TOTAL:,.0f} "
                f"— resetting to $0 (data integrity issue)"
            )
            bond_total = 0.0

        charges_str = " | ".join(charges_list) if charges_list else self._clean(
            raw.get("Charge") or raw.get("Charges") or ""
        )

        dob  = self._clean(raw.get("Date of Birth") or raw.get("DOB") or "")
        race = self._clean(raw.get("Race") or "")
        sex  = self._clean(raw.get("Gender") or raw.get("Sex") or "")

        try:
            extra = {}
            if raw.get("__HAS_ICE"):
                extra["has_ice_hold"] = True

            return ArrestRecord(
                Full_Name=full_name,
                First_Name=first,
                Last_Name=last,
                Middle_Name=mid,
                Booking_Number=booking_number,
                Booking_Date=booking_date,
                Release_Date=release_date if not in_custody else "",
                Status="In Custody" if in_custody else "Released",
                Charges=charges_str,
                Bond_Amount=str(bond_total) if bond_total else "",
                DOB=dob,
                Race=race,
                Sex=sex,
                County="Charlotte",
                Facility="Charlotte County Jail",
                Agency=self._clean(raw.get("__Agency") or ""),
                Mugshot_URL=raw.get("__Mugshot", ""),
                Detail_URL=raw.get("__Detail_URL", ""),
                LastCheckedMode="INITIAL",
                extra_data=extra,
            )
        except Exception as e:
            logger.warning(
                f"[Charlotte] Record build error: {e} | keys: {list(raw.keys())[:10]}"
            )
            return None

    # ── HTTP helper with curl_cffi TLS impersonation ──────────────────────────
    def _fetch(self, session, method: str, url: str, extra_headers: dict = None, **kwargs):
        """HTTP request with Chrome TLS impersonation + retry logic."""
        headers = {**HEADERS}
        if extra_headers:
            headers.update(extra_headers)

        for attempt in range(RETRY_LIMIT):
            try:
                if method.upper() == "GET":
                    resp = session.get(
                        url, headers=headers, impersonate="chrome124",
                        timeout=30, allow_redirects=True, **kwargs
                    )
                else:
                    resp = session.post(
                        url, headers=headers, impersonate="chrome124",
                        timeout=30, allow_redirects=True, **kwargs
                    )

                if resp.status_code == 200:
                    return resp

                if resp.status_code in (429, 500, 502, 503):
                    sleep_s = BACKOFF_BASE_S * (2 ** attempt)
                    logger.warning(f"[Charlotte] HTTP {resp.status_code}, retry in {sleep_s:.1f}s")
                    time.sleep(sleep_s)
                    continue

                # Return non-200 for caller to handle
                return resp

            except Exception as e:
                sleep_s = BACKOFF_BASE_S * (2 ** attempt)
                if attempt < RETRY_LIMIT - 1:
                    logger.warning(f"[Charlotte] HTTP error, retrying in {sleep_s:.1f}s: {e}")
                    time.sleep(sleep_s)
                else:
                    logger.error(f"[Charlotte] HTTP failed after {RETRY_LIMIT} retries: {e}")
                    return None

        return None

    @staticmethod
    def _clean(val) -> str:
        if not val:
            return ""
        return " ".join(str(val).strip().split())

    # ── Single booking fetch (for FirstAppearanceWatcher) ────────────────────
    def _fetch_single_booking(
        self,
        booking_id: str,
        detail_url: Optional[str] = None,
    ) -> Optional[ArrestRecord]:
        """Fetch a single booking by ID for the FirstAppearanceWatcher."""
        if not booking_id and not detail_url:
            return None
        if not detail_url:
            detail_url = f"{BOOKINGS_URL}/{booking_id}"

        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            logger.error("[Charlotte] curl_cffi not installed")
            return None

        session = cffi_requests.Session()

        try:
            # Establish parent context first
            self._fetch(session, "GET", PARENT_URL)
            record = self._extract_detail(session, booking_id, detail_url)
            if record:
                record.LastCheckedMode = "UPDATE"
            return record
        except Exception as e:
            logger.warning(f"[Charlotte] _fetch_single_booking error ({booking_id}): {e}")
            return None
