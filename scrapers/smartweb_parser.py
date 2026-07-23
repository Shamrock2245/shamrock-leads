"""
Shared SmartWEB / SmartCOP jail-card HTML parser.

SmartWEB rosters render inmate *cards* (photo + Booking No + charges table),
not a classic GridView. Scrapers that walk the first ``table`` with the word
"name" only capture the search form (or 1–3 junk rows).

Pattern proven on Suwannee / Bradford (bookno= image + sibling metadata).
"""
from __future__ import annotations

import re
from typing import List, Optional, Set

from core.models import ArrestRecord

_BOOKNO_RE = re.compile(r"bookno=([A-Za-z0-9]+)", re.I)
_NAME_RE = re.compile(
    r"([A-Z][A-Z\s\-\',\.]+,\s*[A-Z][A-Z\s\-\'\.]+)\s*\(([A-Z])/\s*([A-Z]+)",
    re.I,
)
_DOB_RE = re.compile(r"DOB:\s*([\d/]+)", re.I)
_BD_RE = re.compile(r"Booking Date:\s*([\d/]+)", re.I)
_STATUS_RE = re.compile(r"Status:\s*([A-Za-z\s]+)", re.I)
_ADDR_RE = re.compile(r"Address Given:\s*(.+?)(?:\s+HOLDS|\s+CHARGES|\s+STATUTE|$)", re.I)
_BOND_RE = re.compile(r"Bond Amount:\s*\$?([\d,]+\.?\d*)", re.I)


def parse_smartweb_cards(
    html: str,
    *,
    county: str,
    state: str = "FL",
    facility: str = "",
    detail_base: str = "",
    seen: Optional[Set[str]] = None,
) -> List[ArrestRecord]:
    """Parse SmartWEB inmate cards from HTML (full page or AJAX fragment)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    if seen is None:
        seen = set()
    records: List[ArrestRecord] = []

    for img in soup.find_all("img", src=_BOOKNO_RE):
        src = img.get("src") or ""
        bk_m = _BOOKNO_RE.search(src)
        if not bk_m:
            continue
        booking_num = bk_m.group(1).strip()
        if not booking_num or booking_num in seen:
            continue
        seen.add(booking_num)

        block_text = ""
        try:
            row = img.find_parent("tr")
            current = row
            for _ in range(18):
                if not current:
                    break
                block_text += " " + current.get_text(" ", strip=True)
                current = current.find_next_sibling("tr")
        except Exception:
            pass
        block_text = " ".join(block_text.split())

        name_m = _NAME_RE.search(block_text)
        full_name = name_m.group(1).strip() if name_m else ""
        if not full_name:
            # Fallback: text next to Enlarge Photo
            parent = img.find_parent("td") or img.find_parent("tr")
            if parent:
                rough = parent.get_text(" ", strip=True)
                rough = re.sub(r"Enlarge\s*Photo", "", rough, flags=re.I).strip()
                if "," in rough:
                    full_name = rough.split("(")[0].strip()

        full_name = re.sub(r"^(Enlarge\s*Photo\s*)+", "", full_name or "", flags=re.I).strip()
        full_name = re.sub(r"\s+", " ", full_name)

        if not full_name or len(full_name) < 3:
            continue

        last, first, middle = "", "", ""
        if "," in full_name:
            parts = full_name.split(",", 1)
            last = parts[0].strip()
            fm = parts[1].strip().split()
            first = fm[0] if fm else ""
            middle = " ".join(fm[1:]) if len(fm) > 1 else ""
        else:
            last = full_name

        dob_m = _DOB_RE.search(block_text)
        dob = dob_m.group(1) if dob_m else ""
        bd_m = _BD_RE.search(block_text)
        booking_date = bd_m.group(1) if bd_m else ""
        status_m = _STATUS_RE.search(block_text)
        status = (status_m.group(1).strip() if status_m else "In Custody") or "In Custody"
        if any(k in status.lower() for k in ("jail", "custody", "confined", "held")):
            status = "In Custody"
        addr_m = _ADDR_RE.search(block_text)
        address = addr_m.group(1).strip() if addr_m else ""
        bond_m = _BOND_RE.search(block_text)
        bond_amount = "0"
        if bond_m:
            try:
                bond_amount = str(float(bond_m.group(1).replace(",", "")))
            except ValueError:
                bond_amount = "0"

        charges_list: List[str] = []
        total_bond = 0.0
        row = img.find_parent("tr")
        if row:
            sibling = row.find_next_sibling("tr")
            while sibling:
                if sibling.find("img", src=_BOOKNO_RE):
                    break
                table_el = sibling.find("table", class_=re.compile(r"JailViewCharges", re.I))
                if not table_el:
                    table_el = sibling.find("table", id=re.compile(r"JailViewCharges", re.I))
                if table_el:
                    for chg_row in table_el.find_all("tr"):
                        cells = chg_row.find_all("td")
                        if len(cells) < 4:
                            continue
                        texts = [c.get_text(strip=True) for c in cells]
                        # Skip header-ish rows
                        if any(t.upper() in ("STATUTE", "CHARGE", "DEGREE") for t in texts):
                            continue
                        statute = texts[1] if len(texts) > 1 else ""
                        desc = texts[3] if len(texts) > 3 else (texts[2] if len(texts) > 2 else "")
                        if statute or desc:
                            charges_list.append(
                                f"{statute} - {desc}".strip(" -") if statute and desc else (statute or desc)
                            )
                        if len(texts) >= 7:
                            raw = texts[6].replace("$", "").replace(",", "").strip()
                            try:
                                total_bond += float(raw or 0)
                            except ValueError:
                                pass
                    break
                sibling = sibling.find_next_sibling("tr")

        if total_bond > 0:
            bond_amount = str(total_bond)

        detail_url = ""
        if detail_base and src:
            if src.startswith("http"):
                detail_url = src
            else:
                detail_url = f"{detail_base.rstrip('/')}/{src.lstrip('/')}"

        records.append(
            ArrestRecord(
                County=county,
                State=state,
                Booking_Number=booking_num,
                Full_Name=full_name,
                First_Name=first,
                Middle_Name=middle,
                Last_Name=last,
                DOB=dob,
                Booking_Date=booking_date,
                Status=status,
                Facility=facility or f"{county} County Jail",
                Charges=" | ".join(charges_list) if charges_list else "",
                Bond_Amount=bond_amount,
                Address=address,
                Detail_URL=detail_url,
            )
        )

    return records
