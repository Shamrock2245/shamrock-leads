"""
Polk County Arrest Scraper — PCSO Jail Inquiry (Kendo UI JSON API).
Source: Polk County Sheriff's Office
URL: https://polksheriff.org/JailInquiry
Method: Direct HTTP requests POST to SearchJail and GetCharges API endpoints,
through ``scrapers.plain_http`` (honest User-Agent, certificate verified).

Failure is visible, never swallowed:
  * 401/403/429, a challenge page or a reset -> ``egress_block:`` (EgressBlocked)
  * certificate verification failure -> ``tls_cert_invalid:`` (SourceTLSInvalid)
  * any other network failure (refused connection, timeout, ...) -> ``PolkNetworkError``
    with a scrubbed message (no URL, booking number or person data); the original
    exception stays on ``__cause__``
  * SearchJail non-200 -> ``PolkHTTPError`` (carries ``status_code``; classified by status)
  * SearchJail / GetCharges payload that isn't a list of objects -> ``ParseDriftError``
  * a per-booking detail miss (profile non-200, no anti-forgery token, GetCharges
    non-200 or non-JSON) leaves charges and bond "" and is counted in one warning
    per run (no names or ids)

Status is as published: a release date gives "Released" (status_source
``published_release_date``), a published status field is kept
(``published_status_field``), else "". SearchJail is a booking-date search that
returns released bookings too, so presence is not custody.
"""
import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from bs4 import BeautifulSoup
import requests

from scrapers.base_scraper import BaseScraper
from scrapers.published_bond import charge_bond_amount, total_published_bond
from scrapers.plain_http import make_session, request
from scrapers.scraper_resilience import ParseDriftError
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.polksheriff.org"
SEARCH_URL = f"{BASE_URL}/detention/jail-inquiry/SearchJail/"
CHARGES_URL = f"{BASE_URL}/inmate/GetCharges"
FACILITY = "Polk County Jail"
DAYS_BACK = 3  # 3 days of booking date coverage

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
}


RACE_MAP = {
    "W": "White", "B": "Black", "H": "Hispanic",
    "A": "Asian", "I": "American Indian", "U": "Unknown",
}

_STATUS_KEYS = ("Status", "status", "CustodyStatus", "InmateStatus")
STATUS_SOURCE_RELEASE_DATE = "published_release_date"
STATUS_SOURCE_FIELD = "published_status_field"


class PolkHTTPError(RuntimeError):
    """A non-200 SearchJail response that isn't a block; Health classifies it by status, not as drift."""

    def __init__(self, status_code: int, step: str):
        self.status_code = status_code
        super().__init__(f"Polk: HTTP {status_code} from {step}")


class PolkNetworkError(requests.exceptions.ConnectionError):
    """A non-block network failure. The message names only the step and exception type:
    requests/urllib3 messages often embed the URL (``/inmate-profile/<booking>``), and
    BaseScraper logs and alerts ``str(e)``. Health still classifies it as network (retryable)."""

    def __init__(self, step: str, cause: BaseException):
        super().__init__(f"Polk: network error on {step} ({type(cause).__name__})")


def _call(session, method: str, url: str, step: str, **kw):
    """plain_http.request; blocks / TLS raise as-is, other network errors are re-raised scrubbed."""
    try:
        return request(session, method, url, county="Polk", **kw)
    except requests.exceptions.RequestException as exc:
        raise PolkNetworkError(step, exc) from exc


def published_status_fields(b, release_date: str = "") -> Tuple[str, str]:
    """(Status, status_source): release date -> Released; else a published status; else ("", "")."""
    if release_date:
        return "Released", STATUS_SOURCE_RELEASE_DATE
    if isinstance(b, dict):
        for key in _STATUS_KEYS:
            val = b.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip(), STATUS_SOURCE_FIELD
    return "", ""


def published_status(b, release_date: str = "") -> str:
    """Release date -> "Released"; else a published status string; else "" (never invented)."""
    return published_status_fields(b, release_date)[0]


def _json_list(resp, step: str) -> list:
    """A JSON array of objects, or ParseDriftError (layout/contract drift)."""
    try:
        data = resp.json()
    except ValueError:
        raise ParseDriftError(f"Polk: {step} did not return JSON") from None
    if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
        raise ParseDriftError(f"Polk: {step} payload is not a list of objects")
    return data


class PolkCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Polk"

    def scrape(self) -> List[ArrestRecord]:
        session = make_session(HEADERS)
        
        all_records = []
        seen_bookings = set()
        detail_misses = 0
        
        # Loop for target dates
        for days_ago in range(DAYS_BACK):
            target_date = datetime.now() - timedelta(days=days_ago)
            date_str = target_date.strftime("%Y-%m-%d")  # API expects YYYY-MM-DD
            logger.info(f"Polk: Querying bookings for date {date_str}...")
            
            payload = {
                "SelectedSearchType": "BookingDate",
                "BookingDate": date_str
            }
            
            # Certificate verification stays on; a bad chain raises SourceTLSInvalid,
            # a block raises EgressBlocked. Neither is caught here.
            resp = _call(session, "POST", SEARCH_URL, "SearchJail", data=payload, timeout=30)
            if resp.status_code != 200:
                raise PolkHTTPError(resp.status_code, "SearchJail")
            bookings = _json_list(resp, "SearchJail")
            logger.info(f"Polk: Found {len(bookings)} bookings for {date_str}")

            for b in bookings:
                booking_num = b.get("BookingNum")
                if not booking_num or booking_num in seen_bookings:
                    continue
                seen_bookings.add(booking_num)
                record, missed = self._booking_record(session, b)
                all_records.append(record)
                detail_misses += int(missed)
                # Polite rate-limiting between detail calls
                time.sleep(0.1)

        if detail_misses:
            logger.warning("Polk: %d booking detail(s) unavailable; charges/bond left blank", detail_misses)
        logger.info(f"Polk County Scrape Complete: {len(all_records)} total records")
        return all_records

    def _scrape_booking_details(self, session, b) -> ArrestRecord:
        return self._booking_record(session, b)[0]

    def _booking_record(self, session, b) -> Tuple[ArrestRecord, bool]:
        """(record, detail_missed). Blocks, TLS and payload drift raise; a miss leaves charges/bond ""."""
        booking_num = str(b.get("BookingNum", "")).strip()
        full_name = str(b.get("FullName", "")).strip()
        rs_code = str(b.get("RS", "")).strip()
        dob = str(b.get("DOB", "")).strip()
        entry_date = str(b.get("EntryDate", "")).strip()
        release_date = str(b.get("ReleaseDate", "")).strip()
        location = str(b.get("Location", "")).strip()
        
        # Parse Race and Sex from RS code (e.g. "WM", "BF")
        race = ""
        sex = ""
        if rs_code and len(rs_code) >= 2:
            race = RACE_MAP.get(rs_code[0].upper(), rs_code[0])
            sex = rs_code[1].upper()
            
        first_name, middle_name, last_name = self._parse_name(full_name)
        
        # Status only as published: a release date gives "Released", a published
        # status field is kept, else "". SearchJail is a booking-date search that
        # returns released bookings too, so presence is not custody.
        status, status_source = published_status_fields(b, release_date)
            
        # Determine facility from location code
        facility = FACILITY
        loc_upper = location.upper().strip()
        if loc_upper == "IN":
            facility = "Polk County Jail - Main"
        elif loc_upper == "CCJ":
            facility = "Central County Jail"
        elif loc_upper == "SCJ":
            facility = "South County Jail"
            
        # Format booking date (EntryDate is typically MM/DD/YYYY)
        booking_date = ""
        if entry_date:
            try:
                dt = datetime.strptime(entry_date, "%m/%d/%Y")
                booking_date = dt.strftime("%Y-%m-%d")
            except ValueError:
                booking_date = entry_date

        release_formatted = ""
        if release_date:
            try:
                dt = datetime.strptime(release_date, "%m/%d/%Y")
                release_formatted = dt.strftime("%Y-%m-%d")
            except ValueError:
                release_formatted = release_date
                
        detail_url = f"{BASE_URL}/inmate-profile/{booking_num}"
        
        # Load profile page to extract CSRF token for charges API
        token = ""
        missed = True
        profile_resp = _call(session, "GET", detail_url, "inmate-profile", timeout=20)
        if profile_resp.status_code == 200:
            soup = BeautifulSoup(profile_resp.text, "html.parser")
            token_el = soup.find("input", {"name": "__RequestVerificationToken"})
            if token_el:
                token = token_el.get("value") or ""

        charges_list = []
        charge_bonds = []  # published amounts per charge; None = unknown

        # Fetch detailed charges/bonds if token is available
        if token:
            post_headers = {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": BASE_URL,
                "Referer": detail_url
            }
            charges_payload = {
                "bookingNum": booking_num,
                "__RequestVerificationToken": token
            }
            charges_resp = _call(session, "POST", CHARGES_URL, "GetCharges", data=charges_payload, headers=post_headers, timeout=20)
            content_type = (charges_resp.headers.get("Content-Type") or "").lower()
            if charges_resp.status_code == 200 and "json" in content_type:
                # JSON that isn't a list of charge objects is contract drift and raises.
                for chg in _json_list(charges_resp, "GetCharges"):
                    statute = str(chg.get("Statute") or "").strip()
                    desc = str(chg.get("ChargeDesc") or "").strip()
                    bond_str = str(chg.get("BondAmountDue") or "").strip()

                    item = f"{statute} - {desc}" if statute and desc else statute or desc
                    if item:
                        charges_list.append(item)

                    # Only a published dollar amount counts (a real 0 included).
                    charge_bonds.append(charge_bond_amount(bond_str))
                missed = False

        charges_str = " | ".join(charges_list)
        
        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_num,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            DOB=dob,
            Booking_Date=booking_date,
            Status=status,
            Release_Date=release_formatted,
            Facility=facility,
            Race=race,
            Sex=sex,
            Bond_Amount=total_published_bond(charge_bonds),
            Charges=charges_str,
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
            extra_data={"status_source": status_source} if status_source else {},
        ), missed

    @staticmethod
    def _parse_name(name):
        """Parse 'LAST, FIRST MIDDLE' into components."""
        if not name:
            return "", "", ""
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            remainder = parts[1].strip().split()
            first = remainder[0] if remainder else ""
            middle = " ".join(remainder[1:]) if len(remainder) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) >= 3:
            return parts[0], " ".join(parts[1:-1]), parts[-1]
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return name, "", ""
