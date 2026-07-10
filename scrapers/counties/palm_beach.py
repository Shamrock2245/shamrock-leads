"""
Palm Beach County Arrest Scraper — PBSO Booking Blotter (ColdFusion).
Source: Palm Beach County Sheriff's Office
URL: https://www3.pbso.org/blotter/index.cfm
Method: DrissionPage browser automation (date form + paginated card layout)

The PBSO Blotter is a ColdFusion app that requires JavaScript rendering.
Each result page shows ~5 booking cards with:
  - Person info panel (mugshot, name, race, gender, facility, agency, jacket #, booking date/time)
  - Charges/bond table below (booking #, charges, original bond, current bond)

Pagination: "Page X of Y" with numbered page links + » for next.
Date format for search: MM/DD/YYYY in #start_date and #end_date fields.
Submit button: #process

Known challenges:
  - hCaptcha may appear — we wait and retry
  - Some pages load slowly (ColdFusion)
  - Results are in div[id^='allresults_'] containers
"""
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BLOTTER_URL = "https://www3.pbso.org/blotter/index.cfm"
FACILITY = "Palm Beach County Jail"
COUNTY = "Palm Beach"
DAYS_BACK = 2  # Search today + yesterday


class PalmBeachCountyScraper(BaseScraper):
    """Palm Beach County — PBSO Booking Blotter (www3.pbso.org)"""

    @property
    def county(self) -> str:
        return "Palm Beach"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage  # noqa
        except ImportError:
            logger.error("Palm Beach: DrissionPage not installed")
            raise

        co = self._get_browser_options()
        page = ChromiumPage(addr_or_opts=co)
        records = []

        try:
            for i in range(DAYS_BACK - 1, -1, -1):
                target_date = (datetime.now() - timedelta(days=i)).strftime("%m/%d/%Y")
                rows = self._search_and_collect(page, target_date, max_pages=50)

                for row in rows:
                    records.append(ArrestRecord(
                        County=COUNTY,
                        State="FL",
                        Facility=row.get("facility", FACILITY),
                        Agency=row.get("agency", "PBSO"),
                        Full_Name=row.get("full_name", ""),
                        First_Name=row.get("first_name", ""),
                        Middle_Name=row.get("middle_name", ""),
                        Last_Name=row.get("last_name", ""),
                        Booking_Number=row.get("booking_num", ""),
                        Person_ID=row.get("jacket_num", ""),
                        DOB=row.get("dob", ""),
                        Race=row.get("race", ""),
                        Sex=row.get("sex", ""),
                        Booking_Date=row.get("booking_date", ""),
                        Booking_Time=row.get("booking_time", ""),
                        Arrest_Date=row.get("booking_date", ""),
                        Arrest_Time=row.get("booking_time", ""),
                        Status=row.get("status", "In Custody"),
                        Release_Date=row.get("release_date", ""),
                        Charges=row.get("charges", ""),
                        Bond_Amount=row.get("bond_amount", "0"),
                        Mugshot_URL=row.get("mug_url", ""),
                        Detail_URL=BLOTTER_URL,
                        Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                        LastChecked=datetime.now(timezone.utc).isoformat(),
                        LastCheckedMode="scrape",
                    ))

        except Exception as e:
            logger.error(f"Palm Beach: scraper error — {e}")
            raise
        finally:
            try:
                page.quit()
            except:
                pass

        logger.info(f"Palm Beach: total {len(records)} records")
        return records

    # ── Search & Collection ────────────────────────────────────────────────

    def _search_and_collect(self, page, target_date: str, max_pages: int = 50) -> list:
        """Navigate to blotter, search a date, and collect all result pages."""
        logger.info(f"Palm Beach: searching {target_date}")
        page.get(BLOTTER_URL)
        time.sleep(3)

        # Handle hCaptcha if present
        try:
            if page.ele("tag:iframe[src*='hcaptcha.com']"):
                logger.warning("Palm Beach: hCaptcha detected — waiting 30s")
                time.sleep(30)
                # Check again after wait
                if page.ele("tag:iframe[src*='hcaptcha.com']"):
                    logger.error("Palm Beach: hCaptcha still present — aborting")
                    return []
        except:
            pass

        # Wait for date form to load
        if not page.wait.ele_displayed("#start_date", timeout=15):
            logger.error("Palm Beach: search form did not load")
            return []

        # Fill date fields
        start_input = page.ele("#start_date")
        end_input = page.ele("#end_date")
        if start_input:
            start_input.clear()
            start_input.input(target_date)
        if end_input:
            end_input.clear()
            end_input.input(target_date)

        # Submit
        submit_btn = page.ele("#process") or page.ele("css:input[type=submit]")
        if not submit_btn:
            logger.error("Palm Beach: submit button not found")
            return []

        submit_btn.click()
        time.sleep(5)

        # Collect across all pages
        all_rows = []
        current_page = 1

        while current_page <= max_pages:
            # Check for results
            if not page.wait.ele_displayed("css:div[id^='allresults_']", timeout=10):
                # DrissionPage ChromiumPage has no .text — use .html (or body text)
                try:
                    page_text = page.html or ""
                except Exception:
                    try:
                        body = page.ele("tag:body")
                        page_text = (body.text if body else "") or ""
                    except Exception:
                        page_text = ""
                if "0 matches" in page_text or "no results" in page_text.lower():
                    logger.info(f"Palm Beach: no results for {target_date}")
                break

            results = page.eles("css:div[id^='allresults_']")
            logger.info(f"Palm Beach: page {current_page} → {len(results)} records")

            for result_div in results:
                try:
                    data = self._parse_result_card(result_div)
                    if data and data.get("booking_num"):
                        all_rows.append(data)
                except Exception as e:
                    logger.debug(f"Palm Beach: card parse error: {e}")

            # Try next page
            if not self._click_next_page(page):
                break
            current_page += 1
            time.sleep(3)

        logger.info(f"Palm Beach: collected {len(all_rows)} records for {target_date}")
        return all_rows

    # ── Result Card Parsing ────────────────────────────────────────────────

    def _parse_result_card(self, card) -> dict:
        """Parse a single booking result card (div[id^='allresults_']).

        Structure from recon:
        ┌─────────────────────────────────────────────────────┐
        │ [Mugshot]  Name: SENGELMANN, MICHAEL                │
        │            Race: White   Gender: Male               │
        │            Facility:     OBTS Number: N/A           │
        │            Arresting Agency: 01-PBSO                │
        │            Booking Date/Time: 05/15/2026 10:34      │
        │            Release Date: N/A                        │
        │            Holds For Other Agencies: No             │
        │            Jacket Number: 0428603                   │
        ├─────────────────────────────────────────────────────┤
        │ Booking Number: 2026012709                          │
        │ Charges | Original Bond | Current Bond              │
        │ 0003  BOOKED - COMMIT     $0.00         $0.00       │
        └─────────────────────────────────────────────────────┘
        """
        data = {
            "full_name": "", "first_name": "", "middle_name": "", "last_name": "",
            "booking_num": "", "jacket_num": "", "race": "", "sex": "",
            "dob": "", "facility": FACILITY, "agency": "PBSO",
            "booking_date": "", "booking_time": "", "status": "In Custody",
            "release_date": "", "charges": "", "bond_amount": "0", "mug_url": "",
        }

        card_text = card.text or ""

        # ── Extract labeled fields ─────────────────────────────────────────
        def _extract(label):
            """Extract value after 'Label:' in the card text."""
            pattern = rf'{label}\s*:\s*(.+?)(?:\n|$)'
            m = re.search(pattern, card_text, re.I)
            return m.group(1).strip() if m else ""

        data["full_name"] = _extract("Name")
        data["race"] = _extract("Race")
        data["sex"] = _extract("Gender")
        data["dob"] = _extract("DOB") or _extract("Date of Birth")
        data["agency"] = _extract("Arresting Agency") or "PBSO"
        data["jacket_num"] = _extract("Jacket Number")
        release_raw = _extract("Release Date")

        # Booking Date/Time: "05/15/2026 10:34"
        booking_dt_raw = _extract("Booking Date/Time")
        if booking_dt_raw:
            try:
                dt = datetime.strptime(booking_dt_raw.strip(), "%m/%d/%Y %H:%M")
                data["booking_date"] = dt.strftime("%Y-%m-%d")
                data["booking_time"] = dt.strftime("%H:%M:00")
            except ValueError:
                data["booking_date"] = booking_dt_raw.strip()

        # Release date / Status
        if release_raw and "N/A" not in release_raw and release_raw.strip():
            data["status"] = "Released"
            data["release_date"] = release_raw.strip()

        # ── Booking Number (from the charges section link) ─────────────────
        booking_num_m = re.search(r'Booking\s*Number\s*:\s*(\d+)', card_text, re.I)
        if booking_num_m:
            data["booking_num"] = booking_num_m.group(1)
        else:
            # Try link text
            try:
                link = card.ele("css:a")
                if link:
                    link_text = link.text.strip()
                    if link_text and link_text.isdigit():
                        data["booking_num"] = link_text
            except:
                pass

        # ── Mugshot URL ────────────────────────────────────────────────────
        try:
            img = card.ele("css:img")
            if img:
                src = img.attr("src") or ""
                if src and "noimage" not in src.lower():
                    if not src.startswith("http"):
                        src = f"https://www3.pbso.org{src}"
                    data["mug_url"] = src
        except:
            pass

        # ── Charges & Bond ─────────────────────────────────────────────────
        charges = []
        total_bond = 0.0

        # Find charge descriptions — pattern: statute code + charge description
        charge_matches = re.findall(
            r'(\d{3}\.\d+\s+\S.*?)(?:Original Bond|Current Bond|Bond Information|$)',
            card_text, re.I
        )
        for ch in charge_matches:
            clean_ch = " ".join(ch.strip().split())
            if clean_ch and len(clean_ch) > 3:
                charges.append(clean_ch)

        # Fallback: find lines with charge-like patterns
        if not charges:
            for line in card_text.split("\n"):
                line = line.strip()
                # Match lines like "0003 BOOKED - COMMIT" or "322.34 2C (FT) MOVING TRAFFIC VIOL..."
                if re.match(r'^\d{3,4}', line) and not re.match(r'^\d{4}[\-/]', line):
                    # Skip lines that are dates or booking numbers
                    if "Bond" not in line and "Booking" not in line:
                        charges.append(" ".join(line.split()))

        # Bond amounts — "Current Bond: $X,XXX.XX"
        bond_matches = re.findall(r'Current\s+Bond\s*:\s*\$([0-9,]+(?:\.\d{2})?)', card_text, re.I)
        for amt_str in bond_matches:
            try:
                total_bond += float(amt_str.replace(",", ""))
            except (ValueError, TypeError):
                pass

        # Fallback: "Original Bond: $X"
        if total_bond == 0:
            orig_bonds = re.findall(r'Original\s+Bond\s*:\s*\$([0-9,]+(?:\.\d{2})?)', card_text, re.I)
            for amt_str in orig_bonds:
                try:
                    total_bond += float(amt_str.replace(",", ""))
                except (ValueError, TypeError):
                    pass

        data["charges"] = " | ".join(charges) if charges else ""
        data["bond_amount"] = f"{total_bond:.2f}" if total_bond > 0 else "0"

        # ── Parse Name ─────────────────────────────────────────────────────
        fn = data["full_name"]
        if fn:
            data["first_name"], data["middle_name"], data["last_name"] = self._parse_name(fn)

        return data

    # ── Pagination ─────────────────────────────────────────────────────────

    def _click_next_page(self, page) -> bool:
        """Click the next page (» or numbered link)."""
        try:
            # Check current page vs total
            page_info = page.ele("xpath://*[contains(text(), 'Page ')]")
            if page_info:
                m = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", page_info.text)
                if m:
                    current, total = int(m.group(1)), int(m.group(2))
                    if current >= total:
                        return False

                    # Click the next page number
                    next_num = current + 1
                    next_link = page.ele(f'xpath://a[normalize-space(text())="{next_num}"]')
                    if next_link:
                        next_link.click()
                        time.sleep(3)
                        return True

            # Try » link
            for link in page.eles("xpath://a"):
                text = link.text.strip()
                if text == "»":
                    link.click()
                    time.sleep(3)
                    return True

        except Exception as e:
            logger.debug(f"Palm Beach pagination error: {e}")
        return False

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_name(name):
        """Parse 'LAST, FIRST MIDDLE' into components."""
        if not name:
            return "", "", ""
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            remainder = parts[1].strip().split()
            first = remainder[0] if remainder else ""
            middle = " ".join(remainder[1:]) if len(remainder) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) >= 3:
            return parts[0], " ".join(parts[1:-1]), parts[-1]
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return name, "", ""
