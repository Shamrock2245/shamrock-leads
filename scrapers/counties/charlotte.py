"""
Charlotte County Arrest Scraper — Revize CMS Roster via residential proxy
==========================================================================
Source: Charlotte County Sheriff's Office (CCSO)
URL: https://inmates.charlottecountyfl.revize.com/bookings
Method: Patchright/Playwright + APE residential (Warren) with office SOCKS fallback

Extracts data directly from the roster table — detail pages are blocked
by Cloudflare. The roster table contains: Booking #, Last Name, First Name,
Middle, Charge, Arrest Date for all in-custody inmates.

Requires a **true US residential** exit (Warren mac-office on home ISP, or SOCKS).
Datacamp/VPN/Bahamas exits will never clear CF — preflight fails closed.

HISTORY:
- v1–v4: Various CF bypass attempts (DrissionPage, curl_cffi, Obscura, JailTracker)
- v5: Roster table extraction via office SOCKS tunnel
- v6: APE-first residential proxy + SOCKS fallback
- v7 (current): Exit-IP preflight + Patchright + sticky Warren session
"""
import logging
import time
from datetime import datetime
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://inmates.charlottecountyfl.revize.com"
BOOKINGS_URL = f"{BASE_URL}/bookings"
MAX_PAGES = 50


class CharlotteCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Charlotte"

    def scrape(self) -> List[ArrestRecord]:
        from scrapers.socks_proxy import resolve_residential_proxy
        from scrapers.cf_browser import (
            launch_cf_browser,
            new_stealth_context,
            wait_past_cloudflare,
        )

        proxy_url, proxy_source = resolve_residential_proxy(
            self, sticky_session="fl-charlotte"
        )
        logger.info("[Charlotte] proxy source=%s", proxy_source)

        pw = browser = None
        t0 = time.time()
        try:
            # proxy_url may be None when source=direct (office Mac residential)
            pw, browser, engine = launch_cf_browser(
                proxy_url,
                label="Charlotte",
                # already validated for direct; re-check for proxy paths
                verify_residential=(proxy_source != "direct"),
            )
            context = new_stealth_context(browser)
            page = context.new_page()

            records = []
            seen_bookings = set()

            for pg in range(1, MAX_PAGES + 1):
                url = BOOKINGS_URL if pg == 1 else f"{BOOKINGS_URL}?page={pg}"
                logger.info(f"[Charlotte] Roster page {pg} (engine={engine})")

                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if not wait_past_cloudflare(page, label=f"Charlotte page {pg}", max_wait=45):
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
                    logger.info(f"[Charlotte] No rows on page {pg} — end of roster")
                    break

                new_count = 0
                for row in rows:
                    # Booking #, Last Name, First Name, Mid., Charge, Arrest Date
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

                    full_name = f"{last_name}, {first_name}"
                    if middle:
                        full_name = f"{last_name}, {first_name} {middle}"

                    arrest_date = self._parse_date(arrest_date_raw)

                    records.append(ArrestRecord(
                        County="Charlotte",
                        State="FL",
                        Booking_Number=booking_num,
                        Full_Name=full_name,
                        First_Name=first_name,
                        Middle_Name=middle,
                        Last_Name=last_name,
                        Arrest_Date=arrest_date,
                        Booking_Date=arrest_date,
                        Charges=charge,
                        Facility="Charlotte County Jail",
                        Status="In Custody",
                        Detail_URL=f"{BASE_URL}/bookings/{booking_num}",
                    ))

                logger.info(
                    f"[Charlotte] Page {pg}: +{new_count} records (total: {len(records)})"
                )
                if new_count == 0:
                    break

                time.sleep(3)

            logger.info(
                f"[Charlotte] Scraped {len(records)} records "
                f"(proxy={proxy_source}, engine={engine})"
            )
            if records and proxy_source == "ape":
                self.record_proxy_success(proxy_url, (time.time() - t0) * 1000)
            return records

        except Exception as e:
            logger.error(f"[Charlotte] Fatal error: {e}")
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
