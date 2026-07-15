"""
Harris County (TX) Arrest Scraper — HCSO Find Someone in Jail.
URL: https://www.harriscountyso.org/JailInfo/HCSO_FindSomeoneInJail.aspx
Platform: ASP.NET MVC SPA → DrissionPage needed for JS rendering.

Harris County is the 3rd-largest US county (~4.7M pop). The jail roster
is rendered client-side via JavaScript after initial page load.
We iterate A–Z by last name to retrieve the full roster.
"""
from __future__ import annotations

import hashlib
import logging
import re
import string
import time
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
PORTAL_URL = "https://www.harriscountyso.org/JailInfo/HCSO_FindSomeoneInJail.aspx"


class HarrisScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Harris"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen: set = set()

        try:
            page = self._get_browser_page()
            if not page:
                logger.error("Harris: DrissionPage browser unavailable")
                return []

            page.get(PORTAL_URL)
            page.wait.doc_loaded()
            time.sleep(2)  # Allow JS hydration

            for letter in string.ascii_uppercase:
                try:
                    batch = self._search_letter(page, letter)
                    for rec in batch:
                        key = rec.Booking_Number
                        if key in seen:
                            continue
                        seen.add(key)
                        records.append(rec)
                    time.sleep(0.5)
                except Exception as e:
                    logger.debug(f"Harris letter {letter}: {e}")

        except Exception as e:
            logger.error(f"Harris scrape failed: {e}")

        logger.info(f"✅ Harris (TX): {len(records)} records in {time.time()-start:.1f}s")
        return records

    def _get_browser_page(self):
        """Get a DrissionPage ChromiumPage with stealth options."""
        try:
            from DrissionPage import ChromiumPage
            co = self._get_browser_options()
            return ChromiumPage(co)
        except Exception as e:
            logger.error(f"Harris: browser init failed: {e}")
            return None

    def _search_letter(self, page, letter: str) -> List[ArrestRecord]:
        """Search by last name initial, parse the results table."""
        out: List[ArrestRecord] = []

        # Find and fill the last name field
        try:
            ln_input = page.ele('xpath://input[contains(@id, "txtLastName") or contains(@name, "LastName")]')
            if ln_input:
                ln_input.clear()
                ln_input.input(letter)

            # Click search button
            btn = page.ele('xpath://input[contains(@id, "btnSubmit") or contains(@value, "Search") or @id="btn-submit"]')
            if btn:
                btn.click()
                time.sleep(1.5)

        except Exception:
            # Fallback: try form submission via JS
            page.run_js(f'''
                var inputs = document.querySelectorAll('input[type="text"]');
                if (inputs.length > 0) {{ inputs[0].value = "{letter}"; }}
                var btn = document.querySelector('input[type="submit"], button[type="submit"]');
                if (btn) btn.click();
            ''')
            time.sleep(2)

        # Parse the results table
        try:
            tables = page.eles('tag:table')
            for table in tables:
                rows = table.eles('tag:tr')
                if len(rows) < 2:
                    continue
                headers = [th.text.lower().strip() for th in rows[0].eles('tag:th') or rows[0].eles('tag:td')]
                if not any(kw in ' '.join(headers) for kw in ('name', 'inmate', 'booking', 'defendant')):
                    continue

                for row in rows[1:]:
                    cells = [td.text.strip() for td in row.eles('tag:td')]
                    if len(cells) < 3:
                        continue
                    name = cells[0]
                    if not name or len(name) < 2:
                        continue

                    booking_num = ""
                    charges = "Unknown"
                    bond = "0"

                    for i, h in enumerate(headers):
                        if i >= len(cells):
                            break
                        if 'book' in h and 'date' not in h:
                            booking_num = cells[i]
                        elif 'charge' in h or 'offense' in h:
                            charges = cells[i]
                        elif 'bond' in h or 'bail' in h:
                            bond = re.sub(r'[^\d.]', '', cells[i]) or "0"

                    if not booking_num:
                        booking_num = f"HAR_{hashlib.md5(f'{name}|HARRIS'.encode()).hexdigest()[:10]}"

                    # Parse first/last from "Last, First" format
                    first, last = "", name
                    if "," in name:
                        parts = name.split(",", 1)
                        last = parts[0].strip()
                        first = parts[1].strip()

                    out.append(ArrestRecord(
                        County=self.county,
                        State="TX",
                        Full_Name=name,
                        First_Name=first,
                        Last_Name=last,
                        Booking_Number=str(booking_num),
                        Charges=charges or "Unknown",
                        Bond_Amount=bond,
                        Status="In Custody",
                        Detail_URL=PORTAL_URL,
                        Facility="Harris County Jail",
                    ))
                if out:
                    break
        except Exception as parse_err:
            logger.debug(f"Harris parse error for {letter}: {parse_err}")

        return out
