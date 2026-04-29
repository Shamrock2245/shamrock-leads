"""
Hendry County Arrest Scraper — OCV JSON + curl_cffi Detail Enrichment.

Source: Hendry County Sheriff's Office
URL: https://www.hendrysheriff.org/inmateSearch
Method: HTTP bulk fetch from OCV S3 JSON + curl_cffi detail page enrichment

Architecture (2-phase):
  Phase 1: HTTP GET to OCV S3 JSON -> all inmates with demographics (~0.1s)
           Filters: only last MAX_DAYS_BACK days, only "In Custody" inmates
  Phase 2: curl_cffi visits detail pages for recent inmates -> charges + bonds
           Uses Chrome TLS impersonation (no browser automation required)

Self-healing: If Phase 2 fails, Phase 1 data is still returned
with all demographics. Bond amounts default to "0" if enrichment fails.
"""

import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

INMATES_JSON_URL = "https://myocv.s3.amazonaws.com/ocvapps/a102933935/inmates.json"
DETAIL_BASE_URL = "https://www.hendrysheriff.org/inmateSearch"
FACILITY = "Hendry County Jail"

# ── Tuning ────────────────────────────────────────────────────────────────────
MAX_DAYS_BACK = 30            # Only process inmates booked in the last N days
MAX_DETAIL_ENRICHMENT = 60    # Max detail pages to visit per run
DETAIL_DELAY_S = 0.6          # Polite delay between detail page requests
RETRY_LIMIT = 3
BACKOFF_BASE_S = 2.0

# Custody status values that mean "In Custody" vs "Released"
IN_CUSTODY_STATUSES = {"IN"}
RELEASED_STATUSES = {"OUT", "RELEASED", "RWP", "BOND", "ROR", "TRANSFERRED"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class HendryCountyScraper(BaseScraper):
    """Hendry County (FL) arrest scraper - OCV JSON + curl_cffi detail enrichment."""

    @property
    def county(self) -> str:
        return "Hendry"

    def scrape(self) -> List[ArrestRecord]:
        """2-phase scrape: bulk JSON then selective detail enrichment."""
        try:
            import requests
        except ImportError:
            logger.error("requests not installed")
            return []

        records = self._phase1_bulk_json(requests)
        if not records:
            logger.warning("Phase 1 returned 0 records")
            return []

        logger.info(f"Phase 1 complete: {len(records)} in-custody records from OCV JSON")
        self._phase2_enrich_details(records)
        return records

    # ── Phase 1: Bulk JSON fetch with date + custody filtering ────────────────
    def _phase1_bulk_json(self, requests_mod) -> List[ArrestRecord]:
        """Fetch all inmates from OCV S3 JSON endpoint, filtered by date and custody."""
        try:
            resp = requests_mod.get(
                INMATES_JSON_URL,
                headers={"User-Agent": HEADERS["User-Agent"]},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Hendry JSON fetch failed: {e}")
            return []

        if not isinstance(data, list):
            logger.error(f"Unexpected JSON type: {type(data)}")
            return []

        # Sort by date descending (newest first)
        data.sort(key=lambda x: x.get("date", {}).get("sec", 0), reverse=True)

        # Date cutoff: only process recent bookings
        cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=MAX_DAYS_BACK)).timestamp())

        records = []
        seen = set()
        skipped_old = 0
        skipped_released = 0
        total_processed = 0

        for entry in data:
            total_processed += 1

            # ── Date filter: skip old records ──
            date_obj = entry.get("date", {})
            if isinstance(date_obj, dict) and "sec" in date_obj:
                entry_ts = int(date_obj.get("sec", 0))
                if entry_ts < cutoff_ts:
                    skipped_old += 1
                    continue  # Too old — stop processing (data is sorted desc)

            try:
                record = self._parse_json_entry(entry)
                if not record:
                    continue

                # ── Custody filter: skip released inmates ──
                raw_status = getattr(record, '_raw_custody_status', '').upper()
                if raw_status in RELEASED_STATUSES:
                    skipped_released += 1
                    continue

                if record.Booking_Number in seen:
                    continue
                seen.add(record.Booking_Number)
                records.append(record)

            except Exception as e:
                logger.warning(f"Error parsing {entry.get('title', '?')}: {e}")

        logger.info(
            f"[Hendry] JSON processing: {total_processed} total, "
            f"{len(records)} kept, {skipped_old} too old (>{MAX_DAYS_BACK}d), "
            f"{skipped_released} released"
        )
        return records

    def _parse_json_entry(self, entry: dict) -> Optional[ArrestRecord]:
        """Parse a single OCV JSON entry into an ArrestRecord."""
        full_name = entry.get("title", "").strip()
        first_name = entry.get("firstName", "").strip()
        last_name = entry.get("lastName", "").strip()

        if not full_name:
            return None

        middle_name = ""
        if not last_name and "," in full_name:
            parts = full_name.split(",", 1)
            last_name = parts[0].strip()
            fp = parts[1].strip().split()
            first_name = fp[0] if fp else ""
            middle_name = " ".join(fp[1:]) if len(fp) > 1 else ""
        else:
            tf = entry.get("titleWithFirst", "").strip().split()
            if len(tf) > 2:
                middle_name = " ".join(tf[1:-1])

        inmate_id = entry.get("inmateID", "")
        if not inmate_id:
            return None

        booking_date = ""
        date_obj = entry.get("date", {})
        if isinstance(date_obj, dict) and "sec" in date_obj:
            try:
                ts = int(date_obj["sec"])
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                booking_date = dt.strftime("%m/%d/%Y %H:%M")
            except (ValueError, TypeError, OSError):
                pass

        mugshot_url = ""
        images = entry.get("images", [])
        if images and isinstance(images, list):
            img = images[0] if images else {}
            if isinstance(img, dict):
                large = img.get("large", "")
                if large and "missing-image" not in large:
                    mugshot_url = large

        # Parse demographics from the HTML content field
        demos = self._parse_content_html(entry.get("content", ""))

        if not booking_date and demos.get("booking_date"):
            booking_date = demos["booking_date"]

        # Build detail URL from internal _id
        detail_id = ""
        id_obj = entry.get("_id", {})
        if isinstance(id_obj, dict):
            detail_id = id_obj.get("$id", "")
        elif isinstance(id_obj, str):
            detail_id = id_obj
        detail_url = f"{DETAIL_BASE_URL}/{detail_id}" if detail_id else DETAIL_BASE_URL

        # Determine custody status
        raw_custody = demos.get("custody_status_raw", "")
        is_in_custody = raw_custody.upper() in IN_CUSTODY_STATUSES or raw_custody == ""

        record = ArrestRecord(
            County=self.county,
            Booking_Number=inmate_id,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB=demos.get("dob", ""),
            Age_At_Arrest=demos.get("age", ""),
            Booking_Date=booking_date,
            Status="In Custody" if is_in_custody else "Released",
            Release_Date="",
            Facility=FACILITY,
            Race=demos.get("race", ""),
            Sex=demos.get("gender", ""),
            Height=demos.get("height", ""),
            Weight=demos.get("weight", ""),
            Address=demos.get("address", ""),
            City=demos.get("city", ""),
            State=demos.get("state", "FL"),
            ZIP=demos.get("zip", ""),
            Mugshot_URL=mugshot_url,
            Charges="",
            Bond_Amount="0",
            Bond_Paid="NO",
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        )
        # Attach raw custody status for filtering (not persisted)
        record._detail_id = detail_id
        record._raw_custody_status = raw_custody
        return record

    # ── Phase 2: curl_cffi detail enrichment (replaces DrissionPage) ──────────
    def _phase2_enrich_details(self, records: List[ArrestRecord]) -> None:
        """Visit detail pages via curl_cffi to extract charges and bond amounts."""
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("[Hendry] curl_cffi/bs4 not installed — skipping charge enrichment")
            return

        to_enrich = [r for r in records[:MAX_DETAIL_ENRICHMENT]
                     if getattr(r, '_detail_id', '')]
        if not to_enrich:
            logger.info("[Hendry] No records to enrich")
            return

        logger.info(f"[Hendry] Phase 2: enriching {len(to_enrich)} inmates with charges via curl_cffi")

        session = cffi_requests.Session()
        enriched = 0
        with_charges = 0

        for i, record in enumerate(to_enrich):
            try:
                resp = self._fetch(session, "GET", record.Detail_URL, extra_headers={
                    "Referer": DETAIL_BASE_URL,
                })

                if not resp or resp.status_code != 200:
                    continue

                html = resp.text

                # The detail page is a React SPA that may or may not render
                # charges in the initial HTML. OCV sometimes includes charge
                # data in inline script or in the page content.
                charges, total_bond = self._extract_charges_from_html(html)

                if not charges:
                    # Fallback: try plain text extraction
                    text = BeautifulSoup(html, "html.parser").get_text(separator="\n")
                    charges, total_bond = self._extract_charges_from_text(text)

                if charges:
                    record.Charges = charges
                    with_charges += 1
                if total_bond > 0:
                    record.Bond_Amount = str(total_bond)

                enriched += 1

                if (i + 1) % 10 == 0:
                    logger.info(f"[Hendry] Phase 2 progress: {i+1}/{len(to_enrich)}")

            except Exception as e:
                logger.warning(f"[Hendry] Enrichment failed for {record.Full_Name}: {e}")

            time.sleep(DETAIL_DELAY_S)

        logger.info(
            f"[Hendry] Phase 2 done: visited {enriched}/{len(to_enrich)} detail pages, "
            f"{with_charges} have charges"
        )
        if enriched > 0 and with_charges == 0:
            logger.warning(
                f"[Hendry] ⚠️ Phase 2 extracted ZERO charges from {enriched} detail pages — "
                f"OCV detail pages may require JS rendering for charges"
            )

    # ── HTTP helper with curl_cffi TLS impersonation ──────────────────────────
    def _fetch(self, session, method: str, url: str, extra_headers: dict = None, **kwargs):
        """HTTP request with Chrome TLS impersonation + retry logic."""
        headers = {**HEADERS}
        if extra_headers:
            headers.update(extra_headers)

        for attempt in range(RETRY_LIMIT):
            try:
                resp = session.request(
                    method, url, headers=headers, impersonate="chrome124",
                    timeout=30, allow_redirects=True, **kwargs
                )
                if resp.status_code == 200:
                    return resp
                if resp.status_code in (429, 500, 502, 503):
                    sleep_s = BACKOFF_BASE_S * (2 ** attempt)
                    logger.warning(f"[Hendry] HTTP {resp.status_code}, retry in {sleep_s:.1f}s")
                    time.sleep(sleep_s)
                    continue
                return resp
            except Exception as e:
                sleep_s = BACKOFF_BASE_S * (2 ** attempt)
                if attempt < RETRY_LIMIT - 1:
                    logger.warning(f"[Hendry] HTTP error, retrying in {sleep_s:.1f}s: {e}")
                    time.sleep(sleep_s)
                else:
                    logger.error(f"[Hendry] HTTP failed after {RETRY_LIMIT} retries: {e}")
                    return None
        return None

    # ── Charge extraction ─────────────────────────────────────────────────────
    @staticmethod
    def _extract_charges_from_text(text: str) -> Tuple[str, float]:
        """Extract charge descriptions and bond amounts from detail page text."""
        charges_list = []
        total_bond = 0.0

        charge_descs = re.findall(r"Charge Description:\s*(.+?)(?:\n|Bond)", text)
        bond_amounts = re.findall(r"Bond Amount:\s*\$?([\d,]+\.?\d*)", text)

        for desc in charge_descs:
            clean = desc.strip()
            if clean:
                charges_list.append(clean)

        for amt in bond_amounts:
            try:
                val = float(amt.replace(",", ""))
                if val > 0:
                    total_bond += val
            except (ValueError, TypeError):
                pass

        return " | ".join(charges_list) if charges_list else "", total_bond

    @staticmethod
    def _extract_charges_from_html(html: str) -> Tuple[str, float]:
        """Try to extract charges from OCV inline JSON or rendered HTML."""
        charges_list = []
        total_bond = 0.0

        # OCV sometimes embeds charge data in JSON within script tags
        # Look for chargeDescription and bondAmount in inline scripts
        charge_matches = re.findall(
            r'"chargeDescription"\s*:\s*"([^"]+)"', html
        )
        bond_matches = re.findall(
            r'"bondAmount"\s*:\s*"?\$?([\d,]+\.?\d*)"?', html
        )

        for desc in charge_matches:
            clean = desc.strip()
            if clean:
                charges_list.append(clean)

        for amt in bond_matches:
            try:
                val = float(amt.replace(",", ""))
                if val > 0:
                    total_bond += val
            except (ValueError, TypeError):
                pass

        return " | ".join(charges_list) if charges_list else "", total_bond

    # ── Demographics parsing ──────────────────────────────────────────────────
    @staticmethod
    def _parse_content_html(html: str) -> dict:
        """Parse the HTML content field for demographics."""
        if not html:
            return {}

        text = re.sub(r"<[^>]+>", "\n", html)
        text = re.sub(r"\n+", "\n", text).strip()
        result = {}

        m = re.search(r"Main Address:\s*\n(.+?)(?:\n|$)", text)
        if m:
            addr = m.group(1).strip()
            if addr.upper() not in ("HOMELESS AT THIS TIME", "STILL AT LARGE", ""):
                result["address"] = addr

        m = re.search(
            r"(?:Main Address:.*?\n.+?\n)([A-Z][A-Za-z\s]+),?\s*([A-Z]{2})\s*(\d{5})?",
            text,
        )
        if m:
            city = m.group(1).strip()
            if "Currently Unavailable" not in city:
                result["city"] = city
            result["state"] = m.group(2)
            if m.group(3):
                result["zip"] = m.group(3)

        m = re.search(r"Height:\s*(\d+)\s*ft\s*(\d+)", text)
        if m:
            result["height"] = f"{m.group(1)}'{m.group(2)}\""

        m = re.search(r"Weight:\s*(\d+)\s*lbs?", text)
        if m:
            result["weight"] = f"{m.group(1)} lbs"

        m = re.search(r"Gender:\s*([A-Z])", text)
        if m:
            result["gender"] = m.group(1)

        m = re.search(r"Race:\s*([A-Z]+)", text)
        if m:
            result["race"] = m.group(1)

        # Extract age (visible on website detail cards)
        m = re.search(r"Age:\s*(\d+)", text)
        if m:
            result["age"] = m.group(1)

        # Custody Status: keep raw value for filtering
        m = re.search(r"Custody Status:\s*(\S+)", text)
        if m:
            raw_status = m.group(1).upper()
            result["custody_status_raw"] = raw_status

        m = re.search(r"Booked Date:\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})", text)
        if m:
            result["booking_date"] = m.group(1)

        return result

    # ── Single booking fetch (for FirstAppearanceWatcher) ────────────────────
    def _fetch_single_booking(
        self, booking_id: str, detail_url: str = ""
    ) -> Optional[ArrestRecord]:
        """
        Re-fetch a single Hendry County booking by navigating to its
        detail URL via curl_cffi and re-running charge extraction.
        """
        if not detail_url:
            return None

        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            logger.error("[Hendry] curl_cffi not installed")
            return None

        session = cffi_requests.Session()

        try:
            resp = self._fetch(session, "GET", detail_url, extra_headers={
                "Referer": DETAIL_BASE_URL,
            })

            if not resp or resp.status_code != 200:
                return None

            html = resp.text
            charges, total_bond = self._extract_charges_from_html(html)

            if not charges:
                from bs4 import BeautifulSoup
                text = BeautifulSoup(html, "html.parser").get_text(separator="\n")
                charges, total_bond = self._extract_charges_from_text(text)

            record = ArrestRecord(
                County=self.county,
                Booking_Number=booking_id,
                Charges=charges,
                Bond_Amount=str(total_bond) if total_bond > 0 else "0",
                Bond_Paid="NO",
                Detail_URL=detail_url,
                Status="In Custody",
                Facility=FACILITY,
                LastCheckedMode="UPDATE",
            )
            return record

        except Exception as e:
            logger.warning(f"[Hendry] _fetch_single_booking error ({booking_id}): {e}")
            return None
