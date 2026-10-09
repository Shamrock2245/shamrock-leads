"""
Miami-Dade County Arrest Scraper — ArcGIS Open Data API Pattern.
Source: Miami-Dade County Open Data Hub (ArcGIS REST API)
API: https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/ArcGIS/rest/services/miamidade_jail_data/FeatureServer/0
Features:
- ArcGIS REST API pagination (resultOffset/resultRecordCount)
- Date-based filtering to only fetch recent bookings
- Data-minimized public-field retrieval (no address or ZIP fields)
- Fail-closed identity and booking-date validation
- ArcGIS layer has NO bond/bail fields (confirmed 2026-10-07, 2026-10-08), so
  Bond_Amount stays "" (unknown) and is never written as $0
- Plain ``requests``; any HTTP/ArcGIS error, field drift or short page walk
  raises ``MiamiDadeContractError`` (BaseScraper alerts) instead of returning
  a silently truncated batch

2026-10-09: FAIL CLOSED, no source booking number.
The layer (a Table: miamidade_jail_data/FeatureServer/0) publishes BookDate,
Defendant, Address, CityStateZip, DOB, ChargeCode1-3 (statute codes),
Charge1/Code2/Charge3, Zip, Filler (always null), City, State, Zip1, plus the
system ObjectId and GlobalID. There is no booking, jail or case number. The
ObjectId/GlobalID are map row ids, not booking keys: after the 2026-10-09
08:03 ET republish, 840 of the 841 rows in the 2026-10-08 snapshot had a new
GlobalID and only 2 kept their ObjectId. So:

* ``Booking_Number`` is left blank. It is never filled with a row id or a
  derived value, so hydrate, PDF/DocuSeal and UI booking-number fields stay
  empty instead of printing a hash.
* ``extra_data["md_dedupe"]`` holds an internal dedupe key,
  ``md_dedupe_key(Full_Name, Booking_Date, Charges)`` (sha256 over the
  normalised defendant, booking date and full verbatim charge list), clearly
  labelled as NOT a booking number.
* The county is fail_closed (``SOURCE_CONTRACT_VALIDATED = False``): the
  source-contract rule needs a real source booking id, and the writer keys on
  booking_number, so no Miami-Dade row is fetched or written until an owner
  decision on keying by ``md_dedupe`` (and a backed-up cleanup of the GlobalID
  duplicates already stored).
"""

import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# ── Config ──
ARCGIS_BASE_URL = "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/ArcGIS/rest/services/miamidade_jail_data/FeatureServer/0"
QUERY_ENDPOINT = f"{ARCGIS_BASE_URL}/query"
DAYS_BACK = 3  # Fetch bookings from the last 3 days
PAGE_SIZE = 200
MAX_PAGES = 10  # 2,000 rows; the layer adds ~160 bookings/day (476 for 3 days on 2026-10-08)
REQUEST_TIMEOUT = 30
# Retrieve only source fields needed for identity, deduplication, and charges.
# ObjectId is only used to check paging within one run (it is reissued on
# republish and is never stored as a key).
OUT_FIELDS = "ObjectId,BookDate,Defendant,Charge1,Code2,Charge3"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://gis-mdc.opendata.arcgis.com/",
    "Origin": "https://gis-mdc.opendata.arcgis.com",
}

REQUIRED_FIELDS = ("ObjectId", "BookDate", "Defendant", "Charge1", "Code2", "Charge3")

MD_DEDUPE_VERSION = "md_dedupe_v1"
MD_DEDUPE_LABEL = (
    "internal dedupe key, NOT a booking number: sha256 of normalised "
    "Defendant | BookDate | full verbatim charges"
)
_WS_RE = re.compile(r"\s+")


def _norm(text: Any) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip().upper()


def _norm_date(value: Any) -> str:
    s = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def md_dedupe_key(full_name: Any, booking_date: Any, charges: Any) -> str:
    """Internal Miami-Dade dedupe key. Never a booking number.

    Inputs are the stored record fields (Full_Name, Booking_Date as YYYY-MM-DD,
    Charges joined with " | "), so the same key can be recomputed from stored
    arrests docs. Returns "" when the defendant or date is missing.
    """
    name, date = _norm(full_name), _norm_date(booking_date)
    if not name or not date:
        return ""
    charges_norm = " | ".join(_norm(c) for c in str(charges or "").split("|") if _norm(c))
    digest = hashlib.sha256(f"{MD_DEDUPE_VERSION}|{name}|{date}|{charges_norm}".encode("utf-8")).hexdigest()
    return f"{MD_DEDUPE_VERSION}:{digest}"


class MiamiDadeContractError(RuntimeError):
    """The ArcGIS jail-bookings layer no longer matches the verified contract."""


class MiamiDadeCountyScraper(BaseScraper):
    """Miami-Dade County (FL) arrest scraper — ArcGIS Open Data API.

    Fail closed (2026-10-09): the layer publishes no source booking number and
    its row ids are reissued on every republish."""

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Miami-Dade ArcGIS jail layer publishes no booking, jail or case number; "
        "ObjectId/GlobalID are map row ids reissued on republish (2026-10-09 08:03 ET: "
        "840 of 841 snapshot rows got a new GlobalID). Writes stay off until an owner "
        "decision on keying by the internal md_dedupe key."
    )

    @property
    def county(self) -> str:
        return "Miami-Dade"

    @property
    def roster_url(self) -> str:
        return "https://gis-mdc.opendata.arcgis.com/datasets/jail-bookings-may-29-2015-to-current"

    def scrape(self) -> List[ArrestRecord]:
        """Main scrape pipeline: fetch paginated ArcGIS data → return records."""
        start_time = time.time()
        logger.info(f"[{self.county}] Starting ArcGIS Open Data scrape...")

        # Calculate the cutoff date
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d 00:00:00")
        
        # ArcGIS SQL where clause
        where_clause = f"BookDate >= timestamp '{cutoff_str}'"
        
        all_records = []
        offset = 0
        
        session = requests.Session()
        session.headers.update(HEADERS)

        expected = self._count(session, where_clause)
        if expected > PAGE_SIZE * MAX_PAGES:
            raise MiamiDadeContractError(
                f"Miami-Dade: {expected} bookings in {DAYS_BACK} days exceeds the {PAGE_SIZE * MAX_PAGES}-row page cap"
            )

        seen = set()
        fetched = 0
        for page in range(MAX_PAGES):
            if fetched >= expected:
                break
            params = {
                "where": where_clause,
                "outFields": OUT_FIELDS,
                "orderByFields": "BookDate DESC, ObjectId DESC",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
                "f": "json",
            }
            logger.debug(f"[{self.county}] Fetching page {page+1} (offset {offset})...")
            data = self._get_json(session, params)
            features = data.get("features")
            if not isinstance(features, list):
                raise MiamiDadeContractError("Miami-Dade: query response has no features list")
            if not features:
                break
            for feature in features:
                attrs = feature.get("attributes") if isinstance(feature, dict) else None
                if not isinstance(attrs, dict) or any(k not in attrs for k in REQUIRED_FIELDS):
                    raise MiamiDadeContractError("Miami-Dade: feature attribute drift")
                oid = attrs.get("ObjectId")
                if oid in seen:
                    raise MiamiDadeContractError(f"Miami-Dade: ObjectId {oid} repeated across pages")
                seen.add(oid)
                fetched += 1
                record = self._parse_record(attrs)
                if record:
                    all_records.append(record)
            if not data.get("exceededTransferLimit", False) and fetched < expected:
                break
            offset += PAGE_SIZE
            time.sleep(1.0)  # Be nice to the API

        if fetched != expected:
            raise MiamiDadeContractError(
                f"Miami-Dade: walked {fetched} of {expected} bookings (layer changed mid-walk or paging drift)"
            )

        logger.info(f"[{self.county}] Scrape complete. Found {len(all_records)} records in {time.time() - start_time:.1f}s.")
        return all_records

    def _get_json(self, session: requests.Session, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = session.get(QUERY_ENDPOINT, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise MiamiDadeContractError(f"Miami-Dade: ArcGIS query failed: {exc}") from exc
        if not isinstance(data, dict):
            raise MiamiDadeContractError("Miami-Dade: ArcGIS response is not a JSON object")
        if "error" in data:
            raise MiamiDadeContractError(f"Miami-Dade: ArcGIS error {data['error'].get('code') if isinstance(data['error'], dict) else data['error']}")
        return data

    def _count(self, session: requests.Session, where_clause: str) -> int:
        data = self._get_json(session, {"where": where_clause, "returnCountOnly": "true", "f": "json"})
        count = data.get("count")
        if not isinstance(count, int) or count < 0:
            raise MiamiDadeContractError("Miami-Dade: returnCountOnly gave no count")
        return count

    def _parse_record(self, attrs: Dict[str, Any]) -> Optional[ArrestRecord]:
        """Convert an ArcGIS feature attribute dict into an ArrestRecord."""
        try:
            # ArcGIS dates are in milliseconds since epoch
            book_date_ms = attrs.get("BookDate")
            booking_date_str = ""
            if book_date_ms:
                dt = datetime.fromtimestamp(book_date_ms / 1000.0, tz=timezone.utc)
                booking_date_str = dt.strftime("%Y-%m-%d")

            # Name parsing
            full_name = attrs.get("Defendant", "").strip()
            first_name, middle_name, last_name = self._parse_name(full_name)
            
            # Charges — layer fields are Charge1, Code2 (charge 2 text), Charge3.
            # There is no bond/bail attribute on this FeatureServer (2026-10-07).
            charges_list = []
            for key in ("Charge1", "Code2", "Charge3"):
                charge = attrs.get(key)
                if charge and str(charge).strip():
                    charges_list.append(str(charge).strip())

            charges_str = " | ".join(charges_list)  # "" when the layer lists none; never a placeholder
            
            # The layer has no booking number and its row ids (ObjectId/GlobalID)
            # are reissued on republish, so Booking_Number stays blank and the
            # internal md_dedupe key carries identity. The source is
            # date-granular, so no booking time is guessed. A row without a
            # complete name or date is dropped.
            if not booking_date_str or not full_name or len(full_name.replace(',', ' ').split()) < 2:
                return None
            dedupe = md_dedupe_key(full_name, booking_date_str, charges_str)

            return ArrestRecord(
                County=self.county,
                State="FL",
                Booking_Number="",  # no source booking number; never a row id or hash
                Full_Name=full_name,
                First_Name=first_name,
                Middle_Name=middle_name,
                Last_Name=last_name,
                Booking_Date=booking_date_str,
                Charges=charges_str,
                # ArcGIS miamidade_jail_data has no bond/bail fields: unknown, never $0.
                Bond_Amount="",
                Status="Unknown",
                Facility="Miami-Dade Corrections",
                LastCheckedMode="INITIAL",
                extra_data={
                    "booking_key_origin": "none: the source publishes no booking number",
                    "md_dedupe": dedupe,
                    "md_dedupe_label": MD_DEDUPE_LABEL,
                    "bond_published": False,
                },
            )
        except Exception as e:
            logger.warning(f"[{self.county}] Error parsing record {attrs.get('ObjectId')}: {e}")
            return None

    @staticmethod
    def _parse_name(name_str: str) -> tuple[str, str, str]:
        """Parse 'LAST FIRST MIDDLE' or 'LAST, FIRST MIDDLE' into components."""
        if not name_str:
            return "", "", ""
            
        # Handle "LAST, FIRST MIDDLE"
        if "," in name_str:
            parts = name_str.split(",", 1)
            last_name = parts[0].strip()
            first_middle = parts[1].strip().split()
            first_name = first_middle[0] if first_middle else ""
            middle_name = " ".join(first_middle[1:]) if len(first_middle) > 1 else ""
            return first_name, middle_name, last_name
            
        # Handle "LAST FIRST MIDDLE" (common in Miami-Dade data)
        parts = name_str.split()
        if len(parts) == 1:
            return "", "", parts[0]
        elif len(parts) == 2:
            return parts[1], "", parts[0]
        else:
            # Assume first word is last name, second is first name, rest is middle
            return parts[1], " ".join(parts[2:]), parts[0]
