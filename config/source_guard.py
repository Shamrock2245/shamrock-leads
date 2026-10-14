"""One fail-closed guard for every path that contacts a county source.

A county whose source contract is ``fail_closed`` (Health
``SCRAPER_SOURCE_STATES``) or whose scraper sets
``SOURCE_CONTRACT_VALIDATED = False`` must not be contacted by any path. That
covers scheduled runs and also every refetch, recheck, ingest, detail enrich
and dashboard manual trigger. Each of those paths calls
:func:`fail_closed_reason` before it makes a request and stops when it returns
a reason.

The guard looks for the county in three places:
  * the scraper instance or class (``SOURCE_CONTRACT_VALIDATED`` and its label),
  * an explicit county / state pair (``"Charlotte", "FL"`` or ``"Charlotte (FL)"``),
  * the host of a detail or source URL (:data:`SOURCE_HOST_LABELS`).

If the Health map can't be read (``dashboard.extensions`` import or lookup
fails), the county is treated as fail_closed when ``unknown_closed`` is true,
which is the default. That matches ``core.pending_bond_recheck``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

FAIL_CLOSED = "fail_closed"
SOURCE_CONTRACT_UNVALIDATED = "source_contract_unvalidated"
HEALTH_UNREADABLE = "source_state_unreadable"

# Host (or parent domain) of a county source -> registered county label.
# Includes the Revize roster hosts for Charlotte and Manatee, which the
# sheriff sites only iframe.
SOURCE_HOST_LABELS = {
    "sheriffleefl.org": "Lee (FL)",
    "ccso.org": "Charlotte (FL)",
    "inmates.charlottecountyfl.revize.com": "Charlotte (FL)",
    "colliersheriff.org": "Collier (FL)",
    "hendryso.com": "Hendry (FL)",
    "desotosheriff.com": "DeSoto (FL)",
    "manateesheriff.com": "Manatee (FL)",
    "manatee-sheriff.revize.com": "Manatee (FL)",
    # Clerk court records: its own scope (owner exception 2026-10-10), not the jail.
    "manateeclerk.com": "Manatee Clerk (FL)",
    "sarasotasheriff.org": "Sarasota (FL)",
    "hillsboroughcounty.org": "Hillsborough (FL)",
    "pcsoweb.com": "Pinellas (FL)",
    "inmatelookup.mcso.org": "Marion (FL)",
    "pbso.org": "Palm Beach (FL)",
}


class SourceFailClosed(RuntimeError):
    """Raised by :func:`assert_source_allowed` for a fail_closed county."""


def county_label(county: Any, state: Optional[str] = None) -> str:
    """``"Charlotte (FL)"`` from ``"Charlotte"``, ``"Charlotte (FL)"`` or
    ``"Charlotte County", "fl"``. The state defaults to FL. Returns "" when no
    county is given."""
    from config.write_counties import parse_county_state

    bare, st = parse_county_state(str(county or ""), state)
    if not bare:
        return ""
    return f"{bare} ({(st or 'FL').upper()})"


def label_for_url(url: Any) -> str:
    """Registered county label for a source URL's host, or ""."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = (urlsplit(raw).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    while host:
        if host in SOURCE_HOST_LABELS:
            return SOURCE_HOST_LABELS[host]
        if "." not in host:
            break
        host = host.split(".", 1)[1]
    return ""


def _scraper_label(scraper: Any) -> str:
    label = getattr(scraper, "county_label", None)
    if isinstance(label, str) and label:
        return label
    return county_label(getattr(scraper, "county", ""), getattr(scraper, "state", None))


def _health_state(label: str) -> Optional[str]:
    """Health state for a label, or None when it can't be read."""
    try:
        from dashboard.extensions import scraper_source_state

        return scraper_source_state(label)
    except Exception as exc:  # pragma: no cover - exercised via monkeypatch
        logger.warning("source_guard: Health state unavailable for %s (%s)", label, type(exc).__name__)
        return None


def fail_closed_reason(
    county: Any = None,
    state: Optional[str] = None,
    *,
    scraper: Any = None,
    url: Any = None,
    unknown_closed: bool = True,
) -> Optional[str]:
    """None when the source may be contacted, else a short reason code.

    Reason codes: ``fail_closed`` (Health), ``source_contract_unvalidated``
    (``SOURCE_CONTRACT_VALIDATED = False`` on the scraper), and
    ``source_state_unreadable`` (Health unreadable and ``unknown_closed``).
    Every county found (scraper, county/state, URL host) is checked; any one
    of them being fail_closed closes the path.
    """
    labels = []
    if scraper is not None:
        labels.append(_scraper_label(scraper))
    if county:
        labels.append(county_label(county, state))
    if url:
        labels.append(label_for_url(url))
    labels = [lb for i, lb in enumerate(labels) if lb and lb not in labels[:i]]

    unreadable = False
    for label in labels:
        st = _health_state(label)
        if st is None:
            unreadable = True
        elif st == FAIL_CLOSED:
            return FAIL_CLOSED
    if scraper is not None and not bool(getattr(scraper, "SOURCE_CONTRACT_VALIDATED", True)):
        return SOURCE_CONTRACT_UNVALIDATED
    if unreadable and unknown_closed:
        return HEALTH_UNREADABLE
    return None


def is_fail_closed(*args: Any, **kwargs: Any) -> bool:
    return fail_closed_reason(*args, **kwargs) is not None


def assert_source_allowed(*args: Any, **kwargs: Any) -> None:
    """Raise :class:`SourceFailClosed` when :func:`fail_closed_reason` gives a reason."""
    reason = fail_closed_reason(*args, **kwargs)
    if reason:
        raise SourceFailClosed(reason)
