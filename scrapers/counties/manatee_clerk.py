"""
Manatee Clerk (FL) court-filing scraper: records.manateeclerk.com CourtRecords.

Owner exception (Brendan, 2026-10-10; relayed by CoS). This is a SEPARATE
scraper from the Manatee jail scraper (``"Manatee (FL)"``,
``scrapers/counties/manatee.py``), which stays ``fail_closed`` and is not
touched by this module. Staff see the source as
``"Manatee Clerk (court filing)"``.

Source (recon: ``docs/recon/FL_MANATEE_SOURCE_RECON_2026-10-09.md``; scraper
notes: ``docs/recon/FL_MANATEE_CLERK_SCRAPER_2026-10-10.md``):

* List: ``GET /CourtRecords/Search/CaseType/{page}/{size}/{MM-DD-YYYY}/{MM-DD-YYYY}?caseTypeId=N``
  by filing date (10 FELONY, 35 MISDEMEANOR, 37 MISDEMEANOR-MISC). Columns:
  View (a per-row form), Case Number, Party Name, Party Type, Case Type, Case
  Status, File Date, DOB (year). "Matching Results: N" when rows exist,
  "NO RECORDS FOUND" when not.
* Detail: ``POST /CourtRecords/Case/Details`` with the row form's
  anti-forgery token, ``caseId`` and ``searchAddress``.
* Plain HTTPS (IIS, no Cloudflare, no CAPTCHA, terms allow automated access).
  Honest UA, ``REQUEST_DELAY_S`` between requests, ``MAX_DETAILS_PER_RUN`` cap.
  A challenge / CAPTCHA / 401 / 403 / 429 stops the run at once with
  ``EgressBlocked`` (``egress_block:`` prefix; never retried, no stealth, no
  proxy, no impersonation). Structural drift raises ``ParseDriftError``.

Keys and fields:

* There is no booking number. ``Booking_Number`` stays blank and NEVER holds
  the case number. The record is keyed on an internal key
  ``mc_case_v1:<sha256>`` (case number + normalised defendant name;
  ``mc_case_key``) through the narrow allow-listed writer path
  (``core.booking_identity``, scope ``("FL", "Manatee Clerk")`` only). Display
  helpers print it blank.
* ``Case_Number`` carries the case number; ``extra_data["obts_number"]`` (stored
  as ``obts_number``) the OBTS number(s).
* Charges verbatim (Charge Description); per-charge offense date, statute,
  degree, citation and "Arrest Summons Served" date in ``charge_details``.
* Bond only from the Bonds table's bond rows (Active Amount); the
  "N Bond(s)" totals row is ignored. A published $0.00 bond row counts
  ("0.00"); no bond rows -> "" (unknown), never "0".
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from core.booking_identity import MC_KEY_PREFIX, MC_SCOPE_COUNTY
from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.scraper_resilience import EgressBlocked, ParseDriftError

logger = logging.getLogger(__name__)

BASE_URL = "https://records.manateeclerk.com"
LIST_PATH = "/CourtRecords/Search/CaseType/{page}/{size}/{start}/{end}"
DETAIL_PATH = "/CourtRecords/Case/Details"
ROSTER_URL = f"{BASE_URL}/CourtRecords/Search/CaseType"

SOURCE_LABEL = "Manatee Clerk (court filing)"
USER_AGENT = (
    "ShamrockLeadsBot/1.0 (+https://shamrockbailbonds.biz; "
    "Manatee Clerk court filing reader; plain HTTPS)"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# caseTypeId -> Court_Type. 18 CRIMINAL TRAFFIC is not read.
CASE_TYPES: Tuple[Tuple[int, str], ...] = ((10, "Felony"), (35, "Misdemeanor"), (37, "Misdemeanor"))
DAYS_BACK = 2           # filed yesterday and today (filing lags arrest)
PAGE_SIZE = 50
MAX_LIST_PAGES = 4      # per case type
MAX_DETAILS_PER_RUN = 80
REQUEST_DELAY_S = 2.5
REQUEST_TIMEOUT = 30

MC_KEY_VERSION = MC_KEY_PREFIX.rstrip(":")  # "mc_case_v1"
MC_KEY_LABEL = (
    "internal key, NOT a booking number: sha256 of normalised case number | defendant name"
)

LIST_COLUMNS = ("", "View", "Case Number", "Party Name", "Party Type", "Case Type", "Case Status", "File Date", "DOB")

_WS_RE = re.compile(r"\s+")
_TOTALS_ROW_RE = re.compile(r"^\d+\s+Bond\(s\)$", re.IGNORECASE)
_MONEY_RE = re.compile(r"^\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)$")
_DATE_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")
_MATCHING_RE = re.compile(r"Matching Results:\s*(\d+)")
_EVENT_RE = re.compile(r"^\s*\d+:\s*(\d{1,2}/\d{1,2}/\d{4})(?:\s+at\s+(\d{1,2}:\d{2}\s*[AP]M))?\s*-\s*(.*)$", re.IGNORECASE)
_OBTS_RE = re.compile(r"OBTS\s+(\d{6,})", re.IGNORECASE)
_AGENCY_RE = re.compile(r"Agency:\s*([A-Z0-9]{2,6})\b")

# Body markers of an interstitial challenge or CAPTCHA (none on the live pages,
# 2026-10-10). Matched case-insensitively. The specific markers always stop the
# run; the generic ones only when the page is not a normal Clerk page (so a
# charge or docket text can't trip them).
_CHALLENGE_MARKERS = (
    "just a moment...",
    "cf-chl",
    "challenges.cloudflare.com",
    "cf-turnstile",
    "g-recaptcha",
    "www.google.com/recaptcha",
    "hcaptcha.com",
    "h-captcha",
    "attention required! | cloudflare",
)
_GENERIC_BLOCK_MARKERS = ("captcha", "request rejected", "access denied", "are you a robot", "unusual traffic")
_CLERK_PAGE_MARKER = "public records hub"


class ManateeClerkContractError(ParseDriftError):
    """The Clerk pages no longer match the verified shape."""


def _clean(text: Any) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def _norm(text: Any) -> str:
    return _clean(text).upper()


def _iso(mdy: str) -> str:
    try:
        return datetime.strptime(mdy.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return ""


def normalise_case_number(case_number: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", _norm(case_number))


def mc_case_key(case_number: Any, defendant_name: Any) -> str:
    """Internal Manatee Clerk key. Never a booking number; never printed.

    sha256 of the normalised case number and defendant name (co-defendants on
    one case stay separate records). Returns "" when either is missing.
    """
    case, name = normalise_case_number(case_number), _norm(defendant_name)
    if not case or not name:
        return ""
    payload = f"{MC_KEY_VERSION}|{case}|{name}"
    return MC_KEY_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def detect_challenge(status: int, headers: Dict[str, Any], body: str) -> str:
    """A short reason when the response is a block / challenge / CAPTCHA, else ""."""
    hdrs = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    if hdrs.get("cf-mitigated"):
        return f"cf-mitigated={hdrs['cf-mitigated']} (HTTP {status})"
    low = (body or "")[:200_000].lower()
    for marker in _CHALLENGE_MARKERS:
        if marker in low:
            return f"challenge marker {marker!r} (HTTP {status})"
    if status != 200 or _CLERK_PAGE_MARKER not in low:
        for marker in _GENERIC_BLOCK_MARKERS:
            if marker in low:
                return f"block marker {marker!r} (HTTP {status})"
    if status in (401, 403, 429):
        return f"HTTP {status}"
    if status == 503 and "cloudflare" in hdrs.get("server", ""):
        return "HTTP 503 from cloudflare"
    return ""


def parse_bonds(soup: BeautifulSoup) -> Dict[str, Any]:
    """Bond from the Bonds table only.

    Bond rows are (Bond Type, Active Amount); the "N Bond(s)" totals row is
    ignored. Returns amount "" when no bond row is listed (unknown, never
    "0"); a published $0.00 bond row gives "0.00".
    """
    out: Dict[str, Any] = {"amount": "", "types": [], "rows": 0, "table": False}
    panel = _panel(soup, "Bonds")
    if panel is None:
        return out
    table = panel.find("table")
    if table is None:
        return out
    out["table"] = True
    header = [_clean(th.get_text(" ")) for th in table.find_all("th")]
    if header[:2] != ["Bond Type", "Active Amount"]:
        raise ManateeClerkContractError("Manatee Clerk: Bonds table header drift")
    total = Decimal("0")
    for tr in table.find_all("tr"):
        cells = [_clean(td.get_text(" ")) for td in tr.find_all("td")]
        if len(cells) < 2 or _TOTALS_ROW_RE.match(cells[0]) or not cells[0]:
            continue
        m = _MONEY_RE.match(cells[1])
        if not m:
            continue
        try:
            amt = Decimal(m.group(1).replace(",", ""))
        except InvalidOperation:
            continue
        total += amt
        out["rows"] += 1
        if cells[0] not in out["types"]:
            out["types"].append(cells[0])
    if out["rows"]:
        out["amount"] = f"{total:.2f}"
    return out


def _panel(soup: BeautifulSoup, heading: str) -> Optional[Tag]:
    for p in soup.select(".panel"):
        h = p.select_one(".panel-heading")
        if h is not None and _clean(h.get_text(" ")).rstrip(":").startswith(heading):
            return p
    return None


def _labelled(soup: BeautifulSoup, label: str) -> str:
    """Value next to a <strong>/<span class=line-label> label ("Case:", "Judge:")."""
    for el in soup.select("strong, .line-label"):
        if _clean(el.get_text(" ")) == label:
            sib = el.find_next_sibling("span")
            if sib is not None:
                return _clean(sib.get_text(" "))
    return ""


def parse_charges(soup: BeautifulSoup) -> List[Dict[str, str]]:
    section = soup.find(id="charges-list")
    if section is None:
        return []
    charges: List[Dict[str, str]] = []
    keymap = {
        "Offense Date": "offense_date", "Statute": "statute", "Description": "description",
        "Degree": "degree", "Citation": "citation",
    }
    for row in section.select(".data-faux-row"):
        cur: Optional[str] = None
        item: Dict[str, str] = {}
        for child in row.find_all("div", recursive=False):
            classes = child.get("class") or []
            if "faux-label" in classes:
                cur = keymap.get(_clean(child.get_text(" ")))
            elif "faux-td" in classes and cur:
                item[cur] = _clean(child.get_text(" "))
                cur = None
        if item.get("description"):
            if item.get("offense_date"):
                item["offense_date"] = _iso(item["offense_date"]) or item["offense_date"]
            charges.append(item)
    return charges


def parse_obts(soup: BeautifulSoup) -> Dict[str, Any]:
    """OBTS numbers, arresting agency codes and per-count Arrest Summons Served dates."""
    obts: List[str] = []
    agencies: List[str] = []
    served: List[str] = []
    section = soup.find(id="OBTS")
    if section is None:
        return {"obts": obts, "agencies": agencies, "served": served}
    for head in section.select(".obts-heading"):
        main = _clean(head.select_one(".main").get_text(" ")) if head.select_one(".main") else ""
        sub = _clean(head.select_one(".sub").get_text(" ")) if head.select_one(".sub") else ""
        m = _OBTS_RE.search(main)
        if m and m.group(1) not in obts:
            obts.append(m.group(1))
        a = _AGENCY_RE.search(sub)
        if a and a.group(1) not in agencies:
            agencies.append(a.group(1))
    for item in section.select(".offset-list"):
        label = _clean(item.contents[0]) if item.contents and isinstance(item.contents[0], NavigableString) else ""
        if label == "Arrest Summons Served:":
            data = item.select_one(".offset-list-data")
            served.append(_iso(_clean(data.get_text(" "))) if data else "")
    return {"obts": obts, "agencies": agencies, "served": served}


def parse_next_event(soup: BeautifulSoup, today: Optional[date] = None) -> Dict[str, str]:
    """First scheduled event on/after today: date, time, location (+ room)."""
    today = today or date.today()
    for item in soup.select(".event-list-item"):
        h = item.find("h4")
        m = _EVENT_RE.match(_clean(h.get_text(" "))) if h else None
        if not m:
            continue
        iso = _iso(m.group(1))
        if not iso or iso < today.isoformat():
            continue
        loc = item.select_one(".event-location")
        room = item.select_one(".event-room")

        def _val(el: Optional[Tag]) -> str:
            if el is None:
                return ""
            return _clean(el.get_text(" ")).split(":", 1)[-1].strip()

        location = ", ".join(x for x in (_val(loc), _val(room)) if x)
        return {"date": iso, "time": _clean(m.group(2) or ""), "event": _clean(m.group(3)), "location": location}
    return {"date": "", "time": "", "event": "", "location": ""}


def parse_defendant(soup: BeautifulSoup) -> Dict[str, str]:
    """Defendant name, gender and DOB from the Parties table (no address, no attorney)."""
    panel = _panel(soup, "Parties")
    if panel is None:
        return {}
    for tr in panel.select("tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 4 or _clean(tds[0].get_text(" ")) != "Defendant":
            continue
        name_parts: List[str] = []
        for node in tds[1].children:
            if isinstance(node, Tag) and node.name in ("br", "strong"):
                break
            if isinstance(node, NavigableString):
                name_parts.append(str(node))
        return {
            "name": _clean(" ".join(name_parts)),
            "gender": _clean(tds[2].get_text(" ")),
            "dob": _iso(_clean(tds[3].get_text(" "))),
        }
    return {}


def parse_detail(html: str, today: Optional[date] = None) -> Dict[str, Any]:
    """Parse a case Details page. Raises ManateeClerkContractError on drift."""
    soup = BeautifulSoup(html, "html.parser")
    case = _labelled(soup, "Case:")
    if not case:
        raise ManateeClerkContractError("Manatee Clerk: detail page has no Case: label")
    return {
        "case_number": case,
        "filed": _iso(_labelled(soup, "Filed:")),
        "status": _labelled(soup, "Status:"),
        "type": _labelled(soup, "Type:"),
        "judge": _labelled(soup, "Judge:"),
        "defendant": parse_defendant(soup),
        "charges": parse_charges(soup),
        "bonds": parse_bonds(soup),
        "obts": parse_obts(soup),
        "next_event": parse_next_event(soup, today),
    }


def parse_list(html: str) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """Rows of a case-type list page and the "Matching Results" count.

    Raises ManateeClerkContractError when the results table or its header set
    is missing, or rows are present without a count.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="results-table")
    if table is None:
        raise ManateeClerkContractError("Manatee Clerk: list page has no results table")
    headers = [_clean(th.get_text(" ")) for th in table.find_all("th")]
    for col in LIST_COLUMNS[1:]:
        if col not in headers:
            raise ManateeClerkContractError(f"Manatee Clerk: list column missing: {col}")
    text = _clean(soup.get_text(" "))
    m = _MATCHING_RE.search(text)
    rows: List[Dict[str, Any]] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != len(LIST_COLUMNS):
            continue
        form = tds[1].find("form")
        inputs = {i.get("name"): i.get("value", "") for i in form.find_all("input") if i.get("name")} if form else {}
        if not inputs.get("caseId") or "__RequestVerificationToken" not in inputs:
            raise ManateeClerkContractError("Manatee Clerk: list row has no detail form")
        rows.append({
            "case_number": _clean(tds[2].get_text(" ")),
            "party_name": _clean(tds[3].get_text(" ")),
            "party_type": _clean(tds[4].get_text(" ")),
            "case_type": _clean(tds[5].get_text(" ")),
            "case_status": _clean(tds[6].get_text(" ")),
            "file_date": _iso(_clean(tds[7].get_text(" "))),
            "form_action": (form.get("action") if form else "") or DETAIL_PATH,
            "form": inputs,
        })
    if rows and m is None:
        raise ManateeClerkContractError("Manatee Clerk: list rows without a Matching Results count")
    if not rows and m is None and "NO RECORDS FOUND" not in text.upper():
        raise ManateeClerkContractError("Manatee Clerk: empty list page without NO RECORDS FOUND")
    return rows, (int(m.group(1)) if m else 0)


class ManateeClerkScraper(BaseScraper):
    """Manatee Clerk (FL): court filings (felony / misdemeanor) by filing date.

    Owner exception (Brendan 2026-10-10). Separate from the fail_closed
    Manatee jail scraper. No booking number: Booking_Number stays blank and
    rows are keyed on the internal ``mc_case_v1`` key."""

    SOURCE_CONTRACT_VALIDATED = True
    # Narrow opt-in: blank-Booking_Number rows are kept only when
    # core.booking_identity.internal_natural_key accepts them (FL / "Manatee
    # Clerk", exact mc_case_v1 pattern).
    ALLOWS_INTERNAL_NATURAL_KEY = True
    # A challenge stops the run (EgressBlocked is never retried anyway).
    BASE_RETRY_ENABLED = False

    _sleep = staticmethod(time.sleep)

    def __init__(self, session_factory: Any = None):
        self._session_factory = session_factory or requests.Session
        self._requests_made = 0
        super().__init__()

    @property
    def county(self) -> str:
        return MC_SCOPE_COUNTY

    @property
    def roster_url(self) -> str:
        return ROSTER_URL

    # ── HTTP ──
    def _new_session(self) -> Any:
        s = self._session_factory()
        try:
            s.headers.update(HEADERS)
        except AttributeError:
            pass
        return s

    def _request(self, session: Any, method: str, url: str, **kwargs: Any) -> str:
        if self._requests_made:
            self._sleep(REQUEST_DELAY_S)
        self._requests_made += 1
        try:
            resp = session.request(method, url, timeout=REQUEST_TIMEOUT, allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"Manatee Clerk: request failed ({type(exc).__name__})") from exc
        body = resp.text or ""
        reason = detect_challenge(resp.status_code, dict(resp.headers or {}), body)
        if reason:
            # Stop the run: no retry, no stealth, no proxy.
            raise EgressBlocked(f"egress_block: Manatee Clerk {method} answered a challenge/block: {reason}")
        if resp.status_code in (301, 302, 303, 307, 308):
            raise ManateeClerkContractError(f"Manatee Clerk: unexpected redirect (HTTP {resp.status_code})")
        if resp.status_code != 200:
            raise RuntimeError(f"Manatee Clerk: HTTP {resp.status_code}")
        return body

    # ── Pipeline ──
    def scrape(self) -> List[ArrestRecord]:
        from config.source_guard import fail_closed_reason

        guard = fail_closed_reason(scraper=self, url=BASE_URL)
        if guard:
            logger.warning("[%s] source guard: %s; no request made", self.county_label, guard)
            return []

        start = time.time()
        self._requests_made = 0
        end_d = date.today()
        start_d = end_d - timedelta(days=DAYS_BACK - 1)
        start_s, end_s = start_d.strftime("%m-%d-%Y"), end_d.strftime("%m-%d-%Y")

        records: List[ArrestRecord] = []
        seen_keys: set = set()
        listed = details = capped = skipped_non_defendant = 0
        for type_id, court_type in CASE_TYPES:
            # One session per case type: the site keeps search state per session.
            session = self._new_session()
            rows, list_url = self._walk_list(session, type_id, start_s, end_s)
            listed += len(rows)
            rows.sort(key=lambda r: r["file_date"], reverse=True)
            for row in rows:
                if row["party_type"].casefold() != "defendant":
                    skipped_non_defendant += 1
                    continue
                key = mc_case_key(row["case_number"], row["party_name"])
                if not key or key in seen_keys:
                    continue
                if details >= MAX_DETAILS_PER_RUN:
                    capped += 1
                    continue
                html = self._request(
                    session, "POST", BASE_URL + (row["form_action"] or DETAIL_PATH),
                    data=row["form"], headers={"Referer": list_url},
                )
                details += 1
                detail = parse_detail(html)
                if normalise_case_number(detail["case_number"]) != normalise_case_number(row["case_number"]):
                    raise ManateeClerkContractError("Manatee Clerk: detail case number does not match the list row")
                rec = self.build_record(row, detail, court_type)
                if rec is not None:
                    seen_keys.add(key)
                    records.append(rec)

        no_bond = sum(1 for r in records if r.Bond_Amount == "")
        logger.info(
            "[%s] %d records (listed=%d, details=%d, capped=%d, non_defendant=%d, bond_unknown=%d) in %.1fs",
            self.county_label, len(records), listed, details, capped, skipped_non_defendant, no_bond, time.time() - start,
        )
        return records

    def _walk_list(self, session: Any, type_id: int, start_s: str, end_s: str) -> Tuple[List[Dict[str, Any]], str]:
        rows: List[Dict[str, Any]] = []
        url = ""
        expected: Optional[int] = None
        for page in range(1, MAX_LIST_PAGES + 1):
            url = BASE_URL + LIST_PATH.format(page=page, size=PAGE_SIZE, start=start_s, end=end_s) + f"?caseTypeId={type_id}"
            page_rows, count = parse_list(self._request(session, "GET", url))
            expected = count if expected is None else expected
            rows.extend(page_rows)
            if not page_rows or len(rows) >= expected:
                break
        if expected and len(rows) < expected and len(rows) < PAGE_SIZE * MAX_LIST_PAGES:
            raise ManateeClerkContractError(
                f"Manatee Clerk: caseTypeId={type_id} walked {len(rows)} of {expected} rows"
            )
        return rows, url

    def build_record(self, row: Dict[str, Any], detail: Dict[str, Any], court_type: str) -> Optional[ArrestRecord]:
        name = row["party_name"]
        key = mc_case_key(row["case_number"], name)
        if not key or len(name.replace(",", " ").split()) < 2:
            return None
        first, middle, last = self._parse_name(name)
        defendant = detail.get("defendant") or {}
        charges = detail.get("charges") or []
        obts = detail.get("obts") or {}
        served = [d for d in obts.get("served", []) if d]
        if len(obts.get("served", [])) == len(charges):
            for item, srv in zip(charges, obts["served"]):
                item["arrest_summons_served"] = srv
        bonds = detail.get("bonds") or {}
        event = detail.get("next_event") or {}
        filed = detail.get("filed") or row.get("file_date") or ""
        return ArrestRecord(
            County=self.county,
            State="FL",
            Booking_Number="",  # no booking number; NEVER the case number
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            DOB=defendant.get("dob", ""),
            Sex=defendant.get("gender", ""),
            Arrest_Date=min(served) if served else "",
            Booking_Date="",  # a filing date is not a booking date
            Status="Unknown",  # custody is not published
            Facility=SOURCE_LABEL,
            Agency=", ".join(obts.get("agencies", [])),
            Charges=" | ".join(c["description"] for c in charges),
            Bond_Amount=bonds.get("amount", ""),
            Bond_Type=", ".join(bonds.get("types", [])),
            Court_Type=court_type,
            Case_Number=detail.get("case_number") or row["case_number"],
            Court_Date=event.get("date", ""),
            Court_Time=event.get("time", ""),
            Court_Location=event.get("location", ""),
            Detail_URL="",  # detail is a token POST; no GET URL to store
            LastCheckedMode="INITIAL",
            extra_data={
                "booking_key_origin": "none: court filing, the source publishes no booking number",
                "mc_case_key": key,
                "mc_case_key_label": MC_KEY_LABEL,
                "source_label": SOURCE_LABEL,
                "obts_number": ", ".join(obts.get("obts", [])),
                "filing_date": filed,
                "case_status": detail.get("status") or row.get("case_status", ""),
                "judge": detail.get("judge", ""),
                "next_event": event.get("event", ""),
                "charge_details": charges,
                "bond_published": bool(bonds.get("rows")),
            },
        )

    @staticmethod
    def _parse_name(name: str) -> Tuple[str, str, str]:
        """'LAST, FIRST MIDDLE' -> (first, middle, last)."""
        if "," in name:
            last, rest = name.split(",", 1)
            parts = rest.split()
            return (parts[0] if parts else ""), " ".join(parts[1:]), last.strip()
        parts = name.split()
        if len(parts) == 1:
            return "", "", parts[0]
        return parts[0], " ".join(parts[1:-1]), parts[-1]
