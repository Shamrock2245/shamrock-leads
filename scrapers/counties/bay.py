"""
Bay County (FL) Sheriff's Office — official public Jail Inmate Search.

Source contract (re-verified 2026-09-25, docs/recon/FL_GAP_QUEUE_2026-09-25.md):
  * URL: https://www.baysomobile.org/is/  (linked from bayso.org "Inmate Search")
  * Platform: uniGUI / Ext JS 7 (hyb.dll). Plain HTTPS GET + form POST; no login,
    no CAPTCHA, no WAF challenge from ordinary public access.
  * The search requires at least a last-name AND first-name prefix (one letter
    each is accepted; blank / wildcard searches are rejected by the server).
    Current in-custody inmates are enumerated with a bounded initials walk
    (A-Z last x A-Z first) inside ONE session, politely paced.
  * Listing grid (JSON store) exposes, per row: ``Booking #: YYYY-NNNNNN``
    (source-issued), ``Date In``, name, race/sex, charges and per-charge bond.
    Booking_Number is copied verbatim from the source; rows without a source
    booking number are dropped — keys are never synthesized.

Why it was silent before: the old code POSTed the Search click with a
hard-coded ``_seq_=3`` and no form fields, so uniGUI answered HTTP 401 (bad
request sequence) / an empty grid, and the fallback parsed the empty shell page.
"""
from __future__ import annotations

import json
import logging
import re
import string
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://www.baysomobile.org/is"
HANDLE_URL = f"{BASE_URL}/hyb.dll/HandleEvent"
FACILITY = "Bay County Jail"

# uniGUI component names on the Bay "MainForm" (stable across sessions; they are
# asserted against the landing page before any search is submitted).
FORM_OBJ = "O8"          # MainForm (afterrender)
BTN_SEARCH = "O68"       # "Search" button
FLD_LAST = "O5C"         # Last Name
FLD_FIRST = "O80"        # First Name
FLD_MIDDLE = "O8C"       # Middle Name
GRID = "O25"             # UniDBGrid1 (JSON store endpoint)

PAGE_LIMIT = 200
REQUEST_DELAY_S = 0.15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BOOKING_RE = re.compile(r"Booking\s*#\s*:\s*([0-9]{4}-[0-9]{3,8})", re.I)
DATE_IN_RE = re.compile(r"Date\s*In\s*:\s*(\d{1,2}/\d{1,2}/\d{4})(?:\s+(\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M))?", re.I)
RACE_RE = re.compile(r"Race\s*:\s*([A-Z])", re.I)
SEX_RE = re.compile(r"Sex\s*:\s*([A-Z])", re.I)
CHARGE_RE = re.compile(r"Charge\s*\d+\s*:\s*(.*?)\s*(?:Bond\s*:\s*\$?([\d,\.]+|[A-Z ]+))?\s*(?=Charge\s*\d+\s*:|$)", re.I | re.S)


class BaySourceContractError(RuntimeError):
    """Landing page no longer matches the verified uniGUI contract."""


def _fp_field(name: str, value: str) -> str:
    # uniGetValues(): "&<name>=" + enc("\x02" + submitState + "\x02" + stateValue + "\x02" + value)
    return "&" + name + "=" + quote("\x020\x02\x02" + value, safe="")


def _decode_store(text: str) -> Dict:
    """uniGUI store JSON uses JS ``\\xHH`` escapes; convert to valid JSON."""
    fixed = re.sub(r"\\x([0-9A-Fa-f]{2})", lambda m: "\\u00" + m.group(1), text)
    return json.loads(fixed, strict=False)


def _html_lines(fragment: str) -> List[str]:
    text = re.sub(r"(?i)<br\s*/?>", "\n", fragment or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&nbsp;", " ")
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def parse_store_row(row: Dict) -> Optional[Dict[str, str]]:
    """Parse one grid row into plain fields. Returns None without a source booking #."""
    booking_lines = _html_lines(str(row.get("1", "")))
    inmate_lines = _html_lines(str(row.get("2", "")))
    charge_lines = _html_lines(str(row.get("3", "")))

    booking_blob = " ".join(booking_lines)
    m = BOOKING_RE.search(booking_blob)
    if not m:
        return None
    booking_number = m.group(1)
    dm = DATE_IN_RE.search(booking_blob)
    booking_date = dm.group(1) if dm else ""
    booking_time = (dm.group(2) or "").strip() if dm else ""

    full_name = inmate_lines[0] if inmate_lines else ""
    parts = [p.strip() for p in full_name.split(",")]
    last = parts[0] if parts else ""
    first = parts[1] if len(parts) > 1 else ""
    middle = " ".join(parts[2:]) if len(parts) > 2 else ""
    inmate_blob = " ".join(inmate_lines[1:])
    race = RACE_RE.search(inmate_blob)
    sex = SEX_RE.search(inmate_blob)

    charges: List[str] = []
    total_bond = 0.0
    for line in charge_lines:
        cm = re.match(r"Charge\s*\d+\s*:\s*(.+)", line, re.I)
        if cm:
            charges.append(cm.group(1).strip())
            continue
        bm = re.match(r"Bond\s*:\s*\$?\s*([\d,]+(?:\.\d+)?)", line, re.I)
        if bm:
            try:
                total_bond += float(bm.group(1).replace(",", ""))
            except ValueError:
                pass

    return {
        "booking_number": booking_number,
        "booking_date": booking_date,
        "booking_time": booking_time,
        "full_name": full_name,
        "last": last,
        "first": first,
        "middle": middle,
        "race": race.group(1).upper() if race else "",
        "sex": sex.group(1).upper() if sex else "",
        "charges": " | ".join(charges),
        "bond": str(int(total_bond)) if float(total_bond).is_integer() else f"{total_bond:.2f}",
    }


class BayUniGuiSession:
    """One uniGUI session against the official Bay inmate search."""

    def __init__(self, http: Optional[requests.Session] = None, timeout: int = 30):
        self.http = http or requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        self.timeout = timeout
        self.sid = ""
        self.seq = 0
        self._xhr = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE_URL}/",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

    def open(self) -> None:
        resp = self.http.get(f"{BASE_URL}/", timeout=self.timeout)
        resp.raise_for_status()
        html = resp.text
        m = re.search(r"_S_ID=([A-Za-z0-9]+)", html)
        if not m:
            raise BaySourceContractError("uniGUI session id (_S_ID) not found on landing page")
        for comp, label in ((FLD_LAST, "Last Name"), (FLD_FIRST, "First Name"), (BTN_SEARCH, "Search")):
            if not re.search(rf'{comp}=new Ext\.[\w.]+\(\{{id:"{comp}_id"[^;]*?(?:fieldLabel|text):"{label}"', html):
                raise BaySourceContractError(f"component {comp} ({label}) moved — layout changed")
        if "Booking Information" not in html:
            raise BaySourceContractError("grid column 'Booking Information' missing — layout changed")
        self.sid = m.group(1)
        self.seq = 0
        self._event(f"Obj={FORM_OBJ}&Evt=afterrender&this={FORM_OBJ}")

    def _event(self, params: str) -> str:
        body = f"Ajax=1&IsEvent=1&{params}&_S_ID={self.sid}&_seq_={self.seq:x}&_uo_={FORM_OBJ}"
        self.seq += 1
        resp = self.http.post(HANDLE_URL, data=body, headers=self._xhr, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def search(self, last: str, first: str) -> List[Dict]:
        fp = quote(_fp_field(FLD_LAST, last) + _fp_field(FLD_FIRST, first) + _fp_field(FLD_MIDDLE, ""), safe="")
        reply = self._event(f"Obj={BTN_SEARCH}&Evt=click&this={BTN_SEARCH}&_fp_={fp}")
        m = re.search(r'setText\("(\d+) Matching Records"\)', reply)
        if not m:
            return []
        expected = int(m.group(1))
        rows: List[Dict] = []
        start, page = 0, 1
        while start < expected:
            resp = self.http.get(
                HANDLE_URL,
                params={"IsEvent": "1", "Obj": GRID, "Evt": "data", "_S_ID": self.sid,
                        "page": str(page), "start": str(start), "limit": str(PAGE_LIMIT)},
                headers={k: v for k, v in self._xhr.items() if k != "Content-Type"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            batch = _decode_store(resp.text).get("rows") or []
            if not batch:
                break
            rows.extend(batch)
            start += len(batch)
            page += 1
        return rows


class BayCountyScraper(BaseScraper):
    """Bay County (FL) — official uniGUI inmate search, initials walk."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Bay"

    @property
    def state(self) -> str:
        return "FL"

    @property
    def interval_minutes(self) -> int:
        return 120

    def _prefix_pairs(self) -> Iterable[Tuple[str, str]]:
        for last in string.ascii_uppercase:
            for first in string.ascii_uppercase:
                yield last, first

    def scrape(self) -> List[ArrestRecord]:
        session = BayUniGuiSession()
        session.open()
        now = datetime.now(timezone.utc).isoformat()
        seen: set = set()
        records: List[ArrestRecord] = []
        raw_rows = 0
        for last, first in self._prefix_pairs():
            for row in session.search(last, first):
                raw_rows += 1
                parsed = parse_store_row(row)
                if not parsed or parsed["booking_number"] in seen:
                    continue
                seen.add(parsed["booking_number"])
                records.append(ArrestRecord(
                    County=self.county,
                    State="FL",
                    Booking_Number=parsed["booking_number"],
                    Full_Name=parsed["full_name"].upper(),
                    First_Name=parsed["first"].upper(),
                    Middle_Name=parsed["middle"].upper(),
                    Last_Name=parsed["last"].upper(),
                    Booking_Date=parsed["booking_date"],
                    Booking_Time=parsed["booking_time"],
                    Race=parsed["race"],
                    Sex=parsed["sex"],
                    Charges=parsed["charges"][:1000],
                    Bond_Amount=parsed["bond"],
                    Facility=FACILITY,
                    Status="In Custody",
                    Detail_URL=f"{BASE_URL}/",
                    LastChecked=now,
                    LastCheckedMode="INITIAL",
                ))
            time.sleep(REQUEST_DELAY_S)
        if raw_rows and not records:
            raise RuntimeError(f"[BAY] {raw_rows} grid rows but no source booking numbers parsed (parse drift)")
        logger.info("[BAY] %d unique source bookings from %d grid rows", len(records), raw_rows)
        return records
