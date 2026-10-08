"""Shared Florida SmartCOP SmartWEB "JAIL View" fetch + parse.

Used by Suwannee-style FL counties whose official public roster is an ordinary
HTTPS ASP.NET JAIL View with source-issued Booking No (``XXSO<YY>JBN<NNNNNN>``),
booking date/time, charges, and bond on each card.

Caller supplies base URL (…/smartwebclient), facility name, and county label.
Uses plain ``requests`` only — no proxy, stealth browser, or CAPTCHA solver.

Two ``AddMoreResults`` page-method builds exist in the wild (recon 2026-10-07):

* **modern** (Suwannee / Hamilton / Escambia / Sumter): page JS posts
  ``JSON.stringify({ searchVals: SearchVals })`` with ``DateOfBirth`` and
  ``BookingNumber`` keys; response is ``{"d": {"data", "resultsReturned", ...}}``.
* **legacy** (Putnam / Bradford / Dixie / Taylor / Santa Rosa): page JS posts the
  bare ``JSON.stringify(SearchVals)`` without those two keys; response is
  ``{"d": {"Data": {"data", "resultsReturned", ...}}}``. Sending the modern
  wrapper to a legacy host returns HTTP 500, which previously capped those
  counties at the first page of cards.

The build is detected from the search-results page script, never guessed.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 30
MAX_PAGES = 60
REQUEST_DELAY_S = 0.3


def _headers(search_url: str) -> dict:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": search_url,
    }


def _is_legacy_page_method(html: str) -> bool:
    """True when the results page posts bare ``SearchVals`` (legacy build)."""
    if re.search(r"searchVals\s*:\s*SearchVals", html):
        return False
    return bool(re.search(r"JSON\.stringify\(\s*SearchVals\s*\)", html))


def _more_results_payload(begin: str, end: str, loaded: int, *, legacy: bool) -> dict:
    vals = _search_vals(begin, end, loaded)
    if legacy:
        vals.pop("DateOfBirth", None)
        vals.pop("BookingNumber", None)
        return vals
    return {"searchVals": vals}


def _more_results_body(payload: dict) -> dict:
    """Unwrap ``d`` (modern) or ``d.Data`` (legacy) from an AddMoreResults reply."""
    d = (payload or {}).get("d") or {}
    if isinstance(d, dict) and isinstance(d.get("Data"), dict):
        d = d["Data"]
    return d if isinstance(d, dict) else {}


def _search_vals(begin: str, end: str, loaded: int) -> dict:
    return {
        "FirstName": "",
        "MiddleName": "",
        "LastName": "",
        "BeginBookDate": begin,
        "EndBookDate": end,
        "BeginReleaseDate": "",
        "EndReleaseDate": "",
        "TypeJailSearch": 0,
        "RecordsLoaded": loaded,
        "SortOption": 1,
        "SortOrder": 1,
        "IsDefault": False,
        "DateOfBirth": "",
        "BookingNumber": "",
    }


def scrape_smartweb_jail_view(
    *,
    county: str,
    facility: str,
    base_url: str,
    lookback_days: Optional[int] = None,
    log_prefix: Optional[str] = None,
) -> List[ArrestRecord]:
    """Fetch current inmates for a booking-date window from a SmartWEB JAIL View."""
    prefix = log_prefix or county
    base = base_url.rstrip("/")
    search_url = f"{base}/jail.aspx"
    ajax_url = f"{base}/Jail.aspx/AddMoreResults"
    days = LOOKBACK_DAYS if lookback_days is None else lookback_days
    begin = (date.today() - timedelta(days=days)).strftime("%m/%d/%Y")
    end = date.today().strftime("%m/%d/%Y")

    session = requests.Session()
    session.headers.update(_headers(search_url))
    resp = session.get(search_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    if not soup.find("input", {"name": "tbBeginDate"}):
        raise RuntimeError(f"{prefix}: JAIL View search form changed (tbBeginDate missing)")
    # TypeSearch (Current Inmates Only) exists on newer JAIL Views (Suwannee/Hamilton);
    # older Dixie/Taylor builds omit it and still accept a booking-date window POST.

    form = {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}
    form.update(
        {
            "txbLastName": "",
            "txbFirstName": "",
            "txbMiddleName": "",
            "tbBeginDate": begin,
            "tbEndDate": end,
            "tbBeginReleaseDate": "",
            "tbEndReleaseDate": "",
            "SearchSortOption": "1",  # Booking Date
            "SearchOrderOption": "1",  # Descending
            "btnSumit": "Submit",
        }
    )
    if soup.find("select", {"name": "TypeSearch"}):
        form["TypeSearch"] = "0"  # Current Inmates Only
    resp2 = session.post(search_url, data=form, timeout=60)
    resp2.raise_for_status()

    seen: set = set()
    all_records = _parse_html(
        resp2.text, seen, county=county, facility=facility, detail_url=search_url
    )
    m = re.search(r'id="ResultsReturned"[^>]*>(\d+)<', resp2.text)
    loaded = int(m.group(1)) if m else len(all_records)
    legacy = _is_legacy_page_method(resp2.text)
    logger.info(
        "%s: first page %d cards (%s..%s, %s AddMoreResults)",
        prefix, loaded, begin, end, "legacy" if legacy else "modern",
    )

    json_headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": search_url,
    }
    page = 1
    while loaded and page < MAX_PAGES:
        time.sleep(REQUEST_DELAY_S)
        try:
            r = session.post(
                ajax_url,
                json=_more_results_payload(begin, end, loaded, legacy=legacy),
                headers=json_headers,
                timeout=60,
            )
            r.raise_for_status()
            d = _more_results_body(r.json())
        except Exception as exc:
            logger.warning("%s: AddMoreResults stopped after %d cards (%s)", prefix, len(all_records), exc)
            break
        returned = int(d.get("resultsReturned") or 0)
        attempted = int(d.get("resultsAttempted") or 0)
        snippet = d.get("data") or ""
        if returned <= 0 or not snippet:
            break
        all_records.extend(
            _parse_html(
                "<table>" + snippet + "</table>",
                seen,
                county=county,
                facility=facility,
                detail_url=search_url,
            )
        )
        loaded += returned
        page += 1
        if attempted and returned < attempted:
            break

    if all_records and not any(r.Charges for r in all_records):
        # A changed charges-grid markup would otherwise $set blank charges on
        # every booking.
        raise RuntimeError(f"{prefix}: no booking in the run has charges (JailViewCharges markup drift?)")
    logger.info("%s: %d source bookings from %d cards", prefix, len(all_records), loaded)
    return all_records


def _charge_bond_amount(bond_str: str) -> Optional[float]:
    """A charge row's published dollar amount, else None (unknown).

    "$2,500.00 SURETY" is a dollar amount plus a type word; require a ``$`` or
    a pure numeric cell so a reference id in the bond column is not summed.
    NO BOND / HOLD / NONE / N/A / blank are not amounts."""
    text = (bond_str or "").strip()
    if not text:
        return None
    compact = re.sub(r"\s", "", text.upper())
    if any(t in compact for t in ("NOBOND", "NONE", "N/A", "HOLD")):
        return None
    money = re.search(r"([0-9][0-9,]*(?:\.\d+)?)", text)
    pure = re.fullmatch(r"[0-9][0-9,]*(?:\.\d+)?", text)
    if money and ("$" in text or pure):
        try:
            return float(money.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _fmt_amount(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _parse_name_from_header(header_text: str) -> tuple[str, str, str, str]:
    """Parse SearchHeader / card identity line.

    Variants seen in the wild:
      LAST, FIRST MIDDLE (W/ FEMALE )
      LAST, FIRST MIDDLE (W/ FEMALE / DOB: 11/30/1995 )
      LAST, FIRST (W/ MALE )
    Returns (full_name, race, sex, dob_from_header).
    """
    cleaned = re.sub(r"\s+", " ", (header_text or "")).strip()
    cleaned = re.sub(r"(?i)^enlarge\s+photo\s+", "", cleaned).strip()
    m = re.search(
        r"([A-Z][A-Z\s\-\',\.]+,\s*[A-Z][A-Z\s\-\'\.]+?)\s*"
        r"\(([A-Z])\s*/\s*([A-Z]+)(?:\s*/\s*DOB:\s*([\d/]+))?\s*\)",
        cleaned,
        re.IGNORECASE,
    )
    if not m:
        return "", "", "", ""
    full_name = m.group(1).strip()
    race = (m.group(2) or "").upper()
    sex_raw = (m.group(3) or "").upper()
    dob = (m.group(4) or "").strip()
    sex = "M" if sex_raw in ("MALE", "M") else "F" if sex_raw in ("FEMALE", "F") else ""
    return full_name, race, sex, dob


def _header_text_for_card(img) -> str:
    """Prefer the inmate SearchHeader cell; fall back to nearby row text."""
    row = img.find_parent("tr")
    if row:
        sh = row.select_one(".SearchHeader") or row.find(class_="SearchHeader")
        if sh and sh.get_text(strip=True):
            return sh.get_text(" ", strip=True)
        # SearchHeader sometimes sits one sibling down from the photo row
        sib = row.find_next_sibling("tr")
        for _ in range(3):
            if not sib:
                break
            sh = sib.select_one(".SearchHeader") or sib.find(class_="SearchHeader")
            if sh and sh.get_text(strip=True):
                return sh.get_text(" ", strip=True)
            if sib.find("img", src=re.compile(r"bookno=")):
                break
            sib = sib.find_next_sibling("tr")
    return ""


def _parse_html(
    html: str,
    seen: set,
    *,
    county: str,
    facility: str,
    detail_url: str,
) -> List[ArrestRecord]:
    """Parse SmartWEB inmate cards; drop cards without a matching source Booking No."""
    soup = BeautifulSoup(html, "html.parser")
    records: List[ArrestRecord] = []

    for img in soup.find_all("img", src=re.compile(r"bookno=")):
        src = img.get("src", "")
        bk_m = re.search(r"bookno=([A-Z0-9]+)", src)
        if not bk_m:
            continue
        booking_num = bk_m.group(1)
        if booking_num in seen:
            continue

        block_text = ""
        try:
            row = img.find_parent("tr")
            current = row
            for _ in range(15):
                if current:
                    block_text += " " + current.get_text(" ", strip=True)
                    current = current.find_next_sibling("tr")
        except Exception:
            pass

        block_text = " ".join(block_text.split())
        block_text = re.sub(r"(?i)\benlarge\s+photo\b", " ", block_text)
        block_text = " ".join(block_text.split())
        text_bk = re.search(r"Booking No:\s*([A-Z0-9]+)", block_text)
        if not text_bk or text_bk.group(1) != booking_num:
            continue

        header = _header_text_for_card(img)
        full_name, race, sex, header_dob = _parse_name_from_header(header or block_text)
        if not full_name:
            continue
        # Only a card that will be emitted consumes the booking key. A photo
        # whose Booking No text does not match must not hide a later good card.
        seen.add(booking_num)

        last, first, middle = "", "", ""
        if "," in full_name:
            parts = full_name.split(",", 1)
            last = parts[0].strip()
            fm = parts[1].strip().split()
            first = fm[0] if fm else ""
            middle = " ".join(fm[1:]) if len(fm) > 1 else ""

        dob_m = re.search(r"DOB:\s*([\d/]+)", block_text)
        dob = header_dob or (dob_m.group(1) if dob_m else "")
        bd_m = re.search(r"Booking Date:\s*([\d/]+)(?:\s+(\d{1,2}:\d{2}\s*[AP]M))?", block_text)
        booking_date = bd_m.group(1) if bd_m else ""
        booking_time = (bd_m.group(2) or "") if bd_m else ""

        # Bound the status token. A greedy letter capture swallows "Booking No"
        # and used to treat "Out of Jail" as in custody because it contains "jail".
        status_m = re.search(
            r"Status:\s*(Out\s+of\s+Jail|In\s+Jail|In\s+Custody|Released)",
            block_text,
            re.IGNORECASE,
        )
        status_raw = status_m.group(1).strip() if status_m else ""
        if re.search(r"released|out\s+of", status_raw, re.I):
            status = "Released"
        elif re.search(r"jail|custody", status_raw, re.I):
            status = "In Custody"
        else:
            status = "In Custody"

        addr_m = re.search(r"Address Given:\s*([^\n\r\t]+?)(?:\s+CHARGES\b|\s+STATUTE\b|$)", block_text)
        address = addr_m.group(1).strip() if addr_m else ""

        charges_list: list[str] = []
        charge_bonds: list[Optional[float]] = []
        charges_tables = []
        row = img.find_parent("tr")
        if row:
            sibling = row.find_next_sibling("tr")
            while sibling:
                if sibling.find("img", src=re.compile(r"bookno=")):
                    break
                for table_el in sibling.find_all("table", class_="JailViewCharges"):
                    first_row = table_el.find("tr")
                    title = first_row.get_text(" ", strip=True).upper() if first_row else ""
                    if title.startswith("HOLDS"):
                        continue
                    charges_tables.append(table_el)
                sibling = sibling.find_next_sibling("tr")

        for charges_table in charges_tables:
            for chg_row in charges_table.find_all("tr"):
                if chg_row.get("class") and "SearchHeader" in chg_row.get("class"):
                    continue
                cells = chg_row.find_all("td")
                if len(cells) >= 6:
                    statute = cells[1].get_text(strip=True)
                    desc = cells[3].get_text(strip=True)
                    bond_str = cells[6].get_text(strip=True) if len(cells) >= 7 else ""
                    if statute or desc:
                        item = f"{statute} - {desc}" if statute and desc else statute or desc
                        charges_list.append(item)
                    charge_bonds.append(_charge_bond_amount(bond_str))

        # Total only when every charge row publishes a dollar amount (a published
        # $0.00 counts; live 2026-10-08: 19 of ~150 cells were $0.00). NO BOND,
        # HOLD, blank or any other text makes the total unknown ("") — a partial
        # sum would understate the bond, and an unknown is never written as $0.
        bond_amount = ""
        if charge_bonds:
            if all(b is not None for b in charge_bonds):
                bond_amount = _fmt_amount(sum(charge_bonds))  # type: ignore[arg-type]
        else:
            # No charge grid: a positive card-level "Bond Amount: $N" is the
            # source total. A card-level $0.00 is the JAIL View default (live:
            # Santa Rosa/Escambia bookings with no charges entered yet, and
            # cards reading $0.00 above a $250 charge), so it stays unknown.
            card_bond = re.search(r"Bond Amount:\s*\$\s*([0-9][0-9,]*(?:\.\d+)?)", block_text, re.I)
            if card_bond:
                card_value = float(card_bond.group(1).replace(",", ""))
                if card_value > 0:
                    bond_amount = _fmt_amount(card_value)

        records.append(
            ArrestRecord(
                County=county,
                State="FL",
                Facility=facility,
                Full_Name=full_name.upper(),
                First_Name=first.upper(),
                Middle_Name=middle.upper(),
                Last_Name=last.upper(),
                DOB=dob,
                Race=race.upper() if race else "",
                Sex=sex.upper() if sex else "",
                Booking_Number=booking_num,
                Booking_Date=booking_date,
                Booking_Time=booking_time,
                Charges=" | ".join(charges_list),
                Bond_Amount=bond_amount,  # "" = unknown, never $0
                Address=address,
                Status=status,
                Detail_URL=detail_url,
                Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                LastChecked=datetime.now(timezone.utc).isoformat(),
                LastCheckedMode="INITIAL",
            )
        )

    return records
