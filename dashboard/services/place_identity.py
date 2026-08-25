"""County + state identity. Lee FL ≠ Lee GA ≠ Lee SC ≠ Lee NC.

Legacy Florida arrests often omit `state`. Missing state is treated as FL.
Any other state must be explicit.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Tuple

STATE_ALIASES = {
    "FLORIDA": "FL",
    "GEORGIA": "GA",
    "SOUTH CAROLINA": "SC",
    "NORTH CAROLINA": "NC",
    "TENNESSEE": "TN",
    "TEXAS": "TX",
    "ALABAMA": "AL",
    "MISSISSIPPI": "MS",
    "LOUISIANA": "LA",
    "CONNECTICUT": "CT",
    "OHIO": "OH",
}

_STATE_VALUES = {
    "FL": ["FL", "fl", "Florida", "FLORIDA"],
    "GA": ["GA", "ga", "Georgia", "GEORGIA"],
    "SC": ["SC", "sc", "South Carolina", "SOUTH CAROLINA"],
    "NC": ["NC", "nc", "North Carolina", "NORTH CAROLINA"],
    "TN": ["TN", "tn", "Tennessee", "TENNESSEE"],
    "TX": ["TX", "tx", "Texas", "TEXAS"],
    "AL": ["AL", "al", "Alabama", "ALABAMA"],
    "MS": ["MS", "ms", "Mississippi", "MISSISSIPPI"],
    "LA": ["LA", "la", "Louisiana", "LOUISIANA"],
    "CT": ["CT", "ct", "Connecticut", "CONNECTICUT"],
    "OH": ["OH", "oh", "Ohio", "OHIO"],
}


def normalize_state(value: Any) -> str:
    raw = str(value or "").strip().upper()
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return ""
    if len(raw) == 2 and raw.isalpha():
        return raw
    return STATE_ALIASES.get(raw, "")


def parse_place(county: Any = "", state: Any = "") -> Tuple[str, str]:
    """Return (bare county name, 2-letter state).

    Accepts ``Lee (FL)``, ``Lee County``, ``Lee County, FL``.
    """
    raw = str(county or "").strip()
    st = normalize_state(state)
    labeled = re.match(r"^(.+?)\s*\(([A-Za-z]{2})\)\s*$", raw)
    if labeled:
        raw = labeled.group(1).strip()
        st = st or labeled.group(2).upper()
    comma = re.match(r"^(.+?),\s*([A-Za-z]{2}|[A-Za-z ]+)$", raw)
    if comma:
        raw = comma.group(1).strip()
        st = st or normalize_state(comma.group(2))
    raw = re.sub(r"\s+", " ", raw).strip()
    raw = re.sub(r"\s+County$", "", raw, flags=re.I).strip()
    return raw, st


def place_from_doc(doc: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    doc = doc if isinstance(doc, dict) else {}
    nested = doc.get("defendant") if isinstance(doc.get("defendant"), dict) else {}
    county = (
        doc.get("county")
        or doc.get("County")
        or doc.get("defendant_county")
        or nested.get("county")
        or ""
    )
    state = (
        doc.get("state")
        or doc.get("State")
        or nested.get("state")
        or ""
    )
    return parse_place(county, state)


def places_match(
    county_a: Any,
    state_a: Any,
    county_b: Any,
    state_b: Any,
) -> bool:
    """True when both refer to the same county in the same state."""
    name_a, st_a = parse_place(county_a, state_a)
    name_b, st_b = parse_place(county_b, state_b)
    if not name_a or not name_b:
        return False
    if name_a.lower() != name_b.lower():
        return False
    if st_a and st_b:
        return st_a == st_b
    known = st_a or st_b
    if not known or known == "FL":
        return True
    return False


def is_lee_florida(county: Any = "", state: Any = "") -> bool:
    name, st = parse_place(county, state)
    if name.lower() != "lee":
        return False
    return (not st) or st == "FL"


def mongo_state_clause(
    state: str,
    state_fields: Iterable[str] = ("state", "State"),
) -> Dict[str, Any]:
    st = normalize_state(state)
    fields = [f for f in state_fields if f]
    if not st or not fields:
        return {}
    values = list(_STATE_VALUES.get(st, [st, st.lower(), st.title()]))
    if st == "FL":
        missing = []
        for field in fields:
            missing.extend([
                {field: None},
                {field: ""},
                {field: {"$exists": False}},
            ])
        present = [{field: {"$in": values}} for field in fields]
        return {"$or": present + missing}
    return {"$or": [{field: {"$in": values}} for field in fields]}


def mongo_place_clause(
    county: Any,
    state: Any = "",
    *,
    county_fields: Iterable[str] = ("county", "County"),
    state_fields: Iterable[str] = ("state", "State"),
) -> Dict[str, Any]:
    """Mongo filter for one county in one state. Empty county → {}."""
    name, st = parse_place(county, state)
    if not name:
        return {}
    county_re = {"$regex": f"^{re.escape(name)}(?:\\s+County)?$", "$options": "i"}
    county_or = [{field: county_re} for field in county_fields]
    clause: Dict[str, Any] = {"$or": county_or} if len(county_or) > 1 else county_or[0]
    state_clause = mongo_state_clause(st, state_fields=state_fields)
    if not state_clause:
        return clause
    return {"$and": [clause, state_clause]}
