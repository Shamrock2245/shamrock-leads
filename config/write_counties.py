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
