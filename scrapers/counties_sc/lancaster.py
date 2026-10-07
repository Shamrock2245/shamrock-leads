"""
Lancaster County (SC) Arrest Scraper — New World InmateInquiry.

Source contract (recon 2026-10-07, docs/recon/SC_LANCASTER_NEWWORLD_2026-10-07.md):
  * Portal: https://inmate.lancastercountysc.net/NewWorld.InmateInquiry/SC0290000
  * Ordinary public HTTPS (InCustody=True roster); Page=2..N via query string.
  * Listing: Name + demographics + /Inmate/Detail/{id} links (id is not Booking_Number).
  * Detail publishes source Booking ``YYYY-########``, Booking Date, Total Bond Amount,
    charge grid (Number / Charge Description / Bond). Per-charge Bond cells may hold
    bond *reference* ids (also ``YYYY-########``) — never treat those as dollar amounts.
  * Booking_Number is the detail ``<label>Booking</label><span>…</span>`` value only.
  * No proxy / stealth / CAPTCHA. Health stays unverified until a write smoke.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://inmate.lancastercountysc.net/NewWorld.InmateInquiry/SC0290000"
ORIGIN = "https://inmate.lancastercountysc.net"
FACILITY = "Lancaster County Detention Center"
MAX_PAGES = 20
MAX_DETAILS = 400
REQUEST_PAUSE_S = 0.25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": PORTAL_URL,
}

# Source-issued New World booking numbers observed on Lancaster detail pages.
_BOOKING_RE = re.compile(r"^\d{4}-\d{8}$")
_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
_DETAIL_HREF_RE = re.compile(r"/Inmate/Detail/-?\d+", re.I)


class LancasterScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "inmate.lancastercountysc.net NewWorld InmateInquiry SC0290000; "
        "ordinary InCustody roster + Inmate/Detail pages; Booking_Number is "
        "source Booking YYYY-########; charges from BookingCharges grid; "
        "Bond_Amount from Total Bond Amount only (never invented; per-charge "
        "Bond cells may be reference ids, not dollars)."
    )

    @property
    def county(self) -> str:
        return "Lancaster"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)

        links: List[Tuple[str, str]] = []
        seen_hrefs: Set[str] = set()

        for page_num in range(1, MAX_PAGES + 1):
            params = {"InCustody": "True"}
            if page_num > 1:
                params["Page"] = str(page_num)
            try:
                resp = session.get(PORTAL_URL, params=params, timeout=30)
            except requests.RequestException as exc:
                logger.warning("Lancaster listing page %s failed: %s", page_num, exc)
                break
            if resp.status_code != 200:
                logger.warning(
                    "Lancaster listing page %s: HTTP %s", page_num, resp.status_code
                )
                break

            page_links = self.parse_listing_links(resp.text, base_url=PORTAL_URL)
            new_on_page = 0
            for name, url in page_links:
                if url in seen_hrefs:
                    continue
                seen_hrefs.add(url)
                links.append((name, url))
                new_on_page += 1
            if new_on_page == 0:
                break
            # NewWorld pages are typically 100 rows; stop when a short page appears.
            if new_on_page < 5:
                break
            time.sleep(REQUEST_PAUSE_S)

        records: List[ArrestRecord] = []
        for name, detail_url in links[:MAX_DETAILS]:
            try:
                resp = session.get(detail_url, timeout=25)
            except requests.RequestException as exc:
                logger.debug("Lancaster detail fetch failed (%s): %s", detail_url, exc)
                continue
            if resp.status_code != 200:
                continue
            rec = self.parse_detail_to_record(resp.text, fallback_name=name, detail_url=detail_url)
            if rec:
                records.append(rec)
            time.sleep(REQUEST_PAUSE_S)

        logger.info(
            "✅ Lancaster: NewWorld scraped %s records in %.1fs",
            len(records),
            time.time() - start,
        )
        return records

    @classmethod
    def parse_listing_links(cls, html: str, base_url: str = PORTAL_URL) -> List[Tuple[str, str]]:
        """Return (display_name, absolute_detail_url) pairs from a roster page."""
        soup = BeautifulSoup(html, "html.parser")
        out: List[Tuple[str, str]] = []
        seen: Set[str] = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if not _DETAIL_HREF_RE.search(href):
                continue
            name = a_tag.get_text(strip=True)
            if not name or name in ("Back to Search", "Search"):
                continue
            full = href if href.startswith("http") else urljoin(base_url + "/", href)
            if full in seen:
                continue
            seen.add(full)
            out.append((name, full))
        return out

    @classmethod
    def parse_detail_html(cls, html: str) -> Dict[str, str]:
        """Extract source fields from an Inmate/Detail page (no network)."""
        soup = BeautifulSoup(html, "html.parser")
        info: Dict[str, str] = {}

        # Booking number: <div class="Booking"><h3><label>Booking</label><span>YYYY-########</span>
        for label in soup.find_all("label"):
            lab = label.get_text(" ", strip=True).strip().rstrip(":").lower()
            span = label.find_next_sibling("span")
            if span is None:
                continue
            val = span.get_text(" ", strip=True)
            if not val:
                continue
            if lab == "booking" and _BOOKING_RE.fullmatch(val):
                info["booking"] = val
            elif lab == "booking date":
                info["booking_date"] = val
            elif lab == "total bond amount":
                money = _MONEY_RE.search(val)
                if money:
                    info["bond"] = money.group(1).replace(",", "")
            elif lab == "booking origin":
                info["agency"] = val
            elif lab == "name":
                info["name"] = val
            elif lab == "age":
                info["age"] = val
            elif lab in ("gender", "sex"):
                info["sex"] = val
            elif lab == "race":
                info["race"] = val
            elif lab == "address":
                info["address"] = val

        # Charge grid
        charges: List[str] = []
        charge_bond_dollars: List[str] = []
        for table in soup.find_all("table"):
            headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
            if not headers:
                continue
            header_blob = " ".join(headers)
            if "charge description" not in header_blob:
                continue
            desc_idx = next((i for i, h in enumerate(headers) if "charge" in h), None)
            bond_idx = next(
                (i for i, h in enumerate(headers) if h.strip() in ("bond", "chargebond") or h == "bond"),
                None,
            )
            # Prefer explicit ChargeBond / Bond column by class when present
            for row in table.find_all("tr"):
                tds = row.find_all("td")
                if not tds:
                    continue
                desc_cell = row.find("td", class_=re.compile(r"ChargeDescription", re.I))
                bond_cell = row.find("td", class_=re.compile(r"ChargeBond", re.I))
                if desc_cell is not None:
                    desc = desc_cell.get_text(" ", strip=True)
                elif desc_idx is not None and desc_idx < len(tds):
                    desc = tds[desc_idx].get_text(" ", strip=True)
                else:
                    continue
                if not desc or desc.lower() in {"no data", "charge description"}:
                    continue
                charges.append(desc)
                raw_bond = ""
                if bond_cell is not None:
                    raw_bond = bond_cell.get_text(" ", strip=True)
                elif bond_idx is not None and bond_idx < len(tds):
                    raw_bond = tds[bond_idx].get_text(" ", strip=True)
                # Only accept explicit dollar amounts — never YYYY-######## reference ids.
                if raw_bond and not _BOOKING_RE.fullmatch(raw_bond.strip()):
                    money = _MONEY_RE.search(raw_bond) or re.fullmatch(
                        r"[\d,]+(?:\.\d{1,2})?", raw_bond.strip()
                    )
                    if money:
                        amt = (money.group(1) if hasattr(money, "group") and money.lastindex else money.group(0))
                        charge_bond_dollars.append(str(amt).replace(",", ""))
            break

        if charges:
            info["charges"] = " | ".join(charges)

        # If Total Bond Amount was missing/zero but a charge cell published $, keep Total
        # Bond as authoritative when present; otherwise sum only explicit $ cells.
        if "bond" not in info and charge_bond_dollars:
            try:
                total = sum(float(x) for x in charge_bond_dollars)
                info["bond"] = f"{total:.2f}".rstrip("0").rstrip(".") if total else "0"
            except ValueError:
                pass

        # Bond type table (often "No data")
        for table in soup.find_all("table"):
            headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
            if headers == ["bond type", "bond amount", "bond status"]:
                types: List[str] = []
                for row in table.find_all("tr"):
                    tds = row.find_all("td")
                    if not tds:
                        continue
                    if len(tds) == 1 and "no data" in tds[0].get_text(" ", strip=True).lower():
                        continue
                    if tds:
                        bt = tds[0].get_text(" ", strip=True)
                        if bt and bt.lower() != "bond type":
                            types.append(bt)
                if types:
                    info["bond_type"] = " | ".join(types)
                break

        return info

    def parse_detail_to_record(
        self,
        html: str,
        *,
        fallback_name: str,
        detail_url: str,
    ) -> Optional[ArrestRecord]:
        info = self.parse_detail_html(html)
        booking = (info.get("booking") or "").strip()
        if not _BOOKING_RE.fullmatch(booking):
            logger.debug(
                "Lancaster: skip detail without source Booking YYYY-######## (%s)",
                detail_url,
            )
            return None

        name = (info.get("name") or fallback_name or "").strip()
        if not name:
            return None

        first = last = middle = ""
        if "," in name:
            parts = [p.strip() for p in name.split(",", 1)]
            last = parts[0]
            rest = parts[1].split() if len(parts) > 1 else []
            first = rest[0] if rest else ""
            middle = " ".join(rest[1:]) if len(rest) > 1 else ""
        else:
            parts = name.split()
            first = parts[0] if parts else ""
            last = parts[-1] if len(parts) > 1 else ""
            middle = " ".join(parts[1:-1]) if len(parts) > 2 else ""

        booking_date_raw = info.get("booking_date", "")
        booking_date = booking_date_raw
        booking_time = ""
        # e.g. "7/30/2025 5:59 AM"
        m = re.match(
            r"^(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)$",
            booking_date_raw.strip(),
            re.I,
        )
        if m:
            booking_date, booking_time = m.group(1), m.group(2)

        bond = info.get("bond", "0") or "0"
        charges = info.get("charges") or "Unknown"

        sex = (info.get("sex") or "")[:1].upper()
        if sex and sex not in "MF":
            # "Male" / "Female"
            low = (info.get("sex") or "").lower()
            sex = "M" if low.startswith("m") else ("F" if low.startswith("f") else "")

        return ArrestRecord(
            County=self.county,
            State=self.state,
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=booking,
            Booking_Date=booking_date,
            Booking_Time=booking_time,
            Arrest_Date=booking_date,
            Arrest_Time=booking_time,
            Charges=charges,
            Bond_Amount=bond,
            Bond_Type=info.get("bond_type", ""),
            Status="In Custody",
            Facility=FACILITY,
            Agency=info.get("agency", ""),
            Race=info.get("race", ""),
            Sex=sex,
            Age_At_Arrest=info.get("age", ""),
            Address=info.get("address", ""),
            Detail_URL=detail_url,
            extra_data={
                "source": "newworld_inmateinquiry",
                "portal": "SC0290000",
            },
        )
