"""
Sarasota County Arrest Scraper — Revize CMS 3-Level Navigation via DrissionPage
=============================================================================
Source:  Sarasota County Sheriff's Office
Portal:  https://sarasotasheriff.org/arrest-reports/index.php
Backend: https://cms.revize.com/revize/apps/sarasota/ (Revize CMS)

NAVIGATION FLOW (3 levels):
  Level 1: personSearch.php?type=date&date=MM/DD/YYYY
           → Table: [Name, City, State, Charges, Arresting Agency]
           → Name links → pinSearch.php?pin=XXXXX

  Level 2: pinSearch.php?pin=XXXXX
           → "Person Search – LASTNAME, FIRSTNAME"
           → Table: [Arrest Date, Incident Number, Booking Number]
           → Booking Number links → booking.php?bkg=XXXXXXXXXXXX

  Level 3: booking.php?bkg=XXXXXXXXXXXX
           → Full detail: demographics, charges w/ bond amounts, mugshot
           → Sections: Personal Info, Arrest Info, Confinement, Charges

APPROACH:
  Uses DrissionPage browser automation (Chromium headless) to bypass
  Cloudflare challenges on cms.revize.com datacenter IPs. Three steps per person:
  1. Date search pagination -> collect pinSearch URLs.
  2. Load history (pinSearch.php) -> find most recent booking URL.
  3. Load details (booking.php) -> parse full demographics, charges, bonds.
"""
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── URLs ──────────────────────────────────────────────────────────────────────
PARENT_URL = "https://sarasotasheriff.org/arrest-reports/index.php"
BASE_URL   = "https://cms.revize.com/revize/apps/sarasota/"

# ── Tuning ────────────────────────────────────────────────────────────────────
DAYS_BACK          = 14    # Search last 14 days of bookings
MAX_PAGES_PER_DATE = 10    # Pagination cap per date search
DETAIL_DELAY_S     = 1.5   # Polite delay between page requests
PAGE_DELAY_S       = 1.0   # Delay between paginated search pages


class SarasotaCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Sarasota"

    # ── Setup Browser ──────────────────────────────────────────────────────────
    def _setup_browser(self):
        from DrissionPage import ChromiumPage
        co = self._get_browser_options()
        return ChromiumPage(addr_or_opts=co)

    # ── Evasion & Navigation ──────────────────────────────────────────────────
    def _navigate_and_wait(self, page, url) -> bool:
        """Navigate to URL and wait for Cloudflare Challenge bypass."""
        try:
            page.get(url)
            # Wait up to 20 seconds for the WAF 'just a moment' challenge to bypass
            max_wait = 20
            waited = 0
            while waited < max_wait:
                title = page.title.lower() if page.title else ""
                if "just a moment" not in title:
                    return True
                time.sleep(1)
                waited += 1
            return False
        except Exception as e:
            logger.warning(f"[Sarasota] Navigation error for {url}: {e}")
            return False

    # ── Main entry point ──────────────────────────────────────────────────────
    def scrape(self) -> List[ArrestRecord]:
        try:
            from bs4 import BeautifulSoup
            from DrissionPage import ChromiumPage
        except ImportError as e:
            logger.error(f"[Sarasota] Missing dependency: {e}. pip install beautifulsoup4 drissionpage")
            return []

        page = self._setup_browser()

        try:
            # Phase 0: Establish browser context via parent page
            logger.info(f"[Sarasota] Phase 0: Loading parent page for cookie/referer context")
            if self._navigate_and_wait(page, PARENT_URL):
                logger.info("[Sarasota] Parent page loaded — cookies/referer established")
            else:
                logger.warning("[Sarasota] Parent page failed — trying Revize directly")

            # Phase 1: Date search → collect person links (PIN + preview data)
            person_entries = self._collect_person_links(page)
            if not person_entries:
                logger.warning("[Sarasota] No person entries found across all dates")
                return []

            logger.info(f"[Sarasota] Phase 1 complete: {len(person_entries)} unique persons collected")

            # Phase 2+3: For each person → get most recent booking → extract detail
            records = []
            for idx, entry in enumerate(person_entries, 1):
                if idx % 10 == 0:
                    logger.info(
                        f"[Sarasota] Progress: {idx}/{len(person_entries)} "
                        f"({len(records)} records extracted)"
                    )

                try:
                    record = self._process_person(page, entry)
                    if record and record.Full_Name:
                        records.append(record)
                except Exception as e:
                    logger.warning(
                        f"[Sarasota] Error processing person {entry.get('name', '?')}: {e}"
                    )

                time.sleep(DETAIL_DELAY_S)

            logger.info(f"[Sarasota] Scrape complete: {len(records)} valid records from {len(person_entries)} persons")
            return records

        except Exception as e:
            logger.error(f"[Sarasota] Fatal scrape error: {e}")
            return []
        finally:
            try:
                page.quit()
            except Exception:
                pass

    # ── Phase 1: Date search → collect person PIN links ───────────────────────
    def _collect_person_links(self, page) -> List[Dict]:
        """
        Search each of the last N days and collect person links (pinSearch URLs)
        along with preview data from the search results table.

        Returns list of dicts with keys: pin, pin_url, name, city, state, charges, agency
        """
        from bs4 import BeautifulSoup

        all_persons: List[Dict] = []
        seen_pins: set = set()
        today = datetime.now()

        for day_offset in range(DAYS_BACK):
            target_date = today - timedelta(days=day_offset)
            date_str = target_date.strftime("%m/%d/%Y")
            search_url = f"{BASE_URL}personSearch.php?type=date&date={date_str}"

            logger.info(f"[Sarasota] Searching date: {date_str}")

            page_num = 1
            while page_num <= MAX_PAGES_PER_DATE:
                url = search_url if page_num == 1 else f"{search_url}&page={page_num}"

                if not self._navigate_and_wait(page, url):
                    logger.debug(f"[Sarasota] Search page navigation failed for {date_str} p{page_num}")
                    break

                # Check for Cloudflare challenge
                if "just a moment" in (page.title.lower() if page.title else ""):
                    logger.warning(f"[Sarasota] Cloudflare challenge on search for {date_str}")
                    break

                soup = BeautifulSoup(page.html, "html.parser")

                # Find the results table — it has columns: Name, City, State, Charges, Agency
                results_table = None
                for table in soup.find_all("table"):
                    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
                    if "name" in headers and ("charges" in headers or "city" in headers):
                        results_table = table
                        break

                if not results_table:
                    logger.debug(f"[Sarasota] No results table found for {date_str} p{page_num}")
                    break

                # Parse header positions
                headers = [th.get_text(strip=True).lower() for th in results_table.find_all("th")]
                new_count = 0

                for row in results_table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) < 2:
                        continue

                    cell_texts = [c.get_text(strip=True) for c in cells]

                    # Find the name cell with the pinSearch link
                    name_link = None
                    for cell in cells:
                        link = cell.find("a", href=re.compile(r"pinSearch\.php\?pin="))
                        if link:
                            name_link = link
                            break

                    if not name_link:
                        continue

                    href = name_link.get("href", "")
                    if not href:
                        continue

                    # Extract PIN from URL
                    pin_match = re.search(r"pin=(\d+)", href)
                    if not pin_match:
                        continue
                    pin = pin_match.group(1)

                    if pin in seen_pins:
                        continue
                    seen_pins.add(pin)

                    # Build full URL
                    if not href.startswith("http"):
                        pin_url = f"{BASE_URL}{href.lstrip('/')}"
                    else:
                        pin_url = href

                    # Extract preview data from table columns
                    name = name_link.get_text(strip=True)

                    # Map data by header position
                    city = ""
                    state = ""
                    charges_preview = ""
                    agency = ""

                    for i, h in enumerate(headers):
                        if i >= len(cell_texts):
                            break
                        val = cell_texts[i]
                        if h == "city":
                            city = val
                        elif h == "state":
                            state = val
                        elif h == "charges":
                            charges_preview = val
                        elif "agency" in h or "arresting" in h:
                            agency = val

                    all_persons.append({
                        "pin": pin,
                        "pin_url": pin_url,
                        "name": name,
                        "city": city,
                        "state": state or "FL",
                        "charges_preview": charges_preview,
                        "agency": agency,
                    })
                    new_count += 1

                logger.debug(
                    f"[Sarasota]   Date {date_str} p{page_num}: "
                    f"+{new_count} persons (total: {len(all_persons)})"
                )

                if new_count == 0:
                    break

                # Check for next page link
                next_link = soup.find("a", string=re.compile(r"Next|›"))
                if not next_link:
                    # Also check for numbered page link
                    next_link = soup.find("a", href=re.compile(rf"page={page_num + 1}"))
                if not next_link:
                    break

                page_num += 1
                time.sleep(PAGE_DELAY_S)

            time.sleep(PAGE_DELAY_S)

        return all_persons

    # ── Phase 2+3: Person → most recent booking → detail extraction ───────────
    def _process_person(self, page, entry: Dict) -> Optional[ArrestRecord]:
        """
        For a given person entry from search results:
        1. Visit their person history page (pinSearch) → find most recent booking link
        2. Visit the booking detail page (booking.php) → extract full record
        """
        from bs4 import BeautifulSoup

        pin = entry["pin"]
        pin_url = entry["pin_url"]

        # ── Level 2: Person History → get most recent booking URL ──
        if not self._navigate_and_wait(page, pin_url):
            logger.debug(f"[Sarasota] Person history navigation failed for PIN {pin}")
            return None

        if "just a moment" in (page.title.lower() if page.title else ""):
            logger.warning(f"[Sarasota] Cloudflare on person history PIN {pin}")
            return None

        soup = BeautifulSoup(page.html, "html.parser")

        # Find the booking links in the history table
        # Table columns: [Arrest Date, Arrest Agency Incident Number, Booking Number]
        # Booking Number column has links to booking.php?bkg=XXXXXXXXXXXX
        booking_links = soup.find_all("a", href=re.compile(r"booking\.php\?bkg="))

        if not booking_links:
            # Fallback: also try viewInmate.php patterns
            booking_links = soup.find_all("a", href=re.compile(r"viewInmate\.php\?"))

        if not booking_links:
            logger.debug(f"[Sarasota] No booking links found for PIN {pin} ({entry['name']})")
            return None

        # Take the FIRST booking link (most recent, since the table is date-sorted desc)
        first_booking_link = booking_links[0]
        booking_href = first_booking_link.get("href", "")
        booking_number = first_booking_link.get_text(strip=True)

        if not booking_href:
            return None

        # Build full booking detail URL
        if not booking_href.startswith("http"):
            booking_url = f"{BASE_URL}{booking_href.lstrip('/')}"
        else:
            booking_url = booking_href

        time.sleep(DETAIL_DELAY_S)

        # ── Level 3: Booking Detail → full record extraction ──
        return self._extract_booking_detail(
            page, booking_number, booking_url, entry
        )

    # ── Phase 3: Extract booking detail page ──────────────────────────────────
    def _extract_booking_detail(
        self, page, booking_number: str, booking_url: str, preview: Dict
    ) -> Optional[ArrestRecord]:
        """
        Visit the booking detail page and extract all structured data.
        """
        from bs4 import BeautifulSoup

        if not self._navigate_and_wait(page, booking_url):
            logger.warning(f"[Sarasota] Booking detail navigation failed for {booking_number}")
            return None

        if "just a moment" in (page.title.lower() if page.title else ""):
            logger.warning(f"[Sarasota] Cloudflare on booking detail {booking_number}")
            return None

        soup = BeautifulSoup(page.html, "html.parser")

        # ── Extract name from page header ──
        full_name = ""
        first_name = ""
        last_name = ""

        # The detail page has the person's name in an h2/h3 or in a specific section
        # Try multiple patterns
        for tag in ["h2", "h3", "h1"]:
            header = soup.find(tag)
            if header:
                text = header.get_text(strip=True)
                # Skip generic page titles
                if text and text.upper() not in (
                    "ARRESTS & INMATE SEARCH",
                    "PERSON SEARCH",
                    "CURRENT INMATE POPULATION",
                    "ARREST SEARCH DISCLAIMER",
                ):
                    # Check if it looks like a name (LAST, FIRST or contains letters)
                    if re.match(r"^[A-Z\s,\-\']+$", text, re.IGNORECASE):
                        full_name = text
                        break

        # Fallback: use the name from Level 1 search results
        if not full_name:
            full_name = preview.get("name", "")

        if not full_name:
            return None

        # Parse name parts
        if "," in full_name:
            parts = full_name.split(",", 1)
            last_name = parts[0].strip()
            first_name = parts[1].strip()

        # ── Extract all labeled fields ──
        # The detail page uses div-based label/value pairs organized in sections
        field_map = {
            "dob": "dob", "date of birth": "dob",
            "age at arrest": "age", "age": "age",
            "race": "race",
            "sex": "sex", "gender": "sex",
            "height": "height", "weight": "weight",
            "eye color": "eye_color",
            "hair color": "hair_color",
            "address": "address", "street address": "address",
            "city": "city", "state": "state",
            "zip code": "zip", "zip": "zip",
            "place of birth": "pob",
            "citizenship": "citizenship",
            "facility": "facility",
            "arresting agency": "agency", "agency": "agency",
            "arrest date": "booking_date", "date arrested": "booking_date",
            "booking date": "booking_date", "intake date": "booking_date",
            "arrest city": "arrest_city",
            "status": "custody_status",
            "release date": "release_date",
            "releasing reason": "release_reason",
            "pin": "pin_number",
            "booking number": "booking_num",
        }
        fields: Dict[str, str] = {}

        # Method 1: div.text-right or strong/b label elements
        for label_el in soup.find_all(["div", "strong", "b", "span"], class_=re.compile(r"text-right|label|field-label")):
            key = label_el.get_text(strip=True).rstrip(":").lower()
            mapped = field_map.get(key)
            if mapped:
                next_el = label_el.find_next_sibling()
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

        # Method 3: 2-column table rows (label | value)
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) == 2:
                key = cells[0].get_text(strip=True).rstrip(":").lower()
                mapped = field_map.get(key)
                if mapped:
                    val = cells[1].get_text(strip=True)
                    if val and mapped not in fields:
                        fields[mapped] = val

        # Method 4: label + adjacent element
        for label in soup.find_all("label"):
            key = label.get_text(strip=True).rstrip(":").lower()
            mapped = field_map.get(key)
            if mapped:
                next_el = label.find_next_sibling()
                if next_el:
                    val = next_el.get_text(strip=True) or next_el.get("value", "")
                    if val and mapped not in fields:
                        fields[mapped] = val

        # Method 5: Scan all text for label: value patterns
        page_text = soup.get_text()
        for pattern_key, mapped_key in field_map.items():
            if mapped_key in fields:
                continue
            pattern = re.compile(
                rf"{re.escape(pattern_key)}\s*[:\s]\s*(.+?)(?:\n|$)",
                re.IGNORECASE
            )
            match = pattern.search(page_text)
            if match:
                val = match.group(1).strip()
                if val and len(val) < 100:  # Sanity check
                    fields[mapped_key] = val

        # ── Extract charges and bonds ──
        charges_list = []
        total_bond = 0.0

        charge_table = soup.find(id="data-table")
        if not charge_table:
            for table in soup.find_all("table"):
                headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
                header_text = " ".join(headers)
                if any(kw in header_text for kw in ["charge", "offense", "statute", "bond", "docket"]):
                    charge_table = table
                    break

        if charge_table:
            headers = [th.get_text(strip=True).lower() for th in charge_table.find_all("th")]

            for row in charge_table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue

                cell_texts = [c.get_text(strip=True) for c in cells]

                desc = ""
                bond_str = "0"
                case_num = ""

                if headers:
                    for i, h in enumerate(headers):
                        if i >= len(cells):
                            break
                        val = cell_texts[i]
                        if re.search(r"charge|desc|offense", h) and not desc:
                            desc = val
                        elif re.search(r"bond|bail|amount", h):
                            bond_str = val
                        elif re.search(r"docket|case", h) and not case_num:
                            case_num = val
                else:
                    # Positional fallback
                    desc = cell_texts[1] if len(cell_texts) > 1 else ""
                    bond_str = cell_texts[-1] if len(cell_texts) > 3 else "0"

                desc = self._clean_charge_text(desc)
                if desc:
                    charges_list.append(desc)

                bond_clean = re.sub(r"[$,\s]", "", bond_str)
                try:
                    bond_val = float(bond_clean) if bond_clean else 0.0
                    total_bond += bond_val
                except ValueError:
                    pass

        # If no charges from detail page, use Level 1 preview
        if not charges_list and preview.get("charges_preview"):
            charges_list = [preview["charges_preview"]]

        # ── Mugshot ──
        mugshot_url = ""
        for img in soup.find_all("img"):
            src = img.get("src", "")
            alt = img.get("alt", "").lower()
            if src and not src.startswith("data:") and (
                "mugshot" in alt or "mug" in src.lower() or
                "photo" in src.lower() or "inmate" in src.lower() or
                "booking" in alt
            ):
                if not src.startswith("http"):
                    src = f"{BASE_URL}{src.lstrip('/')}"
                mugshot_url = src
                break

        # ── Determine custody status ──
        custody_status = fields.get("custody_status", "In Custody")
        if custody_status.lower() in ("released", "out", "discharged"):
            custody_status = "Released"
        else:
            custody_status = "In Custody"

        # ── Build ArrestRecord ──
        charges_str = " | ".join(charges_list) if charges_list else ""

        city = fields.get("city") or preview.get("city", "")
        state = fields.get("state") or preview.get("state", "FL")
        agency = fields.get("agency") or preview.get("agency", "")

        try:
            return ArrestRecord(
                County=self.county,
                Booking_Number=booking_number,
                Full_Name=full_name,
                First_Name=first_name,
                Last_Name=last_name,
                DOB=fields.get("dob", ""),
                Booking_Date=fields.get("booking_date", ""),
                Status=custody_status,
                Release_Date=fields.get("release_date", ""),
                Facility=fields.get("facility", "Sarasota County Jail"),
                Agency=agency,
                Race=fields.get("race", ""),
                Sex=fields.get("sex", ""),
                Height=fields.get("height", ""),
                Weight=fields.get("weight", ""),
                Address=fields.get("address", ""),
                City=city,
                State=state,
                ZIP=fields.get("zip", ""),
                Mugshot_URL=mugshot_url,
                Charges=charges_str,
                Bond_Amount=str(total_bond) if total_bond > 0 else "0",
                Bond_Paid="NO",
                Detail_URL=booking_url,
                LastCheckedMode="INITIAL",
            )
        except Exception as e:
            logger.warning(f"[Sarasota] Record build error for {booking_number}: {e}")
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _clean_charge_text(raw_charge: str) -> str:
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
            detail_url = f"{BASE_URL}booking.php?bkg={booking_id}"

        page = None
        try:
            page = self._setup_browser()
            # Establish parent context
            self._navigate_and_wait(page, PARENT_URL)
            preview = {"pin": "", "pin_url": "", "name": "", "city": "", "state": "FL", "agency": ""}
            record = self._extract_booking_detail(page, booking_id, detail_url, preview)
            if record:
                record.LastCheckedMode = "UPDATE"
            return record
        except Exception as e:
            logger.warning(f"[Sarasota] _fetch_single_booking error ({booking_id}): {e}")
            return None
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass
