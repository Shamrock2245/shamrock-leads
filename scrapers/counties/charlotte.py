"""
Charlotte County Arrest Scraper — Revize CMS via DrissionPage
==============================================================
Source:  Charlotte County Sheriff's Office (CCSO)
Portal:  https://ccso.org/correctional_facility/local_arrest_database.php
Backend: https://inmates.charlottecountyfl.revize.com/bookings  (Revize CMS)

ROOT CAUSE OF OLD SCRAPER FAILURE
-----------------------------------
The old scraper navigated directly to inmates.charlottecountyfl.revize.com,
which returns HTTP 403 to all datacenter/VPS IPs (Cloudflare WAF).

FIX: Navigate to ccso.org parent page FIRST to establish a legitimate
Referer + cookie context, then navigate to the Revize bookings roster.
This is identical to the Sarasota scraper pattern (sarasotasheriff.org → cms.revize.com).

ADDITIONAL FIX: Switched from patchright+xvfb subprocess to DrissionPage
(same approach as Manatee County — proven reliable on Revize).

ADDITIONAL FIX: Old scraper used days_back=21 cutoff which silently dropped
inmates booked more than 21 days ago who are still in custody.
New scraper scrapes ALL in-custody inmates (no date cutoff on roster).
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
DETAIL_DELAY_S = 1.0   # Polite delay between detail page requests
CF_WAIT_S      = 30    # Max seconds to wait for Cloudflare to clear
PAGE_LOAD_WAIT = 8     # Seconds to wait for table rows to appear


class CharlotteCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Charlotte"

    # ── Main entry point ──────────────────────────────────────────────────────
    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error("[Charlotte] DrissionPage not installed — run: pip install DrissionPage")
            return []

        page = self._setup_browser()
        if not page:
            return []

        try:
            # Phase 1: Establish context via parent page (bypass Cloudflare WAF)
            if not self._load_parent_page(page):
                logger.error("[Charlotte] Failed to load parent page — aborting")
                page.quit()
                return []

            # Phase 2: Collect all booking detail URLs from the roster
            booking_links = self._collect_booking_links(page)
            if not booking_links:
                logger.warning("[Charlotte] No booking links found on roster")
                page.quit()
                return []

            logger.info(f"[Charlotte] Collected {len(booking_links)} booking links")

            # Phase 3: Visit each detail page and extract data
            records = []
            for idx, (booking_id, detail_url) in enumerate(booking_links, 1):
                if idx % 10 == 0:
                    logger.info(
                        f"[Charlotte] Progress: {idx}/{len(booking_links)} ({len(records)} records)"
                    )
                try:
                    record = self._extract_detail(page, booking_id, detail_url)
                    if record and record.Full_Name and record.Booking_Number:
                        records.append(record)
                except Exception as e:
                    logger.warning(f"[Charlotte] Error on detail {booking_id}: {e}")
                time.sleep(DETAIL_DELAY_S)

            logger.info(f"[Charlotte] Scraped {len(records)} in-custody records")
            return records

        except Exception as e:
            logger.error(f"[Charlotte] Scrape error: {e}", exc_info=True)
            return []
        finally:
            try:
                page.quit()
            except Exception:
                pass

    # ── Browser setup ─────────────────────────────────────────────────────────
    def _setup_browser(self):
        """Configure DrissionPage browser using base class options + extra stealth.

        CRITICAL: Must use self._get_browser_options() to set Chrome binary path
        in Docker. The old implementation created raw ChromiumOptions() which
        silently failed inside Docker containers (Chrome not found).
        """
        try:
            from DrissionPage import ChromiumPage
            co = self._get_browser_options()
            # Extra stealth for Cloudflare bypass on ccso.org + Revize CMS
            co.set_argument("--disable-extensions")
            co.set_argument("--disable-features=IsolateOrigins,site-per-process")
            co.set_argument("--disable-web-security")
            co.set_argument("--lang=en-US,en;q=0.9")
            # Critical anti-detection flags for Cloudflare
            co.set_argument("--disable-blink-features=AutomationControlled")
            co.set_argument("--window-size=1920,1080")
            co.set_argument("--disable-infobars")
            page = ChromiumPage(addr_or_opts=co)
            # Inject stealth JS to hide automation markers
            try:
                page.run_js("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    window.chrome = {runtime: {}};
                """)
            except Exception:
                pass  # Non-fatal if JS injection fails
            return page
        except Exception as e:
            logger.error(f"[Charlotte] Browser setup failed: {e}")
            return None

    # ── Phase 1: Load parent page ─────────────────────────────────────────────
    def _load_parent_page(self, page) -> bool:
        """Navigate to ccso.org to establish Referer/cookie context.

        This is the critical bypass step.  Revize blocks direct datacenter
        requests but allows them when they arrive with a ccso.org Referer.
        """
        logger.info(f"[Charlotte] Loading parent page: {PARENT_URL}")
        try:
            page.get(PARENT_URL)
        except Exception as e:
            logger.warning(f"[Charlotte] Parent page load error: {e}")
            # Try anyway — partial load may still set the cookie
            pass

        # Wait for Cloudflare to clear on the parent
        for i in range(CF_WAIT_S):
            time.sleep(1)
            title = page.title or ""
            if title and "just a moment" not in title.lower() and "403" not in title:
                logger.info(f"[Charlotte] Parent page ready after {i}s: {title}")
                return True

        logger.warning("[Charlotte] Parent page Cloudflare did not clear — trying anyway")
        return True  # Attempt roster anyway; sometimes partial load is enough

    # ── Phase 2: Collect booking links ────────────────────────────────────────
    def _collect_booking_links(self, page) -> List[Tuple[str, str]]:
        """Paginate through the Revize bookings roster and collect all detail URLs."""
        all_links: List[Tuple[str, str]] = []
        seen_urls: set = set()

        for current_page in range(1, MAX_PAGES + 1):
            url = BOOKINGS_URL if current_page == 1 else f"{BOOKINGS_URL}?page={current_page}"
            logger.info(f"[Charlotte] Loading roster page {current_page}: {url}")

            try:
                page.get(url)
            except Exception as e:
                logger.warning(f"[Charlotte] Roster page {current_page} load error: {e}")
                break

            # Wait for Cloudflare
            for _ in range(CF_WAIT_S):
                time.sleep(1)
                title = page.title or ""
                if title and "just a moment" not in title.lower() and "403" not in title:
                    break

            # Wait for table rows
            found_rows = False
            for _ in range(PAGE_LOAD_WAIT):
                time.sleep(1)
                try:
                    rows = page.eles("css:table tbody tr")
                    if rows:
                        found_rows = True
                        break
                except Exception:
                    pass

            if not found_rows:
                logger.info(f"[Charlotte] No table rows on page {current_page} — end of roster")
                break

            # Extract booking links via JS
            try:
                page_urls = page.run_js("""
                    (() => {
                        const links = Array.from(document.querySelectorAll('a[href*="/bookings/"]'));
                        const urls = new Set();
                        links.forEach(link => {
                            let href = link.getAttribute('href');
                            if (!href) return;
                            if (/\\/bookings\\/?$/.test(href)) return;
                            if (!href.startsWith('http')) {
                                href = 'https://inmates.charlottecountyfl.revize.com' +
                                       (href.startsWith('/') ? href : '/' + href);
                            }
                            urls.add(href);
                        });
                        return Array.from(urls);
                    })()
                """)
            except Exception as e:
                logger.warning(f"[Charlotte] JS eval error on page {current_page}: {e}")
                page_urls = []

            if not page_urls:
                logger.info(f"[Charlotte] No booking links on page {current_page} — end of roster")
                break

            new_count = 0
            for href in page_urls:
                if href not in seen_urls:
                    seen_urls.add(href)
                    booking_id = href.split("/bookings/")[-1].split("?")[0].strip()
                    all_links.append((booking_id, href))
                    new_count += 1

            logger.info(
                f"[Charlotte] Page {current_page}: +{new_count} links (total: {len(all_links)})"
            )

            # Check for next page button
            try:
                next_btn = (
                    page.ele('css:a[rel="next"]') or
                    page.ele('css:.pagination a:contains("Next")') or
                    page.ele('css:a.page-link:contains("Next")') or
                    page.ele('text:Next')
                )
                if not next_btn:
                    logger.info(f"[Charlotte] No 'Next' button — end of roster at page {current_page}")
                    break
                parent_li = next_btn.parent()
                if parent_li and "disabled" in (parent_li.attr("class") or ""):
                    logger.info("[Charlotte] 'Next' button disabled — end of roster")
                    break
            except Exception:
                logger.info(f"[Charlotte] End of roster at page {current_page}")
                break

        return all_links

    # ── Phase 3: Extract detail page ──────────────────────────────────────────
    def _extract_detail(
        self, page, booking_id: str, detail_url: str
    ) -> Optional[ArrestRecord]:
        """Visit a booking detail page and extract all structured data."""
        try:
            page.get(detail_url)
        except Exception as e:
            logger.warning(f"[Charlotte] Detail page load error {booking_id}: {e}")
            return None

        # Wait for Cloudflare
        for _ in range(CF_WAIT_S):
            time.sleep(1)
            title = page.title or ""
            if title and "just a moment" not in title.lower():
                break

        time.sleep(2)

        try:
            raw = page.run_js("""
                (() => {
                    const result = {};

                    // Label/input pairs
                    document.querySelectorAll('label').forEach(label => {
                        const text = label.textContent.trim().replace(/:$/, '');
                        const forId = label.getAttribute('for');
                        let value = null;
                        if (forId) {
                            const input = document.getElementById(forId);
                            if (input) value = input.value || input.textContent;
                        }
                        if (!value) {
                            const next = label.nextElementSibling;
                            if (next) value = next.value || next.textContent;
                        }
                        if (value && value.trim()) result[text] = value.trim();
                    });

                    // Definition lists
                    document.querySelectorAll('dt').forEach(dt => {
                        const dd = dt.nextElementSibling;
                        if (dd && dd.tagName === 'DD') {
                            result[dt.textContent.trim().replace(/:$/, '')] = dd.textContent.trim();
                        }
                    });

                    // Two-column table rows
                    document.querySelectorAll('table tr').forEach(row => {
                        const cells = row.querySelectorAll('td, th');
                        if (cells.length === 2) {
                            const key = cells[0].textContent.trim().replace(/:$/, '');
                            const val = cells[1].textContent.trim();
                            if (key && val) result[key] = val;
                        }
                    });

                    // Charges table
                    const charges = [];
                    document.querySelectorAll('table').forEach(table => {
                        const headers = Array.from(table.querySelectorAll('th'))
                            .map(h => h.textContent.trim());
                        if (headers.some(h =>
                            h.includes('Statute') || h.includes('Charge') ||
                            h.includes('Desc') || h.includes('Bond Amt')
                        )) {
                            table.querySelectorAll('tbody tr').forEach(row => {
                                const cells = row.querySelectorAll('td');
                                if (cells.length >= 3) {
                                    charges.push({
                                        desc:     cells[0] ? cells[0].textContent.trim() : '',
                                        degree:   cells[1] ? cells[1].textContent.trim() : '',
                                        agency:   cells[2] ? cells[2].textContent.trim() : '',
                                        location: cells[3] ? cells[3].textContent.trim() : '',
                                        bond:     cells[4] ? cells[4].textContent.trim() : '',
                                    });
                                }
                            });
                        }
                    });
                    result['__CHARGES'] = charges;

                    // Bookings table — get booking number, book date, release date
                    document.querySelectorAll('table').forEach(table => {
                        const headers = Array.from(table.querySelectorAll('th'))
                            .map(h => h.textContent.trim().toLowerCase());
                        if (headers.some(h => h.includes('book') && (h.includes('#') || h.includes('num')))) {
                            const firstRow = table.querySelector('tbody tr');
                            if (firstRow) {
                                const cells = firstRow.querySelectorAll('td');
                                if (cells.length >= 2) {
                                    result['__BookNum']  = cells[0] ? cells[0].textContent.trim() : '';
                                    result['__Agency']   = cells[1] ? cells[1].textContent.trim() : '';
                                    result['__BookDate'] = cells[2] ? cells[2].textContent.trim() : '';
                                    result['__RelDate']  = cells[3] ? cells[3].textContent.trim() : '';
                                    result['__RelReason']= cells[4] ? cells[4].textContent.trim() : '';
                                }
                            }
                        }
                    });

                    // ICE hold / immigration detainer
                    const bodyText = document.body.textContent;
                    result['__HAS_ICE'] = bodyText.includes('ICE HOLD') ||
                                          bodyText.includes('IMMIGRATION DETAINER');

                    // Mugshot
                    const img = document.querySelector(
                        'img[src*="photo"], img[src*="mugshot"], img[src*="image"], img[src*="booking"]'
                    );
                    if (img && img.src && !img.src.startsWith('data:')) {
                        result['__Mugshot'] = img.src;
                    }

                    result['__Detail_URL'] = window.location.href;
                    return result;
                })()
            """)
        except Exception as e:
            logger.warning(f"[Charlotte] JS extraction error {booking_id}: {e}")
            return None

        if not raw:
            return None

        raw['__Booking_ID'] = booking_id
        raw['__Detail_URL'] = raw.get('__Detail_URL') or detail_url
        return self._convert_to_record(raw)

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
            bond_total += bond_val
            if desc:
                charges_list.append(
                    f"{desc} [{ch.get('degree', '')}]"
                    + (f" Bond: ${bond_val:,.0f}" if bond_val else "")
                )

        charges_str = " | ".join(charges_list) if charges_list else self._clean(
            raw.get("Charge") or raw.get("Charges") or ""
        )

        dob  = self._clean(raw.get("Date of Birth") or raw.get("DOB") or "")
        race = self._clean(raw.get("Race") or "")
        sex  = self._clean(raw.get("Gender") or raw.get("Sex") or "")

        try:
            return ArrestRecord(
                Full_Name=full_name,
                First_Name=first,
                Last_Name=last,
                Booking_Number=booking_number,
                Booking_Date=booking_date,
                Release_Date=release_date if in_custody is False else "",
                Charges=charges_str,
                Bond_Amount=str(bond_total) if bond_total else "",
                DOB=dob,
                Race=race,
                Sex=sex,
                County="Charlotte",
                Mugshot_URL=raw.get("__Mugshot", ""),
                Detail_URL=raw.get("__Detail_URL", ""),
                In_Custody=in_custody,
                Has_ICE_Hold=bool(raw.get("__HAS_ICE", False)),
            )
        except Exception as e:
            logger.warning(
                f"[Charlotte] Record build error: {e} | keys: {list(raw.keys())[:10]}"
            )
            return None

    @staticmethod
    def _clean(val) -> str:
        if not val:
            return ""
        return str(val).strip()

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
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error("[Charlotte] DrissionPage not installed")
            return None

        page = self._setup_browser()
        if not page:
            return None

        try:
            self._load_parent_page(page)
            record = self._extract_detail(page, booking_id, detail_url)
            if record:
                record.LastCheckedMode = "UPDATE"
            return record
        except Exception as e:
            logger.warning(f"[Charlotte] _fetch_single_booking error ({booking_id}): {e}")
            return None
        finally:
            try:
                page.quit()
            except Exception:
                pass
