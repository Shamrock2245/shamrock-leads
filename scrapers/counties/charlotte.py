"""
Charlotte County Arrest Scraper — Revize CMS with DrissionPage.

Source: Charlotte County Sheriff's Office via Revize-hosted jail roster
URL: https://inmates.charlottecountyfl.revize.com/bookings
Method: DrissionPage browser automation (Chromium headless)

Architecture:
1. Load listing page → collect all booking detail links
2. Visit each detail page → extract via JavaScript injection
3. Parse charges, bond, demographics from Revize HTML structure
4. Date-gated: stops when records fall outside the cutoff window

Note: Charlotte uses the Revize CMS platform (same as Manatee County).
This requires a real browser for JavaScript rendering + Cloudflare bypass.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
BASE_URL = "https://inmates.charlottecountyfl.revize.com"
BOOKINGS_URL = f"{BASE_URL}/bookings"
DAYS_BACK = 21
MAX_PAGES = 10
DETAIL_DELAY_S = 1.0


class CharlotteCountyScraper(BaseScraper):
    """Charlotte County (FL) arrest scraper — Revize CMS via DrissionPage."""

    @property
    def county(self) -> str:
        return "Charlotte"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape Charlotte County via DrissionPage browser automation."""
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error(
                "❌ DrissionPage not installed. "
                "Install with: pip install DrissionPage"
            )
            return []

        page = self._setup_browser()
        cutoff_date = datetime.now() - timedelta(days=DAYS_BACK)

        try:
            # Phase 1: Collect all booking detail links
            booking_links = self._collect_booking_links(page)

            if not booking_links:
                logger.warning("⚠️ No booking links found")
                return []

            logger.info(f"📋 Collected {len(booking_links)} booking links")

            # Phase 2: Visit each detail page and extract data
            records: List[ArrestRecord] = []

            for idx, (booking_id, detail_url) in enumerate(booking_links, 1):
                if idx % 10 == 0:
                    logger.info(
                        f"🔍 Progress: {idx}/{len(booking_links)} "
                        f"({len(records)} records so far)"
                    )

                try:
                    record = self._extract_detail(page, booking_id, detail_url)

                    if not record:
                        continue

                    # Date cutoff check
                    if record.Booking_Date:
                        try:
                            for fmt in ["%m/%d/%Y", "%Y-%m-%d"]:
                                try:
                                    book_dt = datetime.strptime(
                                        record.Booking_Date.split()[0], fmt
                                    )
                                    if book_dt < cutoff_date:
                                        logger.info(
                                            f"⏸️ Past cutoff ({record.Booking_Date}), "
                                            f"stopping. Got {len(records)} records."
                                        )
                                        return records
                                    break
                                except ValueError:
                                    continue
                        except Exception:
                            pass

                    if record.Full_Name and record.Booking_Number:
                        records.append(record)

                except Exception as e:
                    logger.warning(
                        f"⚠️ Error on detail {booking_id}: {e}"
                    )

                time.sleep(DETAIL_DELAY_S)

            logger.info(f"✅ Scraped {len(records)} records from Charlotte")
            return records

        except Exception as e:
            logger.error(f"❌ Charlotte scraper fatal error: {e}")
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
        co.headless(True)  # Always headless in production
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--window-size=1920,1080")
        co.set_argument("--disable-gpu")
        co.set_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        return ChromiumPage(addr_or_opts=co)

    @staticmethod
    def _wait_for_cloudflare(page, max_wait: int = 20) -> bool:
        """Wait for Cloudflare challenge to clear."""
        waited = 0
        while waited < max_wait:
            title = page.title.lower() if page.title else ""
            if "just a moment" not in title:
                return True
            logger.debug(f"⏳ Cloudflare challenge... ({waited}/{max_wait}s)")
            time.sleep(1)
            waited += 1
        return False

    # ── Phase 1: Link Collection ──

    def _collect_booking_links(self, page) -> List[Tuple[str, str]]:
        """Collect all booking detail URLs from listing pages."""
        all_links: List[Tuple[str, str]] = []
        current_page = 1

        while current_page <= MAX_PAGES:
            url = (
                BOOKINGS_URL
                if current_page == 1
                else f"{BOOKINGS_URL}?page={current_page}"
            )
            logger.info(f"📄 Loading listing page {current_page}: {url}")

            page.get(url)
            time.sleep(2)

            if not self._wait_for_cloudflare(page):
                logger.warning("❌ Cloudflare did not clear")
                break

            # Find booking links: /bookings/{id}
            booking_els = page.eles('xpath://a[contains(@href, "/bookings/")]')

            valid_links = []
            for el in booking_els:
                href = el.attr("href") or ""
                if href.endswith("/bookings") or href.endswith("/bookings/"):
                    continue
                if not href.startswith("http"):
                    href = f"{BASE_URL}{href}"
                booking_id = (
                    href.split("/bookings/")[-1].split("?")[0].strip()
                    if "/bookings/" in href
                    else ""
                )
                if booking_id:
                    valid_links.append((booking_id, href))

            logger.info(
                f"📋 Page {current_page}: {len(valid_links)} inmates"
            )

            if not valid_links:
                break

            all_links.extend(valid_links)

            # Check for next page
            next_btn = (
                page.ele('css:a[rel="next"]')
                or page.ele("text:Next")
                or page.ele("css:.pagination .next a")
            )
            if not next_btn:
                break

            current_page += 1
            time.sleep(1)

        # Deduplicate by URL
        seen = set()
        unique = []
        for bid, url in all_links:
            if url not in seen:
                seen.add(url)
                unique.append((bid, url))

        return unique

    # ── Phase 2: Detail Extraction ──

    def _extract_detail(
        self, page, booking_id: str, detail_url: str
    ) -> Optional[ArrestRecord]:
        """Extract structured arrest data from a Revize detail page via JS."""
        page.get(detail_url)
        time.sleep(2)

        if not self._wait_for_cloudflare(page):
            logger.warning(f"⚠️ Cloudflare on detail page {booking_id}")
            return None

        # Run JavaScript to extract all data from the Revize detail page
        js_data = page.run_js("""
            const result = {};

            // 1. Personal Info — labels + inputs/siblings
            const labels = document.querySelectorAll('label, th, td, dt');
            labels.forEach(label => {
                const text = label.textContent.trim().replace(/:$/, '');
                let value = null;
                const parent = label.parentElement;
                const input = parent ? parent.querySelector('input') : null;
                const nextSib = label.nextElementSibling;
                if (input) value = input.value || input.textContent;
                else if (nextSib) value = nextSib.textContent || nextSib.value;
                if (value) result[text] = value.trim();
            });

            // 2. Booking table (#bookings-table)
            const bookTable = document.querySelector('#bookings-table');
            if (bookTable) {
                const row = bookTable.querySelector('tr[data-booking]');
                if (row) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 3) {
                        result['__Booking_Date'] = cells[2].textContent.trim();
                        if (cells.length > 3) result['__Status'] = cells[3].textContent.trim();
                    }
                }
            }

            // 3. Charges from .arrest-table (exclude mobile tables)
            const charges = [];
            document.querySelectorAll('table.arrest-table:not(.table-mobile)').forEach(table => {
                const headers = Array.from(table.querySelectorAll('th')).map(h => h.textContent.trim());
                if (headers.some(h => h.includes('Statute') || h.includes('Desc'))) {
                    table.querySelectorAll('tbody tr').forEach(row => {
                        const cells = row.querySelectorAll('td');
                        if (cells.length >= 6) {
                            charges.push({
                                date: cells[0].textContent.trim(),
                                statute: cells[1].textContent.trim(),
                                desc: cells[2].textContent.trim(),
                                sec_desc: cells[3].textContent.trim(),
                                bond: cells[5].textContent.trim()
                            });
                        }
                    });
                }
            });
            result['__CHARGES'] = charges;

            // 4. ICE hold detection
            result['__HAS_ICE'] = document.body.textContent.includes('ICE HOLD') ||
                                   document.body.textContent.includes('IMMIGRATION DETAINER');

            // 5. Mugshot
            const img = document.querySelector('img[src*="photo"], img[src*="mugshot"], img[src*="image"]');
            if (img && !img.src.startsWith('data:')) result['__Mugshot'] = img.src;

            return result;
        """)

        if not js_data:
            return None

        # Map JS fields → ArrestRecord fields
        first_name = self._clean(js_data.get("First Name", ""))
        last_name = self._clean(js_data.get("Last Name", ""))
        middle_name = self._clean(js_data.get("Middle Name", ""))

        full_name = ""
        if last_name and first_name:
            full_name = f"{last_name}, {first_name}"
            if middle_name:
                full_name += f" {middle_name}"

        # Parse charges & bond
        charges_list = []
        total_bond = 0.0
        booking_date = ""

        for entry in js_data.get("__CHARGES", []):
            desc = self._clean(entry.get("desc", ""))
            statute = self._clean(entry.get("statute", ""))
            sec_desc = self._clean(entry.get("sec_desc", ""))
            bond_str = self._clean(entry.get("bond", "0"))
            arr_date = self._clean(entry.get("date", ""))

            if not booking_date and arr_date:
                booking_date = arr_date

            charge_text = self._clean_charge_text(desc)
            if sec_desc and sec_desc != "A/W":
                charge_text += f" ({sec_desc})"
            if statute:
                charge_text = f"{statute} - {charge_text}"
            if charge_text:
                charges_list.append(charge_text)

            try:
                total_bond += float(
                    bond_str.replace("$", "").replace(",", "")
                )
            except (ValueError, TypeError):
                pass

        # ICE hold
        if js_data.get("__HAS_ICE"):
            charges_list.insert(0, "ICE HOLD")

        # Booking date from bookings-table
        if not booking_date and js_data.get("__Booking_Date"):
            bd = self._clean(js_data["__Booking_Date"])
            if len(bd) > 5:
                booking_date = bd

        # Status
        status = self._clean(js_data.get("__Status", "In Custody"))

        charges_str = " | ".join(charges_list) if charges_list else ""

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_id,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB=self._clean(js_data.get("Date of Birth", "")),
            Booking_Date=booking_date,
            Status=status,
            Facility="Charlotte County Jail",
            Race=self._clean(js_data.get("Race", "")),
            Sex=self._clean(js_data.get("Gender", "")),
            Height=self._clean(js_data.get("Height", "")),
            Weight=self._clean(js_data.get("Weight", "")),
            Address=self._clean(js_data.get("Address", "")),
            City=self._clean(js_data.get("City", "")),
            State=self._clean(js_data.get("State", "FL")),
            ZIP=self._clean(js_data.get("Zip Code", "")),
            Mugshot_URL=js_data.get("__Mugshot", ""),
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
            return ""
        text = re.sub(
            r"^(New Charge:|Weekender:)\s*", "", raw_charge, flags=re.IGNORECASE
        )
        match = re.search(r"[\d.]+[a-z]*\s*-\s*([^(]+)", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        if "(" in text:
            description = text.split("(")[0].strip()
            description = re.sub(r"^[\d.]+[a-z]*\s*-\s*", "", description)
            return description.strip()
        return text.strip()
