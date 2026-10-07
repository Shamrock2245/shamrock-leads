"""York County, South Carolina — public "Inmates in Jail" roster.

Source recon 2026-10-07 (docs/recon/SC_YORK_INMATES_IN_JAIL_2026-10-07.md):
  * URL: https://inmatesinjail.yorkcountygov.com/detentioncenter/inmatesinjail.aspx
    (official York County government host). Plain HTTPS GET, no login, CAPTCHA,
    or WAF. The page states "Only current booking information is available".
  * ASP.NET DataGrid ``dgJackets`` lists 15 people per page and pages through
    ``__doPostBack('dgJackets$ctl01$ctlNN')``. A full walk was 29 pages and 435
    rows, matching ``Results Count: 435``.
  * Each row's bookings table publishes ``Booking Number`` (source key, format
    ``DC<YYYY><NNNNN>``), ``Booking Date`` (date + time), ``Release Date``
    (``*In Jail`` while in custody), ``Total Bond``, and a charge grid
    (Sequence# / Charge Description / Arresting Agency). The mugshot path is
    ``/photos/<Booking Number>.jpg``.
  * The read smoke found 435 of 435 unique source Booking Numbers. The earlier
    timeout no longer reproduces.

Contract: ``Booking_Number`` is the published Booking Number only. A row is
dropped when that number is missing, malformed, or disagrees with its photo
key. Bond comes from ``Total Bond`` only. Nothing is synthesized.
An incomplete page walk (count mismatch against ``Results Count``, repeated
page/postback, or ``MAX_PAGES`` hit early) raises ParseDriftError; no partial
success.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.scraper_resilience import AntiBotBlocked, ParseDriftError

logger = logging.getLogger(__name__)

PORTAL_URL = "https://inmatesinjail.yorkcountygov.com/detentioncenter/inmatesinjail.aspx"
PHOTO_BASE = "https://inmatesinjail.yorkcountygov.com"
FACILITY = "York County Detention Center"
USER_AGENT = "Mozilla/5.0 (compatible; ShamrockRoster/1.0)"
BOOKING_RE = re.compile(r"^DC\d{9}$")
PHOTO_KEY_RE = re.compile(r"/photos/([A-Za-z0-9]+)\.jpg", re.I)
DATETIME_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M)$", re.I)
MAX_PAGES = 80
PAGE_DELAY_S = 0.4


def _text(node: Optional[Tag]) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _split_datetime(raw: str) -> Tuple[str, str]:
    raw = " ".join((raw or "").split())
    m = DATETIME_RE.match(raw)
    if not m:
        return raw, ""
    return m.group(1), m.group(2).upper()


def _bond(raw: str) -> str:
    """Published Total Bond dollars ("$1,250.00" -> "1250"); "0" when blank."""
    cleaned = re.sub(r"[^\d.]", "", raw or "")
    if not cleaned:
        return "0"
    try:
        amount = float(cleaned)
    except ValueError:
        return "0"
    return ("%.2f" % amount).rstrip("0").rstrip(".") or "0"


def _split_name(full_name: str) -> Tuple[str, str, str]:
    """``Last , First Middle`` -> (first, middle, last)."""
    if "," not in full_name:
        return "", "", ""
    last, remainder = [part.strip() for part in full_name.split(",", 1)]
    parts = remainder.split()
    return (parts[0] if parts else ""), " ".join(parts[1:]), last


def _person_info(item: Tag) -> Dict[str, str]:
    info: Dict[str, str] = {}
    table = item.find("table", class_="table2")
    if table is None:
        return info
    name_cell = table.find("td", class_="cell1")
    info["name"] = " ".join(_text(name_cell).replace(" ,", ",").split())
    for row in table.find_all("tr", recursive=False):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 2:
            continue
        label = _text(cells[0]).rstrip(":").casefold()
        if label:
            info[label] = _text(cells[1])
    return info


def _charges(cell: Tag) -> Tuple[str, str]:
    """Return (charge descriptions, arresting agencies) from a nested charge grid."""
    charges: List[str] = []
    agencies: List[str] = []
    for table in cell.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [_text(c).casefold() for c in rows[0].find_all("td")]
        if "charge description" not in headers:
            continue
        ci = headers.index("charge description")
        ai = headers.index("arresting agency") if "arresting agency" in headers else -1
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) > ci and _text(cells[ci]):
                charges.append(_text(cells[ci]))
            if 0 <= ai < len(cells) and _text(cells[ai]):
                agencies.append(_text(cells[ai]))
    return " | ".join(dict.fromkeys(charges)), " | ".join(dict.fromkeys(agencies))


def _bookings(item: Tag) -> List[Dict[str, str]]:
    """Booking rows from the item's ``table5`` (header row, value rows, charge rows)."""
    table = item.find("table", class_="table5")
    if table is None:
        return []
    rows = table.find_all("tr", recursive=False)
    header: List[str] = []
    out: List[Dict[str, str]] = []
    for row in rows:
        cells = row.find_all("td", recursive=False)
        texts = [_text(c) for c in cells]
        lowered = [t.casefold() for t in texts]
        if "booking number" in lowered:
            header = lowered
            continue
        if not header:
            continue
        if len(cells) == len(header):
            out.append(dict(zip(header, texts)))
        elif len(cells) == 1 and out:
            charges, agencies = _charges(cells[0])
            if charges:
                prev = out[-1].get("_charges", "")
                out[-1]["_charges"] = " | ".join(x for x in (prev, charges) if x)
            if agencies:
                out[-1]["_agency"] = agencies
    return out


def parse_page(html: str) -> Tuple[List[ArrestRecord], int]:
    """Parse one roster page. Returns (records, people rows seen)."""
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.find("table", id="dgJackets")
    if grid is None:
        raise ParseDriftError("York: dgJackets grid missing from roster page")
    items = [
        tr for tr in grid.find_all("tr", recursive=False)
        if {"dgItem", "dgAltItem"} & set(tr.get("class") or [])
    ]
    records: List[ArrestRecord] = []
    for item in items:
        info = _person_info(item)
        full_name = info.get("name", "")
        if not full_name:
            continue
        bookings = _bookings(item)
        photo = item.find("img", src=PHOTO_KEY_RE)
        photo_key = ""
        if photo is not None:
            m = PHOTO_KEY_RE.search(photo.get("src", ""))
            photo_key = m.group(1).upper() if m else ""
        for bk in bookings:
            booking = bk.get("booking number", "").strip().upper()
            if not BOOKING_RE.match(booking):
                continue
            if len(bookings) == 1 and photo_key and photo_key != booking:
                logger.warning("York: photo key disagrees with Booking Number; row dropped")
                continue
            booking_date, booking_time = _split_datetime(bk.get("booking date", ""))
            if not booking_date:
                continue
            release_raw = bk.get("release date", "")
            in_jail = "in jail" in release_raw.casefold()
            release_date = "" if in_jail else _split_datetime(release_raw)[0]
            first, middle, last = _split_name(full_name)
            race_sex = info.get("race/sex", "").split()
            records.append(ArrestRecord(
                County="York",
                State="SC",
                Full_Name=full_name,
                First_Name=first,
                Middle_Name=middle,
                Last_Name=last,
                Booking_Number=booking,
                Booking_Date=booking_date,
                Booking_Time=booking_time,
                Arrest_Date=booking_date,
                Arrest_Time=booking_time,
                Status="In Custody" if in_jail else ("Released" if release_date else "Unknown"),
                Release_Date=release_date,
                Facility=FACILITY,
                Agency=bk.get("_agency", ""),
                Race=race_sex[0] if race_sex else "",
                Sex=race_sex[1] if len(race_sex) > 1 else "",
                Age_At_Arrest=info.get("age", ""),
                City=info.get("city", ""),
                Charges=bk.get("_charges", ""),
                Bond_Amount=_bond(bk.get("total bond", "")),
                Mugshot_URL=f"{PHOTO_BASE}/photos/{booking}.jpg" if photo_key == booking else "",
                Detail_URL=PORTAL_URL,
                extra_data={"booking_key_origin": "source-issued public Booking Number"},
            ))
    return records, len(items)


def _next_page_target(html: str) -> Optional[str]:
    """Postback target for the page after the current one (``N+1`` or ``...``)."""
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.find("table", id="dgJackets")
    pager = grid.find("tr", class_="pager") if grid is not None else None
    if pager is None:
        return None
    current = pager.find("span")
    if current is None or not _text(current).isdigit():
        return None
    want = str(int(_text(current)) + 1)
    link = next((a for a in pager.find_all("a") if _text(a) == want), None)
    if link is None:
        sib = current.find_next_sibling("a")
        link = sib if sib is not None and _text(sib) == "..." else None
    if link is None:
        return None
    m = re.search(r"__doPostBack\('([^']+)'", link.get("href", ""))
    return m.group(1) if m else None


def _current_page(html: str) -> Optional[int]:
    """Page number the pager marks as current (the ``<span>``), or None without a pager."""
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.find("table", id="dgJackets")
    pager = grid.find("tr", class_="pager") if grid is not None else None
    current = pager.find("span") if pager is not None else None
    text = _text(current)
    return int(text) if text.isdigit() else None


def _row_keys(html: str) -> List[str]:
    """One identity string per roster row (whitespace-normalised row text).

    Used only to detect repeated pages and to count unique walked rows against
    the published Results Count. It is never written as a booking key.
    """
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.find("table", id="dgJackets")
    if grid is None:
        return []
    return [
        _text(tr) for tr in grid.find_all("tr", recursive=False)
        if {"dgItem", "dgAltItem"} & set(tr.get("class") or [])
    ]


def _hidden_fields(html: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    return {
        i.get("name"): i.get("value", "")
        for i in soup.find_all("input", type="hidden")
        if i.get("name")
    }


def _results_count(html: str) -> Optional[int]:
    m = re.search(r"Results Count:\s*(\d+)", BeautifulSoup(html, "html.parser").get_text(" "))
    return int(m.group(1)) if m else None


class YorkScraper(BaseScraper):
    """York County (SC) public roster keyed on the source Booking Number."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "York"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def roster_url(self) -> str:
        return PORTAL_URL

    def _fetch(self, session: requests.Session, data: Optional[Dict[str, str]] = None) -> str:
        if data is None:
            resp = session.get(PORTAL_URL, timeout=45)
        else:
            resp = session.post(PORTAL_URL, data=data, timeout=45)
        if resp.status_code == 403:
            raise AntiBotBlocked("York roster returned 403")
        resp.raise_for_status()
        return resp.text

    def scrape(self) -> List[ArrestRecord]:
        """Walk every roster page; fail closed unless the walk is provably complete.

        * A page whose pager number was already visited, or whose rows exactly
          repeat an earlier page, raises ParseDriftError (postback loop).
        * Reaching MAX_PAGES while the pager still offers a next page raises.
        * When the page publishes ``Results Count``, the number of unique walked
          rows must equal it exactly; otherwise ParseDriftError. This also covers
          a next-page link that disappears early.
        * When ``Results Count`` is absent there is nothing to verify against:
          the repeat / MAX_PAGES guards still apply, a warning is logged, and the
          walked records are returned (pre-existing behaviour).
        """
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Referer": PORTAL_URL})
        html = self._fetch(session)
        expected = _results_count(html)
        records: List[ArrestRecord] = []
        seen: set = set()
        seen_rows: set = set()
        visited_pages: set = set()
        page_fingerprints: set = set()
        people = 0
        for page in range(1, MAX_PAGES + 1):
            page_records, page_people = parse_page(html)
            current = _current_page(html)
            if current is not None:
                if current in visited_pages:
                    raise ParseDriftError(f"York: postback returned page {current} again (walk step {page})")
                visited_pages.add(current)
            row_keys = _row_keys(html)
            fingerprint = tuple(row_keys)
            if fingerprint and fingerprint in page_fingerprints:
                raise ParseDriftError(f"York: walk step {page} repeated an earlier page's rows")
            page_fingerprints.add(fingerprint)
            seen_rows.update(row_keys)
            people += page_people
            if page_people and not page_records:
                raise ParseDriftError(f"York: page {page} had {page_people} rows but no source Booking Number")
            for rec in page_records:
                if rec.Booking_Number in seen:
                    continue
                seen.add(rec.Booking_Number)
                records.append(rec)
            target = _next_page_target(html)
            if not target:
                break
            if page == MAX_PAGES:
                raise ParseDriftError(
                    f"York: hit MAX_PAGES={MAX_PAGES} with a next page still offered; walk incomplete"
                )
            form = _hidden_fields(html)
            form.update({"__EVENTTARGET": target, "__EVENTARGUMENT": "", "txtLastName": ""})
            time.sleep(PAGE_DELAY_S)
            html = self._fetch(session, form)
        unique_rows = len(seen_rows)
        if expected is None:
            logger.warning(
                "York: Results Count not published; cannot verify completeness of %d walked rows", unique_rows
            )
        elif unique_rows != expected:
            raise ParseDriftError(
                f"York: walked {unique_rows} unique rows but page reports Results Count {expected}; walk incomplete"
            )
        logger.info("York: %d source-keyed bookings from %d roster rows", len(records), people)
        return records
