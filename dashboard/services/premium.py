"""Florida bail-bond premium (consumer/indemnitor charge).

Rule used by Record Bond / paperwork:
  * $100 minimum **per criminal charge**
  * When a charge's penal amount is above $1,000, premium is 10% of that penal
    amount (10% of $1,000 = $100, so this is ``max(100, 0.10 * penal)``)

A $500 single-charge bond is $100, not $50.
"""
from __future__ import annotations

from typing import Iterable, Optional


def statutory_premium(
    bond_amount: float,
    *,
    charge_amounts: Optional[Iterable[float]] = None,
    charge_count: int = 1,
) -> float:
    amounts = [float(a) for a in (charge_amounts or []) if a is not None]
    amounts = [a for a in amounts if a > 0]
    if amounts:
        return round(sum(max(100.0, a * 0.10) for a in amounts), 2)

    ba = float(bond_amount or 0)
    if ba <= 0:
        return 0.0
    n = max(1, int(charge_count or 1))
    return round(max(100.0 * n, ba * 0.10), 2)
