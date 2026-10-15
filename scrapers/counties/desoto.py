"""
DeSoto County (FL) jail roster — DCN DevExpress grid over plain, honest HTTPS.

Source: https://jail.desotosheriff.org/DCN/inmates (DeSoto County Sheriff's Office)

FAIL CLOSED since 2026-10-15 (docs/recon/FL_DESOTO_FAIL_CLOSED_2026-10-15.md):
``scrape()`` returns ``[]`` before any request; nothing is written.

Source-contract status (docs/recon/FL_HOME_COUNTIES_SOURCE_CONTRACT_2026-10-08.md):
  * Roster: Full Name / Age / Race / Sex / Admit Date, each row linking to
    ``inmate-details?id=…&bid=…``. Page 1 is server-rendered; pages 2..N come from
    the grid's own DevExpress pager callback (``scrapers.dcn_base``).
  * No printed booking number. CoS (2026-10-08): ``bid`` is the jail site's
    internal record id, not a source booking number, so DeSoto stays
    ``recon_only`` / Health ``unverified``; CoS then chose fail_closed
    (2026-10-15). The kept fetch path keys rows on ``bid`` exactly as before and
    drops rows without one (never a synthesized name/date hash).

Honest access and no invented values (2026-10-15):
  * Replaces the DrissionPage browser (stealth Chrome UA,
    ``--ignore-certificate-errors``) with ``scrapers.plain_http``: honest
    User-Agent, certificate verified. 401/403/429 or a challenge raises
    ``egress_block:``, a real certificate failure ``tls_cert_invalid:``; nothing
    works around a block.
  * Bond: a charge counts only when the source shows a dollar amount (a
    published 0 is kept); any blank / NO BOND / text charge, or no charges,
    makes the booking bond ``""``.
  * Status: "Released" only when the detail page publishes a release date;
    otherwise ``""`` (the source never says "in custody").
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.dcn_base import DCNBaseScraper
from scrapers.plain_http import HONEST_USER_AGENT, request as plain_request
from scrapers.published_bond import charge_bond_amount, total_published_bond

logger = logging.getLogger(__name__)

BASE_URL = "https://jail.desotosheriff.org"
INMATES_URL = f"{BASE_URL}/DCN/inmates"
FACILITY = "DeSoto County Jail"

_NO_RELEASE = {"", "N/A", "NA", "NONE", "-"}


class _HonestSession(requests.Session):
    """requests.Session whose every call goes through plain_http (verify on, typed blocks)."""

    def __init__(self, county: str):
        super().__init__()
        self._county = county
        self.headers.update({
            "User-Agent": HONEST_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self.verify = True

    def request(self, method, url, **kwargs):  # noqa: D401
        sess = self

        class _Raw:
            def request(self, m, u, **kw):
                return requests.Session.request(sess, m, u, **kw)

        return plain_request(_Raw(), method, url, county=self._county, **kwargs)


def parse_detail(html: str, url: str = INMATES_URL) -> Dict[str, str]:
    """DeSoto detail page -> plain fields. Bond is published-only ("" when unknown)."""
    soup = BeautifulSoup(html or "", "html.parser")
    info: Dict[str, str] = {}

    name_el = soup.find("h3", class_="header-text") or soup.find(id="HeaderText")
    if name_el:
        span = name_el.find("span") if name_el.name == "h3" else None
        info["header_name"] = (span or name_el).get_text(strip=True)

    released = False
    table = soup.find(id="tblDetails")
    for row in (table.find_all("tr") if table else []):
        cells = row.find_all("td")
        if len(cells) != 2:
            continue
        label = cells[0].get_text(strip=True).rstrip(":").lower()
        value = cells[1].get_text(strip=True)
        if not label or not value or "drag a column" in label or "change offset" in label:
            continue
        if "date of birth" in label or label == "dob":
            info["dob"] = value
        elif "sex" in label or "gender" in label:
            info["sex"] = value[:1].upper()
        elif "race" in label:
            info["race"] = value
        elif any(k in label for k in ("booking date", "date in", "admit date")):
            info["admit"] = value
        elif "release date" in label or "date out" in label:
            if value.strip().upper() not in _NO_RELEASE:
                released = True
                info["release_date"] = value
        elif "facility" in label or "housing" in label:
            info["facility"] = value
        elif "height" in label:
            info["height"] = value
        elif "weight" in label:
            info["weight"] = value
        elif "address" in label:
            info["address"] = value

    charges: List[str] = []
    bonds: List[Optional[float]] = []
    bond_type = ""
    for tr in soup.select('[id*="ChargeGrid_DXDataRow"]'):
        cells = tr.find_all("td")
        if not cells:
            continue
        charge = cells[0].get_text(strip=True)
        if not charge or "drag a column" in charge.lower():
            continue
        charges.append(charge)
        bonds.append(charge_bond_amount(cells[5].get_text(strip=True)) if len(cells) > 5 else None)
        if len(cells) > 6 and not bond_type:
            bond_type = cells[6].get_text(strip=True)

    info["charges"] = " | ".join(charges)
    info["bond"] = total_published_bond(bonds)
    info["bond_type"] = bond_type
    info["status"] = "Released" if released else ""

    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if ("photo" in src.lower() or "mugshot" in src.lower()) and not src.startswith("data:"):
            info["mugshot"] = urljoin(BASE_URL + "/", src)
            break
    return info


class DeSotoCountyScraper(DCNBaseScraper):
    """DeSoto County (FL) — DCN roster + detail pages, plain honest HTTPS.

    FAIL CLOSED (CoS 2026-10-15, option 1 on #222): the only row key is the
    site's internal ``bid``, not a source booking number (CoS 2026-10-08).
    ``scrape()`` makes no request and returns nothing until a booking number is
    published and proven. The plain-HTTPS path below is kept in
    ``_fetch_and_parse()``, ready to lift.
    """

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "DeSoto DCN roster publishes no booking number; the only key is the site's "
        "internal bid (CoS 2026-10-08). Refuse emission until a source booking number is proven."
    )

    require_source_bid = True       # never a synthesized name/date key
    paginate_callbacks = True       # pages 2..N via the grid's own pager callback
    max_detail_fetches = 400
    detail_delay_s = 0.5

    @property
    def county(self) -> str:
        return "DeSoto"

    @property
    def state(self) -> str:
        return "FL"

    @property
    def inmates_url(self) -> str:
        return INMATES_URL

    @property
    def facility_name(self) -> str:
        return FACILITY

    def scrape(self) -> List[ArrestRecord]:
        if not self.SOURCE_CONTRACT_VALIDATED:
            logger.warning("DeSoto: SOURCE_CONTRACT_VALIDATED=False — %s", self.SOURCE_CONTRACT_REASON)
            return []
        return self._fetch_and_parse()

    def _fetch_and_parse(self) -> List[ArrestRecord]:
        """Unguarded plain-HTTPS fetch + parse. Only scrape() may call it, once the
        source contract is validated; tests call it directly."""
        session = _HonestSession(self.county)
        resp = session.get(self.inmates_url, timeout=45)   # blocks raise, never swallowed
        resp.raise_for_status()
        origin = self._origin(self.inmates_url)
        roster = self._parse_roster(resp.text, origin)
        if not roster:
            logger.warning("DeSoto: no roster rows with a bid parsed")
            return []
        roster = self._paginate_roster(session, resp.text, origin, roster)
        records: List[ArrestRecord] = []
        budget = self.max_detail_fetches
        for row in roster:
            detail: Dict[str, str] = {}
            if budget > 0 and row.get("detail_url"):
                detail = self._fetch_detail(session, row["detail_url"])
                budget -= 1
                time.sleep(self.detail_delay_s)
            records.append(self._to_record(row, detail))
        logger.info("DeSoto: %d records", len(records))
        return records

    def _fetch_detail(self, session, url: str) -> Dict[str, str]:
        resp = session.get(url, timeout=35)
        resp.raise_for_status()
        return parse_detail(resp.text, url)

    def _to_record(self, row: Dict[str, str], detail: Dict[str, str]) -> ArrestRecord:
        address = detail.get("address") or ""
        city = state = zipcode = ""
        m = re.search(r",\s*(\w[\w\s]*?)\s+([A-Z]{2})\s+(\d{5})", address)
        if m:
            city, state, zipcode = m.group(1).strip(), m.group(2), m.group(3)
        sex = detail.get("sex") or row.get("sex") or ""
        return ArrestRecord(
            County=self.county,
            State="FL",
            Booking_Number=str(row["booking"]),
            Full_Name=row["name"],
            First_Name=row.get("first") or "",
            Middle_Name=row.get("middle") or "",
            Last_Name=row.get("last") or "",
            DOB=detail.get("dob") or "",
            Booking_Date=detail.get("admit") or row.get("admit") or "",
            Status=detail.get("status", ""),
            Release_Date=detail.get("release_date", ""),
            Facility=detail.get("facility") or FACILITY,
            Race=detail.get("race") or row.get("race") or "",
            Sex=sex[:1].upper() if sex else "",
            Age_At_Arrest=str(row.get("age") or ""),
            Height=detail.get("height") or "",
            Weight=detail.get("weight") or "",
            Address=address,
            City=city,
            ZIP=zipcode,
            Mugshot_URL=detail.get("mugshot") or "",
            Charges=detail.get("charges") or "",
            Bond_Amount=detail.get("bond", ""),   # no detail fetched -> unknown
            Bond_Type=detail.get("bond_type") or "",
            Detail_URL=row.get("detail_url") or self.inmates_url,
            LastCheckedMode="INITIAL",
        )
