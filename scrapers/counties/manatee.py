"""
Manatee County Arrest Scraper — Revize CMS Roster Table via SOCKS Proxy
========================================================================
Source: Manatee County Sheriff's Office
URL: https://manatee-sheriff.revize.com/bookings
Method: Playwright + SOCKS5 proxy (office iMac residential IP)

Extracts data directly from the roster table — detail pages are blocked
by Cloudflare. The roster table contains: Booking #, Last Name, First Name,
Middle, Charge, Arrest Date, Released Date for all current inmates.

HISTORY:
- v1–v3: Various CF bypass attempts (DrissionPage, Obscura, JailTracker)
- v4 (current): Roster table extraction via SOCKS proxy. Fast, reliable, no detail pages.
"""
import logging
import re
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
        from playwright.sync_api import sync_playwright
        from scrapers.socks_proxy import require_socks_or_raise

        socks = require_socks_or_raise()
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            proxy={"server": socks},
            args=["--disable-blink-features=AutomationControlled"],
        )

        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)

            records = []
            seen_bookings = set()

            for pg in range(1, MAX_PAGES + 1):
                url = BOOKINGS_URL if pg == 1 else f"{BOOKINGS_URL}?page={pg}"
                logger.info(f"[Manatee] Roster page {pg}")

                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                if not self._wait_past_cloudflare(page, label=f"page {pg}"):
                    break

                # Extract rows from the FIRST table (structured headers)
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
                    # Manatee columns: Booking #, Last Name, First Name, Middle, Charge, Arrest Date, Released Date
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

                    # Parse custody status
                    if "in custody" in release_raw.lower():
                        custody = "In Custody"
                    elif release_raw and release_raw.upper() not in ["", "N/A"]:
                        custody = "Released"
                    else:
                        custody = "In Custody"

                    records.append(ArrestRecord(
                        County="Manatee",
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

                logger.info(f"[Manatee] Page {pg}: +{new_count} records (total: {len(records)})")
                if new_count == 0:
                    break

                time.sleep(3)  # Polite delay — CF rate-limits aggressive paging

            logger.info(f"[Manatee] Scraped {len(records)} records from roster table 🧦")
            return records

        except Exception as e:
            logger.error(f"[Manatee] Fatal error: {e}")
            raise
        finally:
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass

    @staticmethod
    @staticmethod
    def _wait_past_cloudflare(page, label: str = "", max_wait: int = 25) -> bool:
        """Wait for CF challenge to clear; return False if still blocked."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            title = (page.title() or "").lower()
            if "just a moment" not in title and "attention" not in title and "blocked" not in title:
                try:
                    if page.query_selector("table tbody tr"):
                        return True
                except Exception:
                    pass
                time.sleep(1.5)
                try:
                    if page.query_selector("table"):
                        return True
                except Exception:
                    pass
                return True
            time.sleep(1.5)
        logger.error(f"[Manatee] Cloudflare still blocked on {label or 'page'}")
        return False

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
