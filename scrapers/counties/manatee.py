"""
Manatee County Arrest Scraper — Obscura CDP (Stealth Playwright)
=================================================================
Source: Manatee County Sheriff's Office via Revize-hosted bookings page
URL: https://manatee-sheriff.revize.com/bookings
Method: Obscura headless browser (Playwright CDP) — built-in Cloudflare bypass

HISTORY:
- v1 (DrissionPage): Chromium headless → Cloudflare challenge timeout every run
  (25s wait then failure). DrissionPage can't solve modern JS challenges from
  datacenter IPs because Cloudflare detects Chromium automation via TLS/JA3.
- v2 (Obscura): Rust browser engine with native TLS spoofing, fingerprint
  randomization, and V8 JS engine. Solves Cloudflare challenges natively.
"""

import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://manatee-sheriff.revize.com"
BOOKINGS_URL = f"{BASE_URL}/bookings"
DAYS_BACK = 90  # Extended: capture all in-custody inmates
MAX_PAGES = 10
DETAIL_DELAY_S = 1.5
CF_WAIT_S = 20  # Max seconds to wait for Cloudflare challenge


class ManateeCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Manatee"

    def scrape(self) -> List[ArrestRecord]:
        pw = None
        browser = None
        try:
            pw, browser = self._get_obscura_browser_sync()
        except (ConnectionError, ImportError) as e:
            logger.warning(f"[Manatee] Obscura unavailable ({e}), falling back to DrissionPage")
            return self._scrape_drissionpage()

        cutoff_date = datetime.now() - timedelta(days=DAYS_BACK)

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

            booking_links = self._collect_booking_links_pw(page)
            if not booking_links:
                logger.warning("[Manatee] No booking links found on roster")
                return []

            logger.info(f"[Manatee] Collected {len(booking_links)} booking links via Obscura")
            records = []

            for idx, (booking_id, detail_url) in enumerate(booking_links, 1):
                if idx % 10 == 0:
                    logger.info(f"[Manatee] Progress: {idx}/{len(booking_links)} ({len(records)} records)")

                try:
                    record = self._extract_detail_pw(page, booking_id, detail_url)
                    if not record:
                        continue

                    if record.Booking_Date:
                        try:
                            for fmt in ["%m/%d/%Y", "%Y-%m-%d"]:
                                try:
                                    book_dt = datetime.strptime(record.Booking_Date.split()[0], fmt)
                                    if book_dt < cutoff_date:
                                        logger.info(f"[Manatee] Past cutoff ({record.Booking_Date}), stopping. Got {len(records)} records.")
                                        return records
                                    break
                                except ValueError:
                                    continue
                        except Exception:
                            pass

                    if record.Full_Name and record.Booking_Number:
                        records.append(record)
                except Exception as e:
                    logger.warning(f"[Manatee] Error on detail {booking_id}: {e}")

                time.sleep(DETAIL_DELAY_S)

            logger.info(f"[Manatee] Scraped {len(records)} records via Obscura 🦀")
            return records

        except Exception as e:
            logger.error(f"[Manatee] Obscura scraper fatal error: {e}")
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
    def _wait_for_cloudflare_pw(page, max_wait=CF_WAIT_S):
        """Wait for Cloudflare JS challenge to resolve in Playwright page."""
        waited = 0
        while waited < max_wait:
            title = page.title().lower() if page.title() else ""
            if "just a moment" not in title and "checking" not in title:
                time.sleep(2)  # Extra wait for JS-rendered content to load
                return True
            time.sleep(1)
            waited += 1
        logger.warning(f"[Manatee] Cloudflare challenge timeout after {max_wait}s")
        return False

    # ── Collect booking links (Playwright) ────────────────────────────────────
    def _collect_booking_links_pw(self, page) -> List[Tuple[str, str]]:
        all_links = []
        current_page = 1

        while current_page <= MAX_PAGES:
            url = BOOKINGS_URL if current_page == 1 else f"{BOOKINGS_URL}?page={current_page}"
            logger.info(f"[Manatee] Loading listing page {current_page}: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            if not self._wait_for_cloudflare_pw(page):
                break

            booking_urls = page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a[href*="/bookings/"]'));
                    const urls = new Set();
                    links.forEach(link => {
                        let href = link.getAttribute('href');
                        if (!href) return;
                        if (/\\/bookings\\/?$/i.test(href)) return;
                        if (!href.startsWith('http')) {
                            href = 'https://manatee-sheriff.revize.com' +
                                   (href.startsWith('/') ? href : '/' + href);
                        }
                        urls.add(href);
                    });
                    return Array.from(urls);
                }
            """)

            if not booking_urls:
                try:
                    page_text = page.evaluate("() => document.body?.innerText?.substring(0, 500)") or ""
                    all_links_count = page.evaluate('() => document.querySelectorAll("a").length') or 0
                    logger.warning(
                        f"[Manatee] No booking links on p{current_page} "
                        f"(total <a> tags: {all_links_count}, "
                        f"title: {page.title()}) — content: {page_text[:200]}"
                    )
                except Exception:
                    logger.warning(f"[Manatee] No booking links on p{current_page}")
                break

            valid_links = []
            for href in booking_urls:
                booking_id = href.split("/bookings/")[-1].split("?")[0].strip() if "/bookings/" in href else ""
                if booking_id:
                    valid_links.append((booking_id, href))

            if not valid_links:
                break

            all_links.extend(valid_links)

            # Check for next page
            has_next = page.evaluate("""
                () => {
                    const next = document.querySelector('a[rel="next"]');
                    if (next) return true;
                    const nextText = Array.from(document.querySelectorAll('a')).find(
                        a => /next/i.test(a.textContent)
                    );
                    if (nextText) return true;
                    const paginationNext = document.querySelector('.pagination .next a');
                    if (paginationNext) return true;
                    return false;
                }
            """)

            if not has_next:
                break

            current_page += 1
            time.sleep(1)

        # Deduplicate
        seen = set()
        unique = []
        for bid, url in all_links:
            if url not in seen:
                seen.add(url)
                unique.append((bid, url))
        return unique

    # ── Extract detail page (Playwright) ──────────────────────────────────────
    def _extract_detail_pw(self, page, booking_id, detail_url) -> Optional[ArrestRecord]:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        if not self._wait_for_cloudflare_pw(page):
            return None

        js_data = page.evaluate("""
            () => {
                const result = {};
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

                const charges = [];
                const tables = document.querySelectorAll('table');
                tables.forEach(table => {
                    const headers = Array.from(table.querySelectorAll('th'))
                        .map(th => th.textContent.trim());
                    if (headers.some(h => h.includes('Statute') || h.includes('Desc'))) {
                        const rows = table.querySelectorAll('tbody tr');
                        rows.forEach(row => {
                            const cells = row.querySelectorAll('td');
                            if (cells.length >= 6) {
                                charges.push({
                                    arrest_date: cells[0].textContent.trim(),
                                    statute: cells[1].textContent.trim(),
                                    desc: cells[2].textContent.trim(),
                                    sec_desc: cells[3].textContent.trim(),
                                    obts: cells[4].textContent.trim(),
                                    bond_amt: cells[5].textContent.trim()
                                });
                            }
                        });
                    }
                });
                result['__CHARGES'] = charges;

                const img = document.querySelector('img[src*="photo"], img[src*="mugshot"], img[src*="image"]');
                if (img && !img.src.startsWith('data:')) result['__Mugshot'] = img.src;
                return result;
            }
        """)

        if not js_data:
            return None

        first_name = self._clean(js_data.get("First Name", ""))
        last_name = self._clean(js_data.get("Last Name", ""))
        middle_name = self._clean(js_data.get("Middle Name", ""))
        full_name = ""
        if last_name and first_name:
            full_name = f"{last_name}, {first_name}"
            if middle_name:
                full_name += f" {middle_name}"

        charges_list = []
        total_bond = 0.0
        booking_date = ""
        seen_charges = set()

        for entry in js_data.get("__CHARGES", []):
            desc = self._clean(entry.get("desc", ""))
            statute = self._clean(entry.get("statute", ""))
            obts = self._clean(entry.get("obts", ""))
            bond_str = self._clean(entry.get("bond_amt", "0"))
            arr_date = self._clean(entry.get("arrest_date", ""))

            dedup_key = f"{obts}:{statute}:{desc}" if obts else f"{statute}:{desc}:{bond_str}"
            if dedup_key in seen_charges:
                continue
            seen_charges.add(dedup_key)

            if not booking_date and arr_date:
                booking_date = arr_date

            charge_text = f"{statute} - {desc}" if statute else desc
            if charge_text:
                charges_list.append(charge_text)

            try:
                total_bond += float(bond_str.replace("$", "").replace(",", ""))
            except (ValueError, TypeError):
                pass

        if not booking_date:
            booking_date = self._clean(js_data.get("Book Date", js_data.get("Arrest Date", "")))

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_id,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB=self._clean(js_data.get("Date of Birth", "")),
            Booking_Date=booking_date,
            Status="In Custody",
            Release_Date="",
            Facility="Manatee County Jail",
            Race=self._clean(js_data.get("Race", "")),
            Sex=self._clean(js_data.get("Gender", js_data.get("Sex", ""))),
            Height=self._clean(js_data.get("Height", "")),
            Weight=self._clean(js_data.get("Weight", "")),
            Address="",
            City="",
            State="FL",
            ZIP="",
            Mugshot_URL=js_data.get("__Mugshot", ""),
            Charges=" | ".join(charges_list) if charges_list else "",
            Bond_Amount=str(total_bond) if total_bond > 0 else "0",
            Bond_Paid="NO",
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        )

    # ── Fallback: DrissionPage (if Obscura is down) ───────────────────────────
    def _scrape_drissionpage(self) -> List[ArrestRecord]:
        """Legacy DrissionPage fallback — usually blocked by Cloudflare."""
        try:
            from DrissionPage import ChromiumPage
        except ImportError:
            logger.error("[Manatee] DrissionPage not installed")
            return []

        logger.info("[Manatee] Using DrissionPage fallback (likely to be CF-blocked)")
        co = self._get_browser_options()
        page = ChromiumPage(addr_or_opts=co)
        self._inject_stealth_js(page)

        try:
            page.get(BOOKINGS_URL)
            time.sleep(5)

            # Check for Cloudflare
            title = page.title.lower() if page.title else ""
            if "just a moment" in title:
                logger.warning("[Manatee] DrissionPage blocked by Cloudflare — need Obscura")
                return []

            logger.warning("[Manatee] DrissionPage fallback — limited extraction")
            return []
        finally:
            try:
                page.quit()
            except Exception:
                pass

    # ── FirstAppearanceWatcher hook ───────────────────────────────────────────
    def _fetch_single_booking(
        self, booking_id: str, detail_url: str
    ) -> "Optional[ArrestRecord]":
        if not booking_id and not detail_url:
            return None
        if not detail_url:
            detail_url = f"{BASE_URL}/bookings/{booking_id}"

        pw = None
        browser = None
        try:
            pw, browser = self._get_obscura_browser_sync()
            context = browser.new_context()
            page = context.new_page()
            self._wait_for_cloudflare_pw(page)
            record = self._extract_detail_pw(page, booking_id, detail_url)
            if record:
                record.LastCheckedMode = "UPDATE"
            return record
        except Exception as e:
            logger.warning(f"[Manatee] _fetch_single_booking error ({booking_id}): {e}")
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
    def _clean(text):
        if not text:
            return ""
        return " ".join(str(text).strip().split())
