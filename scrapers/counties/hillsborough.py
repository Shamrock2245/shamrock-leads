"""
Hillsborough County Arrest Scraper — HCSO Arrest Inquiry Portal.
================================================================
Source: Hillsborough County Sheriff's Office
URL: https://webapps.hcso.tampa.fl.us/arrestinquiry/
Method: Playwright + SOCKS5 residential proxy + SolveCaptcha reCAPTCHA v2

Requires env vars:
  HCSO_EMAIL       — login email
  HCSO_PASSWORD    — login password
  SOLVECAPTCHA_KEY — SolveCaptcha API key (for reCAPTCHA v2 bypass)

HISTORY:
  v1: DrissionPage + reCAPTCHA checkbox click (unreliable)
  v2 (current): Playwright + SOCKS proxy + SolveCaptcha token injection
"""
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

LOGIN_URL = "https://webapps.hcso.tampa.fl.us/arrestinquiry/Account/Login"
SEARCH_URL = "https://webapps.hcso.tampa.fl.us/arrestinquiry/Home/Search"
RECAPTCHA_SITEKEY = "6LcK1HopAAAAAEZgVeXqiN2_4zp6cQwRRXfc3uKJ"
SOCKS_PROXY = "socks5://172.18.0.1:1080"
DAYS_BACK = 90
MAX_PAGES = 20


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

        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup

        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            proxy={"server": SOCKS_PROXY},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        all_records: List[ArrestRecord] = []
        try:
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
            )
            page = ctx.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

            # Login
            if not self._login(page, hcso_email, hcso_password):
                logger.error("Hillsborough: login failed")
                return []

            # Search
            if not self._perform_search(page):
                logger.warning("Hillsborough: no search results")
                return []

            # Paginate and parse
            for page_num in range(1, MAX_PAGES + 1):
                soup = BeautifulSoup(page.content(), "html.parser")
                page_records = self._parse_results_table(soup)
                if not page_records:
                    break
                all_records.extend(page_records)
                logger.info(
                    f"[Hillsborough] Page {page_num}: +{len(page_records)} "
                    f"(total: {len(all_records)})"
                )

                # Next page
                try:
                    next_btn = page.query_selector("text=Next >")
                    if not next_btn:
                        break
                    cls = next_btn.get_attribute("class") or ""
                    if "disabled" in cls:
                        break
                    next_btn.click()
                    time.sleep(3)
                except Exception:
                    break

            logger.info(f"Hillsborough: {len(all_records)} records total")
            return all_records

        except Exception as e:
            logger.error(f"Hillsborough fatal: {e}")
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

    # ── Login ──────────────────────────────────────────────────────────
    def _login(self, page, email: str, password: str) -> bool:
        logger.info("[Hillsborough] Loading login page...")
        page.goto(LOGIN_URL, wait_until="load", timeout=30000)
        time.sleep(3)

        # Fill credentials via keyboard (more reliable than fill())
        page.locator("#Email").click()
        page.locator("#Email").type(email, delay=30)
        time.sleep(0.3)
        page.locator("#Password").click()
        page.locator("#Password").type(password, delay=30)

        # Remember me
        try:
            page.click("#RememberMe", timeout=2000)
        except Exception:
            pass

        # Solve reCAPTCHA
        if not self._solve_recaptcha(page):
            logger.warning("[Hillsborough] reCAPTCHA not solved, submitting anyway")

        # Submit login — HCSO uses <input type="submit" class="btn btn-primary btn-lg">
        try:
            page.click("input[type='submit']", timeout=5000)
        except Exception:
            try:
                page.click("button:has-text('Log in')", timeout=3000)
            except Exception:
                page.keyboard.press("Enter")

        time.sleep(5)

        # Verify login
        html = page.content()
        if "Log out" in html or "Search" in html:
            logger.info("[Hillsborough] Login successful ✅")
            return True
        if "arrestinquiry" in page.url.lower() and "Login" not in page.url:
            logger.info("[Hillsborough] Login successful (URL check) ✅")
            return True

        logger.warning(f"[Hillsborough] Login appears to have failed. URL: {page.url}")
        return False

    # ── reCAPTCHA Solver (Mouse Click → Audio → SolveCaptcha Token) ──
    def _solve_recaptcha(self, page) -> bool:
        """
        Solve reCAPTCHA v2 with cascading fallback:
          1. Mouse click checkbox (auto-solves with good residential IP reputation)
          2. Audio challenge solver (FREE — speech recognition)
          3. SolveCaptcha API token injection (paid — bypasses all visual challenges)
        """
        # Method 1: Authentic mouse click on checkbox
        # With residential proxy + good IP reputation, this often auto-solves
        if self._try_mouse_click_solve(page):
            logger.info("[Hillsborough] reCAPTCHA solved via checkbox click! ✅ (free)")
            return True

        # Method 2: Audio challenge (free, self-hosted)
        try:
            from scrapers.recaptcha_audio_solver import RecaptchaAudioSolver
            solver = RecaptchaAudioSolver(page)
            if solver.solve():
                logger.info("[Hillsborough] reCAPTCHA solved via audio! ✅ (free)")
                return True
            logger.info("[Hillsborough] Audio solver failed, trying SolveCaptcha...")
        except ImportError:
            logger.info("[Hillsborough] Audio solver not available")
        except Exception as e:
            logger.warning(f"[Hillsborough] Audio solver error: {e}")

        # Method 3: SolveCaptcha API token injection (paid, most reliable)
        # This doesn't need to click anything — it gets a token from the API
        # and injects it directly into the form
        if self._solve_via_api(page):
            return True

        logger.warning("[Hillsborough] All reCAPTCHA methods failed")
        return False

    def _try_mouse_click_solve(self, page) -> bool:
        """Click the reCAPTCHA checkbox with real mouse coordinates.
        
        Google auto-solves when:
          - IP has good reputation (residential proxy)
          - Browser fingerprint looks legitimate
          - Mouse movement is realistic
        """
        try:
            # Find the reCAPTCHA anchor iframe element
            iframe_el = None
            anchor_frame = None
            for el in page.query_selector_all("iframe"):
                src = el.get_attribute("src") or ""
                if "recaptcha" in src and "anchor" in src:
                    iframe_el = el
                    break
            for f in page.frames:
                if "recaptcha" in f.url and "anchor" in f.url:
                    anchor_frame = f
                    break

            if not iframe_el or not anchor_frame:
                return False

            # Get positions for realistic mouse movement
            iframe_box = iframe_el.bounding_box()
            checkbox_box = anchor_frame.evaluate("""() => {
                var cb = document.querySelector('#recaptcha-anchor');
                if (!cb) return null;
                var rect = cb.getBoundingClientRect();
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
            }""")

            if not iframe_box or not checkbox_box:
                return False

            # Calculate absolute coordinates and click
            click_x = iframe_box["x"] + checkbox_box["x"] + checkbox_box["width"] / 2
            click_y = iframe_box["y"] + checkbox_box["y"] + checkbox_box["height"] / 2

            page.mouse.move(click_x, click_y)
            time.sleep(0.3)
            page.mouse.click(click_x, click_y)
            logger.info(f"[Hillsborough] Mouse-clicked checkbox at ({click_x:.0f}, {click_y:.0f})")

            # Wait and check if auto-solved
            time.sleep(5)
            checked = anchor_frame.evaluate(
                "() => document.querySelector('#recaptcha-anchor')?.getAttribute('aria-checked')"
            )
            if checked == "true":
                return True

            logger.info("[Hillsborough] Checkbox click didn't auto-solve (challenge appeared)")
            return False

        except Exception as e:
            logger.warning(f"[Hillsborough] Mouse click error: {e}")
            return False

    def _solve_via_api(self, page) -> bool:
        """Solve reCAPTCHA via SolveCaptcha API token injection.
        
        This bypasses ALL visual challenges — no clicking needed.
        Sends the sitekey to the API, gets a valid token back, and
        injects it into the g-recaptcha-response field.
        """
        api_key = os.getenv("SOLVECAPTCHA_KEY", "")
        if not api_key:
            logger.info("[Hillsborough] No SOLVECAPTCHA_KEY set, skipping API solve")
            return False

        logger.info("[Hillsborough] Solving reCAPTCHA via SolveCaptcha API...")
        try:
            import httpx

            # Step 1: Submit task
            submit_resp = httpx.post(
                "https://api.solvecaptcha.com/in.php",
                data={
                    "key": api_key,
                    "method": "userrecaptcha",
                    "googlekey": RECAPTCHA_SITEKEY,
                    "pageurl": page.url,
                    "json": "1",
                },
                timeout=30,
            )
            submit_data = submit_resp.json()
            if submit_data.get("status") != 1:
                logger.error(f"[Hillsborough] SolveCaptcha submit failed: {submit_data}")
                return False

            task_id = submit_data["request"]
            logger.info(f"[Hillsborough] SolveCaptcha task: {task_id}")

            # Step 2: Poll for result (up to 120s)
            token = None
            for i in range(24):
                time.sleep(5)
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
                result_data = result_resp.json()
                if result_data.get("status") == 1:
                    token = result_data["request"]
                    logger.info(f"[Hillsborough] Token received in {(i+1)*5}s ✅")
                    break
                elif "CAPCHA_NOT_READY" in str(result_data.get("request", "")):
                    continue
                else:
                    logger.error(f"[Hillsborough] SolveCaptcha error: {result_data}")
                    return False

            if not token:
                logger.error("[Hillsborough] SolveCaptcha timeout (120s)")
                return False

            # Step 3: Inject token into the form
            page.evaluate(
                f"""() => {{
                var el = document.querySelector('#g-recaptcha-response')
                    || document.querySelector('[name="g-recaptcha-response"]');
                if (el) {{
                    el.style.display = 'block';
                    el.value = '{token}';
                }}
                // Trigger the callback if defined
                if (typeof ___grecaptcha_cfg !== 'undefined') {{
                    var clients = ___grecaptcha_cfg.clients;
                    for (var cid in clients) {{
                        var client = clients[cid];
                        if (client && client.cb) client.cb('{token}');
                    }}
                }}
            }}"""
            )
            time.sleep(1)
            return True

        except Exception as e:
            logger.error(f"[Hillsborough] SolveCaptcha error: {e}")
            return False

    # ── Search ─────────────────────────────────────────────────────────
    def _perform_search(self, page) -> bool:
        """
        Submit the search form on the HCSO landing page.
        
        The search form is on the SAME page as the login landing page —
        no navigation needed. The form has a SECOND reCAPTCHA that must
        be solved before submission.
        
        Form fields (discovered via element dump):
          #SearchBookingNumber  — text
          #SearchName           — text (Last Name, First Name)
          #SearchBookingDate    — datepicker (MM/DD/YYYY)
          #SearchReleaseDate    — datepicker (MM/DD/YYYY)
          #SearchCurrentInmatesOnly — checkbox
          #SearchIncludeDetails — checkbox
          #button_submit        — submit button
        """
        logger.info("[Hillsborough] Performing search (form is on landing page)...")

        # Check the "Current Inmates Only" checkbox for active arrests
        try:
            is_checked = page.evaluate(
                "() => document.querySelector('#SearchCurrentInmatesOnly')?.checked || false"
            )
            if not is_checked:
                page.click("#SearchCurrentInmatesOnly", timeout=3000)
                logger.info("[Hillsborough] Checked 'Current Inmates Only'")
        except Exception as e:
            logger.warning(f"[Hillsborough] Could not check 'Current Inmates Only': {e}")

        # Check "Include Arrest Details" for charges/bond info
        try:
            is_checked = page.evaluate(
                "() => document.querySelector('#SearchIncludeDetails')?.checked || false"
            )
            if not is_checked:
                page.click("#SearchIncludeDetails", timeout=3000)
                logger.info("[Hillsborough] Checked 'Include Arrest Details'")
        except Exception as e:
            logger.warning(f"[Hillsborough] Could not check details: {e}")

        # Sort by Booking Date (most recent first)
        try:
            page.click("#rbSortBookDate", timeout=2000)
        except Exception:
            pass

        # Solve the SECOND reCAPTCHA on the search form
        logger.info("[Hillsborough] Solving search form reCAPTCHA...")
        try:
            from scrapers.recaptcha_audio_solver import RecaptchaAudioSolver
            solver = RecaptchaAudioSolver(page)
            if solver.solve():
                logger.info("[Hillsborough] Search reCAPTCHA solved! ✅")
            else:
                logger.warning("[Hillsborough] Search reCAPTCHA not solved, submitting anyway")
        except Exception as e:
            logger.warning(f"[Hillsborough] Search reCAPTCHA error: {e}")

        time.sleep(1)

        # Submit search via #button_submit
        try:
            page.click("#button_submit", timeout=5000)
        except Exception:
            try:
                page.click("button[type='submit']", timeout=3000)
            except Exception:
                page.keyboard.press("Enter")

        # Wait for results to load
        time.sleep(8)

        html = page.content()
        has_results = "table-striped" in html or "Booking Name" in html or "Booking #" in html
        if has_results:
            logger.info("[Hillsborough] Search returned results ✅")
        else:
            # Log what we see for debugging
            body_text = page.evaluate("() => document.body.innerText.substring(0, 300)")
            logger.warning(f"[Hillsborough] No results table found. Body: {body_text[:200]}")
        return has_results

    # ── Parsing (preserved from v1) ────────────────────────────────────
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
                cells = row.find_all("td", recursive=False)
                if len(cells) >= 5:
                    name_link = cells[0].find("a")
                    if name_link:
                        record = self._parse_inmate_block(
                            all_rows, i, cells, name_link
                        )
                        if record:
                            records.append(record)
                        i += 4
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
        demo = cells[4].get_text(strip=True)
        demo_parts = [p.strip() for p in demo.split("/")]
        race = demo_parts[0] if len(demo_parts) >= 1 else ""
        sex = demo_parts[1] if len(demo_parts) >= 2 else ""
        dob = demo_parts[3] if len(demo_parts) >= 4 else ""

        address = ""
        if i + 1 < len(all_rows):
            for cell in all_rows[i + 1].find_all("td"):
                text = cell.get_text(strip=True)
                if text.startswith("ADDRESS:"):
                    address = text.replace("ADDRESS:", "").strip()

        booking_date, arrest_date, status = "", "", "In Custody"
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

        charges_list, total_bond, case_number = [], 0.0, ""
        if i + 3 < len(all_rows):
            nested = all_rows[i + 3].find("table")
            if nested:
                for cr in nested.find_all("tr"):
                    cc = cr.find_all("td")
                    if len(cc) >= 2:
                        desc = cc[1].get_text(strip=True)
                        if desc and "Charge Type" not in desc:
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
