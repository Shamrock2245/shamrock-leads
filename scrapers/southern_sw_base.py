"""
Base scraper for Southern Software Citizen Connect.

Current Citizen Connect flow (2026):
  1. GET  /bookingsearch/index.php?AgencyID={agency_id}
  2. Read hidden JMSAgencyID from #formcurrentconfinementsonload
  3. POST /bookingsearch/fetchesforajax/fetch_current_confinements.php
     with JMSAgencyID (+ search/agency/sort)

Returns HTML booking-card fragments with name, booked date, bond, charges.

2026-10-15 honest identity and published-only fields:
  * Every request goes through scrapers.plain_http (honest User-Agent,
    certificate verified, no Chrome UA). 401/403/429, a challenge page, a
    dropped connection or a bad certificate raises (egress_block: /
    tls_cert_invalid:) and is never swallowed; other errors propagate (the run
    fails visibly instead of returning []).
  * Bond_Amount: the card's published "Bond Total:" (a published 0 is kept);
    else the sum of per-charge "Bond:" amounts only when every charge item
    publishes one; otherwise "" (never "0").
  * Charges: published charge-item lines only; "" when none (never "Unknown",
    no free-text line heuristic).
  * Status: "In Custody" only for cards from the source's own "Current
    Confinements" view (fetch_current_confinements; the portal lists it beside
    "Admits/Releases by Date Range"), recorded as
    extra_data.status_source = "published_current_confinements_view". Cards
    from the last-7-days admits fallback get Status "" (scope not custody-only).
  * Booking_Number: source BookingID only (unchanged).
  See docs/recon/SSW_CITIZEN_CONNECT_HONEST_2026-10-15.md.
"""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.plain_http import request as plain_request
from scrapers.published_bond import charge_bond_amount, total_published_bond
from scrapers.scraper_resilience import EgressBlocked
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

CC_BASE = "https://cc.southernsoftware.com/bookingsearch"
STATUS_SOURCE_CURRENT = "published_current_confinements_view"
CURRENT_ENDPOINT = "fetch_current_confinements.php"
_BLOCKS = (EgressBlocked, SourceTLSInvalid)


class _HonestSession(requests.Session):
    """requests.Session whose every call goes through plain_http (honest UA, verify on, typed blocks)."""

    def __init__(self, county: str = ""):
        super().__init__()
        self.headers.update({"User-Agent": HONEST_USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        self.verify = True
        self._county = county

    def request(self, method, url, **kwargs):  # noqa: D401
        sess = self

        class _Raw:
            def request(self, m, u, **kw):
                return requests.Session.request(sess, m, u, **kw)

        return plain_request(_Raw(), method, url, county=self._county or "SouthernSW", **kwargs)


def published_bond_amount(card, text: str) -> str:
    """Published Bond Total (0 kept); else per-charge Bond: sum only if every charge has one; else ""."""
    m = re.search(r"Bond Total:\s*(\$?\s*[\d,]+(?:\.\d+)?)", text, re.I)
    if m:
        total = charge_bond_amount(m.group(1))
        if total is not None:
            return total_published_bond([total])
    items = card.find_all(class_=re.compile(r"charge-item|charge-details", re.I))
    if not items:
        return ""
    amounts = []
    for item in items:
        bm = re.search(r"Bond:\s*(\$?\s*[\d,]+(?:\.\d+)?)", item.get_text(" ", strip=True), re.I)
        amounts.append(charge_bond_amount(bm.group(1)) if bm else None)
    return total_published_bond(amounts)


class SouthernSWBaseScraper(BaseScraper):
    """Subclasses provide ``county`` and ``agency_id`` (Citizen Connect AgencyID)."""

    @property
    def county(self) -> str:
        raise NotImplementedError("Subclasses must define county name")

    @property
    def agency_id(self) -> str:
        raise NotImplementedError("Subclasses must define AgencyID (e.g., 'HarnettCoNC')")

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        agency = self.agency_id
        index_url = f"{CC_BASE}/index.php?AgencyID={agency}"
        logger.info(f"📥 Southern Software roster for {self.county} ({agency})")

        session = _HonestSession(self.county)

        resp = session.get(index_url, timeout=25)
        jms = ""
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            jms = self._extract_jms_agency_id(soup)
        else:
            logger.warning(
                "%s: index HTTP %s — continuing with agency_id as JMS fallback",
                self.county,
                resp.status_code,
            )
        if not jms:
            logger.warning(f"{self.county}: no JMSAgencyID on index; trying agency_id alone")
            jms = agency

        # Paginate current confinements via IDX (20 cards/page)
        records: List[ArrestRecord] = []
        seen: set = set()
        for idx in range(1, 80):  # hard cap ~1600 inmates
            html = self._fetch_endpoint(
                session,
                CURRENT_ENDPOINT,
                jms,
                index_url,
                idx=idx,
            )
            if not html:
                break
            page = self._parse_booking_cards(html, current_view=True)
            if not page:
                break
            new = 0
            for rec in page:
                key = f"{rec.Full_Name}|{rec.Booking_Date}|{rec.Booking_Number}"
                if key in seen:
                    continue
                seen.add(key)
                records.append(rec)
                new += 1
            if new == 0:
                break
            time.sleep(0.2)

        if not records:
            # Fallback: last 7 days admits (single page)
            html = self._fetch_endpoint(
                session, "fetch_last7days.php", jms, index_url, idx=1
            )
            records = self._parse_booking_cards(html) if html else []

        logger.info(
            f"✅ {self.county}: Southern SW {len(records)} records "
            f"in {time.time() - start:.1f}s"
        )
        return records

    @staticmethod
    def _extract_jms_agency_id(soup: BeautifulSoup) -> str:
        tag = soup.find("input", {"name": "JMSAgencyID"}) or soup.find(
            "input", {"id": "JMSAgencyID"}
        )
        if tag and tag.get("value"):
            return tag["value"].strip()
        # Fallback: any NC/SC/GA ORI pattern in page
        m = re.search(r'name="JMSAgencyID"[^>]*value="([^"]+)"', str(soup))
        return m.group(1) if m else ""

    def _fetch_endpoint(
        self,
        session: requests.Session,
        endpoint: str,
        jms: str,
        referer: str,
        idx: int = 1,
    ) -> str:
        url = f"{CC_BASE}/fetchesforajax/{endpoint}"
        data = {
            "JMSAgencyID": jms,
            "search": "",
            "agency": "",
            "sort": "name",
            "IDX": str(idx),
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
            "Origin": "https://cc.southernsoftware.com",
            "Accept": "text/html, */*; q=0.01",
        }
        resp = session.post(url, data=data, headers=headers, timeout=45)
        if resp.status_code != 200:
            logger.warning(f"{self.county}: {endpoint} HTTP {resp.status_code}")
            return ""
        return resp.text or ""

    def _parse_booking_cards(self, html: str, current_view: bool = False) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_=re.compile(r"booking-card", re.I))
        if not cards:
            # Broader card fallback
            cards = [
                c
                for c in soup.find_all("div", class_=re.compile(r"\bcard\b", re.I))
                if c.find(["h4", "h5"]) and (
                    "Booked" in c.get_text() or "Bond" in c.get_text() or "Charge" in c.get_text()
                )
            ]

        records: List[ArrestRecord] = []
        for card in cards:
            try:
                rec = self._parse_one_card(card, current_view=current_view)
                if rec:
                    records.append(rec)
            except Exception as e:
                logger.debug("%s: card parse error (%s)", self.county, type(e).__name__)
        return records

    def _parse_one_card(self, card, current_view: bool = False) -> Optional[ArrestRecord]:
        # Name is typically first h5 in booking-header
        name_el = card.find(["h5", "h4", "h3"])
        if not name_el:
            return None
        name = name_el.get_text(" ", strip=True)
        if not name or name.lower().startswith("total inmates"):
            return None

        text = card.get_text("\n", strip=True)

        def field(pattern: str) -> str:
            m = re.search(pattern, text, re.I | re.M)
            return m.group(1).strip() if m else ""

        booked = field(r"Booked:[ \t]*([^\n]+)")
        if not booked:
            booked = field(r"Booked:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})")
        arrest_dt = field(r"Arrest Date/Time:\s*([^\n]+)")
        agency = field(r"Arresting Agency:\s*([^\n]+)")
        if agency.lower().startswith(("arrest date", "booked", "bond", "demographics", "charges")):
            agency = ""
        demo = field(r"Demographics:\s*([^\n]+)")

        # Charges: lines under Charges section / charge-item blocks
        charges: List[str] = []
        for item in card.find_all(class_=re.compile(r"charge-item|charge-details", re.I)):
            t = item.get_text(" ", strip=True)
            # strip leading index numbers
            t = re.sub(r"^\d+\s*", "", t).strip()
            if t and len(t) > 2 and not t.lower().startswith("docket"):
                # Prefer description before bond/docket noise
                t = re.split(r"\bBond:|\bDocket:", t)[0].strip()
                if t:
                    charges.append(t)
        age = race = sex = ""
        if demo:
            # e.g. "47 years / W / F"
            m = re.match(
                r"(\d+)\s*years?\s*/\s*([A-Za-z]+)\s*/\s*([A-Za-z]+)", demo
            )
            if m:
                age, race, sex = m.group(1), m.group(2), m.group(3)

        first = last = middle = ""
        if "," in name:
            last, rest = [p.strip() for p in name.split(",", 1)]
            parts = rest.split()
            first = parts[0] if parts else ""
            middle = " ".join(parts[1:]) if len(parts) > 1 else ""
        else:
            parts = name.split()
            if len(parts) >= 2:
                first = parts[0]
                last = parts[-1]
                middle = " ".join(parts[1:-1]) if len(parts) > 2 else ""
            else:
                last = name

        booking_num = self._source_booking_number(card, text)
        # Never synthesize an arrest identity from a person's name, age, or date.
        # A Citizen Connect record is emitted only when the public card supplies
        # complete identity, a source-issued booking/inmate identifier, and Booked date.
        if not first or not last or not booking_num or not booked:
            return None
        bond = published_bond_amount(card, text)
        status, status_source = ("In Custody", STATUS_SOURCE_CURRENT) if current_view else ("", "")
        extra = {"booking_key_origin": "source-issued Citizen Connect booking/inmate ID"}
        if status_source:
            extra["status_source"] = status_source

        return ArrestRecord(
            County=self.county,
            State=self.state or "FL",
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=booking_num,
            Booking_Date=booked,
            Arrest_Date=arrest_dt.split()[0] if arrest_dt else "",
            Age_At_Arrest=age,
            Race=race,
            Sex=(sex or "")[:1].upper(),
            Agency=agency,
            Charges=" | ".join(charges),
            Bond_Amount=bond,
            Status=status,
            Detail_URL=f"{CC_BASE}/index.php?AgencyID={self.agency_id}",
            extra_data=extra,
        )


    @staticmethod
    def _source_booking_number(card, text: str) -> str:
        """Return only a public source-issued booking/inmate identifier.

        Citizen Connect deployments vary: some render a labelled identifier,
        some place it in a detail-link query parameter, and some expose it as
        a data attribute. Do not fall back to person-derived surrogate keys.
        """
        for pattern in (
            r"(?:Booking|Inmate)\s*(?:#|Number|ID)\s*:\s*([A-Za-z0-9-]+)",
            r"(?:Booking|Inmate)\s+Number\s*:\s*([A-Za-z0-9-]+)",
        ):
            match = re.search(pattern, text, flags=re.I)
            if match:
                return match.group(1).strip()

        for node in card.find_all(True):
            for attr_name, attr_value in node.attrs.items():
                normalized = attr_name.lower().replace("_", "-")
                if normalized in {
                    "data-booking-id",
                    "data-bookingid",
                    "data-booking-number",
                    "data-bookingnumber",
                    "data-inmate-id",
                    "data-inmateid",
                    "data-inmate-number",
                    "data-inmatenumber",
                } and attr_value:
                    return str(attr_value).strip()

        for anchor in card.find_all("a", href=True):
            href = anchor.get("href", "")
            match = re.search(
                r"[?&](?:bookingid|booking_id|bookingnumber|booking_number|inmateid|inmate_id)=([^&#]+)",
                href,
                flags=re.I,
            )
            if match:
                return match.group(1).strip()

        # Citizen Connect mugshot debug comments: BookingID=10058420
        html = str(card)
        match = re.search(r"BookingID\s*=\s*([A-Za-z0-9-]+)", html, flags=re.I)
        if match:
            return match.group(1).strip()
        return ""


# Alias used by some county modules
SouthernSoftwareBaseScraper = SouthernSWBaseScraper
