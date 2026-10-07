"""Sarasota FL reopen gate: pure checks on the official current-inmate listing.

No network and not used by ``SarasotaCountyScraper.scrape()`` (which stays
fail-closed). This module records *why* Sarasota is closed in testable form so
a future reopen has to clear the same bar every other county did: a public
broad roster that publishes a source-issued booking number and a booking
date/time.

Live evidence (2026-10-07, box egress, plain HTTPS GET):
* ``https://cms.revize.com/revize/apps/sarasota/`` -> 200, "Current Inmate
  Population" dropdown with 1,081 entries ``viewInmate.php?id=<10 digits>``
  labelled ``LAST,FIRST MIDDLE - MM/DD/YYYY`` (name + date of birth).
  No booking number and no booking date/time anywhere in the listing.
* ``.../index.php`` (the URL the sheriff site links), ``viewInmate.php`` and
  ``personSearch.php`` -> 403 ``cf-mitigated: challenge`` (Cloudflare).
So the only key on the listing is an opaque per-person link id, the same
situation as Alachua (#107) and Clay. Whether that id equals a booking number
can only be shown on the CF-challenged detail page.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from typing import List, Tuple

LISTING_URL = "https://cms.revize.com/revize/apps/sarasota/"

_ENTRY_RE = re.compile(
    r'href="viewInmate\.php\?id=(\d+)\s*"[^>]*>\s*([^<]+?)\s*</a>', re.I
)
_BOOKING_LABEL_RE = re.compile(r"booking\s*(?:#|no\.?|number)", re.I)
_BOOKING_TIME_RE = re.compile(r"(?:booking|book)\s*(?:date|time)", re.I)


@dataclass(frozen=True)
class SarasotaListingVerdict:
    reopen_ok: bool
    listed: int
    unique_link_ids: int
    reasons: Tuple[str, ...] = field(default_factory=tuple)


def listing_entries(page_html: str) -> List[Tuple[str, str]]:
    """``[(link_id, label)]`` from the current-inmate dropdown."""
    return [(m.group(1), _html.unescape(m.group(2)).strip()) for m in _ENTRY_RE.finditer(page_html or "")]


def assess_listing(page_html: str) -> SarasotaListingVerdict:
    """Decide whether the listing alone satisfies the booking-safe contract."""
    entries = listing_entries(page_html)
    text = re.sub(r"<[^>]+>", " ", page_html or "")
    reasons: List[str] = []
    if not entries:
        reasons.append("no current-inmate listing entries")
    if not _BOOKING_LABEL_RE.search(text):
        reasons.append("no source booking number on the listing (link id only)")
    if not _BOOKING_TIME_RE.search(text):
        reasons.append("no booking date/time on the listing")
    ids = [e[0] for e in entries]
    if len(set(ids)) != len(ids):
        reasons.append("duplicate link ids on the listing")
    return SarasotaListingVerdict(
        reopen_ok=not reasons,
        listed=len(entries),
        unique_link_ids=len(set(ids)),
        reasons=tuple(reasons),
    )
