"""Florida counties Shamrock will underwrite, plus First Appearance watch extras."""
from __future__ import annotations

import os
import re
from typing import Optional

# Counties the agency will write paper in (OSI primary in Florida).
WRITE_ELIGIBLE_COUNTIES = [
    "Lee",
    "Charlotte",
    "Collier",
    "Sarasota",
    "Manatee",
    "Palm Beach",
]

WATCH_ALSO = [
    "Hendry",
    "DeSoto",
]

_COUNTY_STATE_RE = re.compile(r"^(.+?)\s*\(([A-Za-z]{2})\)$")
_COUNTY_SUFFIX_RE = re.compile(r"\s+county$", re.IGNORECASE)


def parse_county_state(county: str, state: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Return (bare county, 2-letter state). Parenthetical state wins when present."""
    raw = str(county or "").strip()
    paren_state = None
    m = _COUNTY_STATE_RE.match(raw)
    if m:
        raw = m.group(1).strip()
        paren_state = m.group(2).upper()
    bare = _COUNTY_SUFFIX_RE.sub("", raw).strip()
    explicit = str(state).strip().upper() if state else None
    if explicit == "":
        explicit = None
    return bare, paren_state or explicit


def fa_watch_counties() -> list[str]:
    seen = set()
    out: list[str] = []
    for name in WRITE_ELIGIBLE_COUNTIES + WATCH_ALSO:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def resolve_fa_watch_counties(
    env_watch_counties: Optional[str] = None,
    stored_targets: Optional[list[str]] = None,
) -> Optional[list[str]]:
    """County filter for FirstAppearanceWatcher.

    Returns None when unrestricted (WATCH_COUNTIES=*).
    A non-empty WATCH_COUNTIES env value is an ops override.
    Otherwise union stored automation_config targets with fa_watch_counties().
    """
    raw = os.getenv("WATCH_COUNTIES") if env_watch_counties is None else env_watch_counties
    if raw is not None and str(raw).strip() != "":
        stripped = str(raw).strip()
        if stripped == "*":
            return None
        return [c.strip() for c in stripped.split(",") if c.strip()]

    merged: list[str] = []
    seen: set[str] = set()
    for name in list(stored_targets or []) + fa_watch_counties():
        key = str(name).casefold().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(str(name).strip())
    return merged


def fa_query_county_values(counties: Optional[list[str]] = None) -> list[str]:
    """Mongo ``county`` $in values: bare, ``County``, and ``(FL)`` variants."""
    names = counties if counties is not None else fa_watch_counties()
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        bare, st = parse_county_state(str(raw), None)
        if not bare:
            continue
        variants = [bare, f"{bare} (FL)", f"{bare} County", f"{bare} County (FL)"]
        if st and st != "FL":
            variants.append(f"{bare} ({st})")
        for v in variants:
            key = v.casefold()
            if key not in seen:
                seen.add(key)
                out.append(v)
    return out


def is_write_eligible(county: str, state: Optional[str] = "FL") -> bool:
    """True only for an explicit Florida write-book county.

    Fail closed when state is missing/blank or not FL. A parenthetical
    ``County (ST)`` label wins over the ``state`` argument; conflicting
    identities are not eligible.
    """
    if not county:
        return False
    raw = str(county).strip()
    paren_state = None
    m = _COUNTY_STATE_RE.match(raw)
    if m:
        raw = m.group(1).strip()
        paren_state = m.group(2).upper()
    bare = _COUNTY_SUFFIX_RE.sub("", raw).strip()
    if not bare:
        return False
    explicit = str(state).strip().upper() if state else None
    if explicit == "":
        explicit = None
    if paren_state and explicit and paren_state != explicit:
        return False
    resolved = paren_state or explicit
    if resolved != "FL":
        return False
    return any(bare.casefold() == c.casefold() for c in WRITE_ELIGIBLE_COUNTIES)
