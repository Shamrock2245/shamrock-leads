"""Map one stored collateral payment method onto one Palmetto receipt box.

Collateral is not the premium. The receipt reads ``collateral_payment_method``
on the collateral vault record (and the same key when those rows are copied
onto a packet). ``down_payment_method`` and ``payment_method`` are premium
facts and never check a box.

Staff record one of: cash, check, money order, credit card, other.
Alias folding is only for reading a stored token. It does not invent a
method, and it does not accept those aliases on the write API.

Vault ``item_type`` (Cash Deposit, Jewelry, and the rest) is not a payment
method. Disagreeing rows leave every box blank. The receipt has one row
of boxes.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

# Placement data_source values. DocuSeal names for the four live template 5
# boxes stay the *_checkbox names. Other is not on that export.
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

# Live template 5 names. Other is not on that export; template 6 names it cr_other.
DOCUSEAL_NAMES = {
    SOURCE_CASH: "collateral_cash_checkbox",
    SOURCE_CHECK: "collateral_check_checkbox",
    SOURCE_MONEY_ORDER: "collateral_money_order_checkbox",
    SOURCE_CREDIT_CARD: "collateral_credit_card_checkbox",
    SOURCE_OTHER: "collateral_other",
}
OTHER_BOX_FIELD_NAMES = ("cr_other", "collateral_other")
DOCUSEAL_CHECKBOX_NAMES = frozenset(DOCUSEAL_NAMES.values()) | frozenset(
    OTHER_BOX_FIELD_NAMES
)
COLLATERAL_SUBMIT_NAMES = frozenset(DOCUSEAL_CHECKBOX_NAMES)

CHECKED = "Yes"

# Staff select. The write API accepts only these, after case and whitespace fold.
STAFF_METHODS = (
    "cash",
    "check",
    "money order",
    "credit card",
    "other",
)
STAFF_METHOD_TOKENS = frozenset(STAFF_METHODS)

INVALID_METHOD_MESSAGE = (
    "collateral_payment_method must be cash, check, money order, credit card, or other"
)

OTHER_BOX_MISSING_CODE = "collateral_other_box_missing"
OTHER_BOX_MISSING_MESSAGE = (
    "Collateral payment method is Other. This template has no Other checkbox, "
    "so no Other value was sent."
)

# Stored tokens, after strip, lower, and folding _ / - to spaces.
# swipesimple / swipe / card present are card rails. financing is Other.
# Unknown tokens are not in this table.
_EXACT = {
    "cash": SOURCE_CASH,
    "check": SOURCE_CHECK,
    "cheque": SOURCE_CHECK,
    "card": SOURCE_CREDIT_CARD,
    "credit card": SOURCE_CREDIT_CARD,
    "money order": SOURCE_MONEY_ORDER,
    "swipesimple": SOURCE_CREDIT_CARD,
    "swipe": SOURCE_CREDIT_CARD,
    "swipe simple": SOURCE_CREDIT_CARD,
    "card present": SOURCE_CREDIT_CARD,
    "financing": SOURCE_OTHER,
    "other": SOURCE_OTHER,
}

_STAFF_LABEL = {
    SOURCE_CASH: "cash",
    SOURCE_CHECK: "check",
    SOURCE_MONEY_ORDER: "money order",
    SOURCE_CREDIT_CARD: "credit card",
    SOURCE_OTHER: "other",
}

_MISSING = "missing"
_UNKNOWN = "unknown"

_METHOD_KEY = "collateral_payment_method"


class InvalidCollateralPaymentMethod(ValueError):
    """Staff sent a payment method outside the vault select."""


def _fold_text(raw: Any) -> Optional[str]:
    """Folded token, or None when the value is blank or not text.

    None means blank. A non-text value is rejected by the writer and is
    unknown to the mapper.
    """
    if raw is None:
        return None
    if isinstance(raw, (bool, dict, list, tuple, set, int, float)):
        return _UNKNOWN
    text = str(raw).replace("_", " ").replace("-", " ")
    text = " ".join(text.split()).lower()
    if not text:
        return None
    return text


def require_staff_method(raw: Any) -> str:
    """Canonical staff token. Blank and unknown values are rejected."""
    text = _fold_text(raw)
    if text is None or text == _UNKNOWN or text not in STAFF_METHOD_TOKENS:
        raise InvalidCollateralPaymentMethod(INVALID_METHOD_MESSAGE)
    return text


def _token(raw: Any) -> str:
    """Folded known token, or the sentinels missing / unknown."""
    text = _fold_text(raw)
    if text is None:
        return _MISSING
    if text == _UNKNOWN or text not in _EXACT:
        return _UNKNOWN
    return text


def _votes(data: Mapping[str, Any]) -> list:
    """One entry per stored collateral method that is present.

    Premium ``down_payment_method`` / ``payment_method`` are not read.
    """
    found = []
    if not isinstance(data, Mapping):
        return found
    if _METHOD_KEY in data:
        found.append(_token(data.get(_METHOD_KEY)))
    items = data.get("collateral_items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, Mapping):
                found.append(_UNKNOWN)
                continue
            if _METHOD_KEY not in item:
                continue
            found.append(_token(item.get(_METHOD_KEY)))
    return found


def payment_source(data: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The one data_source to check, or None when nothing may be checked.

    Unknown, missing, and disagreeing methods all return None.
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


def context_from_collateral_rows(rows: Optional[Sequence[Any]]) -> Dict[str, Any]:
    """Packet facts from vault rows. Blank when the rows do not agree."""
    clean = [row for row in (rows or []) if isinstance(row, Mapping)]
    out: Dict[str, Any] = {}
    if not clean:
        return out
    out["collateral_items"] = [dict(row) for row in clean]
    source = payment_source({"collateral_items": clean})
    if not source:
        return out
    out[_METHOD_KEY] = _STAFF_LABEL[source]
    if source == SOURCE_OTHER:
        descriptions = []
        for row in clean:
            text = str(row.get("collateral_other_description") or "").strip()
            if text:
                descriptions.append(text)
        if len(set(descriptions)) == 1:
            out["collateral_other_description"] = descriptions[0]
    return out


def checkbox_context(data: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """``Yes`` on the one matching source. The other four are empty strings."""
    source = payment_source(data)
    return {name: (CHECKED if name == source else "") for name in PAYMENT_SOURCES}


def docuseal_checkbox_values(data: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Prefill values keyed by the DocuSeal field name.

    The checked box is boolean True. The others are empty so a submission
    omits them. Other uses ``collateral_other`` here; the submission builder
    renames or drops that name once the target template is known.
    """
    source = payment_source(data)
    values: Dict[str, Any] = {}
    for data_source, field_name in DOCUSEAL_NAMES.items():
        values[field_name] = True if data_source == source else ""
    return values


def collateral_prefill_for_template(
    values: Optional[Mapping[str, Any]],
    template_field_names: Optional[Iterable[str]],
    source: Optional[str],
) -> tuple:
    """Collateral checkbox values that this template can actually hold.

    A name the template does not list is removed. ``other`` on a template
    with no Other box produces a staff warning and sends no Other value.
    Template 6's Other box is ``cr_other``.
    """
    out = dict(values or {})
    warnings: List[Dict[str, str]] = []
    names = None
    if template_field_names is not None:
        names = {str(item) for item in template_field_names if item}
    if source == SOURCE_OTHER:
        chosen = None
        if names is not None:
            for candidate in OTHER_BOX_FIELD_NAMES:
                if candidate in names:
                    chosen = candidate
                    break
        if chosen:
            out[chosen] = True
            for candidate in OTHER_BOX_FIELD_NAMES:
                if candidate != chosen:
                    out.pop(candidate, None)
        else:
            for candidate in OTHER_BOX_FIELD_NAMES:
                out.pop(candidate, None)
            warnings.append(
                {
                    "code": OTHER_BOX_MISSING_CODE,
                    "message": OTHER_BOX_MISSING_MESSAGE,
                }
            )
    else:
        for candidate in OTHER_BOX_FIELD_NAMES:
            out.pop(candidate, None)
    if names is not None:
        for key in list(out):
            if key in COLLATERAL_SUBMIT_NAMES and key not in names:
                out.pop(key, None)
    return out, warnings
