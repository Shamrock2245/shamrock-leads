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
import json
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
SEARCH_URL = "https://webapps.hcso.tampa.fl.us/arrestinquiry/"
RECAPTCHA_SITEKEY = "6LcK1HopAAAAAEZgVeXqiN2_4zp6cQwRRXfc3uKJ"
SOCKS_PROXY = "socks5://172.18.0.1:1080"
DAYS_BACK = 90
MAX_PAGES = 20
COOKIE_FILE = "/tmp/hcso_cookies.json"


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
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
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

            # Try cookie-based login first (skips reCAPTCHA entirely)
            logged_in = self._try_cookie_login(ctx, page)

            # Fall back to full login with reCAPTCHA
            if not logged_in:
                if not self._login(page, hcso_email, hcso_password):
                    logger.error("Hillsborough: login failed")
                    return []
                # Save cookies for next run
                self._save_cookies(ctx)

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

        # Fill credentials
        page.locator("#Email").fill(email)
        time.sleep(0.3)
        page.locator("#Password").fill(password)

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

    # ── Cookie Persistence ────────────────────────────────────────────
    def _try_cookie_login(self, ctx, page) -> bool:
        """Load saved cookies and check if session is still valid."""
        if not os.path.exists(COOKIE_FILE):
            return False

        try:
            with open(COOKIE_FILE, "r") as f:
                cookies = json.load(f)

            if not cookies:
                return False

            # Check cookie age (ASP.NET cookies typically expire after 30 days)
            file_age = time.time() - os.path.getmtime(COOKIE_FILE)
            if file_age > 86400 * 7:  # 7 days
                logger.info("[Hillsborough] Saved cookies expired (>7 days)")
                os.remove(COOKIE_FILE)
                return False

            # Add cookies to context
            ctx.add_cookies(cookies)
            logger.info(f"[Hillsborough] Loaded {len(cookies)} saved cookies")

            # Test if session is valid by hitting the search page
            page.goto(SEARCH_URL, wait_until="load", timeout=20000)
            time.sleep(2)

            if "Login" in page.url:
                logger.info("[Hillsborough] Saved cookies expired (redirected to login)")
                try:
                    os.remove(COOKIE_FILE)
                except Exception:
                    pass
                return False

            html = page.content()
            if "Log Off" in html or "Welcome" in html:
                logger.info("[Hillsborough] Cookie login successful ✅ (skipped reCAPTCHA!)")
                return True

            logger.info("[Hillsborough] Cookie session invalid")
            return False

        except Exception as e:
            logger.warning(f"[Hillsborough] Cookie login error: {e}")
            return False

    def _save_cookies(self, ctx):
        """Save browser cookies for reuse on next run."""
        try:
            cookies = ctx.cookies()
            with open(COOKIE_FILE, "w") as f:
                json.dump(cookies, f)
            logger.info(f"[Hillsborough] Saved {len(cookies)} cookies to {COOKIE_FILE}")
        except Exception as e:
            logger.warning(f"[Hillsborough] Could not save cookies: {e}")

    # ── reCAPTCHA Solver (Unified Flow) ─────────────────────────────────
    def _solve_recaptcha(self, page) -> bool:
        """
        Solve reCAPTCHA v2 — unified flow with ONE checkbox click.
        
        Flow:
          1. Mouse-click checkbox with real coordinates
          2. If auto-solved (good IP reputation) → done
          3. If challenge appeared → switch to audio challenge
          4. If audio loads → transcribe + submit (free)
          5. If audio fails → SolveCaptcha API token injection (paid fallback)
        """
        # Step 1: Click the checkbox with realistic mouse coordinates
        anchor_frame = self._click_recaptcha_checkbox(page)
        if not anchor_frame:
            logger.warning("[Hillsborough] Could not find reCAPTCHA checkbox")
            return self._solve_via_api(page)

        # Step 2: Check if auto-solved (happens with good IP reputation)
        time.sleep(4)
        if self._is_recaptcha_solved(anchor_frame, page):
            logger.info("[Hillsborough] reCAPTCHA auto-solved via checkbox! ✅ (free)")
            return True

        logger.info("[Hillsborough] Challenge appeared, trying audio...")

        # Step 3: Find the challenge bframe and switch to audio
        bframe = self._get_bframe(page)
        if not bframe:
            logger.warning("[Hillsborough] No challenge frame found")
            return self._solve_via_api(page)

        # Try audio challenge (up to 3 attempts)
        for attempt in range(1, 4):
            logger.info(f"[Hillsborough] Audio attempt {attempt}/3")

            # Switch to audio challenge
            if not self._switch_to_audio(bframe):
                logger.warning("[Hillsborough] Could not switch to audio")
                break

            time.sleep(4)

            # Download and transcribe
            audio_url = self._get_audio_url(bframe)
            if not audio_url:
                logger.warning("[Hillsborough] No audio URL found")
                self._reload_challenge(bframe)
                time.sleep(2)
                continue

            transcript = self._transcribe_audio(audio_url)
            if not transcript:
                logger.warning("[Hillsborough] Transcription failed")
                self._reload_challenge(bframe)
                time.sleep(2)
                continue

            # Submit answer
            if self._submit_audio_answer(bframe, transcript, anchor_frame, page):
                logger.info("[Hillsborough] reCAPTCHA solved via audio! ✅ (free)")
                return True

            logger.warning("[Hillsborough] Wrong answer, retrying...")
            self._reload_challenge(bframe)
            time.sleep(2)

        # Step 4: Fallback to SolveCaptcha API (paid)
        logger.info("[Hillsborough] Audio failed, trying SolveCaptcha API...")
        return self._solve_via_api(page)

    def _click_recaptcha_checkbox(self, page):
        """Click checkbox with real mouse coordinates. Returns anchor frame or None."""
        try:
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
                return None

            iframe_box = iframe_el.bounding_box()
            checkbox_box = anchor_frame.evaluate("""() => {
                var cb = document.querySelector('#recaptcha-anchor');
                if (!cb) return null;
                var rect = cb.getBoundingClientRect();
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
            }""")

            if not iframe_box or not checkbox_box:
                return None

            click_x = iframe_box["x"] + checkbox_box["x"] + checkbox_box["width"] / 2
            click_y = iframe_box["y"] + checkbox_box["y"] + checkbox_box["height"] / 2

            page.mouse.move(click_x, click_y)
            time.sleep(0.3)
            page.mouse.click(click_x, click_y)
            logger.info(f"[Hillsborough] Clicked checkbox at ({click_x:.0f}, {click_y:.0f})")
            return anchor_frame

        except Exception as e:
            logger.warning(f"[Hillsborough] Checkbox click error: {e}")
            return None

    def _is_recaptcha_solved(self, anchor_frame, page) -> bool:
        """Check if reCAPTCHA is solved (checkbox checked or token populated)."""
        try:
            checked = anchor_frame.evaluate(
                "() => document.querySelector('#recaptcha-anchor')?.getAttribute('aria-checked')"
            )
            if checked == "true":
                return True
        except Exception:
            pass
        try:
            token = page.evaluate("""() => {
                var el = document.querySelector('#g-recaptcha-response')
                    || document.querySelector('[name="g-recaptcha-response"]');
                return el ? el.value : '';
            }""")
            if token and len(token) > 20:
                return True
        except Exception:
            pass
        return False

    def _get_bframe(self, page):
        """Find the reCAPTCHA challenge bframe."""
        for f in page.frames:
            if "recaptcha" in f.url and "bframe" in f.url:
                return f
        return None

    def _switch_to_audio(self, bframe) -> bool:
        """Click the audio button in the challenge frame."""
        try:
            audio_btn = bframe.query_selector("#recaptcha-audio-button")
            if audio_btn:
                audio_btn.click(force=True, timeout=5000)
                logger.info("[Hillsborough] Switched to audio")
                time.sleep(3)
                return True
        except Exception:
            pass
        try:
            bframe.evaluate(
                "() => { var b = document.querySelector('#recaptcha-audio-button'); if (b) b.click(); }"
            )
            time.sleep(3)
            return True
        except Exception:
            pass
        return False

    def _get_audio_url(self, bframe) -> str:
        """Extract the audio challenge MP3 URL from the bframe."""
        try:
            # Method 1: Download link
            link = bframe.query_selector(".rc-audiochallenge-tdownload-link")
            if link:
                href = link.get_attribute("href")
                if href:
                    return href

            # Method 2: Audio source element
            url = bframe.evaluate("""() => {
                var src = document.querySelector('audio source');
                if (src) return src.src || src.getAttribute('src');
                var audio = document.querySelector('audio');
                if (audio) return audio.src;
                var el = document.querySelector('#audio-source');
                if (el) return el.src || el.getAttribute('src');
                return '';
            }""")
            if url:
                return url

            # Method 3: Payload links
            url = bframe.evaluate("""() => {
                var links = document.querySelectorAll('a[href*="payload"]');
                for (var i = 0; i < links.length; i++) {
                    if (links[i].href) return links[i].href;
                }
                return '';
            }""")
            return url or ""

        except Exception:
            return ""

    def _transcribe_audio(self, audio_url: str) -> str:
        """Download MP3, convert to WAV, transcribe via Google Speech Recognition."""
        import tempfile
        import random
        import urllib.request

        try:
            tmp_dir = tempfile.mkdtemp()
            mp3_path = os.path.join(tmp_dir, f"captcha_{random.randint(1000,9999)}.mp3")
            wav_path = mp3_path.replace(".mp3", ".wav")

            logger.info(f"[Hillsborough] Downloading audio: {audio_url[:60]}...")
            urllib.request.urlretrieve(audio_url, mp3_path)

            import pydub
            sound = pydub.AudioSegment.from_mp3(mp3_path)
            sound.export(wav_path, format="wav")

            import speech_recognition as sr
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)
                transcript = recognizer.recognize_google(audio_data)

            logger.info(f"[Hillsborough] Transcript: '{transcript}'")

            # Cleanup
            try:
                os.remove(mp3_path)
                os.remove(wav_path)
                os.rmdir(tmp_dir)
            except Exception:
                pass

            return transcript.strip()

        except Exception as e:
            logger.warning(f"[Hillsborough] Transcription error: {e}")
            return ""

    def _submit_audio_answer(self, bframe, answer, anchor_frame, page) -> bool:
        """Submit the transcribed answer and check if solved."""
        try:
            input_field = bframe.query_selector("#audio-response")
            if input_field:
                input_field.fill(answer)
                time.sleep(0.5)
                verify_btn = bframe.query_selector("#recaptcha-verify-button")
                if verify_btn:
                    verify_btn.click(force=True)
                    time.sleep(4)
                    return self._is_recaptcha_solved(anchor_frame, page)
        except Exception as e:
            logger.warning(f"[Hillsborough] Submit error: {e}")
        return False

    def _reload_challenge(self, bframe):
        """Click reload to get a new audio challenge."""
        try:
            reload_btn = bframe.query_selector("#recaptcha-reload-button")
            if reload_btn:
                reload_btn.click(force=True)
                time.sleep(3)
        except Exception:
            pass

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

            # Step 2: Poll for result (up to 180s)
            token = None
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
                            logger.error("[Hillsborough] SolveCaptcha: too many HTTP errors")
                            return False
                        continue
                    result_data = result_resp.json()
                except (ValueError, Exception) as parse_err:
                    http_errors += 1
                    logger.warning(f"[Hillsborough] SolveCaptcha parse error (attempt {http_errors}): {parse_err}")
                    if http_errors >= 3:
                        return False
                    continue

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

        # Fill Booking Date (required — form rejects blank searches)
        try:
            from datetime import datetime, timedelta
            booking_date = (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%m/%d/%Y")
            page.locator("#SearchBookingDate").fill(booking_date)
            logger.info(f"[Hillsborough] Set booking date: {booking_date}")
        except Exception as e:
            logger.warning(f"[Hillsborough] Could not set booking date: {e}")

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
        # Go straight to API token injection — avoids clicking checkbox which
        # loads heavy iframe challenge and causes memory crashes in Docker
        logger.info("[Hillsborough] Solving search form reCAPTCHA...")
        if self._solve_via_api(page):
            logger.info("[Hillsborough] Search reCAPTCHA solved via API ✅")
        else:
            logger.warning("[Hillsborough] Search reCAPTCHA not solved, submitting anyway")

        time.sleep(1)

        # Submit search via #button_submit
        try:
            page.click("#button_submit", timeout=5000)
        except Exception:
            try:
                page.click("button[type='submit']", timeout=3000)
            except Exception:
                try:
                    page.keyboard.press("Enter")
                except Exception as e:
                    logger.warning(f"[Hillsborough] Submit fallback failed: {e}")

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
