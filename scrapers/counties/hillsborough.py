"""
Hillsborough County Arrest Scraper — HCSO Arrest Inquiry Portal.

Source: Hillsborough County Sheriff's Office
URL: https://webapps.hcso.tampa.fl.us/arrestinquiry/
Method: DrissionPage browser automation (login + reCAPTCHA + paginated table)

Architecture:
1. Login with HCSO_EMAIL / HCSO_PASSWORD credentials
2. Handle reCAPTCHA v2 checkbox inside iframe
3. Perform date-range search (last N days)
4. Parse paginated results table (4-row blocks per inmate)
5. Extract charges, bond amounts, demographics from nested tables
6. Map all fields → ArrestRecord schema

Requires env vars: HCSO_EMAIL, HCSO_PASSWORD
"""

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
LOGIN_URL = "https://webapps.hcso.tampa.fl.us/arrestinquiry/Account/Login"
SEARCH_URL = "https://webapps.hcso.tampa.fl.us/arrestinquiry/Home/Search"
DAYS_BACK = 3
MAX_PAGES = 20


class HillsboroughCountyScraper(BaseScraper):
    """Hillsborough County (FL) arrest scraper — HCSO portal with login."""

    @property
    def county(self) -> str:
        return "Hillsborough"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape Hillsborough County via DrissionPage with login."""
        hcso_email = os.getenv("HCSO_EMAIL")
        hcso_password = os.getenv("HCSO_PASSWORD")

        if not hcso_email or not hcso_password:
            logger.warning(
                "⚠️ HCSO_EMAIL / HCSO_PASSWORD not set — "
                "Hillsborough requires authorized member login"
            )
            return []

        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("❌ DrissionPage or bs4 not installed")
            return []

        page = self._setup_browser()
        all_records: List[ArrestRecord] = []

        try:
            # Step 1: Login
            if not self._login(page, hcso_email, hcso_password):
                logger.error("❌ Hillsborough: login failed")
                return []

            # Step 2: Search
            if not self._perform_search(page):
                logger.warning("⚠️ Hillsborough: no search results")
                return []

            # Step 3: Parse paginated results
            for page_num in range(1, MAX_PAGES + 1):
                logger.info(f"📄 Hillsborough: parsing page {page_num}")

                soup = BeautifulSoup(page.html, "html.parser")
                page_records = self._parse_results_table(soup)

                if not page_records:
                    logger.info(f"🏁 No records on page {page_num}")
                    break

                all_records.extend(page_records)
                logger.info(
                    f"✅ Page {page_num}: {len(page_records)} records "
                    f"(total: {len(all_records)})"
                )

                # Check for Next button
                try:
                    next_btn = page.ele("text:Next >", timeout=2)
                    if next_btn:
                        btn_class = next_btn.attr("class") or ""
                        if "disabled" in btn_class:
                            break
                        next_btn.click()
                        time.sleep(3)
                    else:
                        break
                except Exception:
                    break

            logger.info(
                f"✅ Hillsborough: scraped {len(all_records)} total records"
            )
            return all_records

        except Exception as e:
            logger.error(f"❌ Hillsborough fatal error: {e}")
            return []
        finally:
            try:
                page.quit()
            except Exception:
                pass

    # ── Browser Setup ──

    @staticmethod
    def _setup_browser():
        from DrissionPage import ChromiumPage, ChromiumOptions

        co = ChromiumOptions()
        co.auto_port()
        co.headless(True)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-gpu")
        co.set_argument("--window-size=1920,1080")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        return ChromiumPage(addr_or_opts=co)

    def _login(self, page, email: str, password: str) -> bool:
        """Log into HCSO portal, handling reCAPTCHA v2."""
        logger.info("🔑 Hillsborough: navigating to login page")
        page.get(LOGIN_URL)
        time.sleep(3)

        # Fill credentials
        email_field = page.ele("#Email", timeout=10)
        if not email_field:
            logger.error("❌ Could not find email field")
            return False
        email_field.clear()
        email_field.input(email)

        pwd_field = page.ele("#Password", timeout=5)
        if not pwd_field:
            logger.error("❌ Could not find password field")
            return False
        pwd_field.clear()
        pwd_field.input(password)

        # Toggle Remember Me
        try:
            remember = page.ele("#RememberMe", timeout=3)
            if remember:
                remember.click()
        except Exception:
            pass

        # Handle reCAPTCHA v2 checkbox
        logger.info("🤖 Checking for reCAPTCHA...")
        try:
            recaptcha_iframe = page.ele(
                "tag:iframe@@title=reCAPTCHA", timeout=5
            ) or page.ele("tag:iframe@@src:recaptcha", timeout=3)

            if recaptcha_iframe:
                checkbox = recaptcha_iframe.ele(
                    "tag:div@@class:recaptcha-checkbox-border", timeout=5
                ) or recaptcha_iframe.ele("#recaptcha-anchor", timeout=3)
                if checkbox:
                    checkbox.click()
                    time.sleep(3)
                    logger.info("✅ reCAPTCHA clicked")
        except Exception as e:
            logger.warning(f"⚠️ reCAPTCHA handling: {e}")

        time.sleep(2)

        # Submit login
        login_btn = page.ele(
            "tag:button@@text():Log in", timeout=5
        ) or page.ele("tag:input@@type=submit", timeout=3)
        if login_btn:
            login_btn.click()
        else:
            pwd_field.input("\n")

        time.sleep(5)

        # Verify login
        html = page.html
        if "Log out" in html or "Welcome" in html or "Search" in html:
            logger.info("✅ Hillsborough: login successful")
            return True
        if "Invalid" in html or "incorrect" in html.lower():
            logger.error("❌ Invalid credentials")
            return False

        return "arrestinquiry" in page.url.lower()

    def _perform_search(self, page) -> bool:
        """Submit date-range search."""
        logger.info("🔍 Hillsborough: performing search")
        page.get(SEARCH_URL)
        time.sleep(3)

        end_date = datetime.now()
        start_date = end_date - timedelta(days=DAYS_BACK)

        start_field = page.ele("#BeginDate", timeout=5) or page.ele(
            "@@name=BeginDate", timeout=3
        )
        if start_field:
            start_field.clear()
            start_field.input(start_date.strftime("%m/%d/%Y"))

        end_field = page.ele("#EndDate", timeout=5) or page.ele(
            "@@name=EndDate", timeout=3
        )
        if end_field:
            end_field.clear()
            end_field.input(end_date.strftime("%m/%d/%Y"))

        search_btn = (
            page.ele("tag:button@@text():Search", timeout=5)
            or page.ele("#searchButton", timeout=3)
            or page.ele("tag:input@@value=Search", timeout=3)
        )
        if search_btn:
            search_btn.click()
        elif end_field:
            end_field.input("\n")

        time.sleep(5)

        html = page.html
        return "table-striped" in html or "Booking Name" in html

    def _parse_results_table(self, soup) -> List[ArrestRecord]:
        """Parse HCSO results table (4-row blocks per inmate)."""
        records = []

        results_table = soup.find("table", class_="table-striped")
        if not results_table:
            return records

        tbody = results_table.find("tbody") or results_table
        all_rows = tbody.find_all("tr", recursive=False)

        i = 0
        while i < len(all_rows):
            try:
                row = all_rows[i]
                cells = row.find_all("td", recursive=False)

                if len(cells) >= 5:
                    name_link = cells[0].find("a")
                    if name_link:
                        record = self._parse_inmate_block(
                            all_rows, i, cells, name_link
                        )
                        if record:
                            records.append(record)
                        i += 4  # Skip related rows
                        continue
                i += 1
            except Exception as e:
                logger.warning(f"⚠️ Row parse error at {i}: {e}")
                i += 1

        return records

    def _parse_inmate_block(
        self, all_rows, i, cells, name_link
    ) -> ArrestRecord:
        """Parse a 4-row inmate block into ArrestRecord."""
        full_name = name_link.get_text(strip=True)
        first_name, middle_name, last_name = self._parse_name(full_name)

        # Detail URL
        href = name_link.get("href", "")
        if href and not href.startswith("http"):
            href = "https://webapps.hcso.tampa.fl.us" + href

        # Booking number
        booking_number = cells[1].get_text(strip=True)

        # Demographics: R / S / E / DOB
        demo = cells[4].get_text(strip=True)
        demo_parts = [p.strip() for p in demo.split("/")]
        race = demo_parts[0] if len(demo_parts) >= 1 else ""
        sex = demo_parts[1] if len(demo_parts) >= 2 else ""
        dob = demo_parts[3] if len(demo_parts) >= 4 else ""

        # Address row
        address = ""
        if i + 1 < len(all_rows):
            for cell in all_rows[i + 1].find_all("td"):
                text = cell.get_text(strip=True)
                if text.startswith("ADDRESS:"):
                    address = text.replace("ADDRESS:", "").strip()

        # Dates + status row
        booking_date = ""
        arrest_date = ""
        status = "In Custody"
        if i + 2 < len(all_rows):
            for cell in all_rows[i + 2].find_all("td"):
                text = cell.get_text(strip=True)
                if text.startswith("ARREST DATE:"):
                    arrest_date = text.replace("ARREST DATE:", "").strip()
                elif text.startswith("BOOKING DATE:"):
                    booking_date = text.replace("BOOKING DATE:", "").strip()
                elif text.startswith("RELEASE DATE:"):
                    release = text.replace("RELEASE DATE:", "").strip()
                    if release:
                        status = "Released"

        # Charges + bond from nested table
        charges_list = []
        total_bond = 0.0
        case_number = ""
        if i + 3 < len(all_rows):
            nested = all_rows[i + 3].find("table")
            if nested:
                for cr in nested.find_all("tr"):
                    charge_cells = cr.find_all("td")
                    if len(charge_cells) >= 2:
                        desc = charge_cells[1].get_text(strip=True)
                        if desc and "Charge Type" not in desc:
                            charges_list.append(desc)
                        if len(charge_cells) >= 5:
                            bond_text = charge_cells[4].get_text(strip=True)
                            try:
                                amt = float(
                                    bond_text.replace("$", "")
                                    .replace(",", "")
                                )
                                total_bond += amt
                            except (ValueError, TypeError):
                                pass
                        if len(charge_cells) >= 4 and not case_number:
                            cn = charge_cells[3].get_text(strip=True)
                            if cn and "-" in cn:
                                case_number = cn

        if not booking_number:
            return None

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_number,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            Booking_Date=booking_date,
            Arrest_Date=arrest_date,
            Status=status,
            Facility="Falkenburg Road Jail",
            Race=race,
            Sex=sex,
            DOB=dob,
            Address=address,
            Charges=" | ".join(charges_list),
            Bond_Amount=str(total_bond) if total_bond > 0 else "0",
            Case_Number=case_number,
            Detail_URL=href,
            LastCheckedMode="INITIAL",
        )

    @staticmethod
    def _parse_name(name_str: str):
        """Parse 'LAST, FIRST MIDDLE' into components."""
        if not name_str:
            return "", "", ""
        if "," in name_str:
            parts = name_str.split(",", 1)
            last_name = parts[0].strip()
            first_middle = parts[1].strip() if len(parts) > 1 else ""
            name_parts = first_middle.split()
            first_name = name_parts[0] if name_parts else ""
            middle_name = (
                " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
            )
            return first_name, middle_name, last_name
        parts = name_str.split()
        if len(parts) >= 2:
            return parts[0], "", parts[-1]
        return name_str, "", ""
