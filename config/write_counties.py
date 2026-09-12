"""
Counties Shamrock will underwrite and post.

Lee + Charlotte are the active write book.
Collier, Sarasota, and Manatee are already scraped and now write-eligible.
Palm Beach is the first non-SWFL write node (Lake Worth AIC / Gun Club post).
"""

# Counties the agency will write paper in (OSI primary in Florida).
WRITE_ELIGIBLE_COUNTIES = [
    "Lee",
    "Charlotte",
    "Collier",
    "Sarasota",
    "Manatee",
    "Palm Beach",
]

# Stay on First Appearance watch even if not the current growth push.
WATCH_ALSO = [
    "Hendry",
    "DeSoto",
]


def fa_watch_counties() -> list[str]:
    seen = set()
    out: list[str] = []
    for name in WRITE_ELIGIBLE_COUNTIES + WATCH_ALSO:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def is_write_eligible(county: str, state: str = "FL") -> bool:
    """Return True if county is in Shamrock's active write-eligible list."""
    if not county:
        return False
    if state and state.upper() != "FL":
        return False
    bare = county.split("(")[0].replace("County", "").strip().casefold()
    return any(bare == c.casefold() for c in WRITE_ELIGIBLE_COUNTIES)

