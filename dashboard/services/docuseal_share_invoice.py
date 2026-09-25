"""
DocuSeal completion → stage-only SwipeSimple Share Invoice (bond_id resolver).

Used by BOTH DocuSeal completion paths, which do not share a handler:
  - dashboard/routers/webhooks.py  docuseal_webhook (submission.completed)
  - dashboard/services/lifecycle_automations.py  run_docuseal_poller (backup)

Each call site invokes, soft-fail and stage-only:
    maybe_issue_share_invoice_for_bond(
        bond_id, channel="imessage", dispatch=False, source=...)

This module only resolves the bond_id from the paperwork packet. It NEVER
guesses: no fallback to booking number, packet_id, or defendant name. If the
packet carries no bond id, or carries conflicting ids, the caller must skip
and log a reason code only (no PII).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

# Fields on paperwork_packets that explicitly link the packet to a BondCase.
# (packet creation stamps bond_case_id — "policy Rule 1" in routers/paperwork.py)
_BOND_ID_FIELDS = ("bond_case_id", "Bond_Case_ID", "bond_id")

SKIP_MISSING = "missing_bond_id"
SKIP_AMBIGUOUS = "ambiguous_bond_id"


def resolve_share_invoice_bond_id(
    packet: Optional[Mapping[str, Any]],
) -> Tuple[Optional[str], str]:
    """
    Return (bond_id, "ok") when the packet names exactly one bond id.

    Returns (None, "missing_bond_id") when no explicit bond id is present and
    (None, "ambiguous_bond_id") when the explicit fields disagree or hold a
    non-scalar value. Callers must skip (never guess) on None.
    """
    if not isinstance(packet, Mapping):
        return None, SKIP_MISSING

    found: set[str] = set()
    for key in _BOND_ID_FIELDS:
        raw = packet.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        if not isinstance(raw, (str, int)):
            # dict / list / ObjectId-ish blobs are not a trustworthy link
            return None, SKIP_AMBIGUOUS
        val = str(raw).strip()
        if val:
            found.add(val)

    if not found:
        return None, SKIP_MISSING
    if len(found) > 1:
        return None, SKIP_AMBIGUOUS
    return next(iter(found)), "ok"
