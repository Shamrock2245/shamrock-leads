"""Map one stored collateral payment method onto one Palmetto receipt box.

The receipt line is Cash, Check, Money Order, Credit Card, or Other/See Item 1.
The bond stores a single method. Write Bond saves ``down_payment_method``
through ``normalize_method`` (lowercase, stripped). Record Bond and the
active-bond create path store the select value without that fold, so case
and surrounding whitespace can still be on the document. This mapper folds
those variants. It does not invent a method.

Vault rows (``collateral_items``) store ``item_type`` (Cash Deposit, Jewelry,
and the rest). That is not a payment method and does not check a box.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

# Placement data_source values. DocuSeal names for the four live template 5
# boxes stay the *_checkbox names; Other is not on that export.
SOURCE_CASH = "collateral_cash"
SOURCE_CHECK = "collateral_check"
SOURCE_MONEY_ORDER = "collateral_money_order"
SOURCE_CREDIT_CARD = "collateral_credit_card"
SOURCE_OTHER = "collateral_other"

PAYMENT_SOURCES = (
    SOURCE_CASH,
    SOURCE_CHECK,
    SOURCE_MONEY_ORDER,
    SOURCE_CREDIT_CARD,
    SOURCE_OTHER,
)

# Live template 5 names, plus Other (no live checkbox on the export).
DOCUSEAL_NAMES = {
    SOURCE_CASH: "collateral_cash_checkbox",
    SOURCE_CHECK: "collateral_check_checkbox",
    SOURCE_MONEY_ORDER: "collateral_money_order_checkbox",
    SOURCE_CREDIT_CARD: "collateral_credit_card_checkbox",
    SOURCE_OTHER: "collateral_other",
}
DOCUSEAL_CHECKBOX_NAMES = frozenset(DOCUSEAL_NAMES.values())

CHECKED = "Yes"

# Tokens the desk actually stores, after case/whitespace fold.
# cash / check / card / swipesimple: Write Bond and Record Bond selects.
# financing / other: Record Bond select. normalize_method also keeps them.
# cheque / swipe / swipe simple / card present: normalize_method aliases.
#   Write Bond stores the canonical token. Record Bond keeps the raw alias.
# money order / credit card: the receipt boxes. Not a select option today.
#   Mapped only when that token is what was stored.
_EXACT = {
    "cash": SOURCE_CASH,
    "check": SOURCE_CHECK,
    "cheque": SOURCE_CHECK,
    "card": SOURCE_CREDIT_CARD,
    "credit card": SOURCE_CREDIT_CARD,
    "money order": SOURCE_MONEY_ORDER,
    "swipesimple": SOURCE_OTHER,
    "swipe": SOURCE_OTHER,
    "swipe simple": SOURCE_OTHER,
    "card present": SOURCE_OTHER,
    "financing": SOURCE_OTHER,
    "other": SOURCE_OTHER,
}

_MISSING = "missing"
_UNKNOWN = "unknown"


def _token(raw: Any) -> str:
    """Folded token, or the sentinels missing / unknown. Never a default method."""
    if raw is None:
        return _MISSING
    if isinstance(raw, (bool, dict, list, tuple, set, int, float)):
        return _UNKNOWN
    text = str(raw).replace("_", " ").replace("-", " ")
    text = " ".join(text.split()).lower()
    if not text:
        return _MISSING
    if text not in _EXACT:
        return _UNKNOWN
    return text


def _votes(data: Mapping[str, Any]) -> list:
    """One entry per stored method that is present. Missing fields are skipped."""
    found = []
    if not isinstance(data, Mapping):
        return found
    for key in ("down_payment_method", "payment_method"):
        if key not in data:
            continue
        found.append(_token(data.get(key)))
    items = data.get("collateral_items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, Mapping):
                found.append(_UNKNOWN)
                continue
            for key in ("down_payment_method", "payment_method"):
                if key not in item:
                    continue
                found.append(_token(item.get(key)))
    return found


def payment_source(data: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The one data_source to check, or None when nothing may be checked.

    Unknown, missing, and disagreeing methods all return None. Two collateral
    rows with different payment methods also return None: the receipt has one
    set of boxes and the bond model stores one method, so more than one box
    is not checked.
    """
    votes = [vote for vote in _votes(data or {}) if vote != _MISSING]
    if not votes:
        return None
    if any(vote == _UNKNOWN for vote in votes):
        return None
    sources = {_EXACT[vote] for vote in votes}
    if len(sources) != 1:
        return None
    return sources.pop()


def checkbox_context(data: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """``Yes`` on the one matching source. The other four are empty strings."""
    source = payment_source(data)
    return {name: (CHECKED if name == source else "") for name in PAYMENT_SOURCES}


def docuseal_checkbox_values(data: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Prefill values keyed by the DocuSeal field name.

    The checked box is boolean True. The others are empty so a submission
    omits them. An empty string is not a guess.
    """
    source = payment_source(data)
    values: Dict[str, Any] = {}
    for data_source, field_name in DOCUSEAL_NAMES.items():
        values[field_name] = True if data_source == source else ""
    return values
