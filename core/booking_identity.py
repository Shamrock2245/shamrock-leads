"""Internal natural keys for counties whose source publishes no booking number.

Owner exception (Brendan, 2026-10-09 9:32 AM ET): Miami-Dade's ArcGIS jail
table has no booking, jail or case number, and its ObjectId/GlobalID are
reissued on every republish. Its rows are keyed on an internal natural key,
``md_dedupe_v2:<sha256>`` (see ``scrapers.counties.miami_dade.md_dedupe_key``).

The arrests collection is uniquely indexed on (state, county, booking_number)
and the dashboard addresses records by ``booking_number``, so the stored
``booking_number`` field must carry that key as the record's identity. It is
NOT a booking number:

* ``ArrestRecord.Booking_Number`` stays blank in the scraper.
* Only the scopes in ``INTERNAL_NATURAL_KEY_SCOPES`` may use it, and only when
  the key matches that scope's exact pattern (``internal_natural_key``). Every
  other county keeps the blank-booking guard unchanged.
* Every place that prints or hydrates a booking number (packet field map,
  DocuSeal prefill, PDF fills, API docs via ``serialize_doc``) passes the
  value through ``public_booking_number`` / ``scrub_internal_keys``, so the key
  shows as blank.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Pattern, Tuple

MD_KEY_PREFIX = "md_dedupe_v2:"
MD_INTERNAL_KEY_RE: Pattern[str] = re.compile(r"^md_dedupe_v2:[0-9a-f]{64}$")

# (STATE, county as written by the scraper) -> exact key pattern.
INTERNAL_NATURAL_KEY_SCOPES: Dict[Tuple[str, str], Pattern[str]] = {
    ("FL", "Miami-Dade"): MD_INTERNAL_KEY_RE,
}

# Any internal key, wherever it appears inside a string (v1 keys from #166 too).
_ANY_INTERNAL_KEY_RE = re.compile(r"md_dedupe_v\d+:[0-9a-f]{16,64}")


def internal_natural_key(record: Any, county: str = "", state: str = "") -> str:
    """The record's approved internal key, or "" (the blank-booking guard applies).

    Requires: blank ``Booking_Number``, an allow-listed (state, county) scope,
    and ``extra_data["md_dedupe"]`` matching that scope's exact pattern.
    """
    if str(getattr(record, "Booking_Number", "") or "").strip():
        return ""
    st = (state or getattr(record, "State", "") or "FL").strip().upper()
    co = (county or getattr(record, "County", "") or "").strip()
    pattern: Optional[Pattern[str]] = INTERNAL_NATURAL_KEY_SCOPES.get((st, co))
    if pattern is None:
        return ""
    extra = getattr(record, "extra_data", None) or {}
    key = str(extra.get("md_dedupe") or "").strip() if isinstance(extra, dict) else ""
    return key if pattern.fullmatch(key) else ""


def is_internal_booking_key(value: Any) -> bool:
    return isinstance(value, str) and bool(_ANY_INTERNAL_KEY_RE.search(value))


def public_booking_number(value: Any) -> Any:
    """The value to print as a booking number: blank for an internal key."""
    return "" if is_internal_booking_key(value) else value


def scrub_internal_keys(value: Any) -> Any:
    """Copy of ``value`` (dict/list/str) with every internal-key string blanked."""
    if isinstance(value, dict):
        return {k: scrub_internal_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_internal_keys(v) for v in value]
    if isinstance(value, tuple):
        return tuple(scrub_internal_keys(v) for v in value)
    return public_booking_number(value)
