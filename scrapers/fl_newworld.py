"""Shared Florida New World ``NewWorld.InmateInquiry`` roster fetch + parse.

Used by Walton and Flagler (recon 2026-10-07,
docs/recon/FL_BREVARD_NEWWORLD_2026-10-07.md).

Contract (ordinary public HTTPS, plain ``requests``, TLS verification on):

* Listing: ``GET <portal>?InCustody=True[&Page=N]`` — up to 100 rows per page, each
  row links ``/Inmate/Detail/-<id>``. The detail id is a portal row id, never a
  booking number.
* Detail: ``<h2>Booking History</h2>`` then one ``div.Booking`` per booking,
  newest first. Each block carries ``<h3><label>Booking</label><span>YYYY-NNNNNNNN</span>``
  plus ``li.BookingDate`` / ``li.ReleaseDate`` / ``li.TotalBondAmount`` and a
  ``div.BookingCharges`` grid (``td.ChargeDescription``).
* The emitted booking is the newest block with an **empty** Release Date (the
  person is on the in-custody roster). A detail page with no open booking, or
  whose open booking number does not match ``YYYY-NNNNNNNN``, is dropped.

The previous per-county parsers took the first ``h2/h3`` that started with
"Booking", which is the ``Booking History`` section heading, so every row got
the same key (``History``). No name/date/row-id keys are ever synthesized and
bond is the source ``Total Bond Amount`` of the open booking only. A blank or
unparseable Total Bond Amount leaves ``Bond_Amount=""`` (unknown) rather than
inventing $0; a failed detail fetch drops the row.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord

logger = logging.getLogger(__name__)

MAX_PAGES = 20
MAX_DETAILS = 600
REQUEST_PAUSE_S = 0.25

BOOKING_RE = re.compile(r"^\d{4}-\d{8}$")
_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
_DETAIL_HREF_RE = re.compile(r"/Inmate/Detail/-?\d+", re.I)
_DATETIME_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)$", re.I)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def parse_listing_links(html: str, base_url: str) -> List[Tuple[str, str]]:
    """Return ``(display_name, absolute_detail_url)`` pairs from a roster page."""
    soup = BeautifulSoup(html, "html.parser")
    out: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if not _DETAIL_HREF_RE.search(href):
            continue
        name = a_tag.get_text(" ", strip=True)
        if not name or name in ("Back to Search", "Search"):
            continue
        full = href if href.startswith("http") else urljoin(base_url, href)
        if full in seen:
            continue
        seen.add(full)
        out.append((name, full))
    return out


def _field(container, css_class: str) -> str:
    li = container.find("li", class_=css_class)
    if li is None:
        return ""
    span = li.find("span")
    return span.get_text(" ", strip=True) if span else ""


def parse_detail_html(html: str) -> Dict[str, object]:
    """Extract subject fields and every booking block from a detail page."""
    soup = BeautifulSoup(html, "html.parser")
    subject: Dict[str, str] = {}
    for css, key in (
        ("Name", "name"),
        ("DateOfBirth", "dob"),
        ("Gender", "sex"),
        ("Race", "race"),
        ("Address", "address"),
    ):
        subject[key] = _field(soup, css)

    bookings: List[Dict[str, str]] = []
    for block in soup.find_all("div", class_="Booking"):
        h3 = block.find("h3")
        if h3 is None:
            continue
        label = h3.find("label")
        span = h3.find("span")
        if label is None or span is None:
            continue
        if label.get_text(" ", strip=True).rstrip(":").lower() != "booking":
            continue
        number = span.get_text(" ", strip=True)
        charges: List[str] = []
        grid = block.find("div", class_="BookingCharges")
        if grid is not None:
            for td in grid.find_all("td", class_="ChargeDescription"):
                desc = td.get_text(" ", strip=True)
                if desc and desc.lower() != "no data":
                    charges.append(desc)
        bond = ""
        money = _MONEY_RE.search(_field(block, "TotalBondAmount"))
        if money:
            bond = money.group(1).replace(",", "")
        bookings.append(
            {
                "booking": number,
                "booking_date": _field(block, "BookingDate"),
                "release_date": _field(block, "ReleaseDate"),
                "bond": bond,
                "agency": _field(block, "BookingOrigin"),
                "charges": " | ".join(charges),
            }
        )
    return {"subject": subject, "bookings": bookings}


def current_booking(bookings: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Newest open booking (empty Release Date) with a source-format number."""
    for b in bookings:
        if b.get("release_date"):
            continue
        if BOOKING_RE.fullmatch(b.get("booking", "")):
            return b
        return None  # open booking exists but number is malformed — drop, never guess
    return None


def _split_name(name: str) -> Tuple[str, str, str]:
    name = " ".join((name or "").split())
    if "," in name:
        last, rest = [p.strip() for p in name.split(",", 1)]
        parts = rest.split()
        return (parts[0] if parts else ""), " ".join(parts[1:]), last
    parts = name.split()
    if len(parts) < 2:
        return (parts[0] if parts else ""), "", ""
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def detail_to_record(
    html: str,
    *,
    county: str,
    facility: str,
    detail_url: str,
    fallback_name: str = "",
) -> Optional[ArrestRecord]:
    parsed = parse_detail_html(html)
    subject = parsed["subject"]  # type: ignore[assignment]
    booking = current_booking(parsed["bookings"])  # type: ignore[arg-type]
    if booking is None:
        return None
    name = (subject.get("name") or fallback_name or "").strip()  # type: ignore[union-attr]
    if not name:
        return None
    first, middle, last = _split_name(name)

    booking_date = booking.get("booking_date", "")
    booking_time = ""
    m = _DATETIME_RE.match(booking_date.strip())
    if m:
        booking_date, booking_time = m.group(1), m.group(2)

    sex_raw = (subject.get("sex") or "").strip().lower()  # type: ignore[union-attr]
    sex = "M" if sex_raw.startswith("m") else ("F" if sex_raw.startswith("f") else "")

    return ArrestRecord(
        County=county,
        State="FL",
        Facility=facility,
        Full_Name=name,
        First_Name=first,
        Middle_Name=middle,
        Last_Name=last,
        DOB=subject.get("dob", ""),  # type: ignore[union-attr]
        Sex=sex,
        Race=subject.get("race", ""),  # type: ignore[union-attr]
        Address=subject.get("address", ""),  # type: ignore[union-attr]
        Booking_Number=booking["booking"],
        Booking_Date=booking_date,
        Booking_Time=booking_time,
        Charges=booking.get("charges", ""),
        # "" = source Total Bond Amount blank/unparseable (unknown); "0.00" only when published.
        Bond_Amount=booking.get("bond", ""),
        Agency=booking.get("agency", ""),
        Status="In Custody",
        Detail_URL=detail_url,
        LastCheckedMode="INITIAL",
    )


def scrape_newworld_roster(
    *,
    county: str,
    facility: str,
    portal_url: str,
    max_pages: int = MAX_PAGES,
    max_details: int = MAX_DETAILS,
    session: Optional[requests.Session] = None,
) -> List[ArrestRecord]:
    """Walk the InCustody roster and emit one record per open source booking."""
    start = time.time()
    sess = session or requests.Session()
    sess.headers.update(HEADERS)
    sess.headers["Referer"] = portal_url
    base = portal_url.rstrip("/") + "/"

    links: List[Tuple[str, str]] = []
    seen_urls: Set[str] = set()
    for page in range(1, max_pages + 1):
        params = {"InCustody": "True"}
        if page > 1:
            params["Page"] = str(page)
        resp = sess.get(portal_url, params=params, timeout=30)
        if resp.status_code != 200:
            if page == 1:
                raise RuntimeError(f"{county}: NewWorld roster HTTP {resp.status_code}")
            logger.warning("%s: NewWorld page %d HTTP %s", county, page, resp.status_code)
            break
        new = 0
        for name, url in parse_listing_links(resp.text, base):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            links.append((name, url))
            new += 1
        if page == 1 and new == 0:
            raise RuntimeError(f"{county}: NewWorld roster has no Inmate/Detail links (layout drift)")
        if new == 0:
            break  # past the last page (portal repeats or returns no rows)
        time.sleep(REQUEST_PAUSE_S)

    records: List[ArrestRecord] = []
    emitted: Set[str] = set()
    dropped = 0
    for name, url in links[:max_details]:
        try:
            resp = sess.get(url, timeout=25)
        except requests.RequestException as exc:
            logger.debug("%s: detail fetch failed (%s): %s", county, url, exc)
            continue
        if resp.status_code != 200:
            continue
        rec = detail_to_record(resp.text, county=county, facility=facility, detail_url=url, fallback_name=name)
        if rec is None or rec.Booking_Number in emitted:
            dropped += 1
        else:
            emitted.add(rec.Booking_Number)
            records.append(rec)
        time.sleep(REQUEST_PAUSE_S)

    if links and not records:
        raise RuntimeError(f"{county}: {len(links)} roster rows but no open source Booking YYYY-NNNNNNNN (parse drift)")
    logger.info(
        "%s: NewWorld %d source bookings from %d roster rows (%d dropped) in %.1fs",
        county, len(records), len(links), dropped, time.time() - start,
    )
    return records
