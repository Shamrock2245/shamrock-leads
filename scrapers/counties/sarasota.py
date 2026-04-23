"""
Sarasota County Arrest Scraper — Revize CMS with DrissionPage.

Source: Sarasota County Sheriff's Office via Revize-hosted bookings
URL: https://cms.revize.com/revize/apps/sarasota/
Method: DrissionPage browser automation (Chromium headless)

Architecture (3-Phase):
1. Date search URL → collect all unique PINs (paginated)
2. PIN pages → resolve to booking detail URLs
3. Booking detail pages → extract structured data

Anti-bot: Cloudflare Managed Challenge (requires full JS execution)

Note: Sarasota is the most complex Revize county — it uses a PIN-based
intermediary step before booking details, and dates must be searched
individually. The legacy scraper was never fully stabilized, hence
this port includes robust fallback paths.

Ported from: swfl-arrest-scrapers/python_scrapers/scrapers/sarasota_solver.py
"""

import logging
import re
import time
import datetime as dt
from typing import List, Optional, Set, Tuple, Dict

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
BASE_URL = "https://cms.revize.com/revize/apps/sarasota/"
DAYS_BACK = 3
MAX_PAGES_PER_DATE = 30
DETAIL_DELAY_S = 1.0


class SarasotaCountyScraper(BaseScraper):
    """Sarasota County (FL) arrest scraper — 3-phase Revize CMS via DrissionPage."""

    @property
    def county(self) -> str:
        return "Sarasota"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape Sarasota County via 3-phase DrissionPage automation."""
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error(
                "❌ DrissionPage not installed. "
                "Install with: pip install DrissionPage"
            )
            return []

        page = self._setup_browser()

        # Build list of target dates (last N days)
        today = dt.datetime.now()
        target_dates: Set[str] = set()
        date_list: List[str] = []
        for i in range(DAYS_BACK):
            d = today - dt.timedelta(days=i)
            date_str = d.strftime('%m/%d/%Y')
            target_dates.add(date_str)
            date_list.append(date_str)

        logger.info(f"📅 Target dates: {', '.join(date_list)}")

        try:
            # ─── Phase 1: Collect PINs across all target dates ───
            logger.info("═══ Phase 1: Collecting PINs from date searches ═══")
            all_pins: Dict[str, Set[str]] = {}  # pin → set of dates

            for date_str in date_list:
                logger.info(f"🔍 Searching: {date_str}")
                pins = self._collect_pins_for_date(page, date_str)
                for pin in pins:
                    if pin not in all_pins:
                        all_pins[pin] = set()
                    all_pins[pin].add(date_str)
                time.sleep(1)

            logger.info(f"📊 Phase 1 complete: {len(all_pins)} unique PINs")

            if not all_pins:
                logger.warning("⚠️ No inmates found for any target date")
                return []

            # ─── Phase 2: Resolve PINs → Booking URLs ───
            logger.info("═══ Phase 2: Resolving PINs to booking URLs ═══")
            booking_set: Set[str] = set()
            booking_list: List[Tuple[str, str]] = []

            for idx, (pin, pin_dates) in enumerate(all_pins.items(), 1):
                logger.info(f"📝 [{idx}/{len(all_pins)}] PIN: {pin}")
                bookings = self._resolve_bookings_for_pin(page, pin, pin_dates)
                for b in bookings:
                    if b[1] not in booking_set:
                        booking_set.add(b[1])
                        booking_list.append(b)
                time.sleep(1)

            logger.info(f"📊 Phase 2 complete: {len(booking_list)} booking URLs")

            # Fallback: direct links if PIN resolution found nothing
            if not booking_list:
                logger.warning("⚠️ No bookings from PIN resolution. Trying direct links...")
                for date_str in date_list:
                    search_url = f"{BASE_URL}personSearch.php?type=date&date={date_str}"
                    page.get(search_url)
                    time.sleep(2)
                    if self._wait_for_cloudflare(page):
                        direct_links = (
                            page.eles('css:a[href*="viewInmate.php"]')
                            or page.eles('css:a[href*="booking.php"]')
                        )
                        for link in direct_links:
                            href = (link.attr('href') or '').replace('%20', '').strip()
                            if not href.startswith('http'):
                                href = BASE_URL + href
                            if href not in booking_set:
                                booking_set.add(href)
                                bid = href.split('=')[-1] if '=' in href else ''
                                booking_list.append((bid, href))

                logger.info(f"📊 Fallback found: {len(booking_list)} direct links")

            if not booking_list:
                logger.warning("⚠️ No booking links found at all")
                return []

            # ─── Phase 3: Extract Details ───
            logger.info(
                f"═══ Phase 3: Extracting details from "
                f"{len(booking_list)} bookings ═══"
            )
            records: List[ArrestRecord] = []

            for idx, (booking_id, detail_url) in enumerate(booking_list, 1):
                if idx % 10 == 0:
                    logger.info(
                        f"🔍 Progress: {idx}/{len(booking_list)} "
                        f"({len(records)} records)"
                    )

                try:
                    record = self._extract_detail(page, booking_id, detail_url)

                    if record and record.Full_Name:
                        records.append(record)
                    else:
                        logger.debug(f"⚠️ No name extracted for {booking_id}, skipping")

                except Exception as e:
                    logger.warning(f"⚠️ Error on {booking_id}: {e}")
                    continue

                time.sleep(DETAIL_DELAY_S)

            logger.info(f"✅ Scraped {len(records)} records from Sarasota")
            return records

        except Exception as e:
            logger.error(f"❌ Sarasota scraper fatal error: {e}")
            return []

        finally:
            try:
                page.quit()
            except Exception:
                pass

    # ── Browser Setup ──

    @staticmethod
    def _setup_browser():
        """Configure and launch DrissionPage Chromium browser."""
        from DrissionPage import ChromiumPage, ChromiumOptions

        co = ChromiumOptions()
        co.auto_port()
        co.headless(True)
        co.set_argument('--headless=new')
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-gpu")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--window-size=1920,1080")
        co.set_argument("--ignore-certificate-errors")
        co.set_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        return ChromiumPage(addr_or_opts=co)

    @staticmethod
    def _wait_for_cloudflare(page, max_wait: int = 30) -> bool:
        """Wait for Cloudflare challenge to clear, if present."""
        waited = 0
        while waited < max_wait:
            title = page.title.lower() if page.title else ''
            if ('just a moment' not in title
                    and 'checking' not in title
                    and 'security challenge' not in title):
                return True
            logger.debug(f"⏳ Cloudflare challenge... ({waited}/{max_wait}s)")
            time.sleep(1)
            waited += 1
        return False

    # ── Phase 1: Date Search → Collect PINs ──

    def _collect_pins_for_date(self, page, date_str: str) -> Set[str]:
        """Navigate to date search page and collect all unique PINs."""
        pins: Set[str] = set()
        page_num = 1

        while page_num <= MAX_PAGES_PER_DATE:
            search_url = f"{BASE_URL}personSearch.php?type=date&date={date_str}"
            if page_num > 1:
                search_url += f"&page={page_num}"

            logger.debug(f"📄 Page {page_num}: {search_url}")
            page.get(search_url)
            time.sleep(2)

            if not self._wait_for_cloudflare(page):
                logger.warning("❌ Cloudflare did not clear on search page")
                break

            # Extract PIN links: <a href="pinSearch.php?pin=XXXXX">
            pin_links = page.eles('css:a[href*="pinSearch.php"]')

            if not pin_links:
                if page_num == 1:
                    logger.debug(f"📋 No inmates found for {date_str}")
                break

            new_count = 0
            for link in pin_links:
                href = link.attr('href') or ''
                if 'pin=' in href:
                    pin = href.split('pin=')[1].split('&')[0].strip().replace('%20', '')
                    if pin and pin not in pins:
                        pins.add(pin)
                        new_count += 1

            logger.debug(f"📋 Page {page_num}: {new_count} new PINs (total: {len(pins)})")

            if new_count == 0:
                break

            # Check for next page
            next_page_links = page.eles(f'css:a[href*="page={page_num + 1}"]')
            if not next_page_links:
                break

            page_num += 1
            time.sleep(1)

        return pins

    # ── Phase 2: PIN Resolution → Booking URLs ──

    def _resolve_bookings_for_pin(
        self, page, pin: str, target_dates: Set[str]
    ) -> List[Tuple[str, str]]:
        """Visit a PIN search page and find booking links."""
        bookings: List[Tuple[str, str]] = []
        pin_url = f"{BASE_URL}pinSearch.php?pin={pin}"

        try:
            page.get(pin_url)
            time.sleep(2)

            if not self._wait_for_cloudflare(page):
                logger.warning(f"⚠️ Cloudflare on PIN page for {pin}")
                return bookings

            # Look for booking links in search result rows
            rows = page.eles('css:tr.search-row') or page.eles('css:tr')

            for row in rows:
                cells = row.eles('tag:td')
                if len(cells) >= 3:
                    arrest_date = self._clean(cells[0].text)
                    if arrest_date in target_dates:
                        booking_link = (
                            row.ele('css:a[href*="booking.php"]')
                            or row.ele('css:a[href*="viewInmate.php"]')
                        )
                        if booking_link:
                            href = (booking_link.attr('href') or '').replace('%20', '').strip()
                            if not href.startswith('http'):
                                href = BASE_URL + href
                            booking_id = ''
                            if 'id=' in href:
                                booking_id = href.split('id=')[1].split('&')[0]
                            elif 'pin=' in href:
                                booking_id = href.split('pin=')[1].split('&')[0]
                            bookings.append((booking_id or pin, href))

            # Fallback: try any booking/viewInmate links on the page
            if not bookings:
                all_links = (
                    page.eles('css:a[href*="booking.php"]')
                    or page.eles('css:a[href*="viewInmate.php"]')
                )
                for link in all_links:
                    href = (link.attr('href') or '').replace('%20', '').strip()
                    if not href.startswith('http'):
                        href = BASE_URL + href
                    booking_id = ''
                    if 'id=' in href:
                        booking_id = href.split('id=')[1].split('&')[0]
                    bookings.append((booking_id or pin, href))

        except Exception as e:
            logger.warning(f"⚠️ Error resolving PIN {pin}: {e}")

        return bookings

    # ── Phase 3: Detail Extraction ──

    def _extract_detail(
        self, page, booking_id: str, detail_url: str
    ) -> Optional[ArrestRecord]:
        """Extract structured data from a booking detail page."""
        page.get(detail_url)
        time.sleep(2)

        if not self._wait_for_cloudflare(page):
            logger.warning("⚠️ Cloudflare on detail page")
            return None

        # ── Name from h1.page-title ──
        full_name = ""
        first_name = ""
        last_name = ""

        h1 = page.ele('css:h1.page-title')
        if h1:
            raw_name = h1.text.split('Print')[0].strip()
            full_name = raw_name
            if ',' in raw_name:
                parts = raw_name.split(',', 1)
                last_name = parts[0].strip()
                first_name = parts[1].strip()

        # ── Personal Info from div.text-right labels ──
        field_map = {
            'dob': 'DOB',
            'date of birth': 'DOB',
            'race': 'Race',
            'sex': 'Sex',
            'gender': 'Sex',
            'height': 'Height',
            'weight': 'Weight',
            'address': 'Address',
            'city': 'City',
            'state': 'State',
            'zip code': 'Zipcode',
            'zip': 'Zipcode',
            'facility': 'Facility',
            'agency': 'Agency',
            'arrest date': 'Booking_Date',
            'arrested': 'Booking_Date',
            'date arrested': 'Booking_Date',
            'booking date': 'Booking_Date',
            'intake date': 'Booking_Date',
        }

        extracted: Dict[str, str] = {}
        label_divs = page.eles('css:div.text-right')
        for ld in label_divs:
            key = ld.text.replace(':', '').strip()
            key_lower = key.lower()
            try:
                val_div = ld.next()
                if val_div:
                    val = self._clean(val_div.text)
                    if val and key_lower in field_map:
                        schema_key = field_map[key_lower]
                        if schema_key not in extracted or not extracted[schema_key]:
                            extracted[schema_key] = val
            except Exception:
                pass

        # ── Charges from #data-table ──
        charges: List[str] = []
        total_bond = 0.0
        booking_date = extracted.get('Booking_Date', '')

        charge_rows = page.eles('css:#data-table tr')
        for row in charge_rows:
            cells = row.eles('tag:td')
            if len(cells) > 4:
                if not booking_id or booking_id == '':
                    bn = self._clean(cells[0].text)
                    if bn:
                        booking_id = bn

                charge_desc = self._clean(cells[1].text)
                if charge_desc:
                    clean_desc = self._clean_charge_text(charge_desc)
                    if clean_desc:
                        charges.append(clean_desc)

                bond_str = cells[4].text.replace('$', '').replace(',', '').strip()
                try:
                    if bond_str:
                        total_bond += float(bond_str)
                except ValueError:
                    pass

                # Intake date from column 6 as fallback
                if len(cells) > 6 and not booking_date:
                    intake = self._clean(cells[6].text)
                    if intake and ('/' in intake or '-' in intake):
                        booking_date = intake

        # ── Also try div.offense blocks (alternate layout) ──
        if not charges:
            offense_divs = page.eles('css:div.offense')
            for off_div in offense_divs:
                charge_data: Dict[str, str] = {}
                label_pairs = off_div.eles('css:div.text-right')
                for lp in label_pairs:
                    lbl = lp.text.replace(':', '').strip()
                    try:
                        vp = lp.next()
                        if vp:
                            charge_data[lbl] = self._clean(vp.text)
                    except Exception:
                        pass

                desc = charge_data.get('Charge Description', '') or charge_data.get('Offense', '')
                if desc:
                    charges.append(self._clean_charge_text(desc))

                bond_val = charge_data.get('Bond Amount', '0').replace('$', '').replace(',', '')
                try:
                    total_bond += float(bond_val)
                except ValueError:
                    pass

                if not booking_date:
                    for dk in ['Arrest Date', 'Date Arrested', 'Booking Date', 'Intake Date']:
                        if charge_data.get(dk):
                            booking_date = charge_data[dk]
                            break

        charges_str = " | ".join(charges) if charges else ""

        # ── Mugshot ──
        mugshot_url = ""
        mug = page.ele('css:.mug img') or page.ele('css:img[alt*="mugshot"]')
        if mug:
            src = mug.attr('src')
            if src and not src.startswith('data:'):
                if not src.startswith('http'):
                    src = BASE_URL + src
                mugshot_url = src

        if not full_name:
            return None

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_id,
            Full_Name=full_name,
            First_Name=first_name,
            Last_Name=last_name,
            DOB=extracted.get('DOB', ''),
            Booking_Date=booking_date,
            Status="In Custody",
            Facility=extracted.get('Facility', 'Sarasota County Jail'),
            Agency=extracted.get('Agency', ''),
            Race=extracted.get('Race', ''),
            Sex=extracted.get('Sex', ''),
            Height=extracted.get('Height', ''),
            Weight=extracted.get('Weight', ''),
            Address=extracted.get('Address', ''),
            City=extracted.get('City', ''),
            State=extracted.get('State', 'FL'),
            ZIP=extracted.get('Zipcode', ''),
            Mugshot_URL=mugshot_url,
            Charges=charges_str,
            Bond_Amount=str(total_bond) if total_bond > 0 else "0",
            Bond_Paid="NO",
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        )

    # ── Utilities ──

    @staticmethod
    def _clean(text: str) -> str:
        """Clean and normalize text."""
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _clean_charge_text(raw_charge: str) -> str:
        """Clean charge text to extract human-readable description."""
        if not raw_charge:
            return ''
        text = re.sub(
            r'^(New Charge:|Weekender:)\s*', '', raw_charge, flags=re.IGNORECASE
        )
        match = re.search(r'[\d.]+[a-z]*\s*-\s*([^(]+)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        if '(' in text:
            description = text.split('(')[0].strip()
            description = re.sub(r'^[\d.]+[a-z]*\s*-\s*', '', description)
            return description.strip()
        return text.strip()
