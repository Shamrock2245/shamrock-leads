"""Shared Florida SmartCOP SmartWEB "JAIL View" fetch + parse.

Used by Suwannee-style FL counties whose official public roster is an ordinary
HTTPS ASP.NET JAIL View with source-issued Booking No (``XXSO<YY>JBN<NNNNNN>``),
booking date/time, charges, and bond on each card.

Caller supplies base URL (…/smartwebclient), facility name, and county label.
Uses plain ``requests`` only — no proxy, stealth browser, or CAPTCHA solver.
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
    if not soup.find("input", {"name": "tbBeginDate"}) or not soup.find("select", {"name": "TypeSearch"}):
        raise RuntimeError(f"{prefix}: JAIL View search form changed (tbBeginDate/TypeSearch missing)")

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
            "TypeSearch": "0",  # Current Inmates Only
            "SearchSortOption": "1",  # Booking Date
            "SearchOrderOption": "1",  # Descending
            "btnSumit": "Submit",
        }
    )
    resp2 = session.post(search_url, data=form, timeout=60)
    resp2.raise_for_status()

    seen: set = set()
    all_records = _parse_html(
        resp2.text, seen, county=county, facility=facility, detail_url=search_url
    )
    m = re.search(r'id="ResultsReturned"[^>]*>(\d+)<', resp2.text)
    loaded = int(m.group(1)) if m else len(all_records)
    logger.info("%s: first page %d cards (%s..%s)", prefix, loaded, begin, end)

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
                json={"searchVals": _search_vals(begin, end, loaded)},
                headers=json_headers,
                timeout=60,
            )
            r.raise_for_status()
            d = (r.json() or {}).get("d") or {}
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

    logger.info("%s: %d source bookings from %d cards", prefix, len(all_records), loaded)
    return all_records


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
        seen.add(booking_num)

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
        text_bk = re.search(r"Booking No:\s*([A-Z0-9]+)", block_text)
        if not text_bk or text_bk.group(1) != booking_num:
            continue

        name_m = re.search(
            r"([A-Z][A-Z\s\-\',]+,\s*[A-Z][A-Z\s\-\'\.]+)\s*\(([A-Z])/\s*([A-Z]+)\s*\)",
            block_text,
            re.IGNORECASE,
        )
        full_name = name_m.group(1).strip() if name_m else ""
        race = name_m.group(2) if name_m else ""
        sex_raw = name_m.group(3) if name_m else ""
        sex = (
            "M"
            if sex_raw.upper() in ("MALE", "M")
            else "F"
            if sex_raw.upper() in ("FEMALE", "F")
            else ""
        )
        if not full_name:
            continue

        last, first, middle = "", "", ""
        if "," in full_name:
            parts = full_name.split(",", 1)
            last = parts[0].strip()
            fm = parts[1].strip().split()
            first = fm[0] if fm else ""
            middle = " ".join(fm[1:]) if len(fm) > 1 else ""

        dob_m = re.search(r"DOB:\s*([\d/]+)", block_text)
        dob = dob_m.group(1) if dob_m else ""
        bd_m = re.search(r"Booking Date:\s*([\d/]+)(?:\s+(\d{1,2}:\d{2}\s*[AP]M))?", block_text)
        booking_date = bd_m.group(1) if bd_m else ""
        booking_time = (bd_m.group(2) or "") if bd_m else ""

        status_m = re.search(r"Status:\s*([a-zA-Z\s]+)", block_text)
        status = status_m.group(1).strip() if status_m else "In Custody"
        if "jail" in status.lower() or "custody" in status.lower():
            status = "In Custody"

        addr_m = re.search(r"Address Given:\s*([^\n\r\t]+)", block_text)
        address = addr_m.group(1).strip() if addr_m else ""

        charges_list: list[str] = []
        total_bond = 0.0
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
                    bond_val = 0.0
                    if bond_str:
                        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
                        if not any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
                            try:
                                bond_val = float(cleaned)
                            except ValueError:
                                pass
                    total_bond += bond_val

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
                Bond_Amount=str(int(total_bond)) if total_bond.is_integer() else f"{total_bond:.2f}",
                Address=address,
                Status=status,
                Detail_URL=detail_url,
                Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                LastChecked=datetime.now(timezone.utc).isoformat(),
                LastCheckedMode="INITIAL",
            )
        )

    return records
