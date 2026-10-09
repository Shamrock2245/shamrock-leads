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

2026-10-09: no source booking number; internal natural key (owner exception).
The layer (a Table: miamidade_jail_data/FeatureServer/0) publishes BookDate,
Defendant, Address, CityStateZip, DOB, ChargeCode1-3 (statute codes),
Charge1/Code2/Charge3, Zip, Filler (always null), City, State, Zip1, plus the
system ObjectId and GlobalID. There is no booking, jail or case number, and the
ObjectId/GlobalID are map row ids reissued on every republish (2026-10-09
08:03 ET: 840 of 841 snapshot rows got a new GlobalID). #166 failed closed.

Owner exception (Brendan, 2026-10-09 9:32 AM ET): no source booking id, an
internal natural key is approved, and the county reopens as ``unverified``.

* ``Booking_Number`` stays blank. It is never a row id or a hash.
* ``extra_data["md_dedupe"]`` is ``md_dedupe_key``: sha256 of the normalised
  defendant, DOB and BookDate. Charges are NOT in the key, so an amended
  charge updates the same record. When the row has no DOB the key falls back
  to defendant + BookDate + full verbatim charges and the record is flagged
  ``md_key_fallback`` (counts logged, never names).
* Two rows in one run with the same key (same person, DOB and BookDate, e.g. a
  re-booking the same day) are merged into one record with the union of their
  charges, never written as two rows.
* ``MongoWriter`` upserts on the key through a narrow, allow-listed path
  (``core.booking_identity``); every other county keeps the blank-booking
  guard. Hydrate, PDF/DocuSeal and API booking-number displays print it blank.
"""

import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple

import requests

from scrapers.base_scraper import BaseScraper
from core.booking_identity import MD_KEY_PREFIX
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
# DOB is part of the internal natural key. ObjectId is only used to check
# paging within one run (it is reissued on republish and is never a key).
OUT_FIELDS = "ObjectId,BookDate,Defendant,DOB,Charge1,Code2,Charge3"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://gis-mdc.opendata.arcgis.com/",
    "Origin": "https://gis-mdc.opendata.arcgis.com",
}

REQUIRED_FIELDS = ("ObjectId", "BookDate", "Defendant", "DOB", "Charge1", "Code2", "Charge3")

MD_DEDUPE_VERSION = MD_KEY_PREFIX.rstrip(":")  # "md_dedupe_v2"
MD_DEDUPE_LABEL = (
    "internal dedupe key, NOT a booking number: sha256 of normalised "
    "Defendant | DOB | BookDate (fallback without DOB: Defendant | BookDate | full verbatim charges)"
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


def _ms_to_date(value: Any) -> str:
    """ArcGIS epoch-ms date (midnight ET, i.e. 04:00/05:00 UTC) -> YYYY-MM-DD."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def _norm_charges(charges: Any) -> str:
    return " | ".join(_norm(c) for c in str(charges or "").split("|") if _norm(c))


def md_dedupe_key(full_name: Any, dob: Any, booking_date: Any, charges: Any = "") -> Tuple[str, bool]:
    """Internal Miami-Dade natural key ``(key, is_fallback)``. Never a booking number.

    Inputs are the stored record fields (Full_Name, DOB and Booking_Date as
    YYYY-MM-DD, Charges joined with " | "), so the same key can be recomputed
    from stored arrests docs. With a DOB the key is defendant + DOB + BookDate
    (charges excluded, so an amended charge updates the same record); without
    one it falls back to defendant + BookDate + full verbatim charges.
    Returns ("", False) when the defendant or booking date is missing.
    """
    name, date = _norm(full_name), _norm_date(booking_date)
    if not name or not date:
        return "", False
    dob_norm = _norm_date(dob) if str(dob or "").strip() else ""
    if dob_norm:
        payload, fallback = f"{MD_DEDUPE_VERSION}|dob|{name}|{dob_norm}|{date}", False
    else:
        payload, fallback = f"{MD_DEDUPE_VERSION}|fallback|{name}|{date}|{_norm_charges(charges)}", True
    return MD_KEY_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest(), fallback


def _merge_charges(a: str, b: str) -> str:
    out: List[str] = []
    for c in [x.strip() for x in f"{a}|{b}".split("|")]:
        if c and _norm(c) not in {_norm(x) for x in out}:
            out.append(c)
    return " | ".join(out)


class MiamiDadeContractError(RuntimeError):
    """The ArcGIS jail-bookings layer no longer matches the verified contract."""


class MiamiDadeCountyScraper(BaseScraper):
    """Miami-Dade County (FL) arrest scraper — ArcGIS Open Data API.

    No source booking number: rows are keyed on the internal natural key
    (owner exception, Brendan 2026-10-09 9:32 AM ET); Booking_Number stays blank."""

    SOURCE_CONTRACT_VALIDATED = True
    # Narrow opt-in: records with a blank Booking_Number are kept only when
    # core.booking_identity.internal_natural_key accepts them (FL/Miami-Dade,
    # exact md_dedupe_v2 pattern).
    ALLOWS_INTERNAL_NATURAL_KEY = True

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
        by_key: Dict[str, ArrestRecord] = {}
        merged_same_key = 0
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
                if not record:
                    continue
                key = record.extra_data["md_dedupe"]
                prior = by_key.get(key)
                if prior is None:
                    by_key[key] = record
                    all_records.append(record)
                else:
                    # Same person, DOB and BookDate twice in one snapshot (e.g. a
                    # same-day re-booking): one record with both charge lists.
                    prior.Charges = _merge_charges(prior.Charges, record.Charges)
                    merged_same_key += 1
            if not data.get("exceededTransferLimit", False) and fetched < expected:
                break
            offset += PAGE_SIZE
            time.sleep(1.0)  # Be nice to the API

        if fetched != expected:
            raise MiamiDadeContractError(
                f"Miami-Dade: walked {fetched} of {expected} bookings (layer changed mid-walk or paging drift)"
            )

        fallback = sum(1 for r in all_records if r.extra_data.get("md_key_fallback"))
        logger.info(
            "[%s] Scrape complete: %d records in %.1fs (md_key_fallback=%d, same_key_rows_merged=%d)",
            self.county, len(all_records), time.time() - start_time, fallback, merged_same_key,
        )
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
            dob_str = _ms_to_date(attrs.get("DOB"))
            dedupe, fallback = md_dedupe_key(full_name, dob_str, booking_date_str, charges_str)
            if not dedupe:
                return None

            return ArrestRecord(
                County=self.county,
                State="FL",
                Booking_Number="",  # no source booking number; never a row id or hash
                Full_Name=full_name,
                DOB=dob_str,
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
                    "md_key_fallback": fallback,
                    "bond_published": False,
                },
            )
        except Exception as e:
            # Row id and error type only: the row carries name and DOB, and an
            # exception message may echo a field value.
            logger.warning("[%s] Error parsing record ObjectId=%s: %s", self.county, attrs.get("ObjectId"), type(e).__name__)
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
