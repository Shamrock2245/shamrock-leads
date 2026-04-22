"""
Hendry County Arrest Scraper — Wix Blog API Interception via DrissionPage.

Source: Hendry County Sheriff's Office inmate search
URL: https://www.hendrysheriff.org/inmateSearch
Method: DrissionPage browser automation with API response interception

Architecture:
1. Load inmate search page → wait for Cloudflare
2. Intercept XHR/Fetch API responses (paginatedBlog JSON)
3. Parse HTML content embedded in API response entries
4. Extract: name, booking info, charges, bond, address, mugshot
5. Handle pagination via "Page Right" button clicks

Note: Hendry's inmate roster is built on the Wix platform, which serves
inmate data through an internal paginatedBlog-style API. The data is
returned as HTML fragments embedded in JSON responses.
"""

import logging
import re
import time
import json
from typing import List, Dict, Any, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
INMATE_SEARCH_URL = "https://www.hendrysheriff.org/inmateSearch"
MAX_PAGES = 50
API_WAIT_TIMEOUT = 15  # seconds to wait for API response


class HendryCountyScraper(BaseScraper):
    """Hendry County (FL) arrest scraper — Wix Blog API interception."""

    @property
    def county(self) -> str:
        return "Hendry"

    def scrape(self) -> List[ArrestRecord]:
        """Scrape Hendry County via DrissionPage + API interception."""
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
        except ImportError:
            logger.error(
                "❌ DrissionPage not installed. "
                "Install with: pip install DrissionPage"
            )
            return []

        page = self._setup_browser()
        records: List[ArrestRecord] = []
        api_responses: List[dict] = []

        try:
            # Set up network listener for API responses
            page.listen.start("paginatedBlog")

            logger.info(f"📡 Loading {INMATE_SEARCH_URL}...")
            page.get(INMATE_SEARCH_URL)
            time.sleep(5)

            # Wait for Cloudflare
            if not self._wait_for_cloudflare(page):
                logger.error("❌ Cloudflare challenge did not clear")
                return []

            # Handle disclaimer
            try:
                agree_btn = page.ele("text:I Agree", timeout=3)
                if agree_btn:
                    agree_btn.click()
                    logger.info("👆 Clicked disclaimer")
                    time.sleep(1)
            except Exception:
                pass

            # Try to sort by newest
            self._try_sort_newest(page)

            # Process pages
            page_num = 1
            session_ids = set()

            while page_num <= MAX_PAGES:
                logger.info(f"📄 Processing page {page_num}...")

                # Wait for API response
                resp_data = self._wait_for_api_response(page)
                if not resp_data:
                    logger.info(f"⚠️ No API data on page {page_num}, stopping")
                    break

                entries = resp_data.get("entries", [])
                if not entries:
                    logger.info(f"⚠️ No entries on page {page_num}, stopping")
                    break

                logger.info(f"📊 Found {len(entries)} entries on page {page_num}")

                # Process each entry
                for i, entry in enumerate(entries):
                    try:
                        record = self._parse_entry(entry)
                        if not record:
                            continue

                        # Dedup within session
                        if record.Booking_Number in session_ids:
                            continue
                        session_ids.add(record.Booking_Number)

                        records.append(record)
                    except Exception as e:
                        logger.warning(f"⚠️ Error parsing entry {i}: {e}")

                # Check for more pages
                pagination = resp_data.get("pagination", {})
                if not pagination.get("next"):
                    logger.info("🏁 No more pages (API)")
                    break

                # Click next page
                if not self._click_next_page(page):
                    break

                page_num += 1

            logger.info(f"✅ Scraped {len(records)} records from Hendry")
            return records

        except Exception as e:
            logger.error(f"❌ Hendry scraper fatal error: {e}")
            return []

        finally:
            try:
                page.listen.stop()
            except Exception:
                pass
            try:
                page.quit()
            except Exception:
                pass

    # ── Browser Setup ──

    @staticmethod
    def _setup_browser():
        """Configure and launch DrissionPage browser."""
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
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        )
        return ChromiumPage(addr_or_opts=co)

    @staticmethod
    def _wait_for_cloudflare(page, max_wait: int = 30) -> bool:
        """Wait for Cloudflare challenge to clear."""
        for attempt in range(max_wait):
            title = page.title.lower() if page.title else ""
            if "just a moment" not in title and "security" not in title:
                logger.info("✅ Page loaded successfully")
                return True
            if attempt % 5 == 0:
                logger.debug(
                    f"⏳ Cloudflare challenge... ({attempt}/{max_wait}s)"
                )
            time.sleep(1)
        return False

    def _try_sort_newest(self, page) -> None:
        """Attempt to sort the inmate list by newest first."""
        try:
            sort_select = page.ele("css:select.form-select, select#sort", timeout=3)
            if sort_select:
                try:
                    sort_select.select("dateDesc")
                    logger.info("✅ Sorted by newest (dateDesc)")
                except Exception:
                    try:
                        sort_select.select("Newest")
                        logger.info("✅ Sorted by newest (label)")
                    except Exception:
                        logger.debug("⚠️ Could not set sort order")
                time.sleep(3)
        except Exception:
            logger.debug("⚠️ Sort select not found")

    def _wait_for_api_response(self, page) -> Optional[dict]:
        """Wait for and capture the paginatedBlog API response."""
        try:
            resp = page.listen.wait(timeout=API_WAIT_TIMEOUT)
            if resp and resp.response:
                try:
                    body = resp.response.body
                    if isinstance(body, str):
                        return json.loads(body)
                    elif isinstance(body, dict):
                        return body
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception as e:
            logger.debug(f"⚠️ API wait error: {e}")
        return None

    def _click_next_page(self, page) -> bool:
        """Click the next page button."""
        try:
            next_btn = page.ele('css:button[aria-label="Page Right"]', timeout=3)
            if next_btn:
                next_btn.click()
                time.sleep(3)
                return True
        except Exception as e:
            logger.debug(f"⚠️ Next page click failed: {e}")
        return False

    # ── Data Parsing ──

    def _parse_entry(self, entry: dict) -> Optional[ArrestRecord]:
        """Parse a single API response entry into an ArrestRecord."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("❌ beautifulsoup4 not installed")
            return None

        html_content = entry.get("content", "")
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, "html.parser")

        # Name from entry metadata
        full_name = entry.get("title", entry.get("titleWithFirst", "")).strip()
        first_name = entry.get("firstName", "").strip()
        last_name = entry.get("lastName", "").strip()

        if not last_name and "," in full_name:
            parts = full_name.split(",", 1)
            last_name = parts[0].strip()
            first_name = parts[1].strip()

        if not full_name:
            return None

        # Booking number
        booking_number = entry.get("inmateID") or self._extract_label(
            soup, "Inmate ID"
        )
        if not booking_number:
            return None

        # Demographics
        booking_date = self._extract_label(soup, "Booked Date") or ""
        sex = self._extract_label(soup, "Gender") or ""
        race = self._extract_label(soup, "Race") or ""
        height = self._extract_label(soup, "Height") or ""
        weight = self._extract_label(soup, "Weight") or ""

        # Address parsing
        address = ""
        city = ""
        state = "FL"
        zip_code = ""
        raw_address = self._extract_label(soup, "Address")
        if raw_address:
            address, city, state, zip_code = self._parse_address(raw_address)

        # Mugshot
        mugshot_url = ""
        images = entry.get("images", [])
        if images and isinstance(images, list) and len(images) > 0:
            img = images[0]
            if isinstance(img, dict):
                mugshot_url = img.get("large", img.get("small", ""))

        # Charges & Bond
        charges_list = []
        total_bond = 0.0

        charge_tags = soup.find_all(
            string=re.compile("Charge Description:", re.IGNORECASE)
        )
        for ctag in charge_tags:
            try:
                desc = str(ctag).split("Charge Description:", 1)[-1].strip()
                clean_desc = self._clean_charge_text(desc)
                if clean_desc:
                    charges_list.append(clean_desc)
            except Exception:
                pass

        bond_tags = soup.find_all(
            string=re.compile("Bond Amount:", re.IGNORECASE)
        )
        for btag in bond_tags:
            try:
                bond_text = (
                    str(btag)
                    .split("Bond Amount:", 1)[-1]
                    .strip()
                    .replace("$", "")
                    .replace(",", "")
                    .strip()
                )
                total_bond += float(bond_text)
            except (ValueError, TypeError):
                pass

        charges_str = " | ".join(charges_list) if charges_list else ""

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_number,
            Full_Name=full_name,
            First_Name=first_name,
            Last_Name=last_name,
            DOB="",  # Not available in Hendry's roster
            Booking_Date=booking_date,
            Status="In Custody",
            Facility="Hendry County Jail",
            Race=race,
            Sex=sex,
            Height=height,
            Weight=weight,
            Address=address,
            City=city,
            State=state,
            ZIP=zip_code,
            Mugshot_URL=mugshot_url,
            Charges=charges_str,
            Bond_Amount=str(total_bond) if total_bond > 0 else "0",
            Bond_Paid="NO",
            Detail_URL=INMATE_SEARCH_URL,
            LastCheckedMode="INITIAL",
        )

    # ── Utilities ──

    @staticmethod
    def _extract_label(soup, label_text: str) -> Optional[str]:
        """Extract a value following a label in the HTML."""
        # Try <b> or <strong> tags first
        for tag_name in ["b", "strong"]:
            tag = soup.find(
                tag_name, string=re.compile(label_text, re.IGNORECASE)
            )
            if tag and tag.next_sibling:
                val = str(tag.next_sibling).strip().replace(":", "").strip()
                if val:
                    return val

        # Fallback: search all text nodes
        for node in soup.find_all(string=True):
            if label_text.lower() in node.lower():
                val = (
                    str(node)
                    .split(label_text, 1)[-1]
                    .strip()
                    .replace(":", "", 1)
                    .strip()
                )
                if val:
                    return val

        return None

    @staticmethod
    def _parse_address(raw: str) -> tuple:
        """Parse a raw address string into (address, city, state, zip)."""
        if not raw:
            return ("", "", "FL", "")

        clean = re.sub(r"\s+", " ", raw).strip()
        zip_code = ""
        state = "FL"
        city = ""
        address = clean

        # Extract ZIP
        zip_match = re.search(r"\b(\d{5})\b$", clean)
        if zip_match:
            zip_code = zip_match.group(1)
            clean = clean[: zip_match.start()].strip()

        # Extract state
        state_match = re.search(r"\b([A-Z]{2})\b$", clean)
        if state_match:
            state = state_match.group(1)
            clean = clean[: state_match.start()].strip().rstrip(",")

        # Extract city
        if "," in clean:
            parts = clean.rsplit(",", 1)
            city = parts[1].strip()
            address = parts[0].strip()

        return (address, city, state, zip_code)

    @staticmethod
    def _clean_charge_text(raw_charge: str) -> str:
        """Clean charge text to extract human-readable description."""
        if not raw_charge:
            return ""
        text = re.sub(
            r"^(New Charge:|Weekender:|Charge Description:)\s*",
            "",
            raw_charge,
            flags=re.IGNORECASE,
        )
        # Extract text after statute number and dash
        match = re.search(r"[\d.]+[a-z]*\s*-\s*(.+)", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # Remove leading statute number
        text = re.sub(r"^[\d.]+[a-z]*\s*", "", text)
        # Remove degree indicators
        text = re.sub(r"\s*\([A-Z]\d?\)\s*$", "", text)
        return text.strip()
