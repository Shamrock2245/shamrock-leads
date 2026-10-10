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

Owner exception (Brendan, 2026-10-10): the Manatee Clerk court-records scraper
(``"Manatee Clerk"``, ``scrapers.counties.manatee_clerk``) reads court filings,
which carry a case number and an OBTS number but no booking number. Its rows are
keyed on ``mc_case_v1:<sha256>`` (sha256 of the normalised case number only; see
``scrapers.counties.manatee_clerk.mc_case_key``). The case number and OBTS are
stored in their own fields (``case_number``, ``obts_number``); ``booking_number``
never holds or prints the case number. Same rules as above: only this exact
(state, county) scope and key pattern may use the path.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Pattern, Tuple

MD_KEY_PREFIX = "md_dedupe_v2:"
MD_INTERNAL_KEY_RE: Pattern[str] = re.compile(r"^md_dedupe_v2:[0-9a-f]{64}$")

# Manatee Clerk (court filings; owner exception, Brendan 2026-10-10).
MC_KEY_PREFIX = "mc_case_v1:"
MC_INTERNAL_KEY_RE: Pattern[str] = re.compile(r"^mc_case_v1:[0-9a-f]{64}$")
MC_SCOPE_COUNTY = "Manatee Clerk"

# (STATE, county as written by the scraper) -> exact key pattern.
INTERNAL_NATURAL_KEY_SCOPES: Dict[Tuple[str, str], Pattern[str]] = {
    ("FL", "Miami-Dade"): MD_INTERNAL_KEY_RE,
    ("FL", MC_SCOPE_COUNTY): MC_INTERNAL_KEY_RE,
}

# (STATE, county) -> the ``extra_data`` field that carries the scope's key.
INTERNAL_NATURAL_KEY_FIELDS: Dict[Tuple[str, str], str] = {
    ("FL", "Miami-Dade"): "md_dedupe",
    ("FL", MC_SCOPE_COUNTY): "mc_case_key",
}

# String markers of every internal-key family (fast pre-checks before the regex).
_INTERNAL_KEY_MARKERS: Tuple[str, ...] = ("md_dedupe_v", "mc_case_v")

# Any internal key, wherever it appears inside a string (v1 keys from #166 too).
_ANY_INTERNAL_KEY_RE = re.compile(r"(?:md_dedupe_v\d+|mc_case_v\d+):[0-9a-f]{16,64}")


def _has_marker(value: str) -> bool:
    return any(m in value for m in _INTERNAL_KEY_MARKERS)


def internal_natural_key(record: Any, county: str = "", state: str = "") -> str:
    """The record's approved internal key, or "" (the blank-booking guard applies).

    Requires: blank ``Booking_Number``, an allow-listed (state, county) scope,
    and that scope's ``extra_data`` key field (``INTERNAL_NATURAL_KEY_FIELDS``:
    ``md_dedupe`` for Miami-Dade, ``mc_case_key`` for Manatee Clerk) matching
    the scope's exact pattern.
    """
    if str(getattr(record, "Booking_Number", "") or "").strip():
        return ""
    st = (state or getattr(record, "State", "") or "FL").strip().upper()
    co = (county or getattr(record, "County", "") or "").strip()
    pattern: Optional[Pattern[str]] = INTERNAL_NATURAL_KEY_SCOPES.get((st, co))
    if pattern is None:
        return ""
    field = INTERNAL_NATURAL_KEY_FIELDS.get((st, co), "")
    extra = getattr(record, "extra_data", None) or {}
    key = str(extra.get(field) or "").strip() if isinstance(extra, dict) and field else ""
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


def redact_internal_keys(value: Any) -> Any:
    """Copy of ``value`` with internal-key substrings removed from every string.

    For free text and outbound payloads (Slack/Telegram/SMS/iMessage/email,
    CSV/XLSX/Sheets rows, dashboard notifications): the rest of the text is
    kept, the key itself prints blank. A value that is only a key becomes "".
    """
    if isinstance(value, str):
        return _ANY_INTERNAL_KEY_RE.sub("", value) if _has_marker(value) else value
    if isinstance(value, dict):
        return {k: redact_internal_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_internal_keys(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_internal_keys(v) for v in value)
    return value


class _RedactingWriter:
    """csv.writer / csv.DictWriter wrapper whose rows pass redact_internal_keys."""

    def __init__(self, inner: Any):
        self._inner = inner

    def writerow(self, row: Any) -> Any:
        return self._inner.writerow(redact_internal_keys(row))

    def writerows(self, rows: Any) -> None:
        for row in rows:
            self.writerow(row)

    def __getattr__(self, name: str) -> Any:  # writeheader, dialect, fieldnames ...
        return getattr(self._inner, name)


def redacting_csv_writer(f: Any, *args: Any, **kwargs: Any) -> _RedactingWriter:
    """Drop-in for ``csv.writer`` on every export path (CSV never prints a key)."""
    import csv

    return _RedactingWriter(csv.writer(f, *args, **kwargs))


def redacting_dict_writer(f: Any, *args: Any, **kwargs: Any) -> _RedactingWriter:
    """Drop-in for ``csv.DictWriter`` on every export path."""
    import csv

    return _RedactingWriter(csv.DictWriter(f, *args, **kwargs))


def redact_workbook(wb: Any) -> Any:
    """Blank internal-key substrings in every string cell of an openpyxl workbook.

    Call right before ``wb.save(...)`` on every XLSX export.
    """
    for ws in getattr(wb, "worksheets", []):
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and _has_marker(v):
                    cell.value = redact_internal_keys(v)
        title = getattr(ws, "title", "")
        if isinstance(title, str) and _has_marker(title):
            ws.title = redact_internal_keys(title) or "Sheet"
    return wb
