"""
Gaston County (NC) Arrest Scraper — New World InmateInquiry (plain HTTPS).

Portal (official, linked from the Gaston County Sheriff's Office):
  https://tepsweb.cityofgastonia.com/NewWorld.InmateInquiry/GastonCounty

Source contract (verified 2026-09-25 from datacenter egress, plain requests):

* The public search form is a GET with ``BookingFromDate`` / ``BookingToDate``
  (ISO ``YYYY-MM-DD``) and ``InCustody=True``. The listing (100 rows/page,
  ``Page=N`` links) shows Name / Subject Number / In Custody / …, but **no
  booking number**. Subject Number is a person id, not a booking key.
* Each listing row links ``/Inmate/Detail/<id>``; the detail page carries a
  ``#BookingHistory`` block with one ``div.Booking`` per booking whose heading
  is ``Booking <YYYY-NNNNNNNN>`` plus Booking Date, Release Date, total bond
  and a charges grid. That heading value is the source booking number and is
  the only key emitted.

So each run lists in-custody people booked in the last ``LOOKBACK_DAYS`` days
and fetches their detail pages (bounded by ``MAX_DETAILS``). Bookings without a
source booking number, or already released, are skipped — no keys are ever
synthesized. No proxy, stealth session, or TLS impersonation.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.scraper_resilience import ParseDriftError

logger = logging.getLogger(__name__)

PORTAL_URL = "https://tepsweb.cityofgastonia.com/NewWorld.InmateInquiry/GastonCounty"
BOOKING_RE = re.compile(r"^\d{4}-\d{6,10}$")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


def parse_listing(html: str, base_url: str = PORTAL_URL) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    """Return ([(listing_name, detail_url)], next_page_url) from a listing page."""
    soup = BeautifulSoup(html, "html.parser")
    links: List[Tuple[str, str]] = []
    seen = set()
    for a in soup.select("td.Name a[href*='/Inmate/Detail/'], a[href*='/Inmate/Detail/']"):
        href = a.get("href") or ""
        url = urljoin(base_url + "/", href)
        name = _text(a)
        if url in seen or not name:
            continue
        seen.add(url)
        links.append((name, url))
    next_url = None
    for a in soup.find_all("a", href=True):
        if _text(a).lower() == "next" and "Page=" in a["href"]:
            next_url = urljoin(base_url + "/", a["href"])
            break
    return links, next_url


def _money(raw: str) -> float:
    cleaned = re.sub(r"[^\d.]", "", raw or "")
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def parse_detail(html: str) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """Return (person_fields, bookings) from a New World detail page."""
    soup = BeautifulSoup(html, "html.parser")
    person: Dict[str, str] = {}
    for li in soup.select("li"):
        if li.find_parent(class_="Booking"):
            continue
        cls = (li.get("class") or [""])[0]
        span = li.find("span")
        if cls and span is not None:
            person[cls] = _text(span)

    bookings: List[Dict[str, str]] = []
    for blk in soup.select("#BookingHistory div.Booking"):
        heading = blk.select_one("#BookingNumberHeading") or blk.find("h3")
        number = _text(heading).replace("Booking", "").strip()
        fields: Dict[str, str] = {}
        for li in blk.select(".BookingData li"):
            cls = (li.get("class") or [""])[0]
            span = li.find("span")
            if cls and span is not None:
                fields[cls] = _text(span)
        charges: List[str] = []
        dockets: List[str] = []
        for tr in blk.select(".BookingCharges tbody tr"):
            desc = _text(tr.select_one("td.ChargeDescription"))
            if desc and desc not in charges:
                charges.append(desc)
            docket = _text(tr.select_one("td.DocketNumber"))
            if docket and docket not in dockets:
                dockets.append(docket)
        bond_types: List[str] = []
        bond_total = 0.0
        for tr in blk.select(".BookingBonds tbody tr"):
            btype = _text(tr.select_one("td.BondType"))
            amount = _text(tr.select_one("td.BondAmount"))
            if btype and btype not in bond_types:
                bond_types.append(btype)
            bond_total += _money(amount)
        total = _money(fields.get("TotalBondAmount", "")) or bond_total
        booking_dt = fields.get("BookingDate", "")
        bdate, _, btime = booking_dt.partition(" ")
        bookings.append({
            "booking_number": number,
            "booking_date": bdate,
            "booking_time": btime.strip(),
            "release_date": fields.get("ReleaseDate", ""),
            "facility": fields.get("HousingFacility", ""),
            "origin": fields.get("BookingOrigin", ""),
            "bond": f"{total:.2f}" if total else "0",
            "bond_type": " | ".join(bond_types),
            "charges": " | ".join(charges),
            "dockets": " | ".join(dockets[:5]),
        })
    return person, bookings


def _split_name(name: str) -> Tuple[str, str, str]:
    name = re.sub(r"\s+", " ", name or "").strip()
    if "," in name:
        last, rest = name.split(",", 1)
        parts = rest.split()
        return last.strip(), (parts[0] if parts else ""), " ".join(parts[1:])
    parts = name.split()
    if len(parts) >= 2:
        return parts[-1], parts[0], " ".join(parts[1:-1])
    return name, "", ""


class GastonScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "New World InmateInquiry GastonCounty (plain HTTPS); booking-date window "
        "listing + /Inmate/Detail Booking heading = source booking number YYYY-NNNNNNNN."
    )

    LOOKBACK_DAYS = 3
    MAX_PAGES = 10
    MAX_DETAILS = 250
    DETAIL_DELAY_S = 0.3

    @property
    def county(self) -> str:
        return "Gaston"

    @property
    def state(self) -> str:
        return "NC"

    def _window(self) -> Tuple[str, str]:
        today = datetime.now(ZoneInfo("America/New_York")).date()
        return (today - timedelta(days=self.LOOKBACK_DAYS)).isoformat(), today.isoformat()

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)
        date_from, date_to = self._window()
        params = {
            "Name": "",
            "SubjectNumber": "",
            "BookingNumber": "",
            "InCustody": "True",
            "BookingFromDate": date_from,
            "BookingToDate": date_to,
            "Facility": "",
        }
        resp = session.get(PORTAL_URL, params=params, timeout=40)
        resp.raise_for_status()
        if "BookingFromDate" not in resp.text or "Inmate Search" not in resp.text:
            raise ParseDriftError("Gaston: New World search page contract changed")

        people: List[Tuple[str, str]] = []
        page_html = resp.text
        for _ in range(self.MAX_PAGES):
            links, next_url = parse_listing(page_html)
            people.extend(links)
            if not next_url:
                break
            time.sleep(self.DETAIL_DELAY_S)
            nxt = session.get(next_url, timeout=40)
            nxt.raise_for_status()
            page_html = nxt.text

        records: List[ArrestRecord] = []
        seen: set = set()
        for listing_name, url in people[: self.MAX_DETAILS]:
            try:
                det = session.get(url, timeout=30)
                det.raise_for_status()
            except Exception as exc:  # one bad detail must not sink the run
                logger.debug("Gaston detail fetch failed (%s)", type(exc).__name__)
                continue
            person, bookings = parse_detail(det.text)
            for bk in bookings:
                number = bk["booking_number"]
                if not BOOKING_RE.match(number) or bk["release_date"] or number in seen:
                    continue
                seen.add(number)
                records.append(self._to_record(listing_name, person, bk, url))
            time.sleep(self.DETAIL_DELAY_S)

        if people and not records:
            raise ParseDriftError(
                f"Gaston: {len(people)} listing rows but no detail page yielded a source booking number"
            )
        logger.info(
            "Gaston: %d bookings from %d in-custody people (window %s..%s) in %.1fs",
            len(records), len(people), date_from, date_to, time.time() - start,
        )
        return records

    def _to_record(self, listing_name: str, person: Dict[str, str], bk: Dict[str, str], url: str) -> ArrestRecord:
        name = person.get("Name") or listing_name
        last, first, middle = _split_name(name)
        return ArrestRecord(
            County=self.county,
            State=self.state,
            Booking_Number=bk["booking_number"],
            Person_ID=person.get("SubjectNumber", ""),
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            DOB=person.get("DateOfBirth", ""),
            Age_At_Arrest=person.get("Age", ""),
            Sex=(person.get("Gender", "") or "")[:1].upper(),
            Race=person.get("Race", ""),
            Height=person.get("Height", ""),
            Weight=person.get("Weight", ""),
            Booking_Date=bk["booking_date"],
            Booking_Time=bk["booking_time"],
            Arrest_Date=bk["booking_date"],
            Charges=bk["charges"] or "Unknown",
            Case_Number=bk["dockets"],
            Bond_Amount=bk["bond"],
            Bond_Type=bk["bond_type"],
            Status="In Custody",
            Facility=bk["facility"] or "Gaston County Jail",
            Agency=bk["origin"],
            Detail_URL=url,
        )
