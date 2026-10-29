"""
Mecklenburg County (NC) Arrest Scraper — MCSO Inmate Inquiry JSON API.

Source contract (recon 2026-10-07, docs/recon/NC_MECKLENBURG_INMATE_API_2026-10-07.md):
  * Portal: https://mecksheriffweb.mecklenburgcountync.gov/Inmate
  * Ordinary public HTTPS (no login / CAPTCHA solved / proxy / stealth).
  * Active roster: GET /Inmate/_Search?activeOnly=true&max=50&page=N
    → Name, PID, JID, ArrestNumber, DOB, DetailsUrl, TotalRows (~2200 actives).
  * Booking_Number is source **JID** (e.g. ``26-125813``). Never invent ``MECK_``
    hashes; never use Person PID alone (multi-booking unsafe).
  * Summary: GET /Inmate/_Summary?pid=&jid= → OBID, CommitedFormatted.
  * Charges: GET /Inmate/_GetCharges?obid= → Description + ActualBailAmount
    (dollars as published; Bond_Amount is the sum of numeric bail cells only).
  * Health stays unverified until a write smoke — do not set verified_public yet.
"""
from __future__ import annotations

import logging
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin

import requests

from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import (
    HONEST_USER_AGENT, EgressBlocked, SourceTLSInvalid, make_session, request as plain_request,
)
from scrapers.scraper_resilience import ParseDriftError
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

ORIGIN = "https://mecksheriffweb.mecklenburgcountync.gov"
PORTAL_URL = f"{ORIGIN}/Inmate"
SEARCH_PATH = "/Inmate/_Search"
SUMMARY_PATH = "/Inmate/_Summary"
CHARGES_PATH = "/Inmate/_GetCharges"
FACILITY = "Mecklenburg County Detention Center"

PAGE_SIZE = 50
MAX_PAGES = 80  # hard cap (~4000 inmates)
MAX_DETAILS = 2500
REQUEST_PAUSE_S = 0.15
_BLOCKS = (EgressBlocked, SourceTLSInvalid)

HEADERS = {
    "User-Agent": HONEST_USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": PORTAL_URL,
}

# Source jail IDs observed on the public roster (YY-NNNNNN / YY-NNNNNNN).
_JID_RE = re.compile(r"^\d{2}-\d{5,8}$")
_ARREST_NUM_RE = re.compile(r"^\d{5,12}$")


class MecklenburgScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "mecksheriffweb.mecklenburgcountync.gov Inmate Inquiry; ordinary "
        "GET /Inmate/_Search activeOnly roster; Booking_Number is source JID "
        "(never MECK_ hashes); charges/bond from _Summary+_GetCharges "
        "ActualBailAmount only when published."
    )

    @property
    def county(self) -> str:
        return "Mecklenburg"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = make_session(HEADERS)

        # Landing page (sets cookies). Blocks and HTTP errors propagate as typed errors.
        resp = plain_request(
            session, "GET", PORTAL_URL, county=self.county, timeout=30,
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        resp.raise_for_status()

        listings = self._fetch_active_roster(session)
        if not listings:
            logger.warning("Mecklenburg: empty active roster")
            return []

        records: List[ArrestRecord] = []
        seen: Set[str] = set()
        enriched = 0

        for item in listings:
            jid = self._source_booking_number(item)
            if not jid or jid in seen:
                continue
            seen.add(jid)

            name = (item.get("Name") or "").strip()
            if not name:
                continue

            pid = str(item.get("PID") or "").strip()
            detail_path = item.get("DetailsUrl") or ""
            detail_url = urljoin(ORIGIN, detail_path) if detail_path else PORTAL_URL

            charges = ""
            bond = ""
            booking_date = ""
            first = (item.get("FirstName") or "").strip()
            last = (item.get("LastName") or "").strip()
            middle = (item.get("MiddleName") or "").strip()

            if enriched < MAX_DETAILS and pid:
                summary = self._get_json(
                    session,
                    SUMMARY_PATH,
                    {"pid": pid, "jid": jid},
                )
                time.sleep(REQUEST_PAUSE_S)
                if isinstance(summary, dict):
                    booking_date = (
                        str(summary.get("CommitedFormatted") or "").strip()
                    )
                    if not first:
                        first = str(summary.get("FirstName") or "").strip()
                    if not last:
                        last = str(summary.get("LastName") or "").strip()
                    obid = summary.get("OBID")
                    if obid is not None and str(obid).strip():
                        charge_rows = self._get_json(
                            session,
                            CHARGES_PATH,
                            {"obid": str(obid).strip()},
                        )
                        time.sleep(REQUEST_PAUSE_S)
                        parsed = self._parse_charges(charge_rows)
                        if parsed is not None:
                            charges, bond = parsed
                    enriched += 1

            dob = str(item.get("DobFormatted") or "").strip()
            records.append(
                ArrestRecord(
                    County=self.county,
                    State="NC",
                    Full_Name=name,
                    First_Name=first,
                    Middle_Name=middle,
                    Last_Name=last,
                    Booking_Number=jid,
                    Person_ID=pid,
                    Booking_Date=booking_date,
                    DOB=dob,
                    Sex=str(item.get("Sex") or "").strip(),
                    Race=str(item.get("Race") or "").strip(),
                    Charges=charges,
                    Bond_Amount=bond,
                    Status="",  # activeOnly scope not proven custody-only; no published custody field
                    Detail_URL=detail_url,
                    Facility=FACILITY,
                )
            )

        logger.info(
            "Mecklenburg: %s records (%s charge-enriched) in %.1fs",
            len(records),
            enriched,
            time.time() - start,
        )
        return records

    def _fetch_active_roster(self, session: requests.Session) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        total_rows: Optional[int] = None
        for page in range(1, MAX_PAGES + 1):
            data = self._get_json(
                session,
                SEARCH_PATH,
                {
                    "lastName": "",
                    "firstName": "",
                    "pid": "",
                    "jid": "",
                    "prisType": "ALL",
                    "activeOnly": "true",
                    "max": str(PAGE_SIZE),
                    "page": str(page),
                },
                strict=True,
            )
            time.sleep(REQUEST_PAUSE_S)
            if not isinstance(data, list) or not data:
                break
            if total_rows is None:
                try:
                    total_rows = int(data[0].get("TotalRows") or 0)
                except (TypeError, ValueError):
                    total_rows = 0
            out.extend(item for item in data if isinstance(item, dict))
            if len(data) < PAGE_SIZE:
                break
            if total_rows and len(out) >= total_rows:
                break
        logger.info(
            "Mecklenburg roster: %s listing rows (TotalRows=%s)",
            len(out),
            total_rows,
        )
        return out

    def _get_json(
        self,
        session: requests.Session,
        path: str,
        params: Dict[str, str],
        strict: bool = False,
    ) -> Any:
        """GET JSON through plain_http. Blocks always raise.

        strict (roster pages): HTTP/network/JSON failures raise too, so a broken
        roster never looks like an empty one. Non-strict (per-booking detail):
        those are logged by type only and the booking keeps "" fields.
        """
        url = urljoin(ORIGIN, path)
        try:
            resp = plain_request(session, "GET", url, county=self.county, params=params, timeout=35)
            resp.raise_for_status()
            return resp.json()
        except _BLOCKS:
            raise
        except ValueError as exc:
            if strict:
                raise ParseDriftError(f"Mecklenburg {path}: non-JSON response") from exc
            logger.debug("Mecklenburg %s non-JSON (%s)", path, type(exc).__name__)
            return None
        except requests.RequestException as exc:
            if strict:
                raise
            logger.debug("Mecklenburg %s failed (%s)", path, type(exc).__name__)
            return None

    @staticmethod
    def _source_booking_number(item: Dict[str, Any]) -> str:
        """Return source JID only; never synthesize MECK_ keys."""
        jid = str(item.get("JID") or "").strip()
        if jid and _JID_RE.match(jid):
            return jid
        # ArrestNumber is a source local booking # on mugshot API, but JID is
        # the public jail-id key. Accept ArrestNumber only when JID is absent
        # and the value is a plain digit string (still source-issued).
        arrest = str(item.get("ArrestNumber") or "").strip()
        if arrest and _ARREST_NUM_RE.match(arrest):
            return arrest
        return ""

    @staticmethod
    def _parse_charges(rows: Any) -> Optional[tuple[str, str]]:
        """Return (charges_text, bond_amount) from source charge rows.

        Bond_Amount is the sum of ActualBailAmount only when every charge row
        publishes a numeric amount; otherwise "" (never invented). A published
        0 is kept. Charges are "" when no description is published.
        """
        if not isinstance(rows, list) or not rows:
            return None
        descriptions: List[str] = []
        total = Decimal("0")
        saw_money = False
        missing = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            desc = str(row.get("Description") or "").strip()
            if desc:
                descriptions.append(desc)
            raw = row.get("ActualBailAmount")
            raw = "" if raw is None else str(raw).strip()
            cleaned = raw.replace("$", "").replace(",", "").strip()
            try:
                amt = Decimal(cleaned)
            except (InvalidOperation, ValueError):
                missing = True
                continue
            total += amt
            saw_money = True
        charges = " | ".join(descriptions)
        if saw_money and not missing:
            # Normalize: drop trailing .00 when whole dollars.
            if total == total.to_integral_value():
                bond = str(int(total))
            else:
                bond = format(total, "f")
        else:
            bond = ""
        return charges, bond

    # --- test helpers (pure, no network) ---

    def parse_search_payload(self, data: Any) -> List[ArrestRecord]:
        """Build listing-only records from a _Search JSON payload (tests)."""
        if not isinstance(data, list):
            return []
        out: List[ArrestRecord] = []
        seen: Set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            jid = self._source_booking_number(item)
            name = (item.get("Name") or "").strip()
            if not jid or not name or jid in seen:
                continue
            seen.add(jid)
            out.append(
                ArrestRecord(
                    County=self.county,
                    State="NC",
                    Full_Name=name,
                    First_Name=str(item.get("FirstName") or "").strip(),
                    Middle_Name=str(item.get("MiddleName") or "").strip(),
                    Last_Name=str(item.get("LastName") or "").strip(),
                    Booking_Number=jid,
                    Person_ID=str(item.get("PID") or "").strip(),
                    DOB=str(item.get("DobFormatted") or "").strip(),
                    Sex=str(item.get("Sex") or "").strip(),
                    Race=str(item.get("Race") or "").strip(),
                    Charges="",
                    Bond_Amount="",
                    Status="",  # activeOnly scope not proven custody-only; no published custody field
                    Detail_URL=urljoin(ORIGIN, item.get("DetailsUrl") or "")
                    or PORTAL_URL,
                    Facility=FACILITY,
                )
            )
        return out

    def record_from_detail(
        self,
        listing: Dict[str, Any],
        summary: Dict[str, Any],
        charges: Any,
    ) -> Optional[ArrestRecord]:
        """Merge listing + summary + charges into one ArrestRecord (tests)."""
        jid = self._source_booking_number(listing)
        name = (listing.get("Name") or summary.get("Name") or "").strip()
        if not jid or not name:
            return None
        parsed = self._parse_charges(charges)
        charges_text, bond = parsed if parsed else ("", "")
        return ArrestRecord(
            County=self.county,
            State="NC",
            Full_Name=name,
            First_Name=str(
                listing.get("FirstName") or summary.get("FirstName") or ""
            ).strip(),
            Middle_Name=str(listing.get("MiddleName") or "").strip(),
            Last_Name=str(
                listing.get("LastName") or summary.get("LastName") or ""
            ).strip(),
            Booking_Number=jid,
            Person_ID=str(listing.get("PID") or summary.get("PID") or "").strip(),
            Booking_Date=str(summary.get("CommitedFormatted") or "").strip(),
            DOB=str(
                listing.get("DobFormatted") or summary.get("DobFormatted") or ""
            ).strip(),
            Sex=str(listing.get("Sex") or summary.get("Sex") or "").strip(),
            Race=str(listing.get("Race") or summary.get("Race") or "").strip(),
            Charges=charges_text,
            Bond_Amount=bond,
            Status="",  # activeOnly scope not proven custody-only; no published custody field
            Detail_URL=urljoin(ORIGIN, listing.get("DetailsUrl") or "")
            or PORTAL_URL,
            Facility=FACILITY,
        )
