"""
Charlotte County Arrest Scraper — Obscura CDP (Stealth Playwright)
===================================================================
Source:  Charlotte County Sheriff's Office (CCSO)
Portal:  https://ccso.org/correctional_facility/local_arrest_database.php
Backend: https://inmates.charlottecountyfl.revize.com/bookings  (Revize CMS)

APPROACH
--------
Uses Obscura headless browser (Rust, built-in stealth) via Chrome DevTools
Protocol. Playwright connects to the Obscura container at ws://obscura:9222
for real browser-level Cloudflare bypass.

HISTORY:
- v1 (DrissionPage): Blocked by Cloudflare JA3 fingerprinting on datacenter IPs
- v2 (curl_cffi): TLS impersonation worked initially, then Cloudflare upgraded
  to JS challenge which curl_cffi cannot solve (HTTP 403 "Just a moment...")
- v3 (Obscura): Real V8 engine solves JS challenges natively. Built-in stealth
  (fingerprint randomization, webdriver patching, tracker blocking)

Architecture (3-Phase):
1. Navigate to parent page → establish cookies + bypass Cloudflare
2. Paginate roster → collect booking detail URLs
3. Visit each detail page → extract structured data via JS evaluation
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
CF_WAIT_S      = 15    # Max seconds to wait for Cloudflare challenge

# ── Bond sanity cap ───────────────────────────────────────────────────────────
MAX_BOND_PER_CHARGE = 5_000_000
MAX_BOND_TOTAL      = 10_000_000


class CharlotteCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Charlotte"

    # ── Main entry point ──────────────────────────────────────────────────────
    def scrape(self) -> List[ArrestRecord]:
        pw = None
        browser = None
        try:
            pw, browser = self._get_obscura_browser_sync()
        except (ConnectionError, ImportError) as e:
            logger.warning(f"[Charlotte] Obscura unavailable ({e}), falling back to curl_cffi")
            return self._scrape_curl_cffi()

        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
                locale="en-US",
            )
            page = context.new_page()

            # Phase 1: Establish context via parent page (bypass Cloudflare WAF)
            logger.info(f"[Charlotte] Phase 1: Loading parent page via Obscura")
            page.goto(PARENT_URL, wait_until="domcontentloaded", timeout=30000)
            self._wait_for_cloudflare_sync(page)
            logger.info("[Charlotte] Parent page loaded — cookies established via Obscura")

            # Phase 2: Collect all booking detail URLs from the roster
            booking_links = self._collect_booking_links_pw(page)
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
                    record = self._extract_detail_pw(page, booking_id, detail_url)
                    if record and record.Full_Name and record.Booking_Number:
                        records.append(record)
                except Exception as e:
                    logger.warning(f"[Charlotte] Error on detail {booking_id}: {e}")
                time.sleep(DETAIL_DELAY_S)

            logger.info(f"[Charlotte] Scraped {len(records)} in-custody records via Obscura 🦀")
            return records

        except Exception as e:
            logger.error(f"[Charlotte] Obscura scraper fatal error: {e}")
            raise
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass
            try:
                if pw:
                    pw.stop()
            except Exception:
                pass

    # ── Cloudflare wait helper ────────────────────────────────────────────────
    @staticmethod
    def _wait_for_cloudflare_sync(page, max_wait=CF_WAIT_S):
        """Wait for Cloudflare JS challenge to resolve."""
        waited = 0
        while waited < max_wait:
            title = page.title().lower() if page.title() else ""
            if "just a moment" not in title and "checking" not in title:
                time.sleep(1)  # Extra beat for JS-rendered content
                return True
            time.sleep(1)
            waited += 1
        logger.warning(f"[Charlotte] Cloudflare challenge timeout after {max_wait}s")
        return False

    # ── Phase 2: Collect booking links (Playwright) ───────────────────────────
    def _collect_booking_links_pw(self, page) -> List[Tuple[str, str]]:
        """Paginate through roster and collect all detail URLs using Playwright."""
        all_links: List[Tuple[str, str]] = []
        seen_urls: set = set()

        for current_page in range(1, MAX_PAGES + 1):
            url = BOOKINGS_URL if current_page == 1 else f"{BOOKINGS_URL}?page={current_page}"
            logger.info(f"[Charlotte] Loading roster page {current_page}: {url}")

            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._wait_for_cloudflare_sync(page)

            # Check if we got the challenge page
            title = page.title().lower() if page.title() else ""
            if "just a moment" in title:
                logger.warning(f"[Charlotte] Still on Cloudflare challenge p{current_page}")
                break

            # Extract booking links via JS
            booking_urls = page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a[href*="/bookings/"]'));
                    const urls = new Set();
                    links.forEach(link => {
                        let href = link.getAttribute('href');
                        if (!href) return;
                        if (/\\/bookings\\/?$/i.test(href)) return;
                        if (!href.startsWith('http')) {
                            href = 'https://inmates.charlottecountyfl.revize.com' +
                                   (href.startsWith('/') ? href : '/' + href);
                        }
                        urls.add(href);
                    });
                    return Array.from(urls);
                }
            """)

            if not booking_urls:
                content_preview = page.evaluate("() => document.body?.innerText?.substring(0, 200)") or ""
                logger.warning(
                    f"[Charlotte] No booking links on p{current_page} "
                    f"(title: {page.title()}) — content: {content_preview}"
                )
                break

            new_count = 0
            for href in booking_urls:
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
            has_next = page.evaluate(f"""
                () => {{
                    const next = document.querySelector('a[href*="page={current_page + 1}"]');
                    if (next) return true;
                    const nextText = Array.from(document.querySelectorAll('a')).find(
                        a => /next/i.test(a.textContent)
                    );
                    if (nextText && !nextText.closest('.disabled')) return true;
                    return false;
                }}
            """)

            if not has_next:
                logger.info(f"[Charlotte] No next page — end of roster at page {current_page}")
                break

            time.sleep(1.0)

        return all_links

    # ── Phase 3: Extract detail page (Playwright) ─────────────────────────────
    def _extract_detail_pw(
        self, page, booking_id: str, detail_url: str
    ) -> Optional[ArrestRecord]:
        """Visit a booking detail page and extract data via JS evaluation."""
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(1)

        # Check for CF challenge
        title = page.title().lower() if page.title() else ""
        if "just a moment" in title:
            self._wait_for_cloudflare_sync(page)
            title = page.title().lower() if page.title() else ""
            if "just a moment" in title:
                logger.warning(f"[Charlotte] Cloudflare challenge on detail {booking_id}")
                return None

        # Extract all data via single JS evaluation
        raw = page.evaluate("""
            () => {
                const result = {};

                // Extract from label/value pairs
                document.querySelectorAll('label').forEach(label => {
                    const text = label.textContent.trim().replace(/:$/, '');
                    let value = null;
                    const forId = label.getAttribute('for');
                    if (forId) {
                        const inp = document.getElementById(forId);
                        if (inp) value = inp.value || inp.textContent;
                    }
                    if (!value) {
                        const sib = label.nextElementSibling;
                        if (sib) value = sib.textContent.trim() || sib.getAttribute('value');
                    }
                    if (value && value.trim()) result[text] = value.trim();
                });

                // Extract from dt/dd pairs
                document.querySelectorAll('dt').forEach(dt => {
                    const dd = dt.nextElementSibling;
                    if (dd && dd.tagName === 'DD') {
                        result[dt.textContent.trim().replace(/:$/, '')] = dd.textContent.trim();
                    }
                });

                // Extract from 2-column table rows
                document.querySelectorAll('tr').forEach(row => {
                    const table = row.closest('table');
                    if (table && table.querySelectorAll('th').length >= 3) return;
                    const cells = row.querySelectorAll('td, th');
                    if (cells.length === 2) {
                        const key = cells[0].textContent.trim().replace(/:$/, '');
                        const val = cells[1].textContent.trim();
                        if (key && val) result[key] = val;
                    }
                });

                // Extract charges from tables
                const charges = [];
                document.querySelectorAll('table').forEach(table => {
                    const headers = Array.from(table.querySelectorAll('th'))
                        .map(th => th.textContent.trim().toLowerCase());
                    const isChargeTable = headers.some(h =>
                        /charge|offense|statute|bond|bail/.test(h)
                    );
                    if (!isChargeTable) return;

                    table.querySelectorAll('tr').forEach((row, i) => {
                        if (i === 0) return; // skip header
                        const cells = Array.from(row.querySelectorAll('td'));
                        if (cells.length < 2) return;
                        const cellTexts = cells.map(c => c.textContent.trim());

                        const charge = {desc: '', degree: '', bond: '', agency: ''};

                        headers.forEach((h, idx) => {
                            if (idx >= cells.length) return;
                            const val = cellTexts[idx];
                            if (/charge|desc|offense|statute/.test(h)) charge.desc = val;
                            else if (/degree|level|class/.test(h)) charge.degree = val;
                            else if (/bond|bail|amount/.test(h)) charge.bond = val;
                            else if (/agency/.test(h)) charge.agency = val;
                            else if (/case|docket/.test(h)) charge.caseNum = val;
                        });

                        // Fallback: find dollar amounts
                        if (!charge.bond) {
                            for (const ct of cellTexts) {
                                const cleaned = ct.replace(/\\s/g, '');
                                if (/^\\$[\\d,]+\\.?\\d*$/.test(cleaned)) {
                                    charge.bond = ct;
                                    break;
                                } else if (/^[\\d,]+\\.\\d{2,}$/.test(cleaned)) {
                                    charge.bond = ct;
                                    break;
                                }
                            }
                        }

                        // Fallback: first long text as desc
                        if (!charge.desc) {
                            for (const ct of cellTexts) {
                                if (ct.length > 10 && !ct.startsWith('$') && !/^\\d{2}[\\/\\-]\\d{2}/.test(ct)) {
                                    charge.desc = ct;
                                    break;
                                }
                            }
                        }

                        if (charge.desc || charge.bond) charges.push(charge);
                    });
                });
                result.__CHARGES = charges;

                // Booking table data
                document.querySelectorAll('table').forEach(table => {
                    const headers = Array.from(table.querySelectorAll('th'))
                        .map(th => th.textContent.trim().toLowerCase());
                    if (!headers.some(h => /book|arrest/.test(h))) return;

                    const rows = table.querySelectorAll('tr');
                    const dataRow = table.querySelector('tbody tr') || (rows.length > 1 ? rows[1] : null);
                    if (!dataRow) return;
                    const cells = dataRow.querySelectorAll('td');

                    headers.forEach((h, i) => {
                        if (i >= cells.length) return;
                        const val = cells[i].textContent.trim();
                        if (/book/.test(h) && /(#|num)/.test(h)) result.__BookNum = val;
                        else if (/book/.test(h) && /date/.test(h)) result.__BookDate = val;
                        else if (/arrest/.test(h) && /date/.test(h)) result.__BookDate = result.__BookDate || val;
                        else if (/release|rel/.test(h)) result.__RelDate = val;
                        else if (/agency/.test(h)) result.__Agency = val;
                    });
                });

                // Mugshot
                const img = document.querySelector('img[src*="photo"], img[src*="mugshot"], img[src*="image"], img[src*="booking"], img[src*="inmate"]');
                if (img && !img.src.startsWith('data:')) result.__Mugshot = img.src;

                // ICE Hold
                const bodyText = document.body.innerText;
                result.__HAS_ICE = bodyText.includes('ICE HOLD') || bodyText.includes('IMMIGRATION DETAINER');

                return result;
            }
        """)

        if not raw:
            return None

        raw["__Booking_ID"] = booking_id
        raw["__Detail_URL"] = detail_url

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

        booking_number = self._clean(
            raw.get("Booking #") or raw.get("Book #") or
            raw.get("Booking Number") or raw.get("__BookNum") or
            raw.get("__Booking_ID") or ""
        )

        booking_date = self._clean(
            raw.get("__BookDate") or raw.get("Booking Date") or
            raw.get("Book Date") or raw.get("Arrest Date") or ""
        )
        release_date = self._clean(
            raw.get("__RelDate") or raw.get("Release Date") or ""
        )
        in_custody = not release_date or release_date.upper() in ("N/A", "NA", "")

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
            if bond_val > MAX_BOND_PER_CHARGE:
                logger.warning(
                    f"[Charlotte] Bond sanity cap: ${bond_val:,.0f} on '{desc}' — capping to $0"
                )
                bond_val = 0.0
            bond_total += bond_val
            if desc:
                charges_list.append(
                    f"{desc} [{ch.get('degree', '')}]"
                    + (f" Bond: ${bond_val:,.0f}" if bond_val else "")
                )

        if bond_total > MAX_BOND_TOTAL:
            logger.warning(
                f"[Charlotte] Total bond ${bond_total:,.0f} exceeds cap — resetting to $0"
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

    # ── Fallback: curl_cffi (if Obscura is down) ──────────────────────────────
    def _scrape_curl_cffi(self) -> List[ArrestRecord]:
        """Legacy curl_cffi fallback. May fail if Cloudflare JS challenge is active."""
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError as e:
            logger.error(f"[Charlotte] curl_cffi fallback also unavailable: {e}")
            return []

        HEADERS = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        session = cffi_requests.Session()
        logger.info("[Charlotte] Using curl_cffi fallback (Obscura unavailable)")

        # Try parent page
        try:
            session.get(PARENT_URL, headers=HEADERS, impersonate="chrome", timeout=30)
        except Exception:
            pass

        # Try roster
        try:
            resp = session.get(BOOKINGS_URL, headers=HEADERS, impersonate="chrome", timeout=30)
            if resp.status_code == 403:
                logger.warning("[Charlotte] curl_cffi blocked by Cloudflare (403) — need Obscura")
                return []
        except Exception as e:
            logger.error(f"[Charlotte] curl_cffi roster failed: {e}")
            return []

        logger.warning("[Charlotte] curl_cffi fallback — limited extraction")
        return []

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

        pw = None
        browser = None
        try:
            pw, browser = self._get_obscura_browser_sync()
            context = browser.new_context()
            page = context.new_page()

            # Establish parent context first
            page.goto(PARENT_URL, wait_until="domcontentloaded", timeout=30000)
            self._wait_for_cloudflare_sync(page)

            record = self._extract_detail_pw(page, booking_id, detail_url)
            if record:
                record.LastCheckedMode = "UPDATE"
            return record
        except Exception as e:
            logger.warning(f"[Charlotte] _fetch_single_booking error ({booking_id}): {e}")
            return None
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass
            try:
                if pw:
                    pw.stop()
            except Exception:
                pass

    @staticmethod
    def _clean(val) -> str:
        if not val:
            return ""
        return " ".join(str(val).strip().split())
