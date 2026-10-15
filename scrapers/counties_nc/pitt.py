"""
Pitt County (NC) Arrest Scraper — Detainee Search ASP.NET GridView (plain HTTPS).

URL: https://apps.pittcountync.gov/apps/detention/detainee/

Source contract (verified 2026-09-25 from datacenter egress, plain requests;
Select-detail hydrate probe 2026-10-07):

* A **blank** "Get Detainee" search returns every current detainee in a
  10-row GridView (``ctl00_mainContent_GridView1``) with columns
  Last Name / Suffix / First Name / Middle Name / Date of Birth /
  **Booking Number** / Gender / Race.
* Later pages are ordinary GridView postbacks
  (``__EVENTTARGET=ctl00$mainContent$GridView1``, ``__EVENTARGUMENT=Page$N``)
  carrying the previous response's ``__VIEWSTATE``/``__EVENTVALIDATION``.
  ~48 pages / ~480 detainees on 2026-09-25.
* ``Booking Number`` (digits) is the source booking key. Rows without one
  are skipped; nothing is synthesized.

**Hydrate limitation (listing-only for Write Bond fields):** the public
listing has no charges or bond columns. A ``Select$N`` detail postback
publishes demographics and Date Confined only. The detail ``Charge`` /
``Sentence`` / ``Print`` submit buttons return to the search form without
charge or bond rows; ``bondHyperLink`` is a static bond-instructions PDF.
Charges and Bond_Amount are ``""`` (unknown) — amounts are never
invented. Do not walk Select for hydrate until the site publishes those
fields publicly.

The previous implementation ran an A–Z / digraph last-name walk and never
followed the pager, so every saturated search was cut at 10 rows.

2026-10-15: honest identity and no invented fields. Every request goes through
scrapers.plain_http (honest User-Agent, certificate verified); a 401/403/429,
challenge page, dropped handshake or bad certificate raises (egress_block: /
tls_cert_invalid:), and any other transport error propagates (the run fails
visibly). Charges are "" and Bond_Amount is "" (the public listing publishes
neither; never "Unknown" or "0"). Status is "": the page is titled "Detainee
Search" with no current-only label, the grid has no release column, and no
population figure is published, so custody-only is not proven (the
"every current detainee" wording above is our own 2026-09-25 description,
not a source label). A published status, if one ever appears in the grid,
wins. See docs/recon/NC_PITT_ROSTER_SCOPE_2026-10-15.md.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import HONEST_USER_AGENT
from scrapers.plain_http import request as plain_request
from scrapers.scraper_resilience import ParseDriftError

logger = logging.getLogger(__name__)

PORTAL_URL = "https://apps.pittcountync.gov/apps/detention/detainee/"
GRID_ID = "ctl00_mainContent_GridView1"
GRID_TARGET = "ctl00$mainContent$GridView1"
BOOKING_RE = re.compile(r"^\d{4,10}$")
EXPECTED_HEADERS = ["Last Name", "First Name", "Date of Birth", "Booking Number"]

# Honest Write Bond / DocuSeal hydrate note — site does not publish these publicly.
HYDRATE_LIMITATION = (
    "listing_only: public GridView + Select detail publish name/DOB/booking/"
    "demographics/date confined only; Charge/Sentence/Print return to search "
    "without charge or bond rows; bondHyperLink is instructions PDF only. "
    "Charges=\"\" Bond_Amount=\"\" (unknown) — do not invent."
)
LISTING_ONLY_FOR_HYDRATE = True
STATUS_SOURCE_PUBLISHED = "published_status"


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

        return plain_request(_Raw(), method, url, county="Pitt", **kwargs)


def status_fields(row: dict):
    """(Status, status_source): a published grid status wins; else "" (scope not proven)."""
    published = (row.get("status") or "").strip()
    if published:
        return published, STATUS_SOURCE_PUBLISHED
    return "", ""


def form_fields(soup: BeautifulSoup) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for inp in soup.find_all("input"):
        name = inp.get("name")
        typ = (inp.get("type") or "text").lower()
        if not name or typ in ("submit", "button", "image", "checkbox", "radio"):
            continue
        data[name] = inp.get("value") or ""
    return data


def parse_grid(soup: BeautifulSoup) -> List[dict]:
    """Parse one GridView page. Raises ParseDriftError if headers moved."""
    table = soup.find("table", id=GRID_ID)
    if table is None:
        return []
    trs = table.find_all("tr")
    if not trs:
        return []
    headers = [c.get_text(" ", strip=True) for c in trs[0].find_all(["th", "td"])]
    if any(h not in headers for h in EXPECTED_HEADERS):
        raise ParseDriftError(f"Pitt: GridView headers changed: {headers[:10]}")
    idx = {h: i for i, h in enumerate(headers)}
    out: List[dict] = []
    for tr in trs[1:]:
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < len(EXPECTED_HEADERS) or not tr.find("a", string="Select"):
            continue  # pager row / spacer

        def cell(label: str) -> str:
            i = idx.get(label)
            return cells[i].strip() if i is not None and i < len(cells) else ""

        booking = cell("Booking Number")
        last = cell("Last Name")
        if not last or not BOOKING_RE.match(booking):
            continue
        out.append({
            "last": last,
            "suffix": cell("Suffix"),
            "first": cell("First Name"),
            "middle": cell("Middle Name"),
            "dob": cell("Date of Birth"),
            "booking": booking,
            "gender": cell("Gender"),
            "race": cell("Race"),
            "status": cell("Status") or cell("Custody Status"),  # published only, if ever added
        })
    return out


def has_next_page(soup: BeautifulSoup, current: int) -> bool:
    """True when the pager offers ``Page$<current+1>`` (a number or the ``...`` link)."""
    pager = str(soup.find("table", id=GRID_ID) or "")
    return f"Page${current + 1}'" in pager


class PittScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "Pitt Detainee Search (plain HTTPS); blank search + GridView Page$N "
        "postbacks; source Booking Number column. Listing-only hydrate: "
        "public site does not publish charges or bond amounts (Select detail "
        "has demographics/date confined only; Charge/Sentence/Print bounce "
        "to search). Charges and Bond_Amount are \"\" (unknown) — do not invent."
    )

    MAX_PAGES = 120  # ~48 used on 2026-09-25; hard stop against pager loops
    PAGE_DELAY_S = 0.25

    @property
    def county(self) -> str:
        return "Pitt"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = _HonestSession()
        session.headers.update({
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        })

        landing = session.get(PORTAL_URL, timeout=40)
        landing.raise_for_status()
        soup = BeautifulSoup(landing.text, "html.parser")
        data = form_fields(soup)
        if "ctl00$mainContent$lastNameTextBox" not in data:
            raise ParseDriftError("Pitt: detainee search form changed")
        data.update({
            "ctl00$mainContent$bookNumTextBox": "",
            "ctl00$mainContent$lastNameTextBox": "",
            "ctl00$mainContent$firstNameTextBox": "",
            "ctl00$mainContent$Button3": "Get  Detainee",
        })
        resp = session.post(PORTAL_URL, data=data, timeout=60)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        rows: Dict[str, dict] = {}
        for r in parse_grid(soup):
            rows.setdefault(r["booking"], r)
        page = 1
        while page < self.MAX_PAGES:
            if not has_next_page(soup, page):
                break
            page += 1
            post = form_fields(soup)
            post["__EVENTTARGET"] = GRID_TARGET
            post["__EVENTARGUMENT"] = f"Page${page}"
            time.sleep(self.PAGE_DELAY_S)
            resp = session.post(PORTAL_URL, data=post, timeout=60)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            page_rows = parse_grid(soup)
            if not page_rows:
                break
            new = 0
            for r in page_rows:
                if r["booking"] not in rows:
                    rows[r["booking"]] = r
                    new += 1
            if new == 0:
                break  # server clamped to the last page

        records = [self._to_record(r) for r in rows.values()]
        logger.info("Pitt: %d detainees over %d pages in %.1fs", len(records), page, time.time() - start)
        return records

    def _to_record(self, row: dict) -> ArrestRecord:
        last, first, middle = row["last"], row["first"], row["middle"]
        suffix = row.get("suffix") or ""
        given = " ".join(p for p in (first, middle, suffix) if p).strip()
        full = f"{last}, {given}".strip(", ")
        status, status_source = status_fields(row)
        return ArrestRecord(
            County=self.county,
            State="NC",
            Full_Name=full,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=row["booking"],
            DOB=row.get("dob") or "",
            Sex=(row.get("gender") or "")[:1].upper(),
            Race=row.get("race") or "",
            # Public source does not publish charges or bond — unknown is "".
            Charges="",
            Bond_Amount="",
            Status=status,
            Detail_URL=PORTAL_URL,
            Facility="Pitt County Detention Center",
            extra_data={
                "hydrate_limitation": HYDRATE_LIMITATION,
                "listing_only_for_hydrate": LISTING_ONLY_FOR_HYDRATE,
                "status_source": status_source,
            },
        )
