"""
Detention Center Network (DCN) base scraper.

Shared by multiple NC (and some SC/FL) county jails that host a DevExpress
Material roster at ``{origin}/dcn/inmates`` with detail pages at
``/DCN/inmate-details?id=…&bid=…``.

List view columns (typical): Full Name | Age | Race | Sex | Admit Date
Detail view: DOB, height/weight, address, charge grid with bond amounts.

Notes
-----
* Initial HTML includes up to the configured page size (usually 100 rows).
  DevExpress AJAX pagination/filter callbacks are unreliable from datacenter
  clients — we scrape the server-rendered first page and optionally enrich
  each row via the public detail URL (plain HTTP works).
* Booking key = URL ``bid`` param when present (stable opaque id), else a
  deterministic hash of name + admit date.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class DCNBaseScraper(BaseScraper):
    """Thin-wrapper base: subclasses set ``county``, ``state``, ``inmates_url``."""

    # Cap detail visits so a 100-row roster finishes in a reasonable window.
    max_detail_fetches: int = 120
    detail_delay_s: float = 0.35
    enrich_details: bool = True

    @property
    def county(self) -> str:
        raise NotImplementedError

    @property
    def state(self) -> str:
        return "NC"

    @property
    def inmates_url(self) -> str:
        """Full URL to the DCN inmates roster (…/dcn/inmates)."""
        raise NotImplementedError

    @property
    def facility_name(self) -> str:
        return f"{self.county} County Detention"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)

        try:
            resp = session.get(self.inmates_url, timeout=45, verify=True)
            resp.raise_for_status()
        except Exception as e:
            logger.error("%s DCN roster GET failed: %s", self.county, e)
            return []

        origin = self._origin(self.inmates_url)
        roster = self._parse_roster(resp.text, origin)
        if not roster:
            logger.warning("%s DCN: no roster rows parsed", self.county)
            return []

        logger.info("%s DCN: %d list rows", self.county, len(roster))

        records: List[ArrestRecord] = []
        detail_budget = self.max_detail_fetches if self.enrich_details else 0

        for idx, row in enumerate(roster):
            detail: Dict[str, str] = {}
            if detail_budget > 0 and row.get("detail_url"):
                try:
                    detail = self._fetch_detail(session, row["detail_url"])
                    detail_budget -= 1
                    time.sleep(self.detail_delay_s)
                except Exception as e:
                    logger.debug("%s detail fail (%s): %s", self.county, row.get("name"), e)

            records.append(self._to_record(row, detail))
            if (idx + 1) % 25 == 0:
                logger.info(
                    "%s DCN progress: %d/%d",
                    self.county,
                    idx + 1,
                    len(roster),
                )

        logger.info(
            "%s DCN: %d records in %.1fs",
            self.county,
            len(records),
            time.time() - start,
        )
        return records

    # ── parsing ──────────────────────────────────────────────────────────

    def _parse_roster(self, html: str, origin: str) -> List[Dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[Dict[str, str]] = []
        seen: set = set()

        for tr in soup.select("tr[id*='DXDataRow']"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if not cells:
                continue
            a = tr.find("a", href=True)
            name = re.sub(r"\s+", " ", (a.get_text(strip=True) if a else cells[0])).strip()
            if not name or len(name) < 2:
                continue

            href = a["href"] if a else ""
            detail_url = urljoin(origin + "/", href.lstrip("/")) if href else ""
            bid = self._bid_from_url(detail_url) if detail_url else ""

            age = cells[1] if len(cells) > 1 else ""
            race = cells[2] if len(cells) > 2 else ""
            sex = cells[3] if len(cells) > 3 else ""
            admit = cells[4] if len(cells) > 4 else ""

            booking = bid or self._synthetic_booking(name, admit, age)
            if booking in seen:
                continue
            seen.add(booking)

            last, first, middle = self._split_name(name)
            out.append({
                "name": name,
                "first": first,
                "middle": middle,
                "last": last,
                "age": age,
                "race": race,
                "sex": (sex or "")[:1].upper(),
                "admit": admit,
                "detail_url": detail_url,
                "booking": booking,
            })
        return out

    def _fetch_detail(self, session: requests.Session, url: str) -> Dict[str, str]:
        resp = session.get(url, timeout=35, verify=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        info: Dict[str, str] = {}

        # Header name (often "FIRST MIDDLE LAST Jr")
        for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
            text = tag.get_text(" ", strip=True)
            if text and len(text) > 2 and "Detention" not in text and "Details" not in text:
                info["header_name"] = text
                break

        # Label/value pairs in detail tables
        for tr in soup.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) != 2:
                continue
            label = cells[0].get_text(" ", strip=True).rstrip(":").lower()
            value = cells[1].get_text(" ", strip=True)
            if not label or not value or "drag a column" in label:
                continue
            if "date of birth" in label or label == "dob":
                info["dob"] = value
            elif label in ("age",):
                info["age"] = value
            elif "race" in label:
                info["race"] = value
            elif label in ("sex", "gender"):
                info["sex"] = value[:1].upper()
            elif "height" in label:
                info["height"] = value
            elif "weight" in label:
                info["weight"] = value
            elif "admit date" in label or "booking date" in label or "date in" in label:
                info["admit"] = value
            elif "address" in label:
                info["address"] = value
            elif "facility" in label or "confining" in label:
                info["facility"] = value

        charges: List[str] = []
        total_bond = 0.0
        bond_type = ""
        for tr in soup.select("tr[id*='ChargeGrid'][id*='DXDataRow'], tr[id*='DXDataRow']"):
            # Prefer ChargeGrid rows when present
            row_id = tr.get("id") or ""
            if "ChargeGrid" not in row_id and soup.select("tr[id*='ChargeGrid_DXDataRow']"):
                continue
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if not cells:
                continue
            charge = cells[0]
            if not charge or "drag a column" in charge.lower():
                continue
            if charge not in charges:
                charges.append(charge)
            # Bond amount usually near end of charge row
            for cell in cells:
                if "$" in cell:
                    cleaned = re.sub(r"[^\d.]", "", cell.replace(",", ""))
                    try:
                        total_bond += float(cleaned)
                    except ValueError:
                        pass
                elif not bond_type and any(
                    k in cell.upper() for k in ("SECURED", "UNSECURED", "CASH", "BOND", "ROR")
                ):
                    if "INCLUDED" not in cell.upper():
                        bond_type = cell

        if charges:
            info["charges"] = " | ".join(charges)
        if total_bond > 0:
            info["bond"] = f"{total_bond:.2f}"
        if bond_type:
            info["bond_type"] = bond_type

        # Mugshot
        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if any(k in src.lower() for k in ("photo", "mug", "inmate")) and not src.startswith("data:"):
                info["mugshot"] = urljoin(url, src)
                break

        return info

    def _to_record(self, row: Dict[str, str], detail: Dict[str, str]) -> ArrestRecord:
        name = row["name"]
        # Prefer list "LAST, FIRST" form for consistency
        first = row.get("first") or ""
        middle = row.get("middle") or ""
        last = row.get("last") or ""

        charges = detail.get("charges") or "Unknown"
        bond = detail.get("bond") or "0"
        bond_type = detail.get("bond_type") or ""
        dob = detail.get("dob") or ""
        height = detail.get("height") or ""
        weight = detail.get("weight") or ""
        address = detail.get("address") or ""
        city = state_from = zipcode = ""
        if address:
            m = re.search(r",\s*([^,]+?)\s+([A-Z]{2})\s+(\d{5})", address)
            if m:
                city, state_from, zipcode = m.group(1).strip(), m.group(2), m.group(3)

        sex = detail.get("sex") or row.get("sex") or ""
        race = detail.get("race") or row.get("race") or ""
        age = detail.get("age") or row.get("age") or ""
        admit = detail.get("admit") or row.get("admit") or ""
        facility = detail.get("facility") or self.facility_name

        return ArrestRecord(
            County=self.county,
            State=self.state,
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=str(row["booking"]),
            Booking_Date=admit,
            Sex=sex[:1].upper() if sex else "",
            Race=race,
            Age_At_Arrest=str(age) if age else "",
            Height=height,
            Weight=weight,
            DOB=dob,
            Charges=charges,
            Bond_Amount=re.sub(r"[^\d.]", "", str(bond)) or "0",
            Bond_Type=bond_type,
            Status="In Custody",
            Detail_URL=row.get("detail_url") or self.inmates_url,
            Facility=facility,
            Address=address,
            City=city,
            ZIP=zipcode,
            Mugshot_URL=detail.get("mugshot") or "",
        )

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _origin(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    @staticmethod
    def _bid_from_url(url: str) -> str:
        qs = parse_qs(urlparse(url).query)
        bid = qs.get("bid", [""])[0]
        if not bid:
            return ""
        # Normalize multi-encoded bid to a stable token
        raw = unquote(unquote(bid))
        # Keep URL-safe compact form
        return re.sub(r"[^A-Za-z0-9_\-+=]", "", raw)[:64] or ""

    @staticmethod
    def _synthetic_booking(name: str, admit: str, age: str) -> str:
        key = f"{name}|{admit}|{age}".upper()
        return "DCN_" + hashlib.md5(key.encode()).hexdigest()[:12]

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str, str]:
        """Parse 'LAST, FIRST MIDDLE' → (last, first, middle)."""
        name = re.sub(r"\s+", " ", name).strip()
        if "," in name:
            last, rest = name.split(",", 1)
            parts = rest.strip().split()
            first = parts[0] if parts else ""
            middle = " ".join(parts[1:]) if len(parts) > 1 else ""
            return last.strip(), first, middle
        parts = name.split()
        if len(parts) >= 2:
            return parts[-1], parts[0], " ".join(parts[1:-1])
        return name, "", ""
