"""
Manatee County Arrest Scraper — Revize CMS Roster via residential proxy
========================================================================
Source: Manatee County Sheriff's Office
URL: https://manatee-sheriff.revize.com/bookings
Method: Patchright/Playwright + APE residential (Warren) with office SOCKS fallback

Extracts data directly from the roster table — detail pages are blocked
by Cloudflare. Requires true US residential egress (see scrapers/cf_browser.py).

HISTORY:
- v1–v3: Various CF bypass attempts (DrissionPage, Obscura, JailTracker)
- v4: Roster table extraction via office SOCKS tunnel
- v5: APE-first residential proxy + SOCKS fallback
- v6 (current): Exit-IP preflight + Patchright + sticky Warren session
"""
import logging
import time
from datetime import datetime
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://manatee-sheriff.revize.com"
BOOKINGS_URL = f"{BASE_URL}/bookings"
MAX_PAGES = 20


class ManateeCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Manatee"

    def scrape(self) -> List[ArrestRecord]:
        from scrapers.socks_proxy import resolve_residential_proxy
        from scrapers.cf_browser import (
            launch_cf_browser,
            new_stealth_context,
            wait_past_cloudflare,
        )

        proxy_url, proxy_source = resolve_residential_proxy(
            self, sticky_session="fl-manatee"
        )
        logger.info("[Manatee] proxy source=%s", proxy_source)

        pw = browser = None
        t0 = time.time()
        try:
            pw, browser, engine = launch_cf_browser(
                proxy_url,
                label="Manatee",
                verify_residential=(proxy_source != "direct"),
            )
            context = new_stealth_context(browser)
            page = context.new_page()

            records = []
            seen_bookings = set()

            for pg in range(1, MAX_PAGES + 1):
                url = BOOKINGS_URL if pg == 1 else f"{BOOKINGS_URL}?page={pg}"
                logger.info(f"[Manatee] Roster page {pg} (engine={engine})")

                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if not wait_past_cloudflare(page, label=f"Manatee page {pg}", max_wait=45):
                    if proxy_source == "ape" and not records:
                        self.record_proxy_failure(proxy_url)
                    break

                rows = page.evaluate("""() => {
                    const table = document.querySelector('table');
                    if (!table) return [];
                    const tbody = table.querySelector('tbody');
                    if (!tbody) return [];
                    return Array.from(tbody.querySelectorAll('tr')).map(r => {
                        const cells = Array.from(r.querySelectorAll('td'));
                        return cells.map(c => c.textContent.trim());
                    });
                }""")

                if not rows:
                    logger.info(f"[Manatee] No rows on page {pg} — end of roster")
                    break

                new_count = 0
                for row in rows:
                    # Booking #, Last Name, First Name, Middle, Charge, Arrest Date, Released
                    if len(row) < 5:
                        continue

                    booking_num = row[0].strip()
                    if not booking_num or booking_num in seen_bookings:
                        continue
                    seen_bookings.add(booking_num)
                    new_count += 1

                    last_name = row[1].strip()
                    first_name = row[2].strip()
                    middle = row[3].strip() if len(row) > 3 else ""
                    charge = row[4].strip() if len(row) > 4 else ""
                    arrest_date_raw = row[5].strip() if len(row) > 5 else ""
                    release_raw = row[6].strip() if len(row) > 6 else ""

                    full_name = f"{last_name}, {first_name}"
                    if middle:
                        full_name = f"{last_name}, {first_name} {middle}"

                    arrest_date = self._parse_date(arrest_date_raw)

                    if "in custody" in release_raw.lower():
                        custody = "In Custody"
                    elif release_raw and release_raw.upper() not in ("", "N/A"):
                        custody = "Released"
                    else:
                        custody = "In Custody"

                    records.append(ArrestRecord(
                        County="Manatee",
                        State="FL",
                        Booking_Number=booking_num,
                        Full_Name=full_name,
                        First_Name=first_name,
                        Middle_Name=middle,
                        Last_Name=last_name,
                        Arrest_Date=arrest_date,
                        Booking_Date=arrest_date,
                        Charges=charge,
                        Facility="Manatee County Jail",
                        Status=custody,
                        Detail_URL=f"{BASE_URL}/bookings/{booking_num}",
                    ))

                logger.info(
                    f"[Manatee] Page {pg}: +{new_count} records (total: {len(records)})"
                )
                if new_count == 0:
                    break

                time.sleep(3)

            logger.info(
                f"[Manatee] Scraped {len(records)} records "
                f"(proxy={proxy_source}, engine={engine})"
            )
            if records and proxy_source == "ape":
                self.record_proxy_success(proxy_url, (time.time() - t0) * 1000)
            return records

        except Exception as e:
            logger.error(f"[Manatee] Fatal error: {e}")
            if proxy_source == "ape":
                try:
                    self.record_proxy_failure(proxy_url)
                except Exception:
                    pass
            raise
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass

    @staticmethod
    def _parse_date(text: str) -> Optional[str]:
        if not text:
            return None
        for fmt in ["%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%y", "%m/%d/%y"]:
            try:
                return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return text.strip() if text.strip() else None
