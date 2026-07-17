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
  - SARASOTA_COUNTY_FL
  - HILLSBOROUGH_COUNTY_FL
  (more to be discovered)

NO Cloudflare protection — direct access from VPS datacenter IP.

CAPTCHA solving priority (open-source first, paid last):
  1. scrapers.captcha_ocr ensemble (FREE):
       preprocess → ddddocr → Tesseract → PaddleOCR → EasyOCR → vote
  2. SolveCaptcha API (if SOLVECAPTCHA_KEY set — ~$0.50/1000)
  3. OpenAI GPT-4o vision (expensive fallback)

  After OCR, API multi-try of case permutations (JailTracker is case-sensitive;
  wrong codes keep the same captchaKey).
"""

import base64
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

JT_BASE = "https://omsweb.public-safety-cloud.com/jtclientweb"
MAX_CAPTCHA_ATTEMPTS = 8
CAPTCHA_WAIT_S = 3
PAGE_LOAD_WAIT_S = 8
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
                        data = resp.json()
                        api_data[key] = data
                        data_size = len(str(data))
                        logger.info(f"[{self.county}] 📡 API: {key} ({data_size} chars)")
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
                # Check api_data for any offender data captured during attempts
                if api_data:
                    logger.info(f"[{self.county}] CAPTCHA failed but checking captured API data... keys: {list(api_data.keys())}")
                    for key, val in api_data.items():
                        if "offender" in key.lower():
                            val_type = type(val).__name__
                            val_preview = str(val)[:300]
                            logger.info(f"[{self.county}] API key '{key}' type={val_type}: {val_preview}")
                            offender_list = None
                            if isinstance(val, list) and val:
                                offender_list = val
                            elif isinstance(val, dict):
                                if val.get("offenders"):
                                    offender_list = val["offenders"]
                                else:
                                    for dk, dv in val.items():
                                        if isinstance(dv, list) and dv:
                                            offender_list = dv
                                            break
                            if offender_list:
                                logger.info(f"[{self.county}] Found {len(offender_list)} offenders in API data!")
                                records = self._parse_api_offenders(offender_list)
                                if records:
                                    logger.info(f"[{self.county}] Recovered {len(records)} records ✅")
                                    return records
                logger.error(f"[{self.county}] Failed to solve CAPTCHA after {MAX_CAPTCHA_ATTEMPTS} attempts")
                return []

            # _solve_captcha returned True — offender data should be in api_data
            roster_key = f"Offender/{self.county_jt_id}/roster"
            if roster_key in api_data:
                roster_data = api_data[roster_key]
                offenders = roster_data.get("offenders", []) if isinstance(roster_data, dict) else roster_data
                logger.info(f"[{self.county}] 🎯 Got {len(offenders)} offenders via response capture")
                records = self._parse_api_offenders(offenders)
                if records:
                    logger.info(f"[{self.county}] Scraped {len(records)} records from JailTracker ✅")
                    return records

            # Fallback: wait for DOM and extract
            time.sleep(PAGE_LOAD_WAIT_S)
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

    @staticmethod
    def _case_permutations(answer: str, limit: int = 32) -> list:
        """Generate case variants. JailTracker is case-sensitive; OCR often wrong-cases."""
        import itertools

        answer = re.sub(r"[^A-Za-z0-9]", "", answer or "")
        if not answer:
            return []
        seen: list = []
        for cand in (answer, answer.upper(), answer.lower(), answer.swapcase()):
            if cand and cand not in seen:
                seen.append(cand)
        # Full 2^n permutations for short codes (typically 3–5 chars)
        if 3 <= len(answer) <= 5:
            for bits in itertools.product([0, 1], repeat=len(answer)):
                out = "".join(
                    ch.upper() if b else ch.lower() for ch, b in zip(answer, bits)
                )
                if out not in seen:
                    seen.append(out)
                if len(seen) >= limit:
                    break
        return seen[:limit]

    def _solve_captcha(self, page, api_data: dict) -> bool:
        """Solve JailTracker image CAPTCHA via OCR + API case multi-try.

        Strategy (validated Jul 2026):
          1. OCR image (ddddocr → SolveCaptcha → OpenAI)
          2. POST /Captcha/validatecaptcha for each case permutation of the OCR
             result against the SAME captchaKey (wrong answers do NOT rotate the
             captcha — only a match consumes it)
          3. On match, immediately POST /Offender/{agency} with the returned
             captchaKey to pull the roster (works for SC/GA agencies; some FL
             agencies currently return HTTP 400 after a valid captcha)

        UI click is only used as a last-resort path when API multi-try fails.
        """
        ddddocr_failures = 0
        max_ddddocr_tries = min(6, MAX_CAPTCHA_ATTEMPTS)
        validate_url = f"{JT_BASE}/Captcha/validatecaptcha"
        offender_url = f"{JT_BASE}/Offender/{self.county_jt_id}"

        for attempt in range(1, MAX_CAPTCHA_ATTEMPTS + 1):
            logger.info(f"[{self.county}] CAPTCHA attempt {attempt}/{MAX_CAPTCHA_ATTEMPTS}")

            captcha_data = {}
            for _ in range(8):
                captcha_data = api_data.get("captcha/getnewcaptchaclient", {})
                if captcha_data.get("captchaImage"):
                    break
                time.sleep(1)

            captcha_image_b64 = captcha_data.get("captchaImage", "")
            captcha_key = captcha_data.get("captchaKey")

            if not captcha_image_b64:
                captcha_img = page.query_selector("img[src*='data:image']")
                if captcha_img:
                    captcha_image_b64 = captcha_img.get_attribute("src") or ""

            if not captcha_image_b64 or not captcha_key:
                logger.warning(f"[{self.county}] No captcha image/key, clicking Get New Code")
                self._click_new_code(page, api_data)
                continue

            b64_part = (
                captcha_image_b64.split(",")[1]
                if "," in captcha_image_b64
                else captcha_image_b64
            )

            # ── Open-source ensemble first (ddddocr + tesseract + paddle + easyocr)
            answer = ""
            used_local_ocr = False
            ocr_seeds: list = []
            if ddddocr_failures < max_ddddocr_tries:
                try:
                    from scrapers.captcha_ocr import solve_captcha_image

                    ocr_result = solve_captcha_image(
                        b64_part, label=self.county
                    )
                    ocr_seeds = ocr_result.all_seeds()
                    if ocr_result.best:
                        answer = ocr_result.best
                        used_local_ocr = True
                except Exception as e:
                    logger.warning(
                        f"[{self.county}] captcha_ocr ensemble error: {e}"
                    )
                    # Legacy single-engine fallback
                    answer = self._ocr_captcha_ddddocr(b64_part)
                    if answer:
                        used_local_ocr = True
                        ocr_seeds = [answer]
            else:
                logger.info(
                    f"[{self.county}] local OCR failed {ddddocr_failures}x, using paid solver"
                )

            if not answer:
                answer = (
                    self._ocr_captcha_solvecaptcha(b64_part)
                    or self._ocr_captcha_openai(b64_part)
                )
                if answer:
                    ocr_seeds = [answer]

            if not answer:
                logger.warning(f"[{self.county}] OCR returned empty, retrying")
                self._click_new_code(page, api_data)
                continue

            answer = re.sub(r"[^A-Za-z0-9]", "", answer.strip())
            if len(answer) < 3 or len(answer) > 6:
                logger.warning(
                    f"[{self.county}] OCR returned '{answer}' (len={len(answer)}), retrying"
                )
                self._click_new_code(page, api_data)
                continue

            # Expand case perms across ALL OCR seeds (ensemble may disagree on case)
            candidates: list = []
            for seed in (ocr_seeds or [answer]):
                for cand in self._case_permutations(seed, limit=24):
                    if cand not in candidates:
                        candidates.append(cand)
            # Cap total tries per captcha image
            candidates = candidates[:48]
            logger.info(
                f"[{self.county}] CAPTCHA OCR={answer!r} seeds={ocr_seeds[:5]!r} "
                f"→ {len(candidates)} case candidates"
            )

            # ── API multi-try: wrong codes keep same key; match returns new key ──
            matched_code = None
            roster_token = None
            for cand in candidates:
                try:
                    result = page.evaluate(
                        """async ([url, captchaKey, cand]) => {
                            const r = await fetch(url, {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json; charset=utf-8'},
                                body: JSON.stringify({
                                    captchaKey, captchaImage: null, userCode: cand
                                }),
                                credentials: 'include',
                            });
                            try { return await r.json(); }
                            catch (e) { return {error: String(e), status: r.status}; }
                        }""",
                        [validate_url, captcha_key, cand],
                    )
                except Exception as e:
                    logger.debug("[%s] validate fetch error: %s", self.county, e)
                    continue

                if isinstance(result, dict) and result.get("captchaMatched") is True:
                    matched_code = cand
                    roster_token = result.get("captchaKey")
                    logger.info(f"[{self.county}] CAPTCHA matched via API: {cand!r} ✅")
                    break

            if not matched_code or not roster_token:
                if used_local_ocr:
                    ddddocr_failures += 1
                    logger.warning(
                        f"[{self.county}] CAPTCHA incorrect "
                        f"(local OCR miss #{ddddocr_failures}), retrying..."
                    )
                else:
                    logger.warning(f"[{self.county}] CAPTCHA incorrect, retrying...")
                self._click_new_code(page, api_data)
                continue

            # ── Pull roster with the one-time token from validate ──
            try:
                roster_result = page.evaluate(
                    """async ([url, token]) => {
                        const r = await fetch(url, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json; charset=utf-8'},
                            body: JSON.stringify({
                                captchaKey: token, captchaImage: null, userCode: ''
                            }),
                            credentials: 'include',
                        });
                        const text = await r.text();
                        let data = null;
                        try { data = JSON.parse(text); } catch (e) {}
                        return {
                            status: r.status,
                            len: text.length,
                            data,
                            preview: text.slice(0, 200),
                        };
                    }""",
                    [offender_url, roster_token],
                )
            except Exception as e:
                logger.warning(f"[{self.county}] Offender fetch error: {e}")
                roster_result = None

            if isinstance(roster_result, dict):
                status = roster_result.get("status")
                data = roster_result.get("data")
                if status == 200 and isinstance(data, dict):
                    offenders = data.get("offenders") or []
                    if offenders:
                        api_data[f"Offender/{self.county_jt_id}/roster"] = data
                        logger.info(
                            f"[{self.county}] 🎯 CAPTURED {len(offenders)} offenders via API! ✅"
                        )
                        return True
                    logger.warning(
                        f"[{self.county}] Captcha OK but roster empty "
                        f"(captchaRequired={data.get('captchaRequired')})"
                    )
                elif status == 400:
                    # Observed for SARASOTA/Manatee/Charlotte FL agencies (Jul 2026):
                    # validate succeeds, Offender POST returns empty 400.
                    logger.error(
                        f"[{self.county}] Offender POST 400 after valid captcha — "
                        f"agency backend rejects roster (FL JailTracker issue). "
                        f"preview={roster_result.get('preview')!r}"
                    )
                    return False
                else:
                    logger.warning(
                        f"[{self.county}] Unexpected offender response: "
                        f"status={status} len={roster_result.get('len')}"
                    )

            # Soft fallback: fill UI with matched code (may already be consumed)
            try:
                page.fill("#captchaCode", matched_code)
                btn = page.query_selector("button:has-text('Validate')")
                if btn:
                    btn.click()
                    page.wait_for_timeout(3000)
                    for key, val in list(api_data.items()):
                        if isinstance(val, dict) and val.get("offenders"):
                            api_data[f"Offender/{self.county_jt_id}/roster"] = val
                            return True
            except Exception:
                pass

            self._click_new_code(page, api_data)

        return False

    def _click_new_code(self, page, api_data: dict):
        """Click Get New Code and wait for new captcha."""
        api_data.pop("captcha/getnewcaptchaclient", None)
        new_code_btn = page.query_selector("button:has-text('Get New Code')")
        if new_code_btn:
            new_code_btn.click()
            time.sleep(2)

    def _ocr_captcha_ddddocr(self, image_b64: str) -> str:
        """Use ddddocr (free, local) for image CAPTCHA. Best cost: $0."""
        try:
            import ddddocr
        except ImportError:
            logger.debug(f"[{self.county}] ddddocr not installed, skipping free OCR")
            return ""

        try:
            ocr = ddddocr.DdddOcr(show_ad=False)
            image_bytes = base64.b64decode(image_b64)
            # Try raw + simple preprocessing variants; pick first plausible length
            candidates = [image_bytes]
            try:
                import io
                from PIL import Image, ImageOps, ImageFilter, ImageEnhance
                base_img = Image.open(io.BytesIO(image_bytes)).convert("L")
                for im in (
                    ImageOps.autocontrast(base_img),
                    ImageOps.autocontrast(base_img).filter(ImageFilter.SHARPEN),
                    ImageEnhance.Contrast(ImageOps.autocontrast(base_img)).enhance(2.0),
                    ImageOps.autocontrast(base_img).resize(
                        (max(base_img.width * 2, 80), max(base_img.height * 2, 40))
                    ),
                ):
                    buf = io.BytesIO()
                    im.save(buf, format="PNG")
                    candidates.append(buf.getvalue())
            except Exception:
                pass

            answers = []
            for blob in candidates:
                try:
                    ans = ocr.classification(blob)
                    ans = re.sub(r"[^a-zA-Z0-9]", "", ans or "")
                    if 3 <= len(ans) <= 6 and ans not in answers:
                        answers.append(ans)
                except Exception:
                    continue
            answer = answers[0] if answers else ""
            if 3 <= len(answer) <= 6:
                logger.info(
                    f"[{self.county}] ddddocr answered: {answer!r} "
                    f"(FREE; alts={answers[1:4]!r})"
                )
                return answer
            logger.warning(
                f"[{self.county}] ddddocr returned '{answer}' (unusable length), falling through"
            )
            return ""
        except Exception as e:
            logger.warning(f"[{self.county}] ddddocr error: {e}")
            return ""

    def _ocr_captcha_solvecaptcha(self, image_b64: str) -> str:
        """Use SolveCaptcha API for image CAPTCHA (cheapest, most reliable)."""
        api_key = os.getenv("SOLVECAPTCHA_KEY", "")
        if not api_key:
            return ""  # Fall through to OpenAI

        try:
            import httpx

            # Submit image captcha task
            submit_resp = httpx.post(
                "https://api.solvecaptcha.com/in.php",
                data={
                    "key": api_key,
                    "method": "base64",
                    "body": image_b64,
                    "json": "1",
                    "min_len": "3",
                    "max_len": "6",
                    "regsense": "1",  # Case-sensitive
                    "numeric": "0",   # Letters + digits
                },
                timeout=30,
            )
            submit_data = submit_resp.json()
            if submit_data.get("status") != 1:
                logger.warning(f"[{self.county}] SolveCaptcha submit error: {submit_data}")
                return ""

            task_id = submit_data["request"]

            # Poll for result (image CAPTCHAs are fast — usually <5s)
            for _ in range(12):
                time.sleep(3)
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
                    answer = result_data["request"]
                    answer = re.sub(r"[^a-zA-Z0-9]", "", answer)
                    if len(answer) > 6:
                        answer = answer[:6]
                    logger.info(f"[{self.county}] SolveCaptcha answered: {answer!r}")
                    return answer
                elif "CAPCHA_NOT_READY" in str(result_data.get("request", "")):
                    continue
                else:
                    logger.warning(f"[{self.county}] SolveCaptcha error: {result_data}")
                    return ""

            logger.warning(f"[{self.county}] SolveCaptcha timeout")
            return ""
        except Exception as e:
            logger.warning(f"[{self.county}] SolveCaptcha error: {e}")
            return ""

    def _ocr_captcha_openai(self, image_b64: str) -> str:
        """Use OpenAI GPT-4o vision to read 3–6 char captcha (paid fallback)."""
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
                    "model": "gpt-4o",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "This is a CAPTCHA image with 3–6 alphanumeric characters. "
                                        "Preserve UPPERCASE vs lowercase carefully — it is case-sensitive. "
                                        "Often yellow/orange letters on a dark blue background. "
                                        "Reply with ONLY the characters. No spaces, quotes, or explanation. "
                                        "Examples: Ab3K, xY9m, H2dR, ycpT"
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
                    "max_tokens": 16,
                    "temperature": 0,
                },
                timeout=15,
            )
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"].strip()
            answer = re.sub(r"[^a-zA-Z0-9]", "", answer)
            if 3 <= len(answer) <= 6:
                return answer
            return answer[:6]
        except Exception as e:
            logger.warning(f"[{self.county}] OpenAI captcha OCR error: {e}")
            return ""

    def _extract_roster(self, page, api_data: dict) -> List[ArrestRecord]:
        """Extract inmate records from the rendered JailTracker roster page."""
        time.sleep(2)

        # The roster displays as a table or card grid
        # First check for offender data in API responses
        for key, val in api_data.items():
            if "offender" in key.lower():
                # Handle both list and dict responses
                if isinstance(val, list) and val:
                    logger.info(f"[{self.county}] Found {len(val)} offenders in API response (list)")
                    return self._parse_api_offenders(val)
                elif isinstance(val, dict) and val.get("offenders"):
                    offenders = val["offenders"]
                    if offenders:
                        logger.info(f"[{self.county}] Found {len(offenders)} offenders in API response (dict)")
                        return self._parse_api_offenders(offenders)

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
