"""
Lake County Arrest Scraper — JSON API + reCAPTCHA v2
Source: Lake County Sheriff's Office
URL: https://www.lcso.org/inmate-search/api/inmates
Method: curl_cffi POST — JSON REST API + reCAPTCHA v2 (SolveCaptcha)
Fields: firstname, lastname, dob, booking_number, facility + charges

HISTORY:
  - v1: DrissionPage JS SPA
  - v2: curl_cffi + token bypass (reCAPTCHA disabled server-side)
  - v3 (current): reCAPTCHA now enforced (2026-07); uses SolveCaptcha API.
        Requires env SOLVECAPTCHA_KEY.
"""
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional

from curl_cffi import requests as cffi_requests
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.lcso.org"
API_URL = f"{BASE_URL}/inmate-search/api/inmates"
FACILITY = "Lake County Jail"
RECAPTCHA_SITEKEY = "6Ldas6IrAAAAAAuFfoBGxbpraKxvnnrHNaLLRjKx"
SEARCH_PAGE_URL = f"{BASE_URL}/inmate-search/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": SEARCH_PAGE_URL,
    "Origin": BASE_URL,
}

# ── Stealth Stack ──────────────────────────────────────────────────────────────
IMPERSONATE = "chrome131"
STEALTH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
}

class LakeCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Lake"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cf
        except ImportError:
            logger.error("curl_cffi not installed")
            raise

        # reCAPTCHA is now enforced (2026-07). Solve via SolveCaptcha API.
        recaptcha_token = self._solve_recaptcha()
        if not recaptcha_token:
            api_key = os.getenv("SOLVECAPTCHA_KEY", "")
            detail = "key missing" if not api_key else "service rejected (key present)"
            raise RuntimeError(
                f"Lake: reCAPTCHA solve failed ({detail})"
            )

        session = cf.Session()
        records: List[ArrestRecord] = []
        seen: set = set()

        payload = {
            "firstname": "",
            "lastname": "",
            "dob": "",
            "booking_number": "",
            "facility": "",
            "token": recaptcha_token,
        }

        try:
            r = session.post(
                API_URL,
                json=payload,
                headers=HEADERS,
                timeout=30,
                impersonate="chrome131",
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.error(f"Lake API error: {e}")
            raise

        inmates = data if isinstance(data, list) else data.get(
            "records", data.get("inmates", data.get("data", []))
        )
        if not inmates:
            logger.warning(
                "Lake: no inmates in response. Keys: %s",
                list(data.keys()) if isinstance(data, dict) else type(data),
            )
            return records

        cutoff = datetime.now() - timedelta(days=7)

        for inmate in inmates:
            if not isinstance(inmate, dict):
                continue

            booking_number = str(
                inmate.get("booking_number")
                or inmate.get("bookingNumber")
                or inmate.get("id")
                or ""
            )
            if not booking_number or booking_number in seen:
                continue
            seen.add(booking_number)

            first = (inmate.get("firstname") or inmate.get("firstName") or "").strip().title()
            last = (inmate.get("lastname") or inmate.get("lastName") or "").strip().title()
            middle = (inmate.get("middlename") or inmate.get("middleName") or "").strip().title()
            dob_raw = inmate.get("dob") or inmate.get("dateOfBirth") or ""
            booking_date_raw = inmate.get("booking_date") or inmate.get("bookingDate") or ""
            facility = inmate.get("facility") or FACILITY
            gender = (inmate.get("gender") or inmate.get("sex") or "").strip()
            race = (inmate.get("race") or "").strip()
            address = (inmate.get("address") or "").strip()
            city = (inmate.get("city") or "").strip()
            state = (inmate.get("state") or "FL").strip()
            zip_code = (inmate.get("zip") or "").strip()

            # Parse charges
            charges_raw = inmate.get("charges") or inmate.get("offenses") or []
            if isinstance(charges_raw, list):
                charges = "; ".join(
                    str(c.get("description") or c.get("charge") or c)
                    for c in charges_raw if c
                )
            else:
                charges = str(charges_raw)

            bond_raw = (
                inmate.get("bond_amount")
                or inmate.get("bondAmount")
                or inmate.get("total_bond")
                or "0"
            )
            bond_amount = re.sub(r"[$,\s]", "", str(bond_raw))

            # Filter by booking date if available
            if booking_date_raw:
                try:
                    bd = datetime.fromisoformat(str(booking_date_raw).replace("Z", ""))
                    if bd < cutoff:
                        continue
                except Exception:
                    pass

            full_address = ", ".join(filter(None, [address, city, state, zip_code]))

            record = ArrestRecord(
                County="Lake",
                Booking_Number=booking_number,
                Full_Name=f"{first} {middle} {last}".strip(),
                First_Name=first,
                Middle_Name=middle,
                Last_Name=last,
                DOB=dob_raw,
                Booking_Date=booking_date_raw,
                Status="In Custody",
                Release_Date="",
                Facility=facility,
                Sex=gender,
                Race=race,
                Address=full_address,
                Charges=charges,
                Bond_Amount=bond_amount,
                Detail_URL=SEARCH_PAGE_URL,
                LastCheckedMode="INITIAL",
            )
            records.append(record)

        logger.info(f"Lake: {len(records)} records")
        return records

    # ── reCAPTCHA solver ──────────────────────────────────────────────────

    def _solve_recaptcha(self) -> Optional[str]:
        """Solve reCAPTCHA v2 via SolveCaptcha API. Returns token string."""
        api_key = os.getenv("SOLVECAPTCHA_KEY", "")
        if not api_key:
            logger.warning("[Lake] No SOLVECAPTCHA_KEY set \u2014 cannot solve reCAPTCHA")
            return None

        logger.info("[Lake] Solving reCAPTCHA via SolveCaptcha API (key len=%d)...", len(api_key))
        try:
            submit_resp = cffi_requests.post(
                "https://api.solvecaptcha.com/in.php",
                data={
                    "key": api_key,
                    "method": "userrecaptcha",
                    "googlekey": RECAPTCHA_SITEKEY,
                    "pageurl": SEARCH_PAGE_URL,
                    "json": "1",
                },
                timeout=30,
            )
            submit_data = submit_resp.json()
            if submit_data.get("status") != 1:
                logger.error(f"[Lake] SolveCaptcha submit failed: {submit_data}")
                return None

            task_id = submit_data["request"]
            logger.info(f"[Lake] SolveCaptcha task: {task_id}")

            # Poll for result (up to 180s)
            for _ in range(36):
                time.sleep(5)
                try:
                    result_resp = cffi_requests.get(
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
                except Exception as e:
                    logger.warning(f"[Lake] SolveCaptcha poll error: {e}")
                    continue

                if result_data.get("status") == 1:
                    token = result_data["request"]
                    logger.info("[Lake] reCAPTCHA solved ✅")
                    return token
                if "CAPCHA_NOT_READY" in str(result_data.get("request", "")):
                    continue
                logger.error(f"[Lake] SolveCaptcha error: {result_data}")
                return None

            logger.error("[Lake] SolveCaptcha timeout (180s)")
            return None
        except Exception as e:
            logger.error(f"[Lake] SolveCaptcha exception: {e}")
            return None
