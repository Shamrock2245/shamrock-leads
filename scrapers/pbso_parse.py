"""Palm Beach PBSO blotter parse helpers.

No network. Used by the county scraper and by dashboard URL ingest.
Fail closed unless a card has both a displayed name and a source booking number.
Does not invent booking identifiers or probe unpublished endpoints.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

BLOTTER_URL = "https://www3.pbso.org/blotter/index.cfm"
FACILITY = "Palm Beach County Jail"
COUNTY = "Palm Beach"

_BOOKING_QUERY_KEYS = (
    "booking", "booking_number", "bookingnumber", "booking_no",
    "bookno", "book_no", "inmate", "jacket",
)


def is_pbso_host(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == "pbso.org" or host.endswith(".pbso.org")


def extract_pbso_booking_id(url: str) -> Optional[str]:
    """Return a source booking id from a PBSO URL, or None."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    qs = parse_qs(parsed.query, keep_blank_values=False)
    for key in _BOOKING_QUERY_KEYS:
        for candidate in qs.get(key, []) + qs.get(key.upper(), []):
            digits = re.sub(r"\D", "", candidate or "")
            if len(digits) >= 6:
                return digits
    frag = unquote(parsed.fragment or "")
    for blob in (frag, parsed.path, url):
        m = re.search(r"(?:booking[_-]?n(?:um(?:ber)?)?|book(?:ing)?)[=:/\s]+(\d{6,})", blob, re.I)
        if m:
            return m.group(1)
    m = re.search(r"/(\d{7,})/?$", parsed.path or "")
    if m:
        return m.group(1)
    return None


def is_pbso_index_only(url: str) -> bool:
    """True when the URL is the blotter search form with no booking identity."""
    if not is_pbso_host(url):
        return False
    path = (urlparse(url).path or "").rstrip("/").lower()
    if "blotter" not in path:
        return False
    return extract_pbso_booking_id(url) is None


def parse_name(name: str) -> tuple[str, str, str]:
    """Parse 'LAST, FIRST MIDDLE' into first, middle, last."""
    if not name:
        return "", "", ""
    name = " ".join(name.strip().split())
    if "," in name:
        parts = name.split(",", 1)
        last = parts[0].strip()
        remainder = parts[1].strip().split()
        first = remainder[0] if remainder else ""
        middle = " ".join(remainder[1:]) if len(remainder) > 1 else ""
        return first, middle, last
    parts = name.split()
    if len(parts) >= 3:
        return parts[0], " ".join(parts[1:-1]), parts[-1]
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return name, "", ""


def parse_pbso_card_text(card_text: str) -> Optional[dict]:
    """Parse one blotter result card. None unless name and booking number exist."""
    card_text = card_text or ""
    if not card_text.strip():
        return None

    def _extract(label: str) -> str:
        pattern = rf"{label}\s*:\s*(.+?)(?:\n|$)"
        m = re.search(pattern, card_text, re.I)
        return m.group(1).strip() if m else ""

    data = {
        "full_name": _extract("Name"),
        "first_name": "",
        "middle_name": "",
        "last_name": "",
        "booking_num": "",
        "jacket_num": _extract("Jacket Number"),
        "race": _extract("Race"),
        "sex": _extract("Gender"),
        "dob": _extract("DOB") or _extract("Date of Birth"),
        "facility": FACILITY,
        "agency": _extract("Arresting Agency") or "PBSO",
        "booking_date": "",
        "booking_time": "",
        "status": "In Custody",
        "release_date": "",
        "charges": "",
        "bond_amount": "0",
        "mug_url": "",
    }

    booking_dt_raw = _extract("Booking Date/Time")
    if booking_dt_raw:
        try:
            dt = datetime.strptime(booking_dt_raw.strip(), "%m/%d/%Y %H:%M")
            data["booking_date"] = dt.strftime("%Y-%m-%d")
            data["booking_time"] = dt.strftime("%H:%M:00")
        except ValueError:
            data["booking_date"] = booking_dt_raw.strip()

    release_raw = _extract("Release Date")
    if release_raw and "N/A" not in release_raw.upper() and release_raw.strip():
        data["status"] = "Released"
        data["release_date"] = release_raw.strip()

    booking_num_m = re.search(r"Booking\s*Number\s*:\s*(\d{6,})", card_text, re.I)
    if booking_num_m:
        data["booking_num"] = booking_num_m.group(1)

    charges: list[str] = []
    charge_matches = re.findall(
        r"(\d{3}\.\d+\s+\S.*?)(?:Original Bond|Current Bond|Bond Information|$)",
        card_text,
        re.I,
    )
    for ch in charge_matches:
        clean_ch = " ".join(ch.strip().split())
        if clean_ch and len(clean_ch) > 3:
            charges.append(clean_ch)
    if not charges:
        for line in card_text.split("\n"):
            line = line.strip()
            if re.match(r"^\d{3,4}", line) and not re.match(r"^\d{4}[\-/]", line):
                if "Bond" not in line and "Booking" not in line:
                    charges.append(" ".join(line.split()))

    total_bond = 0.0
    for amt_str in re.findall(r"Current\s+Bond\s*:\s*\$([0-9,]+(?:\.\d{2})?)", card_text, re.I):
        try:
            total_bond += float(amt_str.replace(",", ""))
        except (ValueError, TypeError):
            pass
    if total_bond == 0:
        for amt_str in re.findall(r"Original\s+Bond\s*:\s*\$([0-9,]+(?:\.\d{2})?)", card_text, re.I):
            try:
                total_bond += float(amt_str.replace(",", ""))
            except (ValueError, TypeError):
                pass

    data["charges"] = " | ".join(charges) if charges else ""
    data["bond_amount"] = f"{total_bond:.2f}" if total_bond > 0 else "0"
    if data["full_name"]:
        data["first_name"], data["middle_name"], data["last_name"] = parse_name(data["full_name"])

    if not data["full_name"] or not data["booking_num"]:
        return None
    return data


def parse_pbso_html(html: str) -> list[dict]:
    """Parse zero or more blotter cards from HTML. Empty list if none are valid."""
    if not html or not str(html).strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div[id^=allresults_]")
    rows: list[dict] = []
    if cards:
        for card in cards:
            row = parse_pbso_card_text(card.get_text("\n"))
            if not row:
                continue
            img = card.find("img")
            if img:
                src = (img.get("src") or "").strip()
                if src and "noimage" not in src.lower():
                    if not src.startswith("http"):
                        src = f"https://www3.pbso.org{src}"
                    row["mug_url"] = src
            rows.append(row)
        return rows
    single = parse_pbso_card_text(soup.get_text("\n"))
    return [single] if single else []


def pbso_html_is_js_shell(html: str) -> bool:
    """True when the response has no parseable card and looks like the CF/JS shell."""
    if parse_pbso_html(html):
        return False
    blob = (html or "").lower()
    return "hcaptcha" in blob or "start_date" in blob or "blotter" in blob


def row_to_ingest_dict(row: dict, source_url: str) -> dict:
    """Map a parsed blotter card to url_ingest's arrest dict."""
    booking = str(row.get("booking_num") or "")
    return {
        "full_name": row.get("full_name") or "",
        "first_name": row.get("first_name") or "",
        "middle_name": row.get("middle_name") or "",
        "last_name": row.get("last_name") or "",
        "booking_number": booking,
        "charges": row.get("charges") or "",
        "bond_amount": row.get("bond_amount") or "0",
        "county": COUNTY,
        "state": "FL",
        "facility": row.get("facility") or FACILITY,
        "agency": row.get("agency") or "PBSO",
        "status": row.get("status") or "In Custody",
        "booking_date": row.get("booking_date") or "",
        "booking_time": row.get("booking_time") or "",
        "dob": row.get("dob") or "",
        "race": row.get("race") or "",
        "sex": row.get("sex") or "",
        "mugshot_url": row.get("mug_url") or "",
        "person_id": row.get("jacket_num") or "",
        "source_url": source_url,
        "detail_url": f"{BLOTTER_URL}?booking={booking}" if booking else BLOTTER_URL,
        "ingestion_method": "pbso_blotter",
    }
