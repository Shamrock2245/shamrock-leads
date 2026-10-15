"""Published-bond helpers: an unpublished bond is "", never a counted 0.

Same convention as the SmartWEB helper (``scrapers/fl_smartweb.py``):
* a charge's bond counts only when the source shows a dollar amount,
  including a real ``0`` / ``$0.00``;
* blank, NO BOND, HOLD, NONE, N/A or other text is unknown (``None``);
* the booking total is the sum only when every charge has a published
  amount; any unknown charge (or no charges) makes the total ``""``.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

_NOT_AMOUNT = ("NOBOND", "NONE", "N/A", "HOLD")


def charge_bond_amount(text: object) -> Optional[float]:
    raw = str(text if text is not None else "").strip()
    if not raw:
        return None
    compact = re.sub(r"\s", "", raw.upper())
    if any(t in compact for t in _NOT_AMOUNT):
        return None
    cleaned = re.sub(r"[$,]", "", compact)
    if not re.fullmatch(r"\d+(\.\d+)?", cleaned):
        return None
    return float(cleaned)


def total_published_bond(amounts: Iterable[Optional[float]]) -> str:
    vals = list(amounts)
    if not vals or any(v is None for v in vals):
        return ""
    total = sum(vals)
    return str(int(total)) if float(total).is_integer() else f"{total:.2f}"
