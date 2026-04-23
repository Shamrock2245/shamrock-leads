"""
Polk County Arrest Scraper — Sheriff's Jail Inquiry via DrissionPage.

Source: Polk County Sheriff's Office
URL: https://polksheriff.org/detention/jail-inquiry
Method: DrissionPage browser automation (migrated from Selenium)

Architecture:
1. Navigate to PCSO jail inquiry page
2. Search by yesterday's date (site doesn't show today's entries)
3. Parse listing page for basic inmate info
4. Click each booking number → detail page for charges + bond
5. Map all fields → ArrestRecord schema

Note: Original solver used Selenium + BeautifulSoup.
Ported to DrissionPage for fleet consistency.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
BASE_URL = "https://polksheriff.org"
SEARCH_URL = f"{BASE_URL}/detention/jail-inquiry"
DAYS_BACK = 3
DETAIL_DELAY_S = 1.0
MAX_DETAILS = 200


class PolkCountyScraper(BaseScraper):
    """Polk County (FL) arrest scraper — PCSO Jail Inquiry."""

    @property
    def county(self) -> str:
        return "Polk"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape Polk County via DrissionPage browser automation."""
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error(
                "❌ DrissionPage not installed. "
                "Install with: pip install DrissionPage"
            )
            return []

        page = self._setup_browser()
        all_records: List[ArrestRecord] = []

        try:
            for days_ago in range(1, DAYS_BACK + 1):
                target_date = datetime.now() - timedelta(days=days_ago)
                date_str = target_date.strftime("%m/%d/%Y")

                logger.info(f"📅 Polk: Searching bookings for {date_str}")

                try:
                    daily = self._scrape_date(page, target_date)
                    all_records.extend(daily)
                    logger.info(f"✅ {date_str}: {len(daily)} records")
                except Exception as e:
                    logger.warning(f"⚠️ Error on {date_str}: {e}")

                time.sleep(2)

            logger.info(
                f"✅ Scraped {len(all_records)} total records from Polk"
            )
            return all_records

        except Exception as e:
            logger.error(f"❌ Polk scraper fatal error: {e}")
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
        co.headless(True)
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

    def _scrape_date(self, page, target_date: datetime) -> List[ArrestRecord]:
        """Scrape bookings for a single date from Polk Sheriff."""
        records: List[ArrestRecord] = []
        date_str = target_date.strftime("%m/%d/%Y")

        # Navigate to jail inquiry
        page.get(SEARCH_URL)
        time.sleep(3)

        # Fill in date fields and submit search
        try:
            # Polk uses date range — set both start & end to same day
            start_input = (
                page.ele('css:#startDate')
                or page.ele('css:input[name*="startDate"]')
                or page.ele('css:input[name*="StartDate"]')
                or page.ele('css:input[placeholder*="Start"]')
            )
            end_input = (
                page.ele('css:#endDate')
                or page.ele('css:input[name*="endDate"]')
                or page.ele('css:input[name*="EndDate"]')
                or page.ele('css:input[placeholder*="End"]')
            )

            if start_input:
                start_input.clear()
                start_input.input(date_str)
            if end_input:
                end_input.clear()
                end_input.input(date_str)

            time.sleep(1)

            # Submit
            search_btn = (
                page.ele('css:button[type="submit"]')
                or page.ele('css:input[type="submit"]')
                or page.ele('text:Search')
                or page.ele('text:Submit')
            )
            if search_btn:
                search_btn.click()
                time.sleep(4)

        except Exception as e:
            logger.warning(f"⚠️ Form submission error on Polk: {e}")
            return records

        # Parse listing results
        rows = page.eles('xpath://table//tr[td]')
        if not rows:
            rows = page.eles('css:.inmate-row') or page.eles('css:.booking-entry')

        logger.info(f"📋 Polk: {len(rows)} rows for {date_str}")

        # Extract basic info from listing
        inmates_basic = []
        for row in rows:
            try:
                cells = row.eles('tag:td')
                if len(cells) < 3:
                    continue

                cell_texts = [self._clean(c.text) for c in cells]

                # Try to find booking link
                link = row.ele('tag:a')
                href = ""
                if link:
                    href = link.attr("href") or ""
                    if href and not href.startswith("http"):
                        href = f"{BASE_URL}{href}"

                inmates_basic.append({
                    "cells": cell_texts,
                    "detail_url": href,
                    "date_str": date_str,
                })
            except Exception:
                continue

        # Visit detail pages for enrichment
        for idx, basic in enumerate(inmates_basic[:MAX_DETAILS]):
            try:
                record = self._enrich_from_detail(page, basic, idx, len(inmates_basic))
                if record and record.Full_Name and record.Booking_Number:
                    records.append(record)
            except Exception as e:
                logger.warning(f"⚠️ Detail fetch error: {e}")

            time.sleep(DETAIL_DELAY_S)

        return records

    def _enrich_from_detail(
        self, page, basic: dict, idx: int, total: int
    ) -> Optional[ArrestRecord]:
        """Visit detail page and create enriched ArrestRecord."""
        cells = basic["cells"]

        # Map from listing row
        name = cells[0] if len(cells) > 0 else ""
        booking_num = cells[1] if len(cells) > 1 else ""
        dob = cells[2] if len(cells) > 2 else ""
        race = cells[3] if len(cells) > 3 else ""
        sex = cells[4] if len(cells) > 4 else ""

        first_name, middle_name, last_name = self._parse_name(name)

        # Default values
        charges = ""
        bond_amount = 0.0
        status = "In Custody"

        # Visit detail page if URL available
        detail_url = basic.get("detail_url", "")
        if detail_url:
            try:
                page.get(detail_url)
                time.sleep(2)

                if idx % 20 == 0:
                    logger.info(f"   [{idx + 1}/{total}] {name}")

                # Extract charges from detail page
                page_text = page.html or ""

                # Look for charges table
                charge_rows = page.eles(
                    'xpath://table[contains(.//th, "Charge") or '
                    'contains(.//th, "Offense")]//tr[td]'
                )
                charge_list = []
                for cr in charge_rows:
                    cr_cells = cr.eles('tag:td')
                    if cr_cells:
                        charge_parts = [self._clean(c.text) for c in cr_cells if self._clean(c.text)]
                        if charge_parts:
                            charge_list.append(" ".join(charge_parts))

                if charge_list:
                    charges = " | ".join(charge_list)

                # Extract bond amount
                bond_match = re.search(
                    r'(?:Total\s+Bond|Bond\s+Amount)[:\s]*\$?([\d,]+(?:\.\d{2})?)',
                    page_text, re.IGNORECASE
                )
                if bond_match:
                    bond_amount = self._parse_bond(bond_match.group(1))

                # Extract status
                status_match = re.search(
                    r'(?:Status|Current\s+Status)[:\s]*([A-Za-z\s]+)',
                    page_text, re.IGNORECASE
                )
                if status_match:
                    status = self._clean(status_match.group(1))

            except Exception as e:
                logger.debug(f"   Detail page error: {e}")

        return ArrestRecord(
            County=self.county,
            Booking_Number=self._clean(booking_num),
            Full_Name=name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB=self._clean(dob),
            Booking_Date=basic.get("date_str", ""),
            Arrest_Date=basic.get("date_str", ""),
            Status=status,
            Facility="Polk County Jail",
            Race=self._clean(race),
            Sex=self._clean(sex),
            Charges=charges,
            Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
            Bond_Paid="NO",
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        )

    # ── Utilities ──

    @staticmethod
    def _clean(text: str) -> str:
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _parse_name(name_str: str):
        if not name_str:
            return "", "", ""
        name_str = " ".join(name_str.strip().split())
        if "," in name_str:
            parts = name_str.split(",", 1)
            last_name = parts[0].strip()
            first_middle = parts[1].strip() if len(parts) > 1 else ""
            name_parts = first_middle.split()
            first_name = name_parts[0] if name_parts else ""
            middle_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
            return first_name, middle_name, last_name
        parts = name_str.split()
        if len(parts) >= 2:
            return parts[0], " ".join(parts[2:]) if len(parts) > 2 else "", parts[-1]
        return name_str, "", ""

    @staticmethod
    def _parse_bond(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
