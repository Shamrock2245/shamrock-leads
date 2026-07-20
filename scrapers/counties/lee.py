"""
Lee County Arrest Scraper — Python port of ArrestScraper_LeeCounty.js (v8.4).

Source: Lee County Sheriff's Office public API
API: https://www.sheriffleefl.org/public-api/bookings
Charges API: https://www.sheriffleefl.org/public-api/bookings/{id}/charges

Features:
- Paginated booking fetch with 4 API query variants
- Per-booking charges API enrichment (bond, court, case data)
- Base64 mugshot detection (v8.4 fix)
- Backfill mode for incomplete records
"""

import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Set, Tuple

import json
import urllib.parse
import os
import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
BASE_URL = "https://www.sheriffleefl.org"
BOOKINGS_API = "/public-api/bookings"
CHARGES_API = "/public-api/bookings/{booking_id}/charges"
DETAIL_PAGE = "/booking/"
SOCKS_PROXY = os.getenv("SOCKS_PROXY", "")  # Route through obscura residential proxy if set

DAYS_BACK = 30  # Reduced from 90 — stay under 480K/12hr API rate limit
PAGE_SIZE = 200
MAX_PAGES = 15                 # Reduced: 15 × 200 = 3000 records max (enough for 30 days)
MAX_ENRICH = 5                 # Conservative: 5 enrichments per run to save API quota
DETAIL_DELAY_S = 8.0           # Increased from 4.0 — more breathing room
DETAIL_JITTER_S = 4.0          # Increased jitter
RETRY_LIMIT = 2                # Reduced from 4 — stop faster on 429 (save quota)
BACKOFF_BASE_S = 5.0           # Increased from 2.0 — harder backoff on 429/503
MAX_EXECUTION_S = 330
CIRCUIT_BREAKER_THRESHOLD = 2  # Trip faster (was 3)
CIRCUIT_BREAKER_COOLDOWN_S = 60  # Longer cooldown (was 45)
VARIANT_DELAY_S = 30           # Increased from 15 — save API quota between variant attempts

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.sheriffleefl.org/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}



class LeeCountyScraper(BaseScraper):
    """Lee County (FL) arrest scraper — API-first with charges enrichment."""

    @property
    def county(self) -> str:
        return "Lee"

    def scrape(self) -> List[ArrestRecord]:
        """Main scrape pipeline: fetch bookings → enrich with charges → return records."""
        start_time = time.time()

        try:
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=DAYS_BACK)
    
            logger.info(
                f"📅 Date range: {start_date.strftime('%Y-%m-%d')} "
                f"to {end_date.strftime('%Y-%m-%d')}"
            )
    
            # Fetch raw bookings from API (try multiple query variants)
            raw_arrests = self._fetch_arrests(start_date, end_date)
            logger.info(f"📥 Total fetched: {len(raw_arrests)}")
    
            if not raw_arrests:
                return []
    
            # Normalize raw API data → intermediate dicts
            normalized = []
            for raw in raw_arrests:
                norm = self._normalize_record(raw)
                if norm and norm.get("booking_number"):
                    normalized.append(norm)
    
            logger.info(f"🔄 Normalized: {len(normalized)} records")
    
            # Enrich with charges API (bond/court/case data)
            elapsed = time.time() - start_time
            remaining = MAX_EXECUTION_S - elapsed
            max_enrich = min(len(normalized), MAX_ENRICH)
    
            if remaining > 60 and max_enrich > 0:
                logger.info(f"🔬 Enriching {max_enrich} records with charges API...")
                enriched = self._enrich_with_charges(normalized[:max_enrich])
                final = enriched + normalized[max_enrich:]
            else:
                logger.info("⏭️ Skipping enrichment (low time budget)")
                final = normalized
    
            # Convert to ArrestRecord instances.
            # NOTE (July 2026): the per-county webhook broadcast was promoted to
            # BaseScraper.run() (_broadcast_scraper_events) so ALL counties emit
            # real-time new_arrest / hot_lead events — not just Lee. The local
            # _broadcast_new_arrests override was removed to avoid double events.
            records = [self._to_arrest_record(n) for n in final]
            return records
        finally:
            self._cleanup()
        
    def _execute_scrape(self):
        pass # implemented in scrape

    # ── API Fetch ──

    def _fetch_arrests(
        self, start_date: datetime, end_date: datetime
    ) -> List[Dict[str, Any]]:
        """Try multiple API query variants, return first that produces results."""
        s = start_date.strftime("%Y-%m-%d")
        e = end_date.strftime("%Y-%m-%d")

        # Rate limit: 480,000 requests per 12-hour window
        # Only try 2 variants to conserve API quota — inCustody is preferred
        # because it returns fewer records (only current inmates) and uses
        # less bandwidth. Date-range is fallback only.
        variants = [
            # Variant 0: inCustody filter — most efficient, returns only current inmates
            {"inCustody": "true"},
            # Variant 1: date-range fallback (only if inCustody returns nothing)
            {"startBooking": s, "endBooking": e},
        ]

        for i, params in enumerate(variants):
            if i > 0:
                logger.info(f"⏳ Waiting {VARIANT_DELAY_S}s before next variant...")
                time.sleep(VARIANT_DELAY_S)
            logger.info(f"🔄 Trying API variant {i + 1}/{len(variants)}")
            results = self._fetch_with_pagination(params)
            if results:
                logger.info(
                    f"✅ Found {len(results)} results with variant {i + 1}"
                )
                return results

        logger.warning("⚠️ No results from any API variant")
        return []

    def _fetch_with_pagination(
        self, params: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """Paginate through the API until exhausted."""
        all_records: List[Dict[str, Any]] = []
        offset = 0

        for page in range(MAX_PAGES):
            query = {**params, "limit": PAGE_SIZE, "offset": offset}
            url = f"{BASE_URL}{BOOKINGS_API}"

            if page == 0:
                logger.info(f"📡 API URL: {url}?{self._qs(query)}")
            elif page % 5 == 0:
                logger.info(f"📄 Page {page + 1} (offset: {offset})")

            resp = self._http_fetch(url, params=query)
            if resp is None or resp.status_code != 200:
                code = resp.status_code if resp else "unknown"
                logger.warning(f"⚠️ API returned status {code}")
                break

            try:
                data = resp.json()
            except ValueError:
                logger.warning("⚠️ Failed to parse JSON")
                break

            records = self._extract_records(data)
            if not records:
                logger.info(f"ℹ️ No more records at page {page + 1}")
                break

            all_records.extend(records)

            if len(records) < PAGE_SIZE:
                logger.info(
                    f"ℹ️ Last page (partial with {len(records)} records)"
                )
                break

            offset += PAGE_SIZE

        return all_records

    @staticmethod
    def _extract_records(data: Any) -> List[Dict[str, Any]]:
        """Extract the record array from various API response shapes."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("results", "data", "bookings", "records"):
                if isinstance(data.get(key), list):
                    return data[key]
        return []

    # ── Charges Enrichment ──

    def _enrich_with_charges(
        self, items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Call the per-booking charges API to get bond/court/case data.

        Uses a circuit breaker pattern: if CIRCUIT_BREAKER_THRESHOLD
        consecutive failures occur, pause for CIRCUIT_BREAKER_COOLDOWN_S
        before continuing. This prevents rate-limit death spirals where
        rapid retries just make the 429 situation worse.
        """
        enriched = []
        ok = fail = 0
        consecutive_failures = 0

        for i, base in enumerate(items):
            bn = base.get("booking_number", "")
            if not bn:
                enriched.append(base)
                continue

            if i > 0 and i % 10 == 0:
                logger.info(
                    f"🔬 Progress: {i}/{len(items)} "
                    f"(success: {ok}, fail: {fail})"
                )

            # Delay with jitter to avoid predictable request patterns
            delay = DETAIL_DELAY_S + random.uniform(0, DETAIL_JITTER_S)
            time.sleep(delay)

            try:
                url = f"{BASE_URL}{CHARGES_API.format(booking_id=bn)}"
                resp = self._http_fetch(url)

                if resp is None or resp.status_code != 200:
                    code = resp.status_code if resp else "error"
                    logger.warning(
                        f"⚠️ Charges API failed for {bn}: HTTP {code}"
                    )
                    enriched.append(base)
                    fail += 1
                    consecutive_failures += 1

                    # Circuit breaker: if too many consecutive failures, cool down
                    if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                        logger.warning(
                            f"🔌 Circuit breaker tripped ({consecutive_failures} consecutive failures). "
                            f"Cooling down {CIRCUIT_BREAKER_COOLDOWN_S}s..."
                        )
                        time.sleep(CIRCUIT_BREAKER_COOLDOWN_S)
                        consecutive_failures = 0  # Reset after cooldown
                    continue

                charges_json = resp.json()
                if not isinstance(charges_json, list):
                    enriched.append(base)
                    fail += 1
                    consecutive_failures += 1
                    continue

                parsed = self._parse_charges(charges_json)
                logger.debug(
                    f"📊 Booking {bn}: {len(parsed['charges'])} charges, "
                    f"Bond Paid: {parsed['bond_paid']}"
                )

                # Merge parsed charge data into base record
                base.update({
                    "charges": parsed["charges"],
                    "bond_amount": parsed["bond_amount"] or base.get("bond_amount", ""),
                    "bond_paid": parsed["bond_paid"] or base.get("bond_paid", ""),
                    "bond_type": parsed["bond_type"] or base.get("bond_type", ""),
                    "court_type": parsed["court_type"] or base.get("court_type", ""),
                    "case_number": parsed["case_number"] or base.get("case_number", ""),
                    "court_date": parsed["court_date"] or base.get("court_date", ""),
                    "court_time": parsed["court_time"] or base.get("court_time", ""),
                    "court_location": parsed["court_location"] or base.get("court_location", ""),
                })
                enriched.append(base)
                ok += 1
                consecutive_failures = 0  # Reset on success

            except Exception as e:
                logger.warning(f"⚠️ Charges API error for {bn}: {e}")
                enriched.append(base)
                fail += 1
                consecutive_failures += 1

        logger.info(f"✅ Enrichment complete: {ok} success, {fail} failed")
        return enriched

    @staticmethod
    def _parse_charges(charges_array: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Parse the charges API response into structured fields."""
        result = {
            "charges": [],
            "bond_amount": "",
            "bond_paid": "NO",
            "bond_type": "",
            "court_type": "",
            "case_number": "",
            "court_date": "",
            "court_time": "",
            "court_location": "",
        }

        if not charges_array:
            return result

        total_bond = 0.0
        bond_types: Set[str] = set()
        case_numbers: Set[str] = set()
        court_locations: Set[str] = set()
        court_dates: List[Tuple[str, str]] = []
        has_paid_bond = False

        for c in charges_array:
            # Charges
            desc = c.get("offenseDescription", "").strip()
            if desc and len(desc) > 3 and not LeeCountyScraper._is_statute_only(desc):
                cleaned = LeeCountyScraper._clean_charge_text(desc)
                if cleaned:
                    result["charges"].append(cleaned)

            # Bond amount
            if c.get("bondAmount"):
                try:
                    amt = float(str(c["bondAmount"]).replace(",", ""))
                    total_bond += amt
                except (ValueError, TypeError):
                    pass

            # Bond type
            bt = str(c.get("bondTypeName", "")).strip().upper()
            if bt:
                bond_types.add(bt)

            # Bond paid
            if c.get("bondDatePosted") or c.get("bondPosted") or c.get("dateBondPosted"):
                has_paid_bond = True

            # Case number
            cn = str(c.get("caseNumber", "")).strip()
            if cn:
                case_numbers.add(cn)

            # Court location
            cl = str(c.get("courtLocation", "")).strip()
            if cl:
                court_locations.add(cl)

            # Hearing date
            hd = c.get("hearingDate", "")
            if hd:
                parsed = LeeCountyScraper._parse_iso_datetime(hd)
                if parsed[0]:
                    court_dates.append(parsed)

        # Deduplicate charges
        seen: Set[str] = set()
        unique_charges = []
        for charge in result["charges"][:15]:
            key = charge.lower()
            if key not in seen:
                seen.add(key)
                unique_charges.append(charge)
        result["charges"] = unique_charges

        result["bond_amount"] = f"{total_bond:.2f}" if total_bond > 0 else ""
        result["bond_type"] = " / ".join(bond_types)
        result["bond_paid"] = "YES" if has_paid_bond else "NO"
        result["case_number"] = ", ".join(case_numbers)
        result["court_location"] = ", ".join(court_locations)

        if court_dates:
            court_dates.sort(key=lambda x: x[0])
            result["court_date"] = court_dates[0][0]
            result["court_time"] = court_dates[0][1]

        # Infer court type
        first_loc = next(iter(court_locations), "").lower()
        if "county" in first_loc:
            result["court_type"] = "County Court"
        elif "circuit" in first_loc:
            result["court_type"] = "Circuit Court"
        elif "federal" in first_loc:
            result["court_type"] = "Federal Court"

        return result

    # ── Normalizer (port of normalizeArrestRecord_) ──

    def _normalize_record(self, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize a raw API record into a flat intermediate dict."""
        first = self._safe(raw.get("givenName") or raw.get("first_name") or raw.get("firstName") or raw.get("fname"))
        last = self._safe(raw.get("surName") or raw.get("surnames") or raw.get("last_name") or raw.get("lastName") or raw.get("lname"))
        middle = self._safe(raw.get("middleName") or raw.get("middle_name") or raw.get("mname"))
        suffix = self._safe(raw.get("suffix") or raw.get("nameSuffix"))

        booking_number = self._safe(
            raw.get("bookingNumber") or raw.get("booking_number")
            or raw.get("booking_id") or raw.get("bookingNo") or raw.get("id")
        )
        if not booking_number:
            return None

        full = self._build_full_name(first, middle, last, suffix)
        booking_iso = self._safe(raw.get("bookingDate") or raw.get("booked_date") or raw.get("arrestDate"))
        dob_iso = self._safe(raw.get("birthDate") or raw.get("birthdate") or raw.get("dob") or raw.get("dateOfBirth"))

        b_date, b_time = self._parse_iso_datetime(booking_iso)
        d_date, _ = self._parse_iso_datetime(dob_iso)

        address = self._safe(raw.get("address") or raw.get("address1") or raw.get("street"))
        city = self._safe(raw.get("city"))
        state = self._safe(raw.get("state") or "FL")
        zip_code = self._safe(raw.get("zip") or raw.get("zipcode") or raw.get("postalCode"))

        # Determine status and release date
        status = "In Custody"
        release_date = ""
        release_raw = raw.get("releaseDate") or raw.get("release_date") or ""
        if release_raw:
            rd, _ = self._parse_iso_datetime(str(release_raw))
            release_date = rd
            status = "Released"
        if raw.get("inCustody") is False or raw.get("in_custody") is False:
            status = "Released"
        if raw.get("currentStatus") or raw.get("status"):
            status = self._safe(raw.get("currentStatus") or raw.get("status"))

        # Mugshot — skip base64 data (v8.4 fix)
        mugshot = ""
        img_val = raw.get("image") or raw.get("photo")
        if img_val and not self._is_base64_image(img_val):
            mugshot = self._make_absolute_url(img_val)

        return {
            "booking_number": booking_number,
            "person_id": self._safe(raw.get("permId") or raw.get("person_id") or raw.get("personId") or raw.get("inmateId")),
            "full_name": full,
            "first_name": first,
            "middle_name": middle,
            "last_name": last,
            "dob": d_date,
            "booking_date": b_date,
            "booking_time": b_time,
            "status": status,
            "release_date": release_date,
            "facility": self._safe(raw.get("housing") or raw.get("facility") or raw.get("location") or "Lee County Jail"),
            "race": self._safe(raw.get("race")),
            "sex": self._safe(raw.get("sex") or raw.get("gender")),
            "height": self._safe(raw.get("height")),
            "weight": self._safe(raw.get("weight")),
            "address": address,
            "city": city,
            "state": state,
            "zip": zip_code,
            "mugshot_url": mugshot,
            "charges": raw.get("charges", []) if isinstance(raw.get("charges"), list) else [],
            "bond_amount": "",
            "bond_paid": "",
            "bond_type": "",
            "court_type": "",
            "case_number": "",
            "court_date": "",
            "court_time": "",
            "court_location": "",
            "detail_url": f"{BASE_URL}{DETAIL_PAGE}?id={booking_number}",
        }

    def _to_arrest_record(self, n: Dict[str, Any]) -> ArrestRecord:
        """Convert an intermediate normalized dict to an ArrestRecord."""
        charges_str = " | ".join(n.get("charges", [])) if isinstance(n.get("charges"), list) else n.get("charges", "")

        return ArrestRecord(
            County=self.county,
            Booking_Number=n.get("booking_number", ""),
            Person_ID=n.get("person_id", ""),
            Full_Name=n.get("full_name", ""),
            First_Name=n.get("first_name", ""),
            Middle_Name=n.get("middle_name", ""),
            Last_Name=n.get("last_name", ""),
            DOB=n.get("dob", ""),
            Arrest_Date=n.get("booking_date", ""),  # Lee API uses booking date as arrest date
            Booking_Date=n.get("booking_date", ""),
            Booking_Time=n.get("booking_time", ""),
            Status=n.get("status", ""),
            Release_Date=n.get("release_date", ""),
            Facility=n.get("facility", ""),
            Race=n.get("race", ""),
            Sex=n.get("sex", ""),
            Height=n.get("height", ""),
            Weight=n.get("weight", ""),
            Address=n.get("address", ""),
            City=n.get("city", ""),
            State=n.get("state", "FL"),
            ZIP=n.get("zip", ""),
            Mugshot_URL=n.get("mugshot_url", ""),
            Charges=charges_str,
            Bond_Amount=n.get("bond_amount", "0"),
            Bond_Paid=n.get("bond_paid", "NO"),
            Bond_Type=n.get("bond_type", ""),
            Court_Type=n.get("court_type", ""),
            Case_Number=n.get("case_number", ""),
            Court_Date=n.get("court_date", ""),
            Court_Time=n.get("court_time", ""),
            Court_Location=n.get("court_location", ""),
            Detail_URL=n.get("detail_url", ""),
            LastCheckedMode="INITIAL",
        )

    # ── HTTP ──

    def _cleanup(self):
        pass

    def _http_fetch(self, url: str, params: Dict[str, Any] = None):
        """Fetch API using curl_cffi or Scrapfly to bypass IP blocks."""
        import urllib.parse
        from curl_cffi import requests as cffi_requests
        
        if params:
            qs = urllib.parse.urlencode(params)
            full_url = f"{url}?{qs}"
        else:
            full_url = url
            
        scrapfly_key = os.getenv("SCRAPFLY_API_KEY", "")
        
        for attempt in range(RETRY_LIMIT):
            try:
                if scrapfly_key:
                    scrapfly_url = f"https://api.scrapfly.io/scrape?key={scrapfly_key}&url={urllib.parse.quote(full_url)}&asp=true"
                    resp = cffi_requests.get(scrapfly_url, impersonate="chrome110", timeout=45)
                    
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                            content = data.get("result", {}).get("content", "{}")
                            class MockScrapflyResp:
                                def __init__(self, text):
                                    self.text = text
                                    self.status_code = 200
                                def json(self):
                                    import json
                                    return json.loads(self.text)
                            return MockScrapflyResp(content)
                        except Exception:
                            pass
                else:
                    # Fallback to curl_cffi with SOCKS proxy
                    proxies = {"http": SOCKS_PROXY, "https": SOCKS_PROXY} if SOCKS_PROXY else None
                    resp = cffi_requests.get(
                        full_url, 
                        impersonate="chrome110", 
                        proxies=proxies,
                        headers=HEADERS,
                        timeout=30
                    )
                
                status_code = resp.status_code
                if status_code == 200:
                    return resp
                    
                if status_code in (429, 500, 502, 503):
                    sleep_s = BACKOFF_BASE_S * (2**attempt) + random.uniform(0, BACKOFF_BASE_S)
                    logger.warning(f"⏳ HTTP {status_code} retry in {sleep_s:.1f}s")
                    time.sleep(sleep_s)
                    continue
                    
                class MockEmptyResp:
                    status_code = status_code
                    def json(self): return {}
                return MockEmptyResp()
                
            except Exception as e:
                sleep_s = BACKOFF_BASE_S * (2**attempt)
                if attempt < RETRY_LIMIT - 1:
                    logger.warning(f"⚠️ HTTP error, retrying in {sleep_s:.1f}s: {e}")
                    time.sleep(sleep_s)
                else:
                    logger.error(f"❌ HTTP fetch failed after retries: {full_url}")
                    return None
        return None

    # ── Utilities ──

    @staticmethod
    def _safe(val: Any) -> str:
        if val is None:
            return ""
        return str(val).strip()

    @staticmethod
    def _qs(params: Dict[str, Any]) -> str:
        return "&".join(f"{k}={v}" for k, v in params.items())

    @staticmethod
    def _is_base64_image(val: Any) -> bool:
        """Detect base64-encoded image data (v8.4 bugfix)."""
        if not val or not isinstance(val, str):
            return False
        if len(val) > 500:
            return True
        prefix = val[:30]
        return bool(re.match(r"^(/9j/|data:image|iVBOR|R0lGO|AAAA)", prefix, re.IGNORECASE))

    @staticmethod
    def _make_absolute_url(url: str) -> str:
        if not url:
            return ""
        if url.startswith("http"):
            return url
        return f"{BASE_URL}{'/' if not url.startswith('/') else ''}{url}"

    @staticmethod
    def _build_full_name(first: str, middle: str, last: str, suffix: str = "") -> str:
        if not first and not last:
            return ""
        if not last:
            return f"{first} {suffix}".strip()
        if not first:
            return f"{last} {suffix}".strip()
        name = f"{last}, {first}"
        if middle:
            name += f" {middle}"
        if suffix:
            name += f" {suffix}"
        return name

    @staticmethod
    def _parse_iso_datetime(iso: str) -> Tuple[str, str]:
        """Parse ISO datetime string into (date, time) tuple."""
        if not iso:
            return ("", "")
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return (dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"))
        except (ValueError, TypeError):
            return (str(iso), "")

    @staticmethod
    def _clean_charge_text(text: str) -> str:
        """Clean up charge description text."""
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s+\b(?:F|M)\d+\s*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+\bFSS?\s*\d[\d.\-]*\s*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+\bF\.?S\.?\s*\d[\d.\-]*\s*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+\(\d[\d.\-]+\)\s*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+\b\d{3,}\.\d+\s*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*\([FfMm]\d+\)\s*$", "", text, flags=re.IGNORECASE)
        return text.strip()

    @staticmethod
    def _is_statute_only(text: str) -> bool:
        """Check if text is just a statute reference with no useful charge info."""
        t = text.strip()
        patterns = [
            r"^(?:F|M)\d+$",
            r"^FSS?\s*\d[\d.\-]*$",
            r"^F\.?S\.?\s*\d[\d.\-]*$",
            r"^\d[\d.\-]+$",
            r"^N/A$",
        ]
        if len(t) < 4:
            return True
        return any(re.match(p, t, re.IGNORECASE) for p in patterns)

    # ── FirstAppearanceWatcher hook ───────────────────────────────────────────
    def _fetch_single_booking(
        self, booking_id: str, detail_url: str
    ) -> "Optional[ArrestRecord]":
        """
        Re-fetch a single booking by ID from the Lee County charges API.

        Called by FirstAppearanceWatcher every 30 minutes for no-bond records
        within 3 days of arrest, so bond set at first appearance is detected
        and re-alerted promptly.

        Returns None on any failure (watcher falls back to generic HTTP).
        """
        if not booking_id:
            return None
        try:
            url = f"{BASE_URL}{CHARGES_API.format(booking_id=booking_id)}"
            resp = self._http_fetch(url)
            if resp is None or resp.status_code != 200:
                logger.debug(
                    f"Lee _fetch_single_booking: charges API returned "
                    f"{resp.status_code if resp else 'None'} for {booking_id}"
                )
                return None
            charges_json = resp.json()
            if not isinstance(charges_json, list):
                return None
            parsed = self._parse_charges(charges_json)
            base = {
                "booking_number": booking_id,
                "detail_url": detail_url or f"{BASE_URL}{DETAIL_PAGE}?id={booking_id}",
                "charges": parsed.get("charges", []),
                "bond_amount": parsed.get("bond_amount", "0"),
                "bond_paid": parsed.get("bond_paid", "NO"),
                "bond_type": parsed.get("bond_type", ""),
                "court_type": parsed.get("court_type", ""),
                "case_number": parsed.get("case_number", ""),
                "court_date": parsed.get("court_date", ""),
                "court_time": parsed.get("court_time", ""),
                "court_location": parsed.get("court_location", ""),
                "full_name": "", "first_name": "", "middle_name": "", "last_name": "",
                "dob": "", "booking_date": "", "booking_time": "", "status": "In Custody",
                "release_date": "", "facility": "Lee County Jail",
                "race": "", "sex": "", "height": "", "weight": "",
                "address": "", "city": "", "state": "FL", "zip": "",
                "mugshot_url": "", "person_id": "",
            }
            record = self._to_arrest_record(base)
            record.County = self.county
            record.LastCheckedMode = "UPDATE"
            return record
        except Exception as e:
            logger.warning(f"Lee _fetch_single_booking error ({booking_id}): {e}")
            return None
        finally:
            self._cleanup()
