"""Fail-closed BlueBubbles delivery for newly finalized DocuSeal packets.

This module implements the explicit, narrow exception for first-time signing-link
notices.  It never creates a packet, never changes DocuSeal state, and never
falls back to packet-level contact fields.  A notice can be sent only when a
packet already has an active DocuSeal submission and a signer is explicitly
bound to that same packet by DocuSeal metadata and external ID.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from dashboard.services.bb_client import get_bb_client
from dashboard.services.paperwork_signers import normalize_role

logger = logging.getLogger(__name__)

AUTOMATION_KEY = "docuseal_initial_delivery"
_ALLOWED_ROLES = frozenset({"indemnitor", "coindemnitor", "defendant"})
_PENDING_PACKET_STATUSES = frozenset({"pending_signature"})


def _phone_digits(value: Any) -> str:
    """Return a 10-digit US phone value or an empty string; never log it."""
    return "".join(ch for ch in str(value or "") if ch.isdigit())[-10:]


def _packet_role(submitter: Dict[str, Any]) -> str:
    """Normalize the DocuSeal role, preferring packet-bound metadata."""
    metadata = submitter.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return normalize_role(metadata.get("party_role") or submitter.get("role"))


def _is_bound_submitter(packet_id: str, submitter: Dict[str, Any]) -> bool:
    """Require both DocuSeal packet metadata and a matching external ID."""
    metadata = submitter.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    external_id = str(submitter.get("external_id") or "")
    expected_prefix = f"{packet_id}:"
    return (
        metadata.get("packet_id") == packet_id
        and external_id.startswith(expected_prefix)
        and bool(submitter.get("sign_url") or submitter.get("slug"))
    )


def _message_template_for_role(config: Dict[str, Any], role: str) -> str:
    """Return the role-specific approved template, or an empty string."""
    if role == "defendant":
        return str(config.get("defendant_message_template") or "").strip()
    return str(config.get("indemnitor_message_template") or "").strip()


def _render_message(template: str, signing_link: str) -> str:
    """Render the only supported placeholder; invalid templates fail closed."""
    if not template or "{signing_link}" not in template:
        return ""
    return template.replace("{signing_link}", signing_link)


def _eligible_submitters(packet: Dict[str, Any], config: Dict[str, Any]) -> Iterable[Dict[str, str]]:
    """Yield only explicitly packet-bound pending DocuSeal signers.

    Indemnitors and co-indemnitors are included by default.  Defendants require
    an explicit configuration opt-in and their own separately approved copy.
    """
    packet_id = str(packet.get("packet_id") or "")
    if not packet_id:
        return []

    include_defendant = bool(config.get("include_defendant", False))
    seen: set[tuple[str, str]] = set()
    rows: List[Dict[str, str]] = []
    for raw in packet.get("docuseal_submitters") or []:
        if not isinstance(raw, dict) or not _is_bound_submitter(packet_id, raw):
            continue
        role = _packet_role(raw)
        if role not in _ALLOWED_ROLES:
            continue
        if role == "defendant" and not include_defendant:
            continue
        phone = _phone_digits(raw.get("phone"))
        sign_url = str(raw.get("sign_url") or "").strip()
        if not phone or not sign_url:
            continue
        # A template may contain only one co-indemnitor role.  Phone+role is a
        # stable dispatch key and keeps this send idempotent per packet request.
        key = (role, phone)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"role": role, "phone": phone, "signing_link": sign_url})
    return rows


def _packet_is_eligible(packet: Dict[str, Any]) -> bool:
    """Reject any non-live, unsigned, unbound, or voided packet state."""
    if not packet or packet.get("voided"):
        return False
    if packet.get("status") not in _PENDING_PACKET_STATUSES:
        return False
    if not packet.get("docuseal_submission_id"):
        return False
    if str(packet.get("docuseal_status") or "").lower() != "sent":
        return False
    return True


async def deliver_initial_docuseal_links(
    *,
    packet: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Dispatch approved first-notice messages for one finalized packet.

    The caller persists the returned non-PII state on the packet and writes the
    matching immutable audit event.  A BlueBubbles error records a failed
    result; it is deliberately not placed into a generic retry queue because a
    later void or signer correction must never cause a stale signing link to be
    sent automatically.
    """
    now = datetime.now(timezone.utc)
    outcome: Dict[str, Any] = {
        "automation": AUTOMATION_KEY,
        "state": "blocked",
        "attempted_at": now,
        "recipients": [],
    }

    if not bool((config or {}).get("enabled", False)):
        outcome["reason"] = "disabled"
        return outcome
    if not _packet_is_eligible(packet):
        outcome["reason"] = "packet_not_eligible"
        return outcome

    candidates = list(_eligible_submitters(packet, config or {}))
    if not candidates:
        outcome["reason"] = "no_bound_recipients"
        return outcome

    dispatched = 0
    for candidate in candidates:
        role = candidate["role"]
        template = _message_template_for_role(config or {}, role)
        message = _render_message(template, candidate["signing_link"])
        if not message:
            outcome["recipients"].append({"role": role, "state": "blocked", "reason": "approved_template_required"})
            continue

        client = get_bb_client(candidate["phone"])
        if not client:
            outcome["recipients"].append({"role": role, "state": "failed", "reason": "bluebubbles_unavailable"})
            continue

        try:
            result = await client.send_text(f"iMessage;-;{candidate['phone']}", message)
        except Exception:
            logger.exception("[docuseal_initial_delivery] BlueBubbles send exception for packet %s role=%s", packet.get("packet_id"), role)
            result = {"success": False}

        if bool((result or {}).get("success")):
            dispatched += 1
            outcome["recipients"].append({"role": role, "state": "sent", "channel": "imessage"})
        else:
            outcome["recipients"].append({"role": role, "state": "failed", "reason": "bluebubbles_send_failed"})

    outcome["state"] = "sent" if dispatched else "blocked"
    outcome["sent_count"] = dispatched
    return outcome
