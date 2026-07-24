"""
SmartWeb (SmartCOP) Jail Roster Parser — Shared Module
Used by: Glades, Putnam, Sumter, Taylor, Dixie counties

SmartWeb returns a card-style HTML layout where each inmate is a table row
containing embedded text like:
  "SMITH, JOHN WILLIAM (W/MALE) Status: In Jail Booking No: XX26JBN001234
   MniNo: XX01MNI000123 Booking Date: 04/19/2026 03:52 AM Bond Amount: $500.00"

The POST endpoint is Jail.aspx with:
  txbLastName, txbFirstName, txbMiddleName, btnSumit (note: typo in original)
  Plus standard ASP.NET __VIEWSTATE, __EVENTVALIDATION, __VIEWSTATEGENERATOR
"""
import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def _clean(text: str) -> str:
    if not text:
        return ""
    return " ".join(str(text).strip().split())


def _parse_name(raw: str) -> Tuple[str, str, str]:
    """Parse 'LAST, FIRST MIDDLE' format into (first, middle, last)."""
    raw = _clean(raw)
    if not raw:
        return "", "", ""
    if "," in raw:
        parts = raw.split(",", 1)
        last = parts[0].strip()
        fm = parts[1].strip().split()
        first = fm[0] if fm else ""
        middle = " ".join(fm[1:]) if len(fm) > 1 else ""
        return first, middle, last
    parts = raw.split()
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def _parse_bond(bond_str: str) -> str:
    """Normalize bond amount string."""
    if not bond_str:
        return "0"
    cleaned = bond_str.strip().upper()
    if any(t in cleaned for t in ["NO BOND", "NONE", "N/A", "HOLD", "CASH ONLY"]):
        return "0"
    # Remove $ and commas
    cleaned = re.sub(r"[$,\s]", "", cleaned)
    try:
        val = float(cleaned)
        return f"{val:.2f}"
    except (ValueError, TypeError):
        return "0"


def scrape_smartweb(
    base_url: str,
    county: str,
    facility: str,
    session,
    ArrestRecord,
) -> List:
    """
    Scrape a SmartWeb jail roster.

    Args:
        base_url: Full URL to Jail.aspx (e.g. https://smartweb.pcso.us/smartwebclient/Jail.aspx)
        county: County name string
        facility: Facility name string
        session: requests.Session instance
        ArrestRecord: ArrestRecord dataclass

    Returns:
        List of ArrestRecord instances
    """
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": base_url,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    session.headers.update(headers)

    # Step 1: GET page for ViewState tokens
    # Works with stdlib requests and curl_cffi (verify=False for incomplete chains).
    def _call(method: str, url: str, *, timeout: int = 20, data=None):
        fn = session.get if method == "GET" else session.post
        try:
            if method == "GET":
                return fn(url, timeout=timeout, verify=False)
            return fn(url, data=data, timeout=timeout, verify=False)
        except TypeError:
            # Older/stdlib sessions that reject verify=
            if method == "GET":
                return fn(url, timeout=timeout)
            return fn(url, data=data, timeout=timeout)

    try:
        r = _call("GET", base_url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        logger.error(f"{county} SmartWeb GET: {e}")
        return []

    def _get_hidden(name: str) -> str:
        el = soup.find("input", {"id": name}) or soup.find("input", {"name": name})
        return el["value"] if el and el.get("value") else ""

    # Step 2: POST empty search to get all inmates
    data = {
        "__VIEWSTATE": _get_hidden("__VIEWSTATE"),
        "__EVENTVALIDATION": _get_hidden("__EVENTVALIDATION"),
        "__VIEWSTATEGENERATOR": _get_hidden("__VIEWSTATEGENERATOR"),
        "txbLastName": "",
        "txbFirstName": "",
        "txbMiddleName": "",
        "btnSumit": "Submit",  # Note: intentional typo in SmartWeb source
    }

    try:
        r2 = _call("POST", base_url, timeout=45, data=data)
        r2.raise_for_status()
        soup2 = BeautifulSoup(r2.text, "html.parser")
    except Exception as e:
        logger.error(f"{county} SmartWeb POST: {e}")
        return []

    records = []

    # SmartWeb card layout: each inmate is in a table row with embedded text
    # Pattern: "LAST, FIRST MIDDLE (RACE/SEX) Status: ... Booking No: ... MniNo: ... Booking Date: ... Bond Amount: ..."
    name_race_sex_re = re.compile(
        r"([A-Z][A-Z\s,'\-\.]+)\s*\(([A-Z])/\s*(MALE|FEMALE|M|F)\s*\)"
    )
    booking_no_re = re.compile(r"Booking\s+No[:\s]+([A-Z0-9]+)", re.IGNORECASE)
    mni_re = re.compile(r"MniNo[:\s]+([A-Z0-9]+)", re.IGNORECASE)
    booking_date_re = re.compile(r"Booking\s+Date[:\s]+([\d/]+ [\d:]+\s*(?:AM|PM)?)", re.IGNORECASE)
    bond_re = re.compile(r"Bond\s+Amount[:\s]+([\$\d,\.]+|NO BOND|NONE|N/A|HOLD|CASH ONLY)", re.IGNORECASE)
    status_re = re.compile(r"Status[:\s]+(In Jail|Released|Out of Jail)", re.IGNORECASE)
    age_re = re.compile(r"Age\s+On\s+Booking\s+Date[:\s]+(\d+)", re.IGNORECASE)
    address_re = re.compile(r"Address\s+Given[:\s]+(.+?)(?:HOLDS|CHARGES|$)", re.IGNORECASE | re.DOTALL)

    # Find the largest table (inmate list)
    best_table = None
    best_rows = 0
    for table in soup2.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) > best_rows:
            best_rows = len(rows)
            best_table = table

    if not best_table or best_rows < 2:
        logger.warning(f"{county} SmartWeb: no data table found (tables={len(soup2.find_all('table'))})")
        return []

    rows = best_table.find_all("tr")
    seen_booking_nos = set()

    for row in rows:
        cells = row.find_all("td")
        if not cells:
            continue

        # Get all text from the row
        row_text = " ".join(c.get_text(" ", strip=True) for c in cells)
        row_text = re.sub(r"\s+", " ", row_text)

        # Must have a booking number to be a valid record
        bn_match = booking_no_re.search(row_text)
        if not bn_match:
            continue
        booking_no = bn_match.group(1).strip()
        if booking_no in seen_booking_nos:
            continue
        seen_booking_nos.add(booking_no)

        # Parse name and race/sex
        nm_match = name_race_sex_re.search(row_text)
        full_name = ""
        race = ""
        sex = ""
        first_name = ""
        middle_name = ""
        last_name = ""
        if nm_match:
            full_name = nm_match.group(1).strip()
            race = nm_match.group(2).strip()
            sex_raw = nm_match.group(3).strip()
            sex = "M" if sex_raw.upper() in ("MALE", "M") else "F"
            first_name, middle_name, last_name = _parse_name(full_name)

        # Parse other fields
        mni_match = mni_re.search(row_text)
        person_id = mni_match.group(1).strip() if mni_match else ""

        bd_match = booking_date_re.search(row_text)
        booking_date = bd_match.group(1).strip() if bd_match else ""

        bond_match = bond_re.search(row_text)
        bond_amount = _parse_bond(bond_match.group(1)) if bond_match else "0"

        status_match = status_re.search(row_text)
        status = status_match.group(1).strip() if status_match else "In Custody"
        if status.lower() in ("in jail",):
            status = "In Custody"

        # Extract charges (statute + charge description)
        charges_list = []
        charge_re = re.compile(
            r"\[\+\]\s*([\d\.]+[a-z]*)\s+([A-Z0-9\s\(\)]+?)\s+([A-Z\-\s]+?)\s+[A-Z]\s+[A-Z]\s+[\$\d,\.]+|NO BOND",
            re.IGNORECASE
        )
        for cm in charge_re.finditer(row_text):
            statute = cm.group(1).strip() if cm.group(1) else ""
            charge_desc = cm.group(3).strip() if cm.group(3) else ""
            if statute and charge_desc:
                charges_list.append(f"{statute} - {charge_desc}")
        charges = "; ".join(charges_list) if charges_list else ""

        # Get detail link
        detail_url = ""
        for cell in cells:
            link = cell.find("a", href=True)
            if link:
                href = link["href"]
                if "detail" in href.lower() or "inmate" in href.lower() or booking_no in href:
                    detail_url = href if href.startswith("http") else f"{base_url.rsplit('/', 1)[0]}/{href.lstrip('/')}"
                    break

        if not full_name and not booking_no:
            continue

        records.append(ArrestRecord(
            County=county,
            Booking_Number=booking_no,
            Person_ID=person_id,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            Booking_Date=booking_date,
            Status=status,
            Facility=facility,
            Race=race,
            Sex=sex,
            Charges=charges,
            Bond_Amount=bond_amount,
            Detail_URL=detail_url,
            LastCheckedMode="INITIAL",
        ))

    logger.info(f"{county} SmartWeb: {len(records)} records from {best_rows} rows")
    return records
