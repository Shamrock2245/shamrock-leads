"""Lake County (FL) — LCSO recent-arrests feed behind Cloudflare Turnstile.

Source: Lake County Sheriff's Office Inmate Search
        https://www.lcso.org/inmate-search/  (data: POST /inmate-search/api/inmates)

Owner decision (Brendan, 2026-09-25): handle Lake like Broward — obtain the
Turnstile token the endpoint requires from SolveCaptcha (env
``SOLVECAPTCHA_KEY``) via the shared helper ``scrapers/solvecaptcha.py``.
Plain HTTPS through ``scrapers.plain_http`` (honest User-Agent, certificate
verified); no proxy, Obscura, stealth browser, Chrome disguise or TLS
impersonation. Status is never invented as "In Custody" (``""`` unless the
source publishes a custody field). Bond is published-only.

Contract (verified 2026-09-25):
* The page renders Turnstile (reCAPTCHA-compat) with sitekey
  ``TURNSTILE_SITEKEY``, no ``data-action``. Tokens are single-use.
* ``POST /inmate-search/api/inmates`` takes a JSON body (jQuery ``$.post``
  form content type). Without a token → HTTP 400 ``{"required": {"token": …}}``.
* ``{"token": T, "recent_data": true}`` returns ``{"records": [...]}``: the
  data behind LCSO's "recent arrests" PDF (about the last day of bookings).
  One Turnstile solve per run.
* The UI labels ``pin`` as **Booking #** (8 digits, e.g. ``26NNNNNN``). That is
  the only key used; rows without a well-formed ``pin`` are dropped, never
  synthesized. Rows flagged ``exemption`` != 0 are skipped.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from core.models import ArrestRecord
from scrapers import plain_http
from scrapers.base_scraper import BaseScraper
from scrapers.published_bond import charge_bond_amount
from scrapers.scraper_resilience import AntiBotBlocked, ParseDriftError
from scrapers.solvecaptcha import solve_turnstile, solvecaptcha_key

logger = logging.getLogger(__name__)

BASE_URL = "https://www.lcso.org"
SEARCH_PAGE_URL = f"{BASE_URL}/inmate-search/"
API_URL = f"{BASE_URL}/inmate-search/api/inmates"
TURNSTILE_SITEKEY = "0x4AAAAAAEFZn-Q0bIVuiZHe"
FACILITY = "Lake County Jail"
BOOKING_RE = re.compile(r"^\d{8}$")
API_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": SEARCH_PAGE_URL,
    "Origin": BASE_URL,
}


def _fmt_datetime(raw: str) -> tuple[str, str]:
    """``YYYY-MM-DD HH:MM:SS`` → (``MM/DD/YYYY``, ``HH:MM AM``); passthrough on drift."""
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return dt.strftime("%m/%d/%Y"), ("" if fmt == "%Y-%m-%d" else dt.strftime("%I:%M %p"))
    return raw, ""


def _clean(val) -> str:
    s = re.sub(r"\s+", " ", str(val or "")).strip()
    return "" if s.lower() in ("n/a", "none", "null") else s


def published_status(row: dict) -> str:
    """Custody status only as LCSO published it; unknown is ""."""
    if not isinstance(row, dict):
        return ""
    for key in ("status", "Status", "custody_status", "custodyStatus", "in_custody_text"):
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    flag = row.get("in_custody")
    if flag is None:
        flag = row.get("inCustody")
    if isinstance(flag, str):
        flag = {"true": True, "false": False, "1": True, "0": False}.get(flag.strip().lower())
    if flag is True:
        return "In Custody"
    if flag is False:
        return "Released"
    return ""


def _published_bond(row: dict) -> tuple[str, str]:
    """``total_bond_amount``: missing/blank -> ""; explicit 0 kept; negative -> NO BOND + ""."""
    if "total_bond_amount" not in row and "total_bond" not in row:
        return "", ""
    raw = row.get("total_bond_amount", row.get("total_bond"))
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return "", ""
    try:
        bond_val = float(raw)
    except (TypeError, ValueError):
        amt = charge_bond_amount(raw)
        if amt is None:
            return "", ""
        bond_val = float(amt)
    if bond_val < 0:
        return "", "NO BOND"
    if bond_val == 0:
        return "0", ""
    return (str(int(bond_val)) if float(bond_val).is_integer() else f"{bond_val:.2f}"), ""


def parse_record(row: dict) -> Optional[ArrestRecord]:
    """Map one LCSO record to an ArrestRecord, or ``None`` when it must be dropped."""
    if not isinstance(row, dict):
        return None
    pin = str(row.get("pin") or "").strip()
    if not BOOKING_RE.match(pin):
        return None
    if int(row.get("exemption") or 0) != 0:
        return None

    last = _clean(row.get("lastname")).upper()
    first = _clean(row.get("firstname")).upper()
    middle = _clean(row.get("middle")).upper()
    if not last:
        return None
    booking_date, booking_time = _fmt_datetime(row.get("comdate"))
    arrest_date, arrest_time = _fmt_datetime(row.get("ar_date"))
    dob, _ = _fmt_datetime(row.get("birth"))

    charges: List[str] = []
    cases: List[str] = []
    for ch in row.get("charges") or []:
        if not isinstance(ch, dict):
            continue
        desc = _clean(ch.get("note"))
        level = " ".join(p for p in (_clean(ch.get("morf")), _clean(ch.get("degree"))) if p)
        if desc:
            charges.append(f"{desc} ({level})" if level else desc)
        case = _clean(ch.get("casenum"))
        if case and case.upper() != "N/A" and case not in cases:
            cases.append(case)

    bond_amount, bond_type = _published_bond(row)

    full = f"{last}, {first}" + (f" {middle}" if middle else "")
    return ArrestRecord(
        County="Lake",
        State="FL",
        Booking_Number=pin,
        Full_Name=full,
        First_Name=first,
        Middle_Name=middle,
        Last_Name=last,
        DOB=dob,
        Age_At_Arrest=str(row.get("age") or ""),
        Race=_clean(row.get("race")),
        Sex=_clean(row.get("sex"))[:1].upper(),
        Arrest_Date=arrest_date,
        Arrest_Time=arrest_time,
        Booking_Date=booking_date,
        Booking_Time=booking_time,
        Agency=_clean(row.get("arrest")),
        Facility=FACILITY,
        Status=published_status(row),
        Charges=" | ".join(charges),
        Case_Number=" | ".join(cases),
        Bond_Amount=bond_amount,
        Bond_Type=bond_type,
        Detail_URL=SEARCH_PAGE_URL,
        LastCheckedMode="INITIAL",
        Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
    )


def parse_records(rows: Iterable[dict]) -> List[ArrestRecord]:
    seen: set = set()
    out: List[ArrestRecord] = []
    for row in rows:
        rec = parse_record(row)
        if rec is None or rec.Booking_Number in seen:
            continue
        seen.add(rec.Booking_Number)
        out.append(rec)
    return out


class LakeCountyScraper(BaseScraper):
    """Lake (FL) — LCSO recent-arrests JSON after one SolveCaptcha Turnstile solve."""

    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "LCSO /inmate-search/api/inmates recent_data after SolveCaptcha Turnstile "
        "(owner-approved 2026-09-25, Broward precedent); Booking # = pin."
    )

    @property
    def county(self) -> str:
        return "Lake"

    @property
    def state(self) -> str:
        return "FL"

    @property
    def roster_url(self) -> str:
        return SEARCH_PAGE_URL

    def scrape(self) -> List[ArrestRecord]:
        api_key = solvecaptcha_key()
        if not api_key:
            raise RuntimeError(f"Lake requires SOLVECAPTCHA_KEY to solve Turnstile on {SEARCH_PAGE_URL}")

        session = plain_http.make_session(API_HEADERS)
        # Landing GET: blocks / TLS failures raise egress_block / tls_cert_invalid.
        landing = plain_http.request(session, "GET", SEARCH_PAGE_URL, county="Lake", timeout=30)
        if TURNSTILE_SITEKEY not in landing.text:
            raise ParseDriftError("Lake inmate-search page no longer carries the verified Turnstile sitekey")

        token = solve_turnstile(api_key, sitekey=TURNSTILE_SITEKEY, pageurl=SEARCH_PAGE_URL, log_prefix="[Lake]")
        if not token:
            raise AntiBotBlocked("Lake Turnstile token could not be obtained from SolveCaptcha")

        # API POST keeps the same session (honest UA, verify on). SolveCaptcha
        # stays on its own transport inside solve_turnstile (unchanged).
        resp = plain_http.request(
            session,
            "POST",
            API_URL,
            county="Lake",
            data=json.dumps({"token": token, "recent_data": True}),
            headers=API_HEADERS,
            timeout=60,
        )
        try:
            data = resp.json()
        except ValueError as e:
            raise ParseDriftError(f"Lake inmate API returned non-JSON ({resp.headers.get('content-type')})") from e
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            raise ParseDriftError(f"Lake inmate API response has no records list: {keys}")
        captcha = data.get("captcha") or {}
        if isinstance(captcha, dict) and captcha.get("success") is False:
            raise AntiBotBlocked("Lake inmate API rejected the Turnstile token")

        rows = data["records"]
        records = parse_records(rows)
        if rows and not records:
            raise ParseDriftError(f"Lake: {len(rows)} rows but none had a well-formed Booking # (pin)")
        logger.info("Lake: %d records (%d rows from recent_data)", len(records), len(rows))
        return records

