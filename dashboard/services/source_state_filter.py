"""Keep fail-closed source contracts out of the default lead views.

A county is ``fail_closed`` in ``SCRAPER_SOURCE_STATES`` when its public
roster has not proven a real source booking key (Sarasota, Alachua, Hardee,
St. Johns and the rest). Rows already in ``arrests`` from such a county stay
in the database; this module only builds the Mongo clause that hides them from
the default lead list, the bond-ready queue and the Write Book preset.

Staff can still see them: naming the county in the county filter, or passing
``include_fail_closed=true``, drops the clause for that request. Nothing is
deleted.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from dashboard.extensions import SCRAPER_SOURCE_STATES, parse_registered_county


def fail_closed_labels() -> tuple[str, ...]:
    """Every ``County (ST)`` label whose source contract is ``fail_closed``."""
    return tuple(sorted(
        label for label, state in SCRAPER_SOURCE_STATES.items() if state == "fail_closed"
    ))


def is_fail_closed(county: str, state: str | None = None) -> bool:
    """True when ``county`` (bare or labeled) resolves to a fail_closed label.

    A row without a state is Florida, the same rule the lead filters use.
    """
    bare, st = parse_registered_county(county or "")
    bare = re.sub(r"\s+county$", "", bare, flags=re.IGNORECASE).strip()
    st = (st or state or "FL").strip().upper()
    if st == "FLORIDA":
        st = "FL"
    target = f"{bare} ({st})".casefold()
    return any(label.casefold() == target for label in fail_closed_labels())


def _named(explicit_counties: Iterable[str]) -> tuple[set[str], set[tuple[str, str]]]:
    bare_names: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for raw in explicit_counties or ():
        raw = (raw or "").strip()
        if not raw:
            continue
        bare, st = parse_registered_county(raw)
        # "Sarasota County (FL)" from a legacy suffixed row names Sarasota.
        key = re.sub(r"\s+county$", "", bare, flags=re.IGNORECASE).strip().casefold()
        if st:
            pairs.add((key, st.upper()))
        else:
            bare_names.add(key)
    return bare_names, pairs


def fail_closed_exclusion(
    explicit_counties: Iterable[str] = (),
    *,
    include_fail_closed: bool = False,
) -> dict | None:
    """Return a ``{"$nor": [...]}`` clause hiding fail_closed counties.

    ``None`` when ``include_fail_closed`` is set or every fail_closed county
    was named explicitly. Names are grouped per state so the clause stays one
    regex per state instead of one per county.
    """
    if include_fail_closed:
        return None
    from dashboard.services.intel_population import state_clause

    bare_names, pairs = _named(explicit_counties)
    by_state: dict[str, list[str]] = defaultdict(list)
    for label in fail_closed_labels():
        bare, st = parse_registered_county(label)
        if not st:
            continue
        key = bare.casefold()
        if key in bare_names or (key, st) in pairs:
            continue
        by_state[st].append(bare)
    if not by_state:
        return None
    nor: list[dict] = []
    for st in sorted(by_state):
        names = "|".join(re.escape(n) for n in sorted(by_state[st]))
        nor.append({"$and": [
            {"county": {"$regex": f"^(?:{names})(?:\\s+County)?$", "$options": "i"}},
            state_clause(st),
        ]})
    return {"$nor": nor}


def default_write_book_counties(write_counties: Iterable[str]) -> list[str]:
    """Write Book preset counties (Florida names) minus fail_closed ones.

    ``WRITE_ELIGIBLE_COUNTIES`` itself is not changed: it still gates where
    the agency may write paper (a walk-in Sarasota bond is allowed). Only the
    preset that seeds the lead list drops counties with no proven source.
    """
    return [c for c in write_counties if not is_fail_closed(c, "FL")]
