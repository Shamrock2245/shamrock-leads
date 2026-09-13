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
from scrapers.pbso_parse import (
    BLOTTER_URL,
    COUNTY,
    FACILITY,
    extract_pbso_booking_id,
    is_pbso_index_only,
    parse_name,
    parse_pbso_card_text,
    parse_pbso_html,
)
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

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
                    records.append(self._row_to_arrest_record(row, mode="scrape"))

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
        """Parse a single booking result card (div[id^='allresults_'])."""
        text = card.text or ""
        data = parse_pbso_card_text(text)
        if not data:
            booking_from_link = ""
            try:
                link = card.ele("css:a")
                if link:
                    link_text = (link.text or "").strip()
                    if link_text.isdigit() and len(link_text) >= 6:
                        booking_from_link = link_text
            except Exception:
                pass
            if booking_from_link:
                data = parse_pbso_card_text(text + f"\nBooking Number: {booking_from_link}\n")
            if not data:
                return {}
        try:
            img = card.ele("css:img")
            if img:
                src = img.attr("src") or ""
                if src and "noimage" not in src.lower():
                    if not src.startswith("http"):
                        src = f"https://www3.pbso.org{src}"
                    data["mug_url"] = src
        except Exception:
            pass
        return data

    def _row_to_arrest_record(self, row: dict, mode: str = "scrape") -> ArrestRecord:
        booking = str(row.get("booking_num") or "")
        detail = f"{BLOTTER_URL}?booking={booking}" if booking else BLOTTER_URL
        return ArrestRecord(
            County=COUNTY,
            State="FL",
            Facility=row.get("facility", FACILITY),
            Agency=row.get("agency", "PBSO"),
            Full_Name=row.get("full_name", ""),
            First_Name=row.get("first_name", ""),
            Middle_Name=row.get("middle_name", ""),
            Last_Name=row.get("last_name", ""),
            Booking_Number=booking,
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
            Detail_URL=detail,
            Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
            LastChecked=datetime.now(timezone.utc).isoformat(),
            LastCheckedMode=mode,
        )

    def _fetch_single_booking(self, booking_id: str, detail_url: str):
        """Re-fetch one PBSO booking from a booking-tagged public URL.

        The blotter search form is not a per-inmate page. Index-only URLs
        are not fetched (no generic GET, no browser, no unpublished probe).
        Returns None when the HTML has no matching name+booking card.
        """
        if not booking_id:
            return None
        url = (detail_url or "").strip() or BLOTTER_URL
        tagged = extract_pbso_booking_id(url)
        if is_pbso_index_only(url) and not tagged:
            return None
        if tagged and tagged != str(booking_id).strip():
            return None
        try:
            import requests
            resp = requests.get(
                url,
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                },
            )
            if resp is None or resp.status_code != 200:
                return None
            rows = parse_pbso_html(resp.text)
            wanted = str(booking_id).strip()
            matches = [r for r in rows if str(r.get("booking_num") or "") == wanted]
            if len(matches) != 1:
                return None
            record = self._row_to_arrest_record(matches[0], mode="UPDATE")
            record.LastCheckedMode = "UPDATE"
            return record
        except Exception as e:
            logger.warning("Palm Beach _fetch_single_booking error (%s): %s", booking_id, e)
            return None

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
        return parse_name(name)
