"""
Orange County (NC) Arrest Scraper — daily "Detainees In Confinement" PDF.

Portal: https://www.ocsonc.com/detention/current-detainees  (Orange County
Sheriff's Office, Wix site). The page links the current daily PDF report
("Daily In Confinement Report by Facility") either as
``https://www.ocsonc.com/_files/ugd/<id>.pdf`` or on the Wix file host
``https://<uuid>.usrfiles.com/ugd/<id>.pdf``. The report id changes over time,
so the PDF is discovered from the page on every run and the newest report
(by its printed ``MM/DD/YYYY HH:MM:SS`` header timestamp) is used.

Report rows (pdfplumber text), one per detainee::

    LAST, FIRST MIDDLE  A/J  R  S  <Bk #>  <charge> / … / <docket> / $<bond> / …  MM/DD/YYYY HHMM  <days>

``Bk #`` is the source booking number (digits) and the only key emitted.
Continuation lines add further charges/bonds to the current detainee.

Fixed 2026-09-25: discovery ignored the ``usrfiles.com`` link (the current
report) and fell back to hard-coded stale PDFs, and the row regex expected the
booking number glued to a race letter, so every run parsed 0 rows. Plain
HTTPS with TLS verification; no proxy or stealth.

2026-10-15: honest identity and published-only fields. Every request goes
through scrapers.plain_http (honest User-Agent, certificate verified); a
401/403/429, challenge page, dropped handshake or bad certificate raises
(egress_block: / tls_cert_invalid:) and is never swallowed by the PDF loop.
Charges are "" when none are printed. Bond_Amount is the sum of the printed
per-charge ``$`` amounts only when every charge line prints one (a printed $0
is kept); any charge without an amount (blank or NOBD) makes it "" (unknown).
Status is "In Custody" only when the report carries the source's own
current-only title ("Daily In Custody Report" / "Detainees In Confinement
Report"), linked from the "Current Detainees" page; status_source is
"published_in_custody_report_label". Without that title Status is "".
See docs/recon/NC_ORANGE_ROSTER_SCOPE_2026-10-15.md.
"""
from __future__ import annotations

import io
import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.plain_http import request as plain_request
from scrapers.published_bond import charge_bond_amount, total_published_bond
from scrapers.scraper_resilience import EgressBlocked, ParseDriftError, SourceUrlChanged

_BLOCKS = (EgressBlocked, SourceTLSInvalid)

logger = logging.getLogger(__name__)

PORTAL_URL = "https://www.ocsonc.com/detention/current-detainees"
PDF_LINK_RE = re.compile(
    r"https?://(?:www\.)?ocsonc\.com/_files/ugd/[A-Za-z0-9_]+\.pdf"
    r"|https?://[a-z0-9\-]+\.usrfiles\.com/ugd/[A-Za-z0-9_]+\.pdf"
)
REPORT_TS_RE = re.compile(r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})")
ROW_RE = re.compile(
    r"^(?P<name>[^,\d][^,]*,\s*[^\d/$]*?)\s+(?P<aj>[AJ])\s+(?P<race>[A-Z])\s+"
    r"(?P<sex>[MFUX])\s+(?P<bk>\d{4,6})(?:\s+(?P<rest>.*))?$"
)
BOOKED_TAIL_RE = re.compile(r"(?<![\d/])(\d{2}/\d{2}/\d{4})\s+(\d{4})(?:\s+(\d+))?\s*$")
BOND_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
DOCKET_RE = re.compile(r"\b(\d{2}[A-Z]{2}\d{5,}(?:-\d+)?)\b")
HEADER_PREFIXES = (
    "user:", "detainees in confinement", "daily in", "confinement report", "facility:",
    "days in", "name a/j", "orange county", "page ",
)
MAX_REPORT_AGE = timedelta(hours=48)
CUSTODY_LABEL_RE = re.compile(r"^\s*(?:Daily\s+In\s+Custody|Detainees\s+In\s+Confinement)\s+Report\b", re.I | re.M)
STATUS_SOURCE_REPORT_LABEL = "published_in_custody_report_label"


class _HonestSession(requests.Session):
    """requests.Session whose every call goes through plain_http (honest UA, verify on, typed blocks)."""

    def __init__(self):
        super().__init__()
        self.headers.update({"User-Agent": HONEST_USER_AGENT})
        self.verify = True

    def request(self, method, url, **kwargs):  # noqa: D401
        sess = self

        class _Raw:
            def request(self, m, u, **kw):
                return requests.Session.request(sess, m, u, **kw)

        return plain_request(_Raw(), method, url, county="Orange", **kwargs)


def status_fields(report_text: str):
    """(Status, status_source): In Custody only under the source's current-only report title."""
    if CUSTODY_LABEL_RE.search(report_text or ""):
        return "In Custody", STATUS_SOURCE_REPORT_LABEL
    return "", ""


def discover_pdf_urls(html: str) -> List[str]:
    found: List[str] = []
    for m in PDF_LINK_RE.findall((html or "").replace("\\/", "/")):
        if m not in found:
            found.append(m)
    return found


def report_timestamp(text: str) -> Optional[datetime]:
    m = REPORT_TS_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%m/%d/%Y %H:%M:%S")
    except ValueError:
        return None


def _add_charge_bits(cur: dict, text: str) -> None:
    charge = text.split(" / ")[0].strip()
    if charge and not re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}", charge) and charge not in cur["charges"]:
        cur["charges"].append(charge[:120])
    amounts = BOND_RE.findall(text)
    if not amounts:
        cur["bonds"].append(None)  # this charge prints no amount (blank / NOBD)
    for b in amounts:
        cur["bonds"].append(charge_bond_amount(b))
    for d in DOCKET_RE.findall(text):
        if d not in cur["dockets"]:
            cur["dockets"].append(d)


def parse_report_text(text: str) -> List[dict]:
    """Parse pdfplumber text of the daily report into detainee dicts."""
    out: List[dict] = []
    cur: Optional[dict] = None
    for raw in (text or "").splitlines():
        ln = re.sub(r"\s+", " ", raw).strip()
        if not ln:
            continue
        low = ln.lower()
        if any(low.startswith(p) for p in HEADER_PREFIXES) or re.match(r"^\S*\d+ page \d+$", low):
            continue
        m = ROW_RE.match(ln)
        if m:
            rest = m.group("rest") or ""
            bdate = btime = days = ""
            tail = BOOKED_TAIL_RE.search(rest)
            if tail:
                bdate, btime, days = tail.group(1), tail.group(2), tail.group(3) or ""
                rest = rest[: tail.start()].strip()
            name = m.group("name").strip()
            last, _, given = name.partition(",")
            parts = given.split()
            cur = {
                "name": name,
                "last": last.strip(),
                "first": parts[0] if parts else "",
                "middle": " ".join(parts[1:]),
                "race": m.group("race"),
                "sex": m.group("sex"),
                "booking": m.group("bk"),
                "booking_date": bdate,
                "booking_time": f"{btime[:2]}:{btime[2:]}" if btime else "",
                "days_in": days,
                "charges": [],
                "bonds": [],
                "dockets": [],
            }
            out.append(cur)
            if rest:
                _add_charge_bits(cur, rest)
            continue
        if cur is not None and " / " in ln:
            _add_charge_bits(cur, ln)
    for r in out:
        r["bond"] = total_published_bond(r.pop("bonds"))
    return out


class OrangeScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "OCSO current-detainees page -> daily 'Detainees In Confinement' PDF "
        "(discovered each run, newest report); source Bk # (digits)."
    )

    @property
    def county(self) -> str:
        return "Orange"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = _HonestSession()
        page = session.get(PORTAL_URL, timeout=40)
        page.raise_for_status()
        urls = discover_pdf_urls(page.text)
        if not urls:
            raise SourceUrlChanged("Orange NC: no roster PDF link on current-detainees page")

        pdf_url, text, ts = self._newest_report(session, urls)
        if not text:
            raise SourceUrlChanged("Orange NC: linked roster PDFs could not be read")
        now = datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
        if ts is None or now - ts > MAX_REPORT_AGE:
            raise ParseDriftError(f"Orange NC: newest linked report is stale or undated ({ts})")
        if "Bk #" not in text:
            raise ParseDriftError("Orange NC: report layout changed (no 'Bk #' column)")

        rows = parse_report_text(text)
        status = status_fields(text)
        seen: set = set()
        records: List[ArrestRecord] = []
        for r in rows:
            if r["booking"] in seen:
                continue
            seen.add(r["booking"])
            records.append(self._to_record(r, pdf_url, status))
        if not records:
            raise ParseDriftError("Orange NC: report parsed to 0 detainee rows")
        logger.info("Orange NC: %d detainees from report %s in %.1fs", len(records), ts, time.time() - start)
        return records

    def _newest_report(self, session: requests.Session, urls: List[str]) -> Tuple[Optional[str], str, Optional[datetime]]:
        best: Tuple[Optional[str], str, Optional[datetime]] = (None, "", None)
        for url in urls:
            try:
                resp = session.get(url, timeout=45)
                if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
                    continue
                text = self._extract_text(resp.content)
            except _BLOCKS:
                raise
            except Exception as exc:
                logger.debug("Orange NC PDF fetch failed (%s)", type(exc).__name__)
                continue
            ts = report_timestamp(text)
            if ts and (best[2] is None or ts > best[2]):
                best = (url, text, ts)
            elif best[0] is None:
                best = (url, text, ts)
        return best

    @staticmethod
    def _extract_text(content: bytes) -> str:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)

    def _to_record(self, r: dict, pdf_url: Optional[str], status: Tuple[str, str] = ("", "")) -> ArrestRecord:
        rec = ArrestRecord(
            County=self.county,
            State="NC",
            Full_Name=r["name"],
            First_Name=r["first"],
            Middle_Name=r["middle"],
            Last_Name=r["last"],
            Booking_Number=r["booking"],
            Booking_Date=r["booking_date"],
            Booking_Time=r["booking_time"],
            Race=r["race"],
            Sex=r["sex"],
            Case_Number=" | ".join(r["dockets"][:5]),
            Charges=" | ".join(r["charges"]),
            Bond_Amount=r["bond"],
            Status=status[0],
            Facility="Orange County Detention Center",
            Agency="Orange County Sheriff's Office",
            Detail_URL=pdf_url or PORTAL_URL,
        )
        if status[1]:
            extra = dict(getattr(rec, "extra_data", None) or {})
            extra["status_source"] = status[1]
            rec.extra_data = extra
        return rec
