"""
SmartWeb (SmartCOP) card-bounded HTML parser — shared FL module
================================================================
Used by Putnam, Santa Rosa, Suwannee, Sumter, Taylor, Glades (and similar).

Parses the classic SmartWeb inmate card layout:
  - ``td.SearchHeader`` with name / race / sex / DOB
  - Sibling rows until the next inmate header
  - ``table.JailViewCharges`` for statutes + bond amounts

Hardens against UI chrome (``ENLARGE PHOTO``) and greedy Status capture that
previously polluted Address/Charges and tanked lead scores.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple

from core.models import ArrestRecord

logger = logging.getLogger(__name__)

_UI_NOISE_RE = re.compile(
    r"\b(?:ENLARGE\s+PHOTO|VIEW\s+PHOTO|CLICK\s+TO\s+ENLARGE|\[\+\]|\[\-\])\b",
    re.IGNORECASE,
)

_HEADER_RE = re.compile(
    r"([A-Z\s,'\-\.]+)\s*\(([A-Z])/\s*(MALE|FEMALE|M|F)\s*/\s*DOB:\s*([\d/]+)\s*\)",
    re.IGNORECASE,
)

_STATUS_RE = re.compile(
    r"Status[:\s]+(In\s+Jail|In\s+Custody|Released|Out\s+of\s+Jail|"
    r"Booked|Active|Inmate)",
    re.IGNORECASE,
)


def strip_ui_noise(text: str) -> str:
    if not text:
        return ""
    cleaned = _UI_NOISE_RE.sub(" ", text)
    return " ".join(cleaned.split())


def is_ui_label(text: str) -> bool:
    if not text:
        return True
    t = text.strip().upper()
    return t in (
        "ENLARGE PHOTO", "VIEW PHOTO", "PHOTO", "[+]", "[-]",
        "STATUTE", "CHARGE", "BOND", "DEGREE", "LEVEL",
    )


def parse_bond_cell(bond_str: str) -> Tuple[float, Optional[str]]:
    """Return (amount, bond_type) from a SmartWeb bond cell."""
    if not bond_str:
        return 0.0, None
    raw = bond_str.strip().upper()
    cleaned = re.sub(r"[$,\s]", "", raw)
    if not cleaned or cleaned in ("NONE", "N/A", "NA", "-"):
        return 0.0, None
    if "NOBOND" in cleaned or "NO BOND" in raw:
        return 0.0, "NO BOND"
    if "HOLD" in cleaned or "HOLD" in raw:
        return 0.0, "NO BOND"
    if "ROR" in cleaned or "R.O.R" in raw:
        return 0.0, "ROR"
    if "CASH" in raw and "SURETY" in raw:
        btype = "CASH/SURETY"
    elif "CASH" in raw:
        btype = "CASH"
    elif "SURETY" in raw:
        btype = "SURETY"
    else:
        btype = "SURETY"
    num_m = re.search(r"([\d]+(?:\.\d+)?)", cleaned)
    if not num_m:
        return 0.0, btype if any(k in raw for k in ("CASH", "SURETY", "BOND")) else None
    try:
        return float(num_m.group(1)), btype
    except ValueError:
        return 0.0, None


def _card_nodes(header_td):
    """Return BeautifulSoup nodes that belong to this inmate card only."""
    nodes = []
    detail_table = header_td.find_parent("table")
    if detail_table:
        nodes.append(detail_table)
    top_row = detail_table.find_parent("tr") if detail_table else header_td.find_parent("tr")
    if not top_row:
        return nodes
    sibling = top_row.find_next_sibling("tr")
    while sibling:
        next_header = sibling.find("td", class_="SearchHeader")
        if next_header:
            nh_text = next_header.get_text(" ", strip=True)
            if "DOB:" in nh_text or re.search(
                r"\([A-Z]/\s*(?:MALE|FEMALE|M|F)", nh_text, re.I
            ):
                break
        nodes.append(sibling)
        sibling = sibling.find_next_sibling("tr")
    return nodes


def _find_charges_table(card_nodes):
    for node in card_nodes:
        classes = node.get("class") or []
        if getattr(node, "name", None) == "table" and "JailViewCharges" in classes:
            return node
        found = node.find("table", class_="JailViewCharges") if hasattr(node, "find") else None
        if found:
            return found
    return None


def _resolve_bond_type(bond_types: list, total_bond: float) -> str:
    if bond_types:
        unique = list(dict.fromkeys(bond_types))
        if any(t in ("SURETY", "CASH", "CASH/SURETY") for t in unique) and "NO BOND" in unique:
            unique = [t for t in unique if t != "NO BOND"]
        return " / ".join(unique)
    if total_bond > 0:
        return "CASH/SURETY"
    return ""


def parse_smartweb_cards(
    html: str,
    *,
    county: str,
    facility: str,
    detail_url: str,
    seen: Optional[Set[str]] = None,
    state: str = "FL",
    log_prefix: Optional[str] = None,
) -> List[ArrestRecord]:
    """Parse SmartWeb HTML snippet into ArrestRecords (card-bounded).

    Args:
        html: Full page or AJAX HTML fragment containing SearchHeader cards.
        county / facility / detail_url: Record metadata.
        seen: Mutable set of booking numbers already collected (dedup across pages).
        state: Two-letter state code (default FL).
        log_prefix: Logger label for parse warnings (default county).
    """
    from bs4 import BeautifulSoup

    if seen is None:
        seen = set()
    prefix = log_prefix or county
    soup = BeautifulSoup(html, "html.parser")
    records: List[ArrestRecord] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    headers = soup.find_all("td", class_="SearchHeader")
    for header_td in headers:
        try:
            header_text = strip_ui_noise(header_td.get_text(" ", strip=True))
            header_match = _HEADER_RE.search(header_text)
            if not header_match:
                continue

            full_name = header_match.group(1).strip()
            race = header_match.group(2).strip()
            sex_raw = header_match.group(3).strip()
            sex = "M" if sex_raw.upper() in ("MALE", "M") else "F"
            dob = header_match.group(4).strip()

            last, first, middle = "", "", ""
            if "," in full_name:
                parts = full_name.split(",", 1)
                last = parts[0].strip()
                fm = parts[1].strip().split()
                first = fm[0] if fm else ""
                middle = " ".join(fm[1:]) if len(fm) > 1 else ""

            card_nodes = _card_nodes(header_td)
            detail_text = strip_ui_noise(
                " ".join(n.get_text(" ", strip=True) for n in card_nodes)
            )

            booking_no_match = re.search(
                r"Booking\s+No[:\s]+([A-Z0-9]+)", detail_text, re.IGNORECASE
            )
            booking_number = booking_no_match.group(1).strip() if booking_no_match else ""
            if not booking_number or booking_number in seen:
                continue
            seen.add(booking_number)

            booking_date_match = re.search(
                r"Booking\s+Date[:\s]+([\d/]+\s+[\d:]+\s*(?:AM|PM)?)",
                detail_text,
                re.IGNORECASE,
            )
            booking_date = booking_date_match.group(1).strip() if booking_date_match else ""

            address_match = re.search(
                r"Address\s+Given[:\s]+(.+?)(?:HOLDS|CHARGES|Status:|Booking\s+No|$)",
                detail_text,
                re.IGNORECASE | re.DOTALL,
            )
            address = (
                strip_ui_noise(" ".join(address_match.group(1).strip().split()))
                if address_match
                else ""
            )

            status_match = _STATUS_RE.search(detail_text)
            status = status_match.group(1).strip() if status_match else "In Custody"
            if re.search(r"jail|custody|booked|inmate|active", status, re.I):
                status = "In Custody"
            elif re.search(r"released|out\s+of", status, re.I):
                status = "Released"

            charges_list: List[str] = []
            bond_types: List[str] = []
            court_dates: List[str] = []
            total_bond = 0.0

            charges_table = _find_charges_table(card_nodes)
            if charges_table:
                for chg_row in charges_table.find_all("tr"):
                    classes = chg_row.get("class") or []
                    if "SearchHeader" in classes:
                        continue
                    cells = chg_row.find_all("td")
                    if len(cells) < 6:
                        continue
                    statute = strip_ui_noise(cells[1].get_text(strip=True))
                    court_case = strip_ui_noise(cells[2].get_text(strip=True))
                    desc = strip_ui_noise(cells[3].get_text(strip=True))
                    bond_str = cells[6].get_text(strip=True) if len(cells) >= 7 else ""

                    if not statute and not desc:
                        continue
                    if is_ui_label(statute) or is_ui_label(desc):
                        continue

                    item = f"{statute} - {desc}" if statute and desc else (statute or desc)
                    charges_list.append(item)

                    cd_m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", court_case)
                    if cd_m:
                        court_dates.append(cd_m.group(1))

                    bond_val, btype = parse_bond_cell(bond_str)
                    if btype:
                        bond_types.append(btype)
                    total_bond += bond_val

            if total_bond == 0.0:
                roster_bond = re.search(
                    r"Bond\s+Amount[:\s]+([\$\d,\.]+|NO\s*BOND|HOLD|NONE|N/A)",
                    detail_text,
                    re.IGNORECASE,
                )
                if roster_bond:
                    bond_val, btype = parse_bond_cell(roster_bond.group(1))
                    total_bond = bond_val
                    if btype:
                        bond_types.append(btype)

            bond_type = _resolve_bond_type(bond_types, total_bond)
            court_date = court_dates[0] if court_dates else ""
            charges_str = " | ".join(charges_list)

            records.append(ArrestRecord(
                County=county,
                State=state,
                Facility=facility,
                Full_Name=full_name.upper(),
                First_Name=first.upper(),
                Middle_Name=middle.upper(),
                Last_Name=last.upper(),
                DOB=dob,
                Race=race.upper() if race else "",
                Sex=sex.upper() if sex else "",
                Booking_Number=booking_number,
                Booking_Date=booking_date,
                Charges=charges_str,
                Bond_Amount=(
                    str(int(total_bond)) if total_bond.is_integer() else f"{total_bond:.2f}"
                ),
                Bond_Type=bond_type,
                Court_Date=court_date,
                Address=address,
                Status=status,
                Detail_URL=detail_url,
                Scrape_Timestamp=now_iso,
                LastChecked=now_iso,
                LastCheckedMode="INITIAL",
            ))
        except Exception as e:
            logger.warning("%s: failed to parse inmate card: %s", prefix, e)
            continue

    return records
