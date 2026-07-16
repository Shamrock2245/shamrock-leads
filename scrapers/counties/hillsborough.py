"""
Hillsborough County Arrest Scraper — HCSO Arrest Inquiry Portal.
================================================================
Source: Hillsborough County Sheriff's Office
URL: https://webapps.hcso.tampa.fl.us/arrestinquiry/
Method: Pure HTTP (httpx) + SOCKS5 proxy + SolveCaptcha reCAPTCHA v2

Zero browser needed — uses direct form POST with token injection.
Memory-safe: ~50MB vs ~500MB+ for Playwright/Chromium.

Requires env vars:
  HCSO_EMAIL       — login email
  HCSO_PASSWORD    — login password
  SOLVECAPTCHA_KEY — SolveCaptcha API key (for reCAPTCHA v2 bypass)

HISTORY:
  v1: DrissionPage + reCAPTCHA checkbox click (unreliable)
  v2: Playwright + SOCKS proxy + SolveCaptcha token injection (OOM crashes)
  v3 (current): Pure httpx + SolveCaptcha — no browser at all
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://webapps.hcso.tampa.fl.us/arrestinquiry"
LOGIN_URL = f"{BASE_URL}/Account/Login"
SEARCH_URL = f"{BASE_URL}/"
RECAPTCHA_SITEKEY = "6LcK1HopAAAAAEZgVeXqiN2_4zp6cQwRRXfc3uKJ"
DAYS_BACK = 90
MAX_PAGES = 20
COOKIE_FILE = "/tmp/hcso_cookies.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class HillsboroughCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Hillsborough"

    def scrape(self) -> List[ArrestRecord]:
        hcso_email = os.getenv("HCSO_EMAIL")
        hcso_password = os.getenv("HCSO_PASSWORD")
        if not hcso_email or not hcso_password:
            logger.warning("HCSO_EMAIL / HCSO_PASSWORD not set")
            return []

        proxy_url = None
        proxy_source = "none"
        client = None
        try:
            from scrapers.socks_proxy import resolve_residential_proxy

            # APE/Warren → office SOCKS → direct residential (when host is home ISP)
            proxy_url, proxy_source = resolve_residential_proxy(self)
            logger.info("[Hillsborough] proxy source=%s", proxy_source)

            client_kwargs = {
                "headers": HEADERS,
                "follow_redirects": True,
                "timeout": 30.0,
                "verify": True,
            }
            if proxy_url:
                client_kwargs["proxy"] = proxy_url
            client = httpx.Client(**client_kwargs)
            t0 = time.time()

            # Step 1: Try cookie login
            if not self._try_cookie_login(client):
                # Step 2: Fresh login
                if not self._login(client, hcso_email, hcso_password):
                    logger.error("[Hillsborough] Login failed")
                    if proxy_source == "ape":
                        self.record_proxy_failure(proxy_url)
                    return []
                self._save_cookies(client)

            # Step 3: Search
            results_html = self._perform_search(client)
            if not results_html:
                logger.warning("[Hillsborough] Search returned no results")
                return []

            # Step 4: Parse results + paginate
            all_records: List[ArrestRecord] = []
            seen_bookings = set()
            page_num = 1
            current_html = results_html

            while page_num <= MAX_PAGES:
                soup = BeautifulSoup(current_html, "html.parser")
                page_records = self._parse_results_table(soup)
                if not page_records:
                    break

                # Dedup
                new_records = []
                for r in page_records:
                    if r.Booking_Number not in seen_bookings:
                        seen_bookings.add(r.Booking_Number)
                        new_records.append(r)

                if not new_records:
                    logger.info(f"[Hillsborough] Page {page_num}: all duplicates, stopping")
                    break

                all_records.extend(new_records)
                logger.info(
                    f"[Hillsborough] Page {page_num}: +{len(new_records)} new "
                    f"(total: {len(all_records)})"
                )

                # Try next page
                next_html = self._get_next_page(client, soup, page_num)
                if not next_html:
                    break

                current_html = next_html
                page_num += 1

            logger.info(
                f"Hillsborough: {len(all_records)} unique records total "
                f"(proxy={proxy_source})"
            )
            if all_records and proxy_source == "ape":
                self.record_proxy_success(proxy_url, (time.time() - t0) * 1000)
            return all_records

        except Exception as e:
            logger.error(f"Hillsborough fatal: {e}")
            try:
                if proxy_source == "ape":
                    self.record_proxy_failure(proxy_url)
            except Exception:
                pass
            raise
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    # ── Login ──────────────────────────────────────────────────────────
    def _login(self, client: httpx.Client, email: str, password: str) -> bool:
        logger.info("[Hillsborough] Loading login page...")

        # GET login page → extract __RequestVerificationToken
        resp = client.get(LOGIN_URL)
        if resp.status_code != 200:
            logger.error(f"[Hillsborough] Login page returned {resp.status_code}")
            return False

        soup = BeautifulSoup(resp.text, "html.parser")
        token = self._extract_verification_token(soup)
        if not token:
            logger.error("[Hillsborough] Could not find __RequestVerificationToken")
            return False

        # Solve reCAPTCHA via SolveCaptcha API
        recaptcha_token = self._solve_recaptcha_api(LOGIN_URL)
        if not recaptcha_token:
            logger.warning("[Hillsborough] reCAPTCHA not solved for login")

        # POST login form
        form_data = {
            "__RequestVerificationToken": token,
            "Email": email,
            "Password": password,
            "RememberMe": "true",
            "g-recaptcha-response": recaptcha_token or "",
        }

        resp = client.post(LOGIN_URL, data=form_data)

        # Check if login succeeded (redirects to search page)
        if resp.status_code == 200 and ("Log Off" in resp.text or "Welcome" in resp.text):
            logger.info("[Hillsborough] Login successful ✅")
            return True

        # Check URL — successful login redirects away from /Account/Login
        if "Login" not in str(resp.url):
            logger.info(f"[Hillsborough] Login successful (redirected to {resp.url}) ✅")
            return True

        logger.warning(f"[Hillsborough] Login failed. Status: {resp.status_code}, URL: {resp.url}")
        return False

    # ── Cookie Persistence ────────────────────────────────────────────
    def _try_cookie_login(self, client: httpx.Client) -> bool:
        """Load saved cookies and check if session is still valid."""
        if not os.path.exists(COOKIE_FILE):
            return False

        try:
            with open(COOKIE_FILE, "r") as f:
                cookies = json.load(f)

            if not cookies:
                return False

            # Check cookie age
            file_age = time.time() - os.path.getmtime(COOKIE_FILE)
            if file_age > 86400 * 7:  # 7 days
                logger.info("[Hillsborough] Saved cookies expired (>7 days)")
                os.remove(COOKIE_FILE)
                return False

            # Set cookies on client
            for name, value in cookies.items():
                client.cookies.set(name, value)

            logger.info(f"[Hillsborough] Loaded {len(cookies)} saved cookies")

            # Test if session is valid
            resp = client.get(SEARCH_URL)
            if resp.status_code == 200 and ("Log Off" in resp.text or "Welcome" in resp.text):
                logger.info("[Hillsborough] Cookie login successful ✅ (skipped reCAPTCHA!)")
                return True

            logger.info("[Hillsborough] Cookie session invalid")
            try:
                os.remove(COOKIE_FILE)
            except Exception:
                pass
            # Clear invalid cookies
            client.cookies.clear()
            return False

        except Exception as e:
            logger.warning(f"[Hillsborough] Cookie load error: {e}")
            return False

    def _save_cookies(self, client: httpx.Client):
        """Save session cookies for future runs."""
        try:
            cookies = {name: value for name, value in client.cookies.items()}
            if cookies:
                with open(COOKIE_FILE, "w") as f:
                    json.dump(cookies, f)
                logger.info(f"[Hillsborough] Saved {len(cookies)} cookies to {COOKIE_FILE}")
        except Exception as e:
            logger.warning(f"[Hillsborough] Cookie save error: {e}")

    # ── Search ─────────────────────────────────────────────────────────
    def _perform_search(self, client: httpx.Client) -> Optional[str]:
        """Submit search form and return results HTML."""
        logger.info("[Hillsborough] Performing search...")

        # GET search page → extract verification token
        resp = client.get(SEARCH_URL)
        if resp.status_code != 200:
            logger.error(f"[Hillsborough] Search page returned {resp.status_code}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        token = self._extract_verification_token(soup)

        # Solve search reCAPTCHA
        recaptcha_token = self._solve_recaptcha_api(SEARCH_URL)
        if not recaptcha_token:
            logger.warning("[Hillsborough] Search reCAPTCHA not solved")

        # Build search form data — use 90-day lookback for booking date
        # Site requires at least one field (Name, Booking#, BookingDate, or ReleaseDate)
        booking_date = (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%m/%d/%Y")
        logger.info(f"[Hillsborough] Search: current inmates, booking date >= {booking_date}")

        form_data = {
            "SearchBookingNumber": "",
            "SearchName": "",
            "SearchBookingDate": booking_date,
            "SearchReleaseDate": "",
            "SearchCurrentInmatesOnly": "true",
            "SearchIncludeDetails": "true",
            "SortOrder": "BookDate",
            "g-recaptcha-response": recaptcha_token or "",
        }
        if token:
            form_data["__RequestVerificationToken"] = token

        # POST search
        resp = client.post(SEARCH_URL, data=form_data)

        if resp.status_code != 200:
            logger.warning(f"[Hillsborough] Search POST returned {resp.status_code}")
            return None

        # Check for results
        html = resp.text

        # Check for failures FIRST (before success — "Booking" appears in form labels too)
        if "Captcha validation has failed" in html:
            logger.error("[Hillsborough] Search failed: Captcha validation failed")
            return None

        if "must be entered to perform a search" in html:
            logger.error("[Hillsborough] Search failed: required field missing")
            return None

        # Now check for actual results table
        if "table-striped" in html:
            # Double-check it's a results table, not just the form
            from bs4 import BeautifulSoup as BS
            check_soup = BS(html, "html.parser")
            results_table = check_soup.find("table", class_="table-striped")
            if results_table:
                rows = results_table.find_all("tr")
                logger.info(f"[Hillsborough] Search returned results ✅ ({len(rows)} rows)")
                return html

        logger.warning(f"[Hillsborough] Search returned no results table (len={len(html)})")
        return html

    # ── Pagination ─────────────────────────────────────────────────────
    def _get_next_page(self, client: httpx.Client, soup: BeautifulSoup, current_page: int) -> Optional[str]:
        """Navigate to next results page via POST (pagination is JS-based)."""
        # HCSO uses JavaScript void(0) pagination buttons with class 'btn-pager'
        # We need to POST the form again with page number
        pager_links = soup.find_all("a", class_="btn-pager")
        has_next = False
        for link in pager_links:
            text = link.get_text(strip=True)
            if text == str(current_page + 1) or text.startswith("Next"):
                has_next = True
                break

        if not has_next:
            logger.info(f"[Hillsborough] No page {current_page + 1} available, stopping")
            return None

        # POST with page parameter
        token_input = soup.find("input", {"name": "__RequestVerificationToken"})
        token = token_input.get("value", "") if token_input else ""

        form_data = {
            "SearchBookingNumber": "",
            "SearchName": "",
            "SearchBookingDate": (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%m/%d/%Y"),
            "SearchReleaseDate": "",
            "SearchCurrentInmatesOnly": "true",
            "SearchIncludeDetails": "true",
            "SortOrder": "BookDate",
            "page": str(current_page + 1),
            "g-recaptcha-response": "",
        }
        if token:
            form_data["__RequestVerificationToken"] = token

        resp = client.post(SEARCH_URL, data=form_data)
        if resp.status_code == 200 and "table-striped" in resp.text:
            logger.info(f"[Hillsborough] Loaded page {current_page + 1}")
            return resp.text

        # Also try GET with page param
        resp = client.get(f"{SEARCH_URL}?page={current_page + 1}")
        if resp.status_code == 200 and "table-striped" in resp.text:
            logger.info(f"[Hillsborough] Loaded page {current_page + 1} (GET)")
            return resp.text

        logger.info(f"[Hillsborough] Could not load page {current_page + 1}")
        return None

    # ── SolveCaptcha API ───────────────────────────────────────────────
    def _solve_recaptcha_api(self, page_url: str) -> Optional[str]:
        """Solve reCAPTCHA v2 via SolveCaptcha API. Returns token string."""
        api_key = os.getenv("SOLVECAPTCHA_KEY", "")
        if not api_key:
            logger.info("[Hillsborough] No SOLVECAPTCHA_KEY set")
            return None

        logger.info("[Hillsborough] Solving reCAPTCHA via SolveCaptcha API...")
        try:
            # Step 1: Submit task
            submit_resp = httpx.post(
                "https://api.solvecaptcha.com/in.php",
                data={
                    "key": api_key,
                    "method": "userrecaptcha",
                    "googlekey": RECAPTCHA_SITEKEY,
                    "pageurl": page_url,
                    "json": "1",
                },
                timeout=30,
            )
            submit_data = submit_resp.json()
            if submit_data.get("status") != 1:
                logger.error(f"[Hillsborough] SolveCaptcha submit failed: {submit_data}")
                return None

            task_id = submit_data["request"]
            logger.info(f"[Hillsborough] SolveCaptcha task: {task_id}")

            # Step 2: Poll for result (up to 180s)
            http_errors = 0
            for i in range(36):
                time.sleep(5)
                try:
                    result_resp = httpx.get(
                        "https://api.solvecaptcha.com/res.php",
                        params={
                            "key": api_key,
                            "action": "get",
                            "id": task_id,
                            "json": "1",
                        },
                        timeout=15,
                    )
                    if result_resp.status_code != 200:
                        http_errors += 1
                        logger.warning(f"[Hillsborough] SolveCaptcha HTTP {result_resp.status_code} (attempt {http_errors})")
                        if http_errors >= 3:
                            return None
                        continue
                    result_data = result_resp.json()
                except Exception as e:
                    http_errors += 1
                    logger.warning(f"[Hillsborough] SolveCaptcha poll error ({http_errors}): {e}")
                    if http_errors >= 3:
                        return None
                    continue

                if result_data.get("status") == 1:
                    token = result_data["request"]
                    logger.info(f"[Hillsborough] Token received in {(i+1)*5}s ✅")
                    return token
                elif "CAPCHA_NOT_READY" in str(result_data.get("request", "")):
                    continue
                else:
                    logger.error(f"[Hillsborough] SolveCaptcha error: {result_data}")
                    return None

            logger.error("[Hillsborough] SolveCaptcha timeout (180s)")
            return None

        except Exception as e:
            logger.error(f"[Hillsborough] SolveCaptcha error: {e}")
            return None

    # ── Helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _extract_verification_token(soup: BeautifulSoup) -> Optional[str]:
        """Extract ASP.NET __RequestVerificationToken from form."""
        token_input = soup.find("input", {"name": "__RequestVerificationToken"})
        if token_input:
            return token_input.get("value", "")
        return None

    # ── Parsing ────────────────────────────────────────────────────────
    def _parse_results_table(self, soup):
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
                # Skip separator rows
                cls = row.get("class", [])
                if "table-separator" in cls:
                    i += 1
                    continue

                cells = row.find_all("td", recursive=False)
                if len(cells) >= 5:
                    name_link = cells[0].find("a")
                    if name_link:
                        record = self._parse_inmate_block(
                            all_rows, i, cells, name_link
                        )
                        if record:
                            records.append(record)
                        # Skip remaining rows for this inmate block
                        # Structure: name(i) → address(i+1) → dates(i+2) → charges(i+3) → separator(i+4)
                        i += 5
                        continue
                i += 1
            except Exception:
                i += 1
        return records

    def _parse_inmate_block(self, all_rows, i, cells, name_link):
        full_name = name_link.get_text(strip=True)
        first_name, middle_name, last_name = self._parse_name(full_name)
        href = name_link.get("href", "")
        if href and not href.startswith("http"):
            href = "https://webapps.hcso.tampa.fl.us" + href
        booking_number = cells[1].get_text(strip=True)
        agency = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        demo = cells[4].get_text(strip=True)
        demo_parts = [p.strip() for p in demo.split("/")]
        race = demo_parts[0] if len(demo_parts) >= 1 else ""
        sex = demo_parts[1] if len(demo_parts) >= 2 else ""
        dob = demo_parts[3] if len(demo_parts) >= 4 else ""

        # Row i+1: Address
        address = ""
        if i + 1 < len(all_rows):
            for cell in all_rows[i + 1].find_all("td"):
                text = cell.get_text(strip=True)
                if text.startswith("ADDRESS:"):
                    address = text.replace("ADDRESS:", "").strip()

        # Row i+2: Release date / code / SOID
        booking_date, arrest_date, status = "", "", "In Custody"
        if i + 2 < len(all_rows):
            for cell in all_rows[i + 2].find_all("td"):
                text = cell.get_text(strip=True)
                if text.startswith("RELEASE DATE:"):
                    release = text.replace("RELEASE DATE:", "").strip()
                    if release:
                        status = "Released"
                elif text.startswith("ARREST DATE:"):
                    arrest_date = text.replace("ARREST DATE:", "").strip()
                elif text.startswith("BOOKING DATE:"):
                    booking_date = text.replace("BOOKING DATE:", "").strip()

        # Row i+3: Charges table (nested <table>)
        charges_list, total_bond, case_number = [], 0.0, ""
        if i + 3 < len(all_rows):
            nested = all_rows[i + 3].find("table")
            if nested:
                for cr in nested.find_all("tr"):
                    cc = cr.find_all("td")
                    if len(cc) >= 2:
                        desc = cc[1].get_text(strip=True)
                        if desc and "Charge Type" not in desc and "Charge(s)" not in desc:
                            charges_list.append(desc)
                        if len(cc) >= 5:
                            bond_text = cc[4].get_text(strip=True)
                            try:
                                total_bond += float(
                                    bond_text.replace("$", "").replace(",", "")
                                )
                            except (ValueError, TypeError):
                                pass
                        if len(cc) >= 4 and not case_number:
                            cn = cc[3].get_text(strip=True)
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
            Release_Date="",
            Facility="Falkenburg Road Jail",
            Race=race,
            Sex=sex,
            DOB=dob,
            Address=address,
            Charges=" | ".join(charges_list),
            Bond_Amount=str(total_bond) if total_bond > 0 else "0",
            Case_Number=case_number,
            Detail_URL=href,
            Agency=agency,
            LastCheckedMode="INITIAL",
        )

    @staticmethod
    def _parse_name(name_str):
        if not name_str:
            return "", "", ""
        if "," in name_str:
            parts = name_str.split(",", 1)
            last_name = parts[0].strip()
            first_middle = parts[1].strip() if len(parts) > 1 else ""
            name_parts = first_middle.split()
            return (
                name_parts[0] if name_parts else "",
                " ".join(name_parts[1:]) if len(name_parts) > 1 else "",
                last_name,
            )
        parts = name_str.split()
        return parts[0], "", parts[-1] if len(parts) >= 2 else ""
