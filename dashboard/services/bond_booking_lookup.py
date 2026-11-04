"""Alias-aware, read-only bond lookup by booking number.

``booking_number`` is canonical. The legacy ``Booking_Number`` spelling is
read as an alias only when ``booking_number`` is blank, and is never written.
Stored values padded with whitespace match through an anchored, escaped
regex (the same rule as ``cosigner_link_service._booking_query`` and
``payment_log_claims._matching_bonds``). Rows are deduped by ``_id``. More
than one distinct document in a collection fails closed with
``BondAmbiguous``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from dashboard import extensions

logger = logging.getLogger(__name__)

BOND_COLLECTIONS = ("bond_cases", "active_bonds")


class BondAmbiguous(Exception):
    """More than one distinct bond document carries this booking."""


def booking_query(booking: str) -> Dict[str, Any]:
    """Either spelling, exact or whitespace-padded. Blank booking: caller must not query."""
    padded = {"$regex": r"^\s*" + re.escape(booking) + r"\s*$"}
    return {"$or": [
        {"booking_number": booking},
        {"Booking_Number": booking},
        {"booking_number": padded},
        {"Booking_Number": padded},
    ]}


def canonical_booking(doc: Any) -> str:
    """``booking_number`` when non-blank, else the legacy ``Booking_Number``."""
    if not isinstance(doc, dict):
        return ""
    for key in ("booking_number", "Booking_Number"):
        text = str(doc.get(key) or "").strip()
        if text:
            return text
    return ""


async def _rows(cursor) -> List[dict]:
    if hasattr(cursor, "to_list"):
        rows = await cursor.to_list(length=50)
        return [r for r in rows or [] if isinstance(r, dict)]
    out: List[dict] = []
    if hasattr(cursor, "__aiter__"):
        async for row in cursor:
            if isinstance(row, dict):
                out.append(row)
    return out


def dedupe_by_id(rows: List[dict]) -> List[dict]:
    seen = set()
    out: List[dict] = []
    for doc in rows:
        key = repr(doc.get("_id")) if doc.get("_id") is not None else None
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        out.append(doc)
    return out


async def bonds_for_booking(booking: Any, collection: str, get_coll=None) -> List[dict]:
    """Distinct docs in ``collection`` whose canonical booking is ``booking``."""
    booking = str(booking or "").strip()
    if not booking:
        return []
    getter = get_coll or extensions.get_collection
    rows = await _rows(getter(collection).find(booking_query(booking)))
    rows = [r for r in rows if canonical_booking(r) == booking]
    out = dedupe_by_id(rows)
    for doc in out:
        doc["_collection"] = collection
    return out


async def resolve_bond_pair(booking: Any, get_coll=None) -> Tuple[Optional[dict], Optional[dict]]:
    """``(bond_cases doc, active_bonds doc)`` for a booking; either may be None.

    Raises ``BondAmbiguous`` when a collection holds more than one distinct
    document for the booking, or when both rows carry case ids that differ.
    """
    found: Dict[str, Optional[dict]] = {}
    for name in BOND_COLLECTIONS:
        rows = await bonds_for_booking(booking, name, get_coll)
        if len(rows) > 1:
            raise BondAmbiguous(name)
        found[name] = rows[0] if rows else None
    case, active = found["bond_cases"], found["active_bonds"]
    if case and active:
        a = str(case.get("bond_case_id") or "").strip()
        b = str(active.get("bond_case_id") or "").strip()
        if a and b and a != b:
            raise BondAmbiguous("bond_case_id_mismatch")
    return case, active
