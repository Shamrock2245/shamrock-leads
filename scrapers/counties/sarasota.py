"""
Sarasota County Arrest Scraper — Revize CMS via SOCKS Proxy
============================================================
Source: Sarasota County Sheriff's Office
URL:    https://cms.revize.com/revize/apps/sarasota/index.php
        (embedded in https://www.sarasotasheriff.org/arrest-reports/index.php)
Method: Playwright + SOCKS5 proxy (office iMac residential IP)

TWO-PHASE STRATEGY:
  Phase 1 — Navigate to the Revize CMS main page, expand the "SELECT AN INMATE"
            dropdown, and extract the full roster list (name, DOB, detail URL).
  Phase 2 — Visit each detail page (viewInmate.php?id=XXX) to extract charges,
            bond amounts, booking dates, demographics, and case numbers.

The Revize CMS domain (cms.revize.com) is behind Cloudflare Turnstile.
The SOCKS5 proxy routes traffic through the office iMac's residential IP,
which passes Turnstile automatically (same proven pattern as Charlotte/Manatee).

HISTORY:
- v1: JailTracker Blazor WASM — dead (global 400 error on all JailTracker counties)
- v2 (current): Revize CMS via Playwright + SOCKS5 — full data: charges, bonds, demographics
"""
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Revize CMS URLs ──
REVIZE_BASE = "https://cms.revize.com/revize/apps/sarasota"
MAIN_URL = f"{REVIZE_BASE}/index.php"
DETAIL_URL_TPL = f"{REVIZE_BASE}/viewInmate.php?id={{inmate_id}}"

# ── Proxy & Limits ──
SOCKS_PROXY = "socks5://172.18.0.1:1080"
DETAIL_DELAY_S = 1.0          # Polite delay between detail page visits
MAX_INMATES = 1500             # Safety cap (typical population ~600-800)
CF_WAIT_S = 8                  # Wait for Cloudflare Turnstile to auto-solve
PAGE_LOAD_TIMEOUT = 30000      # 30s page load timeout


class SarasotaCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Sarasota"

    def scrape(self) -> List[ArrestRecord]:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            proxy={"server": SOCKS_PROXY},
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

            # ── Phase 1: Load Main Page + Extract Roster ──
            logger.info("[Sarasota] Phase 1: Loading Revize CMS main page")
            page.goto(MAIN_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            time.sleep(CF_WAIT_S)  # Let Cloudflare Turnstile auto-solve

            # Check for Cloudflare block
            title = (page.title() or "").lower()
            if "just a moment" in title or "attention" in title:
                logger.warning("[Sarasota] Cloudflare challenge detected, waiting longer...")
                time.sleep(10)
                title = (page.title() or "").lower()
                if "just a moment" in title:
                    logger.error("[Sarasota] Cloudflare blocked — cannot proceed")
                    return []

            # Wait for the inmate dropdown to appear
            try:
                page.wait_for_selector(
                    "button.dropdown-toggle, .dropdown-menu a[href*='viewInmate']",
                    timeout=15000
                )
            except Exception:
                logger.warning("[Sarasota] Dropdown not found yet, trying to click it")

            # Click the "SELECT AN INMATE" button to expand dropdown
            dropdown_btn = page.query_selector("button.dropdown-toggle")
            if dropdown_btn:
                dropdown_btn.click()
                time.sleep(2)
                logger.info("[Sarasota] Expanded inmate dropdown")
            else:
                logger.warning("[Sarasota] No dropdown button found — trying direct extraction")

            # Extract all inmate links from the dropdown
            roster = page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="viewInmate.php"]');
                return Array.from(links).map(a => ({
                    text: a.textContent.trim(),
                    href: a.href,
                }));
            }""")

            if not roster:
                logger.error("[Sarasota] No inmate links found in dropdown")
                return []

            logger.info(f"[Sarasota] Found {len(roster)} inmates in roster dropdown")

            # Parse roster entries: "LASTNAME,FIRSTNAME MIDDLE - MM/DD/YYYY"
            inmates: List[Dict[str, str]] = []
            for entry in roster[:MAX_INMATES]:
                parsed = self._parse_roster_entry(entry["text"], entry["href"])
                if parsed:
                    inmates.append(parsed)

            logger.info(f"[Sarasota] Parsed {len(inmates)} valid roster entries")

            # ── Phase 2: Visit Detail Pages ──
            logger.info("[Sarasota] Phase 2: Visiting detail pages for full data")
            records: List[ArrestRecord] = []
            seen_bookings = set()

            for i, inmate in enumerate(inmates):
                try:
                    detail_records = self._extract_detail(
                        page, inmate, seen_bookings
                    )
                    records.extend(detail_records)

                    if (i + 1) % 50 == 0:
                        logger.info(
                            f"[Sarasota] Progress: {i + 1}/{len(inmates)} inmates, "
                            f"{len(records)} records"
                        )

                    time.sleep(DETAIL_DELAY_S)

                except Exception as e:
                    logger.warning(
                        f"[Sarasota] Error on inmate {inmate.get('name', '?')}: {e}"
                    )
                    continue

            logger.info(f"[Sarasota] Scraped {len(records)} records from {len(inmates)} inmates 🧦")
            return records

        except Exception as e:
            logger.error(f"[Sarasota] Fatal error: {e}")
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

    # ── Roster Entry Parser ──
    @staticmethod
    def _parse_roster_entry(text: str, href: str) -> Optional[Dict[str, str]]:
        """
        Parse a roster dropdown entry.
        Format: "LASTNAME,FIRSTNAME MIDDLE - MM/DD/YYYY"
        URL:    https://cms.revize.com/revize/apps/sarasota/viewInmate.php?id=0201029792
        """
        if not text or "viewInmate" not in href:
            return None

        # Extract inmate ID from URL
        id_match = re.search(r'id=(\d+)', href)
        if not id_match:
            return None
        inmate_id = id_match.group(1)

        # Split name and DOB: "LASTNAME,FIRSTNAME MIDDLE - MM/DD/YYYY"
        parts = text.rsplit(" - ", 1)
        name_part = parts[0].strip()
        dob_part = parts[1].strip() if len(parts) > 1 else ""

        # Parse name: "LASTNAME,FIRSTNAME MIDDLE"
        name_pieces = name_part.split(",", 1)
        last_name = name_pieces[0].strip()
        rest = name_pieces[1].strip() if len(name_pieces) > 1 else ""

        first_name = ""
        middle_name = ""
        if rest:
            name_tokens = rest.split()
            first_name = name_tokens[0] if name_tokens else ""
            middle_name = " ".join(name_tokens[1:]) if len(name_tokens) > 1 else ""

        full_name = f"{last_name}, {first_name}"
        if middle_name:
            full_name = f"{last_name}, {first_name} {middle_name}"

        return {
            "inmate_id": inmate_id,
            "name": name_part,
            "full_name": full_name,
            "first_name": first_name,
            "middle_name": middle_name,
            "last_name": last_name,
            "dob": dob_part,
            "detail_url": href,
        }

    # ── Detail Page Extractor ──
    def _extract_detail(
        self,
        page: Any,
        inmate: Dict[str, str],
        seen_bookings: set,
    ) -> List[ArrestRecord]:
        """
        Navigate to a detail page and extract full arrest data.

        Detail page structure (viewInmate.php?id=XXX):
          Personal Information:
            - PIN (same as inmate_id)
            - Date of Birth
            - Race, Sex
            - Location (cell/unit)
          Criminal Charges Information (table):
            - Booking Number | Offense Description | Counts | Arraign Date
            - Bond Amount | Bond Type | Intake Date/Time | Court Case Number
            - Release Date/Time | Hold
        """
        detail_url = inmate["detail_url"]
        inmate_id = inmate["inmate_id"]

        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            time.sleep(1.5)

            # Check for Cloudflare
            title = (page.title() or "").lower()
            if "just a moment" in title:
                logger.warning(f"[Sarasota] CF challenge on detail page {inmate_id}")
                time.sleep(CF_WAIT_S)
                title = (page.title() or "").lower()
                if "just a moment" in title:
                    return []

            # Extract all data from the page
            data = page.evaluate("""() => {
                const result = {
                    personal: {},
                    charges: [],
                };

                // Get all text nodes
                const allText = document.body.innerText || "";
                const lines = allText.split('\\n').map(l => l.trim()).filter(Boolean);

                // Extract personal info fields
                const personalFields = ['PIN:', 'Date of Birth:', 'Race:', 'Sex:', 'Location:'];
                for (let i = 0; i < lines.length; i++) {
                    for (const field of personalFields) {
                        if (lines[i] === field && i + 1 < lines.length) {
                            result.personal[field.replace(':', '')] = lines[i + 1];
                        }
                    }
                }

                // Extract charges from the table structure
                // The table has headers: Booking Number, Offense Description, Counts,
                // Arraign Date, Bond Amount, Bond Type, Intake Date/Time,
                // Court Case Number, Release Date/Time, Hold
                const tables = document.querySelectorAll('table');
                for (const table of tables) {
                    const rows = table.querySelectorAll('tr');
                    for (const row of rows) {
                        const cells = Array.from(row.querySelectorAll('td'));
                        if (cells.length >= 8) {
                            result.charges.push({
                                booking_number: cells[0]?.textContent?.trim() || '',
                                offense: cells[1]?.textContent?.trim() || '',
                                counts: cells[2]?.textContent?.trim() || '',
                                arraign_date: cells[3]?.textContent?.trim() || '',
                                bond_amount: cells[4]?.textContent?.trim() || '',
                                bond_type: cells[5]?.textContent?.trim() || '',
                                intake_datetime: cells[6]?.textContent?.trim() || '',
                                case_number: cells[7]?.textContent?.trim() || '',
                                release_datetime: cells[8]?.textContent?.trim() || '',
                                hold: cells[9]?.textContent?.trim() || '',
                            });
                        }
                    }
                }

                // Fallback: if no table found, try parsing from text structure
                if (result.charges.length === 0) {
                    // Look for booking number pattern (10+ digits)
                    const bookingPattern = /\\b(\\d{12,})\\b/g;
                    let match;
                    while ((match = bookingPattern.exec(allText)) !== null) {
                        result.charges.push({
                            booking_number: match[1],
                            offense: '',
                            counts: '',
                            arraign_date: '',
                            bond_amount: '',
                            bond_type: '',
                            intake_datetime: '',
                            case_number: '',
                            release_datetime: '',
                            hold: '',
                        });
                    }
                }

                return result;
            }""")

            if not data:
                return []

            personal = data.get("personal", {})
            charges_list = data.get("charges", [])

            # Get demographic data from personal info
            race = personal.get("Race", "")
            sex = personal.get("Sex", "")
            pin = personal.get("PIN", inmate_id)

            records: List[ArrestRecord] = []

            if charges_list:
                # Group charges by booking number to create one record per booking
                bookings: Dict[str, List[Dict]] = {}
                for charge in charges_list:
                    bn = charge.get("booking_number", "").strip()
                    if not bn:
                        bn = pin  # Fallback to PIN
                    if bn not in bookings:
                        bookings[bn] = []
                    bookings[bn].append(charge)

                for booking_num, charges in bookings.items():
                    dedup_key = f"Sarasota:{booking_num}"
                    if dedup_key in seen_bookings:
                        continue
                    seen_bookings.add(dedup_key)

                    # Combine charges
                    charge_descriptions = []
                    total_bond = 0.0
                    bond_type = ""
                    intake_datetime = ""
                    case_number = ""
                    arraign_date = ""
                    release_datetime = ""
                    has_hold = False

                    for c in charges:
                        offense = c.get("offense", "").strip()
                        if offense:
                            charge_descriptions.append(offense)

                        # Parse bond amount
                        bond_str = c.get("bond_amount", "")
                        bond_val = self._parse_bond_amount(bond_str)
                        if bond_val is not None:
                            total_bond += bond_val

                        # Get bond type (use first non-empty)
                        bt = c.get("bond_type", "").strip()
                        if bt and not bond_type:
                            bond_type = bt

                        # Get intake datetime (use first non-empty)
                        idt = c.get("intake_datetime", "").strip()
                        if idt and not intake_datetime:
                            intake_datetime = idt

                        # Get case number (use first non-empty)
                        cn = c.get("case_number", "").strip()
                        if cn and not case_number:
                            case_number = cn

                        # Get arraign date (use first non-empty)
                        ad = c.get("arraign_date", "").strip()
                        if ad and not arraign_date:
                            arraign_date = ad

                        # Get release date (use first non-empty)
                        rd = c.get("release_datetime", "").strip()
                        if rd and not release_datetime:
                            release_datetime = rd

                        if c.get("hold", "").strip().upper() == "Y":
                            has_hold = True

                    # Determine status
                    status = "In Custody"
                    if release_datetime:
                        status = "Released"

                    # Parse dates
                    booking_date, booking_time = self._parse_datetime(intake_datetime)
                    arrest_date = booking_date or self._parse_date(arraign_date)
                    release_date_str = self._parse_datetime(release_datetime)[0] if release_datetime else ""

                    # Check for "No Bond" in charges
                    if "No Bond" in str(charges) or has_hold:
                        bond_type = bond_type or "No Bond"

                    records.append(ArrestRecord(
                        County="Sarasota",
                        Booking_Number=booking_num,
                        Person_ID=pin,
                        Full_Name=inmate["full_name"],
                        First_Name=inmate["first_name"],
                        Middle_Name=inmate["middle_name"],
                        Last_Name=inmate["last_name"],
                        DOB=self._parse_date(inmate["dob"]) or "",
                        Arrest_Date=arrest_date or "",
                        Arrest_Time="",
                        Booking_Date=booking_date or "",
                        Booking_Time=booking_time or "",
                        Status=status,
                        Release_Date=release_date_str or "",
                        Facility="Sarasota County Jail",
                        Race=race,
                        Sex=sex,
                        Charges=" | ".join(charge_descriptions),
                        Bond_Amount=str(total_bond) if total_bond > 0 else "0",
                        Bond_Type=bond_type,
                        Case_Number=case_number,
                        Court_Date=self._parse_date(arraign_date) or "",
                        Detail_URL=detail_url,
                    ))
            else:
                # No charges found — create a minimal record from roster data
                dedup_key = f"Sarasota:{pin}"
                if dedup_key not in seen_bookings:
                    seen_bookings.add(dedup_key)
                    records.append(ArrestRecord(
                        County="Sarasota",
                        Booking_Number=pin,
                        Person_ID=pin,
                        Full_Name=inmate["full_name"],
                        First_Name=inmate["first_name"],
                        Middle_Name=inmate["middle_name"],
                        Last_Name=inmate["last_name"],
                        DOB=self._parse_date(inmate["dob"]) or "",
                        Status="In Custody",
                        Facility="Sarasota County Jail",
                        Race=race,
                        Sex=sex,
                        Detail_URL=detail_url,
                    ))

            return records

        except Exception as e:
            logger.warning(f"[Sarasota] Failed to extract detail for {inmate_id}: {e}")
            return []

    # ── Utilities ──
    @staticmethod
    def _parse_bond_amount(text: str) -> Optional[float]:
        """Parse bond amount from strings like '$2,500.00', 'No Bond Available'."""
        if not text:
            return None
        text = text.strip()
        if "no bond" in text.lower() or "n/a" in text.lower():
            return 0.0
        # Remove $ and commas
        clean = re.sub(r'[,$]', '', text)
        try:
            return float(clean)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_date(text: str) -> Optional[str]:
        """Parse date from various formats to YYYY-MM-DD."""
        if not text:
            return None
        text = text.strip()
        for fmt in ["%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%y"]:
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return text if text else None

    @staticmethod
    def _parse_datetime(text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse datetime string like '2025-11-20 06:50:47.000' into (date, time).
        Returns (YYYY-MM-DD, HH:MM) or (None, None).
        """
        if not text or not text.strip():
            return None, None
        text = text.strip()

        # Try ISO-like: "2025-11-20 06:50:47.000"
        for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                dt = datetime.strptime(text, fmt)
                return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
            except ValueError:
                continue

        # Try US format: "11/20/2025 06:50"
        for fmt in ["%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y"]:
            try:
                dt = datetime.strptime(text, fmt)
                return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
            except ValueError:
                continue

        return None, None
