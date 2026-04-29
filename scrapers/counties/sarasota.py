"""
Sarasota County Arrest Scraper — Revize CMS via curl_cffi (TLS Impersonation)
==============================================================================
Source:  Sarasota County Sheriff's Office
Portal:  https://sarasotasheriff.org/arrest-reports/index.php
Backend: https://cms.revize.com/revize/apps/sarasota/ (Revize CMS)

APPROACH
--------
Uses curl_cffi with Chrome TLS fingerprint impersonation to bypass the
cms.revize.com 403 block on datacenter IPs. Replaces the previous fragile
patchright subprocess approach.

The session first visits sarasotasheriff.org (which loads fine from datacenter
IPs) to establish referrer cookies, then accesses the Revize CMS search
endpoints which serve PHP-rendered HTML.

Architecture (2-Phase):
1. Date-based search: personSearch.php?type=date&date=MM/DD/YYYY → collect inmate links
2. Detail extraction: viewInmate.php?id=X → extract name, charges, bond, demographics
"""
import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── URLs ──────────────────────────────────────────────────────────────────────
PARENT_URL = "https://sarasotasheriff.org/arrest-reports/index.php"
BASE_URL   = "https://cms.revize.com/revize/apps/sarasota/"

# ── Tuning ────────────────────────────────────────────────────────────────────
DAYS_BACK          = 14    # Search last 14 days of bookings
MAX_PAGES_PER_DATE = 30    # Pagination cap per date search
DETAIL_DELAY_S     = 0.8   # Polite delay between detail page requests
RETRY_LIMIT        = 3
BACKOFF_BASE_S     = 2.0

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


class SarasotaCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Sarasota"

    # ── Main entry point ──────────────────────────────────────────────────────
    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError as e:
            logger.error(f"[Sarasota] Missing dependency: {e}. pip install curl-cffi beautifulsoup4")
            return []

        session = cffi_requests.Session()

        # Phase 1: Establish context via parent page
        logger.info(f"[Sarasota] Phase 1: Loading parent page {PARENT_URL}")
        parent_resp = self._fetch(session, "GET", PARENT_URL)
        if parent_resp and parent_resp.status_code == 200:
            logger.info("[Sarasota] Parent page loaded — cookies/referer established")
        else:
            logger.warning("[Sarasota] Parent page failed — trying Revize directly")

        # Phase 2: Collect booking links by date search
        booking_links = self._collect_booking_links(session)
        if not booking_links:
            logger.warning("[Sarasota] No booking links found")
            return []

        logger.info(f"[Sarasota] Phase 2 complete: {len(booking_links)} booking links collected")

        # Phase 3: Visit each detail page and extract data
        records = []
        for idx, (booking_id, detail_url) in enumerate(booking_links, 1):
            if idx % 10 == 0:
                logger.info(
                    f"[Sarasota] Progress: {idx}/{len(booking_links)} ({len(records)} records)"
                )
            try:
                record = self._extract_detail(session, booking_id, detail_url)
                if record and record.Full_Name:
                    records.append(record)
            except Exception as e:
                logger.warning(f"[Sarasota] Error on detail {booking_id}: {e}")
            time.sleep(DETAIL_DELAY_S)

        logger.info(f"[Sarasota] Scraped {len(records)} valid records")
        return records

    # ── Phase 2: Date-based search → collect inmate links ─────────────────────
    def _collect_booking_links(self, session) -> List[Tuple[str, str]]:
        """Search each of the last N days and collect all inmate detail URLs."""
        from bs4 import BeautifulSoup

        all_links: List[Tuple[str, str]] = []
        seen_urls: set = set()
        today = datetime.now()

        for day_offset in range(DAYS_BACK):
            target_date = today - timedelta(days=day_offset)
            date_str = target_date.strftime("%m/%d/%Y")
            search_url = f"{BASE_URL}personSearch.php?type=date&date={date_str}"

            logger.info(f"[Sarasota] Searching date: {date_str}")

            page_num = 1
            while page_num <= MAX_PAGES_PER_DATE:
                url = search_url if page_num == 1 else f"{search_url}&page={page_num}"

                resp = self._fetch(session, "GET", url, extra_headers={
                    "Referer": PARENT_URL if page_num == 1 and day_offset == 0 else search_url,
                })

                if not resp or resp.status_code != 200:
                    logger.debug(f"[Sarasota] Search page returned {resp.status_code if resp else 'None'} for {date_str} p{page_num}")
                    break

                # Check for Cloudflare or 403
                if "just a moment" in resp.text.lower()[:500] or resp.status_code == 403:
                    logger.warning(f"[Sarasota] Cloudflare/403 on search for {date_str}")
                    break

                soup = BeautifulSoup(resp.text, "html.parser")

                # Find links to inmate detail pages
                links = soup.find_all("a", href=re.compile(r"viewInmate\.php|booking\.php|pinSearch\.php"))
                new_count = 0

                for link in links:
                    href = link.get("href", "")
                    if not href:
                        continue

                    # Build full URL
                    if not href.startswith("http"):
                        href = f"{BASE_URL}{href.lstrip('/')}"

                    if href in seen_urls:
                        continue
                    seen_urls.add(href)

                    # Extract booking ID from URL
                    bid = ""
                    if "id=" in href:
                        bid = href.split("id=")[-1].split("&")[0]
                    elif "pin=" in href:
                        bid = href.split("pin=")[-1].split("&")[0]

                    all_links.append((bid, href))
                    new_count += 1

                logger.debug(f"[Sarasota]   Page {page_num}: +{new_count} bookings (total: {len(all_links)})")

                if new_count == 0:
                    break

                # Check for next page
                next_link = soup.find("a", href=re.compile(rf"page={page_num + 1}"))
                if not next_link:
                    break

                page_num += 1
                time.sleep(0.5)

            time.sleep(0.5)

        return all_links

    # ── Phase 3: Extract detail page ──────────────────────────────────────────
    def _extract_detail(
        self, session, booking_id: str, detail_url: str
    ) -> Optional[ArrestRecord]:
        """Visit an inmate detail page and extract structured data with BS4."""
        from bs4 import BeautifulSoup

        resp = self._fetch(session, "GET", detail_url, extra_headers={
            "Referer": f"{BASE_URL}personSearch.php",
        })

        if not resp or resp.status_code != 200:
            logger.warning(f"[Sarasota] Detail page {booking_id}: HTTP {resp.status_code if resp else 'None'}")
            return None

        if "just a moment" in resp.text.lower()[:500]:
            logger.warning(f"[Sarasota] Cloudflare challenge on detail {booking_id}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # ── Extract name from h1 ──
        full_name = ""
        first_name = ""
        last_name = ""
        h1 = soup.find("h1", class_="page-title") or soup.find("h1")
        if h1:
            name_text = h1.get_text(strip=True)
            # Remove "Print" button text if present
            if "Print" in name_text:
                name_text = name_text.split("Print")[0].strip()
            full_name = name_text
            if "," in full_name:
                parts = full_name.split(",", 1)
                last_name = parts[0].strip()
                first_name = parts[1].strip()

        if not full_name:
            return None

        # ── Extract labeled fields ──
        # Sarasota Revize uses div.text-right for labels with values in next sibling
        field_map = {
            "dob": "dob", "date of birth": "dob",
            "race": "race", "sex": "sex", "gender": "sex",
            "height": "height", "weight": "weight",
            "address": "address", "city": "city", "state": "state",
            "zip code": "zip", "zip": "zip",
            "facility": "facility", "agency": "agency",
            "arrest date": "booking_date", "arrested": "booking_date",
            "date arrested": "booking_date", "booking date": "booking_date",
            "intake date": "booking_date",
        }
        fields = {}

        # Method 1: div.text-right label divs
        for label_div in soup.find_all("div", class_="text-right"):
            key = label_div.get_text(strip=True).rstrip(":").lower()
            mapped = field_map.get(key)
            if mapped:
                next_el = label_div.find_next_sibling()
                if next_el:
                    val = next_el.get_text(strip=True)
                    if val and mapped not in fields:
                        fields[mapped] = val

        # Method 2: dt/dd pairs
        for dt in soup.find_all("dt"):
            key = dt.get_text(strip=True).rstrip(":").lower()
            mapped = field_map.get(key)
            if mapped:
                dd = dt.find_next_sibling("dd")
                if dd:
                    val = dd.get_text(strip=True)
                    if val and mapped not in fields:
                        fields[mapped] = val

        # Method 3: label/value pairs
        for label in soup.find_all("label"):
            key = label.get_text(strip=True).rstrip(":").lower()
            mapped = field_map.get(key)
            if mapped:
                next_el = label.find_next_sibling()
                if next_el:
                    val = next_el.get_text(strip=True) or next_el.get("value", "")
                    if val and mapped not in fields:
                        fields[mapped] = val

        # Method 4: 2-column table rows
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) == 2:
                key = cells[0].get_text(strip=True).rstrip(":").lower()
                mapped = field_map.get(key)
                if mapped:
                    val = cells[1].get_text(strip=True)
                    if val and mapped not in fields:
                        fields[mapped] = val

        # ── Extract charges from data-table ──
        charges_list = []
        charges_raw = []
        total_bond = 0.0

        # Look for the charges table (Sarasota uses #data-table or tables with charge headers)
        charge_table = soup.find(id="data-table")
        if not charge_table:
            # Fallback: find any table with charge/offense/statute headers
            for table in soup.find_all("table"):
                headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
                if any(kw in " ".join(headers) for kw in ["charge", "offense", "statute", "bond"]):
                    charge_table = table
                    break

        if charge_table:
            headers = [th.get_text(strip=True).lower() for th in charge_table.find_all("th")]
            for row in charge_table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue

                cell_texts = [c.get_text(strip=True) for c in cells]

                # Extract charge description and bond
                desc = ""
                bond_str = "0"

                if headers:
                    # Map by header position
                    for i, h in enumerate(headers):
                        if i >= len(cells):
                            break
                        val = cell_texts[i]
                        if re.search(r"charge|desc|offense|statute", h) and not desc:
                            desc = val
                        elif re.search(r"bond|bail|amount", h):
                            bond_str = val
                else:
                    # Positional fallback: Sarasota typically has
                    # [arrest_date, charge_desc, degree, obts, bond_amount]
                    desc = cell_texts[1] if len(cell_texts) > 1 else ""
                    bond_str = cell_texts[4] if len(cell_texts) > 4 else "0"

                # Clean charge description
                desc = self._clean_charge_text(desc)
                if desc:
                    charges_list.append(desc)

                # Parse bond amount
                bond_clean = re.sub(r"[$,\s]", "", bond_str)
                try:
                    bond_val = float(bond_clean) if bond_clean else 0.0
                    total_bond += bond_val
                    charges_raw.append({"desc": desc, "bond": bond_val})
                except ValueError:
                    charges_raw.append({"desc": desc, "bond": 0.0})

        # ── Mugshot ──
        mugshot_url = ""
        for img in soup.find_all("img"):
            src = img.get("src", "")
            alt = img.get("alt", "").lower()
            if src and not src.startswith("data:") and (
                "mugshot" in alt or "mug" in src.lower() or
                "photo" in src.lower() or "inmate" in src.lower()
            ):
                if not src.startswith("http"):
                    src = f"{BASE_URL}{src.lstrip('/')}"
                mugshot_url = src
                break

        # ── Build ArrestRecord ──
        charges_str = " | ".join(charges_list) if charges_list else ""

        try:
            return ArrestRecord(
                County=self.county,
                Booking_Number=booking_id,
                Full_Name=full_name,
                First_Name=first_name,
                Last_Name=last_name,
                DOB=fields.get("dob", ""),
                Booking_Date=fields.get("booking_date", ""),
                Status="In Custody",
                Release_Date="",
                Facility=fields.get("facility", "Sarasota County Jail"),
                Agency=fields.get("agency", ""),
                Race=fields.get("race", ""),
                Sex=fields.get("sex", ""),
                Height=fields.get("height", ""),
                Weight=fields.get("weight", ""),
                Address=fields.get("address", ""),
                City=fields.get("city", ""),
                State=fields.get("state", "FL"),
                ZIP=fields.get("zip", ""),
                Mugshot_URL=mugshot_url,
                Charges=charges_str,
                Bond_Amount=str(total_bond) if total_bond > 0 else "0",
                Bond_Paid="NO",
                Detail_URL=detail_url,
                LastCheckedMode="INITIAL",
            )
        except Exception as e:
            logger.warning(f"[Sarasota] Record build error: {e}")
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
                    logger.warning(f"[Sarasota] HTTP {resp.status_code}, retry in {sleep_s:.1f}s")
                    time.sleep(sleep_s)
                    continue

                return resp

            except Exception as e:
                sleep_s = BACKOFF_BASE_S * (2 ** attempt)
                if attempt < RETRY_LIMIT - 1:
                    logger.warning(f"[Sarasota] HTTP error, retrying in {sleep_s:.1f}s: {e}")
                    time.sleep(sleep_s)
                else:
                    logger.error(f"[Sarasota] HTTP failed after {RETRY_LIMIT} retries: {e}")
                    return None

        return None

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _clean(text):
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _clean_charge_text(raw_charge):
        """Clean Revize charge description text."""
        if not raw_charge:
            return ""
        text = re.sub(r"^(New Charge:|Weekender:)\s*", "", raw_charge, flags=re.IGNORECASE)
        match = re.search(r"[\d.]+[a-z]*\s*-\s*([^(]+)", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        if "(" in text:
            desc = text.split("(")[0].strip()
            desc = re.sub(r"^[\d.]+[a-z]*\s*-\s*", "", desc)
            return desc.strip()
        return text.strip()

    # ── Single booking fetch (for FirstAppearanceWatcher) ────────────────────
    def _fetch_single_booking(
        self, booking_id: str, detail_url: Optional[str] = None
    ) -> Optional[ArrestRecord]:
        """Fetch a single Sarasota County booking by ID."""
        if not booking_id and not detail_url:
            return None
        if not detail_url:
            detail_url = f"{BASE_URL}viewInmate.php?id={booking_id}"

        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            logger.error("[Sarasota] curl_cffi not installed")
            return None

        session = cffi_requests.Session()

        try:
            # Establish parent context
            self._fetch(session, "GET", PARENT_URL)
            record = self._extract_detail(session, booking_id, detail_url)
            if record:
                record.LastCheckedMode = "UPDATE"
            return record
        except Exception as e:
            logger.warning(f"[Sarasota] _fetch_single_booking error ({booking_id}): {e}")
            return None
