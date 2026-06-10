"""
JailTracker Base Scraper — Reusable for all JailTracker counties
================================================================
Handles the full flow:
  1. Load Blazor WASM app via Playwright (local Chromium, no Obscura needed)
  2. Solve 4-char image CAPTCHA via OpenAI GPT-4o-mini vision
  3. Extract offender roster data from rendered DOM
  4. Normalize to ArrestRecord schema

Counties using JailTracker (omsweb.public-safety-cloud.com):
  - Charlotte_County_FL
  - Manatee_County_FL
  (more to be discovered)

NO Cloudflare protection — direct access from VPS datacenter IP.
"""

import base64
import logging
import os
import re
import time
from datetime import datetime
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

JT_BASE = "https://omsweb.public-safety-cloud.com/jtclientweb"
MAX_CAPTCHA_ATTEMPTS = 3
CAPTCHA_WAIT_S = 3
PAGE_LOAD_WAIT_S = 5
DETAIL_DELAY_S = 1.0


class JailTrackerBaseScraper(BaseScraper):
    """
    Base scraper for counties using the JailTracker (public-safety-cloud.com)
    Blazor WASM inmate roster. Subclasses only need to set county_jt_id and county.
    """

    # Subclasses MUST override these
    county_jt_id: str = ""        # e.g. "Manatee_County_FL"
    facility_name: str = ""       # e.g. "Manatee County Jail"

    @property
    def county(self) -> str:
        raise NotImplementedError("Subclass must define county property")

    def scrape(self) -> List[ArrestRecord]:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)

        try:
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()

            # Capture JSON API responses from Blazor
            api_data = {}

            def on_response(resp):
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    try:
                        key = resp.url.split("/jtclientweb/")[-1]
                        api_data[key] = resp.json()
                    except Exception:
                        pass

            page.on("response", on_response)

            # Load JailTracker with unique session
            session_id = f"shamrock_{int(time.time())}"
            url = f"{JT_BASE}/(S({session_id}))/jailtracker/index/{self.county_jt_id}"
            logger.info(f"[{self.county}] Loading JailTracker: {url}")
            page.goto(url, wait_until="networkidle", timeout=45000)
            time.sleep(CAPTCHA_WAIT_S)

            # Solve CAPTCHA
            if not self._solve_captcha(page, api_data):
                logger.error(f"[{self.county}] Failed to solve CAPTCHA after {MAX_CAPTCHA_ATTEMPTS} attempts")
                return []

            # Wait for roster to load after captcha
            time.sleep(PAGE_LOAD_WAIT_S)

            # Extract offender data from rendered DOM
            records = self._extract_roster(page, api_data)
            logger.info(f"[{self.county}] Scraped {len(records)} records from JailTracker")
            return records

        except Exception as e:
            logger.error(f"[{self.county}] JailTracker scraper error: {e}")
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

    def _solve_captcha(self, page, api_data: dict) -> bool:
        """Solve the 4-character image CAPTCHA using OpenAI vision."""
        for attempt in range(1, MAX_CAPTCHA_ATTEMPTS + 1):
            logger.info(f"[{self.county}] CAPTCHA attempt {attempt}/{MAX_CAPTCHA_ATTEMPTS}")

            # Get captcha image from API data
            captcha_data = api_data.get("captcha/getnewcaptchaclient", {})
            captcha_image_b64 = captcha_data.get("captchaImage", "")

            if not captcha_image_b64:
                # Try getting it from the page
                captcha_img = page.query_selector("img[src*='data:image']")
                if captcha_img:
                    captcha_image_b64 = captcha_img.get_attribute("src") or ""

            if not captcha_image_b64:
                logger.warning(f"[{self.county}] No captcha image found")
                # Click "Get New Code" and retry
                new_code_btn = page.query_selector("button:has-text('Get New Code')")
                if new_code_btn:
                    new_code_btn.click()
                    time.sleep(2)
                continue

            # Extract base64 data
            b64_part = captcha_image_b64.split(",")[1] if "," in captcha_image_b64 else captcha_image_b64

            # Solve with OpenAI vision
            answer = self._ocr_captcha_openai(b64_part)
            if not answer:
                logger.warning(f"[{self.county}] OCR returned empty, retrying")
                self._click_new_code(page, api_data)
                continue

            logger.info(f"[{self.county}] CAPTCHA answer: {answer!r}")

            # Fill in the captcha
            captcha_input = page.query_selector("#captchaCode")
            if captcha_input:
                captcha_input.fill(answer)
            else:
                page.fill("input", answer)

            # Click Validate
            validate_btn = page.query_selector("button:has-text('Validate')")
            if validate_btn:
                validate_btn.click()
            time.sleep(3)

            # Check if we passed
            page_text = page.evaluate("() => document.body?.innerText || ''")
            if "incorrect" in page_text.lower():
                logger.warning(f"[{self.county}] CAPTCHA incorrect, retrying...")
                # Click retry link
                retry = page.query_selector("text=Click Here to Try Again")
                if retry:
                    retry.click()
                    time.sleep(2)
                    # Re-capture the new captcha data
                    api_data.pop("captcha/getnewcaptchaclient", None)
                    time.sleep(1)
                continue
            elif "login" in page_text.lower() and "validate" in page_text.lower():
                logger.warning(f"[{self.county}] Still on captcha page, retrying...")
                self._click_new_code(page, api_data)
                continue
            else:
                logger.info(f"[{self.county}] CAPTCHA solved! ✅")
                return True

        return False

    def _click_new_code(self, page, api_data: dict):
        """Click Get New Code and wait for new captcha."""
        api_data.pop("captcha/getnewcaptchaclient", None)
        new_code_btn = page.query_selector("button:has-text('Get New Code')")
        if new_code_btn:
            new_code_btn.click()
            time.sleep(2)

    def _ocr_captcha_openai(self, image_b64: str) -> str:
        """Use OpenAI GPT-4o-mini vision to read 4-char captcha."""
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            logger.warning(f"[{self.county}] OPENAI_API_KEY not set, cannot solve captcha")
            return ""

        try:
            import httpx
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Read the 4 characters in this CAPTCHA image. "
                                        "Reply with ONLY the 4 characters, nothing else. "
                                        "The characters are case-sensitive and may include "
                                        "uppercase letters, lowercase letters, and digits."
                                    ),
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{image_b64}",
                                    },
                                },
                            ],
                        }
                    ],
                    "max_tokens": 10,
                    "temperature": 0,
                },
                timeout=15,
            )
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"].strip()
            # Clean: only keep alphanumeric chars
            answer = re.sub(r"[^a-zA-Z0-9]", "", answer)
            return answer[:4]  # Ensure max 4 chars
        except Exception as e:
            logger.warning(f"[{self.county}] OpenAI captcha OCR error: {e}")
            return ""

    def _extract_roster(self, page, api_data: dict) -> List[ArrestRecord]:
        """Extract inmate records from the rendered JailTracker roster page."""
        time.sleep(2)

        # The roster displays as a table or card grid
        # First check for offender data in API responses
        for key, val in api_data.items():
            if "offender" in key.lower() and isinstance(val, list) and val:
                logger.info(f"[{self.county}] Found {len(val)} offenders in API response")
                return self._parse_api_offenders(val)

        # Fall back to DOM extraction
        records = self._extract_from_dom(page)
        return records

    def _parse_api_offenders(self, offenders: list) -> List[ArrestRecord]:
        """Parse offender data from JailTracker API JSON response."""
        records = []
        for o in offenders:
            try:
                last_name = (o.get("lastName") or o.get("LastName") or "").strip()
                first_name = (o.get("firstName") or o.get("FirstName") or "").strip()
                middle_name = (o.get("middleName") or o.get("MiddleName") or "").strip()

                full_name = ""
                if last_name and first_name:
                    full_name = f"{last_name}, {first_name}"
                    if middle_name:
                        full_name += f" {middle_name}"

                booking_id = str(
                    o.get("arrestNo") or o.get("ArrestNo") or
                    o.get("bookingNumber") or o.get("BookingNumber") or
                    o.get("id") or o.get("Id") or ""
                ).strip()

                booking_date = (
                    o.get("bookingDate") or o.get("BookingDate") or
                    o.get("arrestDate") or o.get("ArrestDate") or ""
                )
                if booking_date and "T" in str(booking_date):
                    booking_date = str(booking_date).split("T")[0]

                dob = o.get("dob") or o.get("DOB") or o.get("dateOfBirth") or ""
                if dob and "T" in str(dob):
                    dob = str(dob).split("T")[0]

                charges_raw = o.get("charges") or o.get("Charges") or []
                charges_list = []
                total_bond = 0.0
                if isinstance(charges_raw, list):
                    for ch in charges_raw:
                        desc = ch.get("description") or ch.get("chargeDescription") or ""
                        statute = ch.get("statute") or ch.get("statuteCode") or ""
                        charge_text = f"{statute} - {desc}" if statute else desc
                        if charge_text:
                            charges_list.append(charge_text.strip())
                        try:
                            bond = float(str(ch.get("bondAmount") or ch.get("bond") or 0).replace("$", "").replace(",", ""))
                            total_bond += bond
                        except (ValueError, TypeError):
                            pass

                record = ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_id,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    DOB=str(dob),
                    Booking_Date=str(booking_date),
                    Status="In Custody",
                    Release_Date="",
                    Facility=self.facility_name,
                    Race=str(o.get("race") or o.get("Race") or ""),
                    Sex=str(o.get("sex") or o.get("Sex") or o.get("gender") or ""),
                    Height="",
                    Weight="",
                    Address="",
                    City="",
                    State="FL",
                    ZIP="",
                    Mugshot_URL=str(o.get("photoUrl") or o.get("mugshot") or ""),
                    Charges=" | ".join(charges_list) if charges_list else "",
                    Bond_Amount=str(total_bond) if total_bond > 0 else "0",
                    Bond_Paid="NO",
                    Detail_URL=f"{JT_BASE}/Jailtracker/Index/{self.county_jt_id}",
                    LastCheckedMode="INITIAL",
                )
                if record.Full_Name and record.Booking_Number:
                    records.append(record)
            except Exception as e:
                logger.warning(f"[{self.county}] Error parsing offender: {e}")
        return records

    def _extract_from_dom(self, page) -> List[ArrestRecord]:
        """Extract inmate data from rendered DOM (fallback if no API JSON)."""
        records = []

        # Wait for roster table to appear
        try:
            page.wait_for_selector("table, .offender-card, .roster-item", timeout=10000)
        except Exception:
            pass

        time.sleep(2)

        # Try table extraction
        rows_data = page.evaluate("""() => {
            const results = [];
            // Method 1: Table rows
            const rows = document.querySelectorAll('table tbody tr');
            rows.forEach(row => {
                const cells = Array.from(row.querySelectorAll('td'));
                if (cells.length >= 3) {
                    const record = {};
                    cells.forEach((cell, i) => {
                        record['col' + i] = cell.textContent.trim();
                    });
                    // Try to get detail link
                    const link = row.querySelector('a[href], [onclick]');
                    if (link) {
                        record['link'] = link.getAttribute('href') || link.getAttribute('onclick') || '';
                    }
                    results.push(record);
                }
            });

            // Method 2: Card-style layout
            if (results.length === 0) {
                const cards = document.querySelectorAll('.card, .offender, [class*=offender]');
                cards.forEach(card => {
                    const text = card.innerText;
                    const record = {fullText: text};
                    results.push(record);
                });
            }

            // Method 3: Any content with name patterns
            if (results.length === 0) {
                const bodyText = document.body?.innerText || '';
                record = {pageContent: bodyText.substring(0, 5000)};
                results.push(record);
            }

            return results;
        }""")

        if not rows_data:
            logger.warning(f"[{self.county}] No DOM data found after captcha")
            page_text = page.evaluate("() => document.body?.innerText?.substring(0, 1000) || ''")
            logger.info(f"[{self.county}] Page content: {page_text[:500]}")
            return []

        logger.info(f"[{self.county}] Found {len(rows_data)} DOM rows")

        for i, row in enumerate(rows_data):
            try:
                # JailTracker typically shows: Name, Booking#, BookDate, Charges
                # Column mapping depends on agency config
                name = row.get("col0", row.get("fullText", ""))
                booking_num = row.get("col1", str(i))

                if not name or len(name) < 3:
                    continue

                # Parse name (LAST, FIRST MIDDLE format)
                first_name = last_name = middle_name = ""
                if "," in name:
                    parts = name.split(",", 1)
                    last_name = parts[0].strip()
                    rest = parts[1].strip().split()
                    first_name = rest[0] if rest else ""
                    middle_name = rest[1] if len(rest) > 1 else ""
                else:
                    parts = name.split()
                    first_name = parts[0] if parts else ""
                    last_name = parts[-1] if len(parts) > 1 else ""
                    middle_name = " ".join(parts[1:-1]) if len(parts) > 2 else ""

                full_name = f"{last_name}, {first_name}"
                if middle_name:
                    full_name += f" {middle_name}"

                record = ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_num,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    DOB=row.get("col2", ""),
                    Booking_Date=row.get("col3", ""),
                    Status="In Custody",
                    Release_Date="",
                    Facility=self.facility_name,
                    Race="",
                    Sex="",
                    Height="",
                    Weight="",
                    Address="",
                    City="",
                    State="FL",
                    ZIP="",
                    Mugshot_URL="",
                    Charges=row.get("col4", ""),
                    Bond_Amount="0",
                    Bond_Paid="NO",
                    Detail_URL=f"{JT_BASE}/Jailtracker/Index/{self.county_jt_id}",
                    LastCheckedMode="INITIAL",
                )
                if record.Full_Name:
                    records.append(record)
            except Exception as e:
                logger.warning(f"[{self.county}] Error parsing DOM row {i}: {e}")

        return records
