"""Record identity decoupled from the printed booking number (DRAFT, phase 1).

Today the arrests collection is uniquely indexed on (state, county,
booking_number) and the dashboard routes by ``booking_number``, so a
Miami-Dade record (no public booking number) stores its internal natural key
(``md_dedupe_v2:<sha256>``) in ``booking_number`` and every display blanks it.

This module introduces ``record_key``:
  * ``record_key`` = the source booking number for every county, or the
    internal natural key where the source publishes none (Miami-Dade).
  * Unique index (state, county, record_key); booking_number keeps a
    *partial* unique index (non-empty values only).
  * Routing resolves ``record_key`` first and falls back to legacy
    ``booking_number`` URLs, so old links keep working.
  * With ``RECORD_KEY_MODE=on`` (only after the migration has run, see
    scripts/migrations/record_key_migration.py) writers upsert on record_key
    and store ``booking_number=""`` for internal-key records.

Default ``RECORD_KEY_MODE=off``: writes still key on booking_number exactly as
before, but every written doc also carries ``record_key`` (harmless, and it
lets the migration's backfill converge).
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.booking_identity import is_internal_booking_key

RECORD_KEY_FIELD = "record_key"
RECORD_KEY_INDEX = "dedup_state_county_record_key"
BOOKING_PARTIAL_INDEX = "uniq_state_county_booking_nonempty"
LEGACY_BOOKING_INDEX = "dedup_state_county_booking"


def record_key_mode() -> bool:
    """True only when ops has run the migration and set RECORD_KEY_MODE=on."""
    return (os.getenv("RECORD_KEY_MODE") or "off").strip().lower() in ("on", "1", "true", "yes")


def record_key_for(doc: Dict[str, Any]) -> str:
    """The record's identity: stored record_key, else md_dedupe, else booking_number."""
    if not isinstance(doc, dict):
        return ""
    rk = str(doc.get(RECORD_KEY_FIELD) or "").strip()
    if rk:
        return rk
    md = str(doc.get("md_dedupe") or "").strip()
    if md and is_internal_booking_key(md):
        return md
    return str(doc.get("booking_number") or "").strip()


def stored_booking_number(identity: str, internal: bool, mode_on: Optional[bool] = None) -> str:
    """booking_number to persist: "" for an internal-key record once mode is on."""
    on = record_key_mode() if mode_on is None else mode_on
    return "" if (internal and on) else identity


def arrest_ref_query(ref: str, **scope: Any) -> Dict[str, Any]:
    """Query resolving a route ref: record_key first, legacy booking_number URL second.

    A blank ref never matches (it would hit every blank-booking record).
    """
    ref = str(ref or "").strip()
    if not ref:
        return {"_id": {"$exists": False}}  # matches nothing
    q: Dict[str, Any] = {"$or": [{RECORD_KEY_FIELD: ref}, {"booking_number": ref}]}
    q.update({k: v for k, v in scope.items() if v not in (None, "")})
    return q


def route_ref(doc: Dict[str, Any]) -> str:
    """What a client should put in a URL for this record."""
    return record_key_for(doc)


def doc_filter(doc: Optional[Dict[str, Any]], ref: str) -> Dict[str, Any]:
    """Update filter for a resolved doc: its _id, else the ref query."""
    if isinstance(doc, dict) and doc.get("_id") is not None:
        return {"_id": doc["_id"]}
    return arrest_ref_query(ref)
