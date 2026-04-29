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

        # ── Check if detail page has sub-arrest links to click ──
        # Some Revize booking pages show a person profile with a list of arrests;
        # bond/charge data only appears after clicking into a specific arrest.
        try:
            arrest_link = page.run_js("""
                (() => {
                    // Look for an arrests/bookings sub-table with clickable rows
                    const tables = document.querySelectorAll('table');
                    for (const table of tables) {
                        const headers = Array.from(table.querySelectorAll('th'))
                            .map(h => h.textContent.trim().toLowerCase());
                        // Detect arrest history table (usually has "arrest date", "book date", etc.)
                        const isArrestTable = headers.some(h =>
                            /arrest|book.*date|booking|charge/.test(h)
                        );
                        if (isArrestTable) {
                            // Click the FIRST (most recent) row link
                            const firstLink = table.querySelector('tbody tr a, tbody tr td a, tr:nth-child(2) a');
                            if (firstLink && firstLink.href) {
                                return firstLink.href;
                            }
                        }
                    }
                    // Also check for any prominent arrest detail links
                    const arrestLinks = document.querySelectorAll('a[href*="arrest"], a[href*="booking"]');
                    for (const a of arrestLinks) {
                        if (a.href && a.href !== window.location.href && !a.href.includes('#')) {
                            return a.href;
                        }
                    }
                    return null;
                })()
            """)

            if arrest_link:
                logger.info(f"[Charlotte] Found sub-arrest link for {booking_id}: {arrest_link}")
                page.get(arrest_link)
                # Wait for sub-page
                for _ in range(CF_WAIT_S):
                    time.sleep(1)
                    title = page.title or ""
                    if title and "just a moment" not in title.lower():
                        break
                time.sleep(2)
        except Exception as e:
            logger.debug(f"[Charlotte] No sub-arrest link for {booking_id}: {e}")

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

                    // Definition lists (Revize CMS often uses <dl>/<dt>/<dd>)
                    document.querySelectorAll('dt').forEach(dt => {
                        const dd = dt.nextElementSibling;
                        if (dd && dd.tagName === 'DD') {
                            result[dt.textContent.trim().replace(/:$/, '')] = dd.textContent.trim();
                        }
                    });

                    // Two-column table rows (key-value pairs)
                    document.querySelectorAll('table tr').forEach(row => {
                        const cells = row.querySelectorAll('td, th');
                        if (cells.length === 2) {
                            const key = cells[0].textContent.trim().replace(/:$/, '');
                            const val = cells[1].textContent.trim();
                            if (key && val) result[key] = val;
                        }
                    });

                    // ── Universal table scanner ──
                    // Scan ALL tables and capture headers + data for diagnostics
                    const charges = [];
                    const allTableInfo = [];
                    document.querySelectorAll('table').forEach((table, tIdx) => {
                        const headers = Array.from(table.querySelectorAll('th'))
                            .map(h => h.textContent.trim());
                        const rows = table.querySelectorAll('tbody tr, tr');
                        const tableData = {idx: tIdx, headers: headers, rowCount: rows.length, rows: []};

                        rows.forEach((row, rIdx) => {
                            const cells = row.querySelectorAll('td');
                            if (cells.length < 2) return;
                            const cellTexts = Array.from(cells).map(c => c.textContent.trim());
                            if (rIdx < 5) tableData.rows.push(cellTexts);

                            // Detect charge/bond table by content patterns
                            const rowText = cellTexts.join(' ');
                            const hasDollar = /\$[\d,]+/.test(rowText);
                            const hasStatute = /\d{3}\.\d+/.test(rowText);
                            const hasCharge = headers.some(h =>
                                /charge|offense|statute|desc|bond|bail/i.test(h)
                            ) || hasStatute;

                            if (hasCharge && cells.length >= 2) {
                                // Try to extract charge info from this row
                                const charge = {desc: '', degree: '', agency: '', location: '', bond: ''};

                                // Map cells to fields based on header text
                                headers.forEach((h, i) => {
                                    if (i >= cells.length) return;
                                    const cellVal = cells[i].textContent.trim();
                                    const hLow = h.toLowerCase();
                                    if (/charge|desc|offense|statute/.test(hLow)) charge.desc = cellVal;
                                    else if (/degree|level|class/.test(hLow)) charge.degree = cellVal;
                                    else if (/bond|bail|amount/.test(hLow)) charge.bond = cellVal;
                                    else if (/agency|arr.*agency/.test(hLow)) charge.agency = cellVal;
                                    else if (/case|docket/.test(hLow)) charge.caseNum = cellVal;
                                });

                                // Fallback: if no header match, scan cells for $ amounts
                                if (!charge.bond) {
                                    for (let ci = 0; ci < cells.length; ci++) {
                                        const ct = cells[ci].textContent.trim();
                                        if (/^\$?[\d,]+(\.\d{2})?$/.test(ct.replace(/\s/g, '')) && parseFloat(ct.replace(/[$,]/g, '')) > 0) {
                                            charge.bond = ct;
                                            break;
                                        }
                                    }
                                }

                                // Fallback: if no desc from header, use first long text cell
                                if (!charge.desc) {
                                    for (let ci = 0; ci < cells.length; ci++) {
                                        const ct = cells[ci].textContent.trim();
                                        if (ct.length > 10 && !/^\$/.test(ct) && !/^\d{2}[/-]\d{2}/.test(ct)) {
                                            charge.desc = ct;
                                            break;
                                        }
                                    }
                                }

                                if (charge.desc || charge.bond) {
                                    charges.push(charge);
                                }
                            }
                        });

                        allTableInfo.push(tableData);
                    });
                    result['__CHARGES'] = charges;
                    result['__TABLE_DIAG'] = allTableInfo;

                    // ── Scan entire page text for bond amounts (fallback) ──
                    const bodyText = document.body.textContent || '';
                    const bondPatterns = bodyText.match(/(?:bond|bail|total)[^$]*\$[\d,]+(?:\.\d{2})?/gi) || [];
                    if (bondPatterns.length > 0) {
                        result['__BondTextMatches'] = bondPatterns.map(m => m.substring(0, 80));
                    }

                    // Scan for any dollar amounts on the page
                    const dollarMatches = bodyText.match(/\$[\d,]+(?:\.\d{2})?/g) || [];
                    if (dollarMatches.length > 0) {
                        result['__AllDollarAmounts'] = dollarMatches;
                    }

                    // ── Bookings table — booking number, book date, release date ──
                    document.querySelectorAll('table').forEach(table => {
                        const headers = Array.from(table.querySelectorAll('th'))
                            .map(h => h.textContent.trim().toLowerCase());
                        // Match booking table by various header patterns
                        if (headers.some(h => (h.includes('book') && (h.includes('#') || h.includes('num') || h.includes('date'))) || h.includes('arrest date'))) {
                            const rows = table.querySelectorAll('tbody tr');
                            // Take the FIRST (most recent) row
                            const firstRow = rows[0] || table.querySelector('tr:nth-child(2)');
                            if (firstRow) {
                                const cells = firstRow.querySelectorAll('td');
                                if (cells.length >= 2) {
                                    // Map by headers if available
                                    headers.forEach((h, i) => {
                                        if (i >= cells.length) return;
                                        const val = cells[i].textContent.trim();
                                        if (/book.*#|book.*num/.test(h)) result['__BookNum'] = val;
                                        else if (/agency/.test(h)) result['__Agency'] = val;
                                        else if (/book.*date|arrest.*date/.test(h)) result['__BookDate'] = val;
                                        else if (/release|rel.*date/.test(h)) result['__RelDate'] = val;
                                        else if (/reason/.test(h)) result['__RelReason'] = val;
                                    });
                                    // Fallback positional if no header match
                                    if (!result['__BookNum'] && cells[0]) result['__BookNum'] = cells[0].textContent.trim();
                                    if (!result['__BookDate'] && cells[2]) result['__BookDate'] = cells[2].textContent.trim();
                                }
                            }
                        }
                    });

                    // ICE hold / immigration detainer
                    result['__HAS_ICE'] = bodyText.includes('ICE HOLD') ||
                                          bodyText.includes('IMMIGRATION DETAINER');

                    // Mugshot — try multiple image selectors
                    const img = document.querySelector(
                        'img[src*="photo"], img[src*="mugshot"], img[src*="image"], img[src*="booking"], img[src*="inmate"], .inmate-photo img, .booking-photo img'
                    );
                    if (img && img.src && !img.src.startsWith('data:')) {
                        result['__Mugshot'] = img.src;
                    }

                    result['__Detail_URL'] = window.location.href;
                    result['__PageTitle'] = document.title || '';
                    return result;
                })()
            """)
        except Exception as e:
            logger.warning(f"[Charlotte] JS extraction error {booking_id}: {e}")
            return None

        if not raw:
            return None

        # ── Diagnostic logging for debugging bond extraction ──
        page_title = raw.get('__PageTitle', '')
        table_diag = raw.get('__TABLE_DIAG', [])
        dollar_amounts = raw.get('__AllDollarAmounts', [])
        bond_matches = raw.get('__BondTextMatches', [])
        charges_found = raw.get('__CHARGES', [])

        logger.info(f"[Charlotte] Detail page '{page_title}' for {booking_id}: "
                     f"{len(table_diag)} tables, {len(charges_found)} charges, "
                     f"{len(dollar_amounts)} $ amounts on page")

        if table_diag:
            for t in table_diag[:5]:  # Log first 5 tables
                logger.debug(f"[Charlotte]   Table {t.get('idx')}: "
                             f"headers={t.get('headers', [])}, "
                             f"rows={t.get('rowCount', 0)}, "
                             f"sample={t.get('rows', [])[:2]}")

        if dollar_amounts:
            logger.info(f"[Charlotte]   Dollar amounts found: {dollar_amounts[:10]}")
        if bond_matches:
            logger.info(f"[Charlotte]   Bond text matches: {bond_matches[:5]}")
        if not charges_found:
            logger.warning(f"[Charlotte]   ⚠ No charges extracted for {booking_id}")

        # Clean up diagnostic keys before passing to record converter
        for diag_key in ('__TABLE_DIAG', '__AllDollarAmounts', '__BondTextMatches', '__PageTitle'):
            raw.pop(diag_key, None)

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
