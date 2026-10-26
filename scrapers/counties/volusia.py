"""
Volusia County Arrest Scraper — Direct ASP.NET Postback Search.
Source: Volusia County Division of Corrections Inmate Inquiry
URL: https://volusiamug.vcgov.org/
Method: plain HTTPS via ``scrapers.plain_http`` (honest User-Agent, certificate
verified): GET the disclaimer, POST its Accept, POST ``Recent Bookings``, then
GET each booking's ``Details.aspx`` page.

Failure is visible, never swallowed:
  * 401/403/429 or a challenge page -> ``egress_block:`` (EgressBlocked)
  * certificate verification failure -> ``tls_cert_invalid:`` (SourceTLSInvalid)
  * any other non-200 on the list flow -> ``VolusiaHTTPError`` (classified by status)
  * missing ViewState, missing Recent Bookings table or changed columns ->
    ``ParseDriftError``

Records are published-only (docs/recon/FL_VOLUSIA_HONEST_2026-10-22.md):
  * Booking_Number is the list's ``Booking #`` column; a row without one is skipped.
    ``Inmate ID`` is kept as Person_ID only, never as a key.
  * Status comes from the published ``InCustody`` column (Y -> In Custody,
    N -> Released, status_source ``published_in_custody_column``), else a published
    Release Date (Released, ``published_release_date``), else "". A blank cell is
    never read as custody.
  * Charges and per-charge bonds come from the detail ``Charge #`` grid. Bond is
    the sum only when every charge row publishes an amount (a published $0 is
    kept), else "". A detail page that isn't HTTP 200 leaves charges and bond "".
"""
import datetime
import logging
import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import make_session, request
from scrapers.published_bond import charge_bond_amount, total_published_bond
from scrapers.scraper_resilience import ParseDriftError

logger = logging.getLogger(__name__)

FACILITY = "Volusia County Branch Jail"
COUNTY = "Volusia"
BASE_URL = "https://volusiamug.vcgov.org/"
DISCLAIMER_URL = "https://volusiamug.vcgov.org/Disclaimer.aspx"
SEARCH_URL = "https://volusiamug.vcgov.org/Default.aspx"

STATUS_SOURCE_COLUMN = "published_in_custody_column"
STATUS_SOURCE_RELEASE_DATE = "published_release_date"

# Recent Bookings columns as published (agent-box read 2026-10-22).
LIST_COLUMNS = ("booking #", "photo", "inmate id", "last name", "first name", "middle", "suffix",
                "sex", "race", "booking date", "release date", "incustody")


class VolusiaHTTPError(RuntimeError):
    """A non-200 list-flow response that isn't a block; classified by its status, not as drift."""

    def __init__(self, status_code: int, step: str):
        self.status_code = status_code
        super().__init__(f"Volusia: HTTP {status_code} on {step}")


def status_fields(in_custody: str, release_date: str) -> Tuple[str, str]:
    """(Status, status_source) from the published InCustody / Release Date cells only."""
    v = (in_custody or "").strip().upper()
    if v == "Y":
        return "In Custody", STATUS_SOURCE_COLUMN
    if v == "N":
        return "Released", STATUS_SOURCE_COLUMN
    if (release_date or "").strip():
        return "Released", STATUS_SOURCE_RELEASE_DATE
    return "", ""


def _hidden(soup, name: str) -> str:
    el = soup.find("input", {"name": name})
    return (el.get("value") or "") if el else ""


def _form_state(html: str, step: str) -> Dict[str, str]:
    soup = BeautifulSoup(html or "", "html.parser")
    if not soup.find("input", {"name": "__VIEWSTATE"}):
        raise ParseDriftError(f"Volusia: missing ViewState on {step}")
    return {n: _hidden(soup, n) for n in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")}


def _split_dt(raw: str) -> Tuple[str, str]:
    raw = (raw or "").strip()
    return tuple(raw.split(" ", 1)) if " " in raw else (raw, "")  # type: ignore[return-value]


def parse_recent_bookings(html: str) -> List[Dict[str, str]]:
    """Recent Bookings table -> raw row dicts. Missing table or changed columns -> ParseDriftError."""
    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.find("table")
    if table is None or not table.find("tr"):
        raise ParseDriftError("Volusia: Recent Bookings table not found")
    rows = table.find_all("tr")
    header = tuple(c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"]))
    if header[: len(LIST_COLUMNS)] != LIST_COLUMNS:
        raise ParseDriftError("Volusia: Recent Bookings columns changed")
    out = []
    for r in rows[1:]:
        cells = r.find_all("td")
        if len(cells) < len(LIST_COLUMNS):
            continue  # pager / spacer row
        val = {name: cells[i].get_text(strip=True) for i, name in enumerate(LIST_COLUMNS)}
        booking = val["booking #"]
        link = r.find("a", href=True)
        rid_m = re.search(r"InmateRID=(\d+)", link["href"]) if link else None
        if not booking or not rid_m:
            continue
        val["inmate_rid"] = rid_m.group(1)
        out.append(val)
    return out


def parse_detail(html: str) -> Dict[str, object]:
    """Details.aspx -> demographics, charges, per-charge bonds (None = unpublished), bond types, case #."""
    soup = BeautifulSoup(html or "", "html.parser")

    def _val(el_id: str) -> str:
        el = soup.find("input", {"id": el_id})
        return (el.get("value") or "").strip() if el else ""

    charges: List[str] = []
    bonds: List[Optional[float]] = []
    bond_types: List[str] = []
    case_number = ""
    for dt in soup.find_all("table"):
        d_rows = dt.find_all("tr")
        if not d_rows:
            continue
        header = [hc.get_text(strip=True).lower() for hc in d_rows[0].find_all(["td", "th"])]
        if "charge #" not in header or "statute description" not in header:
            continue
        # Columns: Charge#, Statute, Statute Description, Bond Type, Bond Amount, ..., Court Case # (col 7)
        for dr in d_rows[1:]:
            c = dr.find_all("td")
            if len(c) < 5:
                continue
            statute, desc = c[1].get_text(strip=True), c[2].get_text(strip=True)
            bond_type, bond_amount = c[3].get_text(strip=True), c[4].get_text(strip=True)
            if len(c) >= 8 and not case_number:
                case_number = c[7].get_text(strip=True)
            charge = f"{statute} - {desc}" if statute and desc else statute or desc
            if charge:
                charges.append(charge)
            if bond_type and bond_type not in bond_types:
                bond_types.append(bond_type)
            bonds.append(charge_bond_amount(bond_amount))
    return {
        "zip": _val("txtZipCode"), "city": _val("txtCity"), "state": _val("txtState") or "FL",
        "age": _val("txtDOB"),  # labelled Age on the page; the id is txtDOB
        "charges": charges, "bonds": bonds, "bond_types": bond_types, "case_number": case_number,
    }


class VolusiaCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Volusia"

    def scrape(self) -> List[ArrestRecord]:
        session = make_session()

        def _post_or_get(method: str, url: str, step: str, **kw):
            resp = request(session, method, url, timeout=20, county="Volusia", **kw)
            if resp.status_code != 200:
                raise VolusiaHTTPError(resp.status_code, step)
            return resp

        home = _post_or_get("GET", BASE_URL, "disclaimer page")
        accepted = _post_or_get("POST", DISCLAIMER_URL, "disclaimer accept",
                                data={**_form_state(home.text, "disclaimer page"), "ButtonAccept": "Accept"})
        recent = _post_or_get("POST", SEARCH_URL, "recent bookings",
                              data={**_form_state(accepted.text, "search page"),
                                    "txtBookingNo": "", "txtLname": "", "txtFName": "",
                                    "btnRecentBookings": "Recent"})
        rows = parse_recent_bookings(recent.text)
        logger.info("Volusia: %d bookings listed", len(rows))

        records = []
        detail_misses = 0
        for row in rows:
            detail_url = f"https://volusiamug.vcgov.org/Details.aspx?InmateRID={row['inmate_rid']}"
            d_resp = request(session, "GET", detail_url, timeout=15, county="Volusia")
            if d_resp.status_code == 200:
                detail = parse_detail(d_resp.text)
            else:
                detail_misses += 1
                detail = parse_detail("")
            records.append(self.build_record(row, detail, detail_url))
        if detail_misses:
            logger.warning("Volusia: %d detail page(s) not HTTP 200; charges/bond left blank", detail_misses)
        logger.info("Volusia: parsed %d records", len(records))
        return records

    def build_record(self, row: Dict[str, str], detail: Dict[str, object], detail_url: str) -> ArrestRecord:
        last, first = row["last name"], row["first name"]
        middle, suffix = row["middle"], row["suffix"]
        full_name = f"{last}, {first}" + (f" {middle}" if middle else "") + (f" {suffix}" if suffix else "")
        booking_date, booking_time = _split_dt(row["booking date"])
        release_date, _ = _split_dt(row["release date"])
        status, status_source = status_fields(row["incustody"], release_date)
        return ArrestRecord(
            Scrape_Timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            County=self.county,
            Booking_Number=row["booking #"],
            Person_ID=row["inmate id"],
            Full_Name=full_name.upper(),
            First_Name=first.upper(),
            Middle_Name=middle.upper(),
            Last_Name=last.upper(),
            DOB="",  # not published
            Arrest_Date=booking_date,
            Arrest_Time=booking_time,
            Booking_Date=booking_date,
            Booking_Time=booking_time,
            Status=status,
            Release_Date=release_date,
            Facility=FACILITY,
            Race=row["race"],
            Sex=row["sex"],
            Age_At_Arrest=str(detail["age"]),
            City=str(detail["city"]).upper(),
            State=str(detail["state"]).upper(),
            ZIP=str(detail["zip"]),
            Mugshot_URL=f"https://volusiamug.vcgov.org/FullsizeMugshotHandler.ashx?InmateRID={row['inmate_rid']}",
            Charges=" | ".join(detail["charges"]),  # type: ignore[arg-type]
            Bond_Amount=total_published_bond(detail["bonds"]),  # type: ignore[arg-type]
            Bond_Type=", ".join(detail["bond_types"]),  # type: ignore[arg-type]
            Case_Number=str(detail["case_number"]),
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
            extra_data={"status_source": status_source} if status_source else {},
        )

    @staticmethod
    def _parse_bond_val(bond_str: str):
        """Published amount (a real 0 included) or None when not published."""
        return charge_bond_amount(bond_str)
