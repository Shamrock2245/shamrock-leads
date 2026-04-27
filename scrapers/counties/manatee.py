"""
Manatee County Arrest Scraper — Revize CMS with DrissionPage.

Source: Manatee County Sheriff's Office via Revize-hosted bookings page
URL: https://manatee-sheriff.revize.com/bookings
Method: DrissionPage browser automation (Chromium headless)

Ported from: swfl-arrest-scrapers/python_scrapers/scrapers/manatee_solver_v2.py
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
DAYS_BACK = 21
MAX_PAGES = 10
DETAIL_DELAY_S = 1.5


class ManateeCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Manatee"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error("DrissionPage not installed.")
            return []

        page = self._setup_browser()
        cutoff_date = datetime.now() - timedelta(days=DAYS_BACK)

        try:
            booking_links = self._collect_booking_links(page)
            if not booking_links:
                logger.warning("No booking links found on Manatee roster")
                return []

            logger.info(f"Collected {len(booking_links)} booking links")
            records = []

            for idx, (booking_id, detail_url) in enumerate(booking_links, 1):
                if idx % 10 == 0:
                    logger.info(f"Progress: {idx}/{len(booking_links)} ({len(records)} records)")

                try:
                    record = self._extract_detail(page, booking_id, detail_url)
                    if not record:
                        continue

                    if record.Booking_Date:
                        try:
                            for fmt in ["%m/%d/%Y", "%Y-%m-%d"]:
                                try:
                                    book_dt = datetime.strptime(record.Booking_Date.split()[0], fmt)
                                    if book_dt < cutoff_date:
                                        logger.info(f"Past cutoff ({record.Booking_Date}), stopping. Got {len(records)} records.")
                                        return records
                                    break
                                except ValueError:
                                    continue
                        except Exception:
                            pass

                    if record.Full_Name and record.Booking_Number:
                        records.append(record)
                except Exception as e:
                    logger.warning(f"Error on detail {booking_id}: {e}")

                time.sleep(DETAIL_DELAY_S)

            logger.info(f"Scraped {len(records)} records from Manatee")
            return records

        except Exception as e:
            logger.error(f"Manatee scraper fatal error: {e}")
            return []
        finally:
            try:
                page.quit()
            except Exception:
                pass

    def _setup_browser(self):
        from DrissionPage import ChromiumPage
        co = self._get_browser_options()
        return ChromiumPage(addr_or_opts=co)

    @staticmethod
    def _wait_for_cloudflare(page, max_wait=20):
        waited = 0
        while waited < max_wait:
            title = page.title.lower() if page.title else ""
            if "just a moment" not in title:
                return True
            time.sleep(1)
            waited += 1
        return False

    def _collect_booking_links(self, page):
        all_links = []
        current_page = 1

        while current_page <= MAX_PAGES:
            url = BOOKINGS_URL if current_page == 1 else f"{BOOKINGS_URL}?page={current_page}"
            logger.info(f"Loading listing page {current_page}: {url}")
            page.get(url)
            time.sleep(5)

            if not self._wait_for_cloudflare(page):
                break

            booking_urls = page.run_js("""
                const links = Array.from(document.querySelectorAll('a[href*="/bookings/"]'));
                const urls = new Set();
                links.forEach(link => {
                    let href = link.getAttribute('href');
                    if (!href) return;
                    if (/\\/bookings\\/?$/i.test(href)) return;
                    if (!href.startsWith('http')) {
                        href = 'https://manatee-sheriff.revize.com' + (href.startsWith('/') ? href : '/' + href);
                    }
                    urls.add(href);
                });
                return Array.from(urls);
            """)

            if not booking_urls:
                break

            valid_links = []
            for href in booking_urls:
                booking_id = href.split("/bookings/")[-1].split("?")[0].strip() if "/bookings/" in href else ""
                if booking_id:
                    valid_links.append((booking_id, href))

            if not valid_links:
                break

            all_links.extend(valid_links)

            next_btn = page.ele('css:a[rel="next"]') or page.ele("text:Next") or page.ele("css:.pagination .next a")
            if not next_btn:
                break

            current_page += 1
            time.sleep(1)

        seen = set()
        unique = []
        for bid, url in all_links:
            if url not in seen:
                seen.add(url)
                unique.append((bid, url))
        return unique

    def _extract_detail(self, page, booking_id, detail_url):
        page.get(detail_url)
        time.sleep(2)

        if not self._wait_for_cloudflare(page):
            return None

        js_data = page.run_js("""
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
                const headers = Array.from(table.querySelectorAll('th')).map(th => th.textContent.trim());
                if (headers.some(h => h.includes('Statute') || h.includes('Desc'))) {
                    const rows = table.querySelectorAll('tbody tr, tr');
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

        for entry in js_data.get("__CHARGES", []):
            desc = self._clean(entry.get("desc", ""))
            statute = self._clean(entry.get("statute", ""))
            bond_str = self._clean(entry.get("bond_amt", "0"))
            arr_date = self._clean(entry.get("arrest_date", ""))

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

    @staticmethod
    def _clean(text):
        if not text:
            return ""
        return " ".join(str(text).strip().split())
