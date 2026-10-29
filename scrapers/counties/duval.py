"""
Duval County (Jacksonville) Arrest Scraper — JSO / ATIMS Inmate Search (fail closed).
Source: Jacksonville Sheriff's Office, https://inmatesearch.jaxsheriff.org/

Method: plain HTTPS via ``scrapers.plain_http`` (honest User-Agent, certificate
verified). The DrissionPage browser (Chrome UA, stealth JS, API interception) is
retired: no browser, no Chrome disguise, no impersonation, no certificate bypass.

Fail closed (2026-10-21, docs/recon/FL_DUVAL_HONEST.md): the host sends only its
leaf certificate (Network Solutions RSA OV SSL CA 3 intermediate missing), so an
honest verified GET raises ``tls_cert_invalid``. The search is also an Angular
SPA behind an ATIMS "I'm not a robot" gate, and its JSON API contract has never
been read honestly. No source request is made while ``SOURCE_CONTRACT_VALIDATED``
is False; reopening is a CoS decision.

Failure is visible, never worked around:
  * 401/403/429 or a challenge page -> ``egress_block:`` (EgressBlocked)
  * certificate verification failure -> ``tls_cert_invalid:`` (SourceTLSInvalid)
  * the ATIMS robot gate -> ``captcha_gate:`` (AntiBotBlocked); it is never clicked
  * a page or payload that no longer matches the parser -> ParseDriftError
  * any other non-200 -> ``DuvalHTTPError`` (classified by its status code)

Records (pure parsers, kept for the reopen path and its tests):
  * Booking_Number only from a published booking-number field; person, jacket and
    internal ids are never used as keys, and a row without one is skipped.
  * Status: a published release date -> "Released"; else a published status
    field verbatim; else "". ``status_source`` is recorded. The search is not
    proven custody-only (docs/recon/FL_DUVAL_ROSTER_SCOPE_2026-10-15.md), so
    presence never sets "In Custody".
  * Bond only from a published bond field (a published 0 is kept), else "".
  * Charges only from a published charge field, else "".
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Optional, Tuple

from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers import plain_http
from scrapers.base_scraper import BaseScraper
from scrapers.published_bond import charge_bond_amount
from scrapers.scraper_resilience import AntiBotBlocked, ParseDriftError

logger = logging.getLogger(__name__)

BASE_URL = "https://inmatesearch.jaxsheriff.org/"
FACILITY = "Duval County Pre-Trial Detention Facility"

STATUS_SOURCE_RELEASE_DATE = "published_release_date"
STATUS_SOURCE_FIELD = "published_status_field"

_STATUS_KEYS = ("status", "Status", "custodyStatus", "custody_status", "inmateStatus", "inmate_status")
_BOOKING_KEYS = ("bookingNumber", "booking_number", "bookingNo", "BookingNumber")
_LIST_KEYS = ("data", "results", "inmates", "entries", "items", "records", "bookings")
_NAME_KEYS = ("name", "full_name", "fullName", "inmateName", "defendant_name", "offenderName")
_BOND_KEYS = ("bond", "bondAmount", "bond_amount", "totalBond")
_CHARGE_KEYS = ("charges", "charge", "offense", "offenses")
_BOOKING_DATE_KEYS = ("bookingDate", "booking_date")
_RELEASE_KEYS = ("releaseDate", "release_date", "releasedDate", "dateReleased")
_GATE_RE = re.compile(r"i['’]m not a robot", re.I)
_DOM_BOOKING_RE = re.compile(r"Booking\s*(?:Number|No\.?|#)\s*:?\s*([A-Z0-9-]{4,})", re.I)
_DOM_BOND_RE = re.compile(r"Bond(?:\s*Amount)?\s*:?\s*(\$\s*[\d,]+(?:\.\d{2})?)", re.I)


def _published_bond_str(value) -> str:
    """Source bond value -> "1500" / "250.50" / "0"; None, blank, NO BOND or text -> ""."""
    if isinstance(value, bool):
        return ""
    amount = charge_bond_amount(value if not isinstance(value, (int, float)) else repr(value))
    if amount is None:
        return ""
    return str(int(amount)) if float(amount).is_integer() else f"{amount:.2f}"


def published_status_fields(entry, release_date: str = "") -> Tuple[str, str]:
    """(Status, status_source): release date -> Released; else a published status; else ("", "")."""
    if release_date:
        return "Released", STATUS_SOURCE_RELEASE_DATE
    if isinstance(entry, dict):
        for key in _STATUS_KEYS:
            val = entry.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip(), STATUS_SOURCE_FIELD
    return "", ""


def published_status(entry, release_date: str = "") -> str:
    """Release date -> "Released"; else a published status string; else ""."""
    return published_status_fields(entry, release_date)[0]


def _first(entry: dict, keys) -> str:
    for key in keys:
        val = entry.get(key)
        if val not in (None, "") and not isinstance(val, (dict, list)):
            return str(val).strip()
    return ""


class DuvalHTTPError(RuntimeError):
    """A non-200 response that isn't a block; Health classifies it by status, not as drift."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Duval: HTTP {status_code} from search page")


class DuvalCountyScraper(BaseScraper):
    """Duval (FL) — fail closed until an honest verified read is proven."""

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "inmatesearch.jaxsheriff.org omits its intermediate certificate, so an honest "
        "verified GET fails as tls_cert_invalid (2026-10-21); the search is an SPA behind an "
        "ATIMS robot gate with no honestly read API contract. No certificate bypass, browser or "
        "gate click; reopening is a CoS decision (docs/recon/FL_DUVAL_HONEST.md)."
    )

    @property
    def county(self) -> str:
        return "Duval"

    def scrape(self) -> List[ArrestRecord]:
        if not self.SOURCE_CONTRACT_VALIDATED:
            logger.warning("Duval FL fails closed: %s", self.SOURCE_CONTRACT_REASON)
            return []
        session = plain_http.make_session({"Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
        return self._scrape_with_session(session)

    def _scrape_with_session(self, session) -> List[ArrestRecord]:
        """One honest GET of the search page; blocks raise typed errors, drift raises."""
        resp = plain_http.request(session, "GET", BASE_URL, county="Duval", timeout=30)
        html = resp.text or ""
        if _GATE_RE.search(html):
            raise AntiBotBlocked("captcha_gate: Duval ATIMS 'I'm not a robot' gate shown; not clicked")
        if resp.status_code != 200:
            raise DuvalHTTPError(resp.status_code)
        records = self._parse_dom(html)
        logger.info("Duval: %d records", len(records))
        return records

    # ── Parsers (no network) ───────────────────────────────────────────────

    def _record(self, *, booking: str, full_name: str, status: Tuple[str, str], **kw: Any) -> ArrestRecord:
        first, middle, last = self._parse_name(full_name)
        extra = {"status_source": status[1]} if status[1] else {}
        return ArrestRecord(
            County=self.county, State="FL", Booking_Number=booking,
            Full_Name=full_name, First_Name=first, Middle_Name=middle, Last_Name=last,
            Status=status[0], Detail_URL=BASE_URL, Facility=FACILITY,
            LastCheckedMode="INITIAL", extra_data=extra, **kw,
        )

    def _extract_from_api(self, data) -> List[ArrestRecord]:
        """Search-API JSON -> records. A payload that isn't a list of entries raises ParseDriftError."""
        entries: Optional[list] = data if isinstance(data, list) else None
        if isinstance(data, dict):
            entries = next((data[k] for k in _LIST_KEYS if isinstance(data.get(k), list)), None)
        if entries is None:
            raise ParseDriftError("Duval: search payload has no list of entries")
        records = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ParseDriftError("Duval: search entry is not an object")
            booking = _first(entry, _BOOKING_KEYS)
            if not booking:
                continue  # never key on person / jacket / internal ids
            full_name = _first(entry, _NAME_KEYS)
            if not full_name:
                fn = _first(entry, ("firstName", "first_name"))
                ln = _first(entry, ("lastName", "last_name"))
                full_name = f"{ln}, {fn}" if fn and ln else ""
            bond = ""
            for key in _BOND_KEYS:
                if key in entry:
                    bond = _published_bond_str(entry[key])
                    break
            charges = ""
            for key in _CHARGE_KEYS:
                val = entry.get(key)
                if val:
                    if isinstance(val, list):
                        descs = []
                        for c in val:
                            d = (c.get("description") or c.get("charge") or c.get("offense")) if isinstance(c, dict) else c
                            if d:
                                descs.append(str(d).strip())
                        charges = " | ".join(descs)
                    else:
                        charges = str(val).strip()
                    break
            release_date = _first(entry, _RELEASE_KEYS)
            records.append(self._record(
                booking=booking, full_name=full_name,
                status=published_status_fields(entry, release_date),
                Booking_Date=_first(entry, _BOOKING_DATE_KEYS),
                DOB=_first(entry, ("dob", "dateOfBirth", "date_of_birth", "DOB")),
                Race=_first(entry, ("race", "Race")),
                Sex=_first(entry, ("sex", "gender", "Sex")),
                Charges=charges, Bond_Amount=bond, Release_Date=release_date,
            ))
        return records

    def _parse_dom(self, html: str) -> List[ArrestRecord]:
        """Result rows that publish a labelled booking number. No rows -> ParseDriftError.

        Parse errors are not swallowed. Bond is read only from a labelled bond
        value, never from any dollar figure in the row; status is "" (the row
        publishes none).
        """
        soup = BeautifulSoup(html or "", "html.parser")
        rows = soup.select("table tr, .inmate-row, .result-row")
        records = []
        for row in rows:
            text = row.get_text(" ", strip=True)
            booking_m = _DOM_BOOKING_RE.search(text)
            name_m = re.search(r"([A-Z][A-Za-z'-]+(?:\s[A-Z][A-Za-z'-]+)*,\s*[A-Z][A-Za-z'-]+(?:\s[A-Z][A-Za-z'-]+)*)", text)
            if not booking_m or not name_m:
                continue
            bond_m = _DOM_BOND_RE.search(text)
            records.append(self._record(
                booking=booking_m.group(1), full_name=name_m.group(1), status=("", ""),
                Bond_Amount=_published_bond_str(bond_m.group(1)) if bond_m else "",
            ))
        if not records:
            raise ParseDriftError("Duval: no result rows with a published booking number")
        return records

    @staticmethod
    def _parse_name(name_str):
        if not name_str:
            return "", "", ""
        if "," in name_str:
            last, rest = (p.strip() for p in name_str.split(",", 1))
            parts = rest.split()
            return (parts[0] if parts else ""), " ".join(parts[1:]), last
        parts = name_str.split()
        return parts[0], "", (parts[-1] if len(parts) >= 2 else "")
