"""Staff confirmation of a paperwork packet's surety (OSI / Palmetto / published carrier).

Used by ``POST /api/paperwork/packets/{packet_id}/confirm-surety`` (the Super
CRM packet screen picker) and by the DocuSeal push for chat-agent packets.

Rules:
  - The value goes through ``surety_registry.optional_surety``: canonical id,
    case-insensitive. Missing answers 422 ``surety_required``; unknown or
    inactive answers 422 ``surety_unsupported``. There is never an OSI default.
  - Confirming the packet's current surety is a no-op (no write, no event), so
    packets that already have one, including walk-in auto-OSI, are unchanged.
  - A change of surety is allowed while the packet is unsent, and is audited
    with the previous value. It is refused (409 ``surety_locked``) once the
    packet has a DocuSeal submission or is sent/signed/completed/voided, and
    (409 ``surety_poa_mismatch``) when the assigned POA belongs to another surety.
  - The packet update is compare-and-set on the previous ``surety_id`` so two
    staff confirming at once cannot silently overwrite each other (409
    ``surety_conflict``). History lives in ``audit_events`` (append-only);
    nothing is deleted.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LOCKED_STATUSES = {"sent", "signed", "completed", "partially_signed", "voided", "signnow_pending"}


class SuretyConfirmError(Exception):
    def __init__(self, code: str, status_code: int, message: str):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message

    def body(self) -> dict:
        return {"success": False, "error": self.code, "code": self.code, "message": self.message}


def validate_surety_choice(raw: object) -> str:
    """Canonical surety id or SuretyConfirmError (422). Never defaults."""
    from dashboard.services.surety_registry import UnsupportedSuretyError, optional_surety

    try:
        sid = optional_surety(raw)
    except UnsupportedSuretyError:
        raise SuretyConfirmError(
            "surety_unsupported", 422,
            "Unknown or inactive surety. Pick a configured surety (OSI or Palmetto).",
        )
    if not sid:
        raise SuretyConfirmError("surety_required", 422, "A surety must be confirmed before paperwork.")
    return sid


def _current_surety(packet: dict) -> str:
    from dashboard.services.surety_registry import normalize_surety

    return normalize_surety(packet.get("surety_id") or packet.get("template"))


def _is_locked(packet: dict) -> bool:
    if packet.get("voided") or packet.get("docuseal_submission_id"):
        return True
    return str(packet.get("status") or "").strip().lower() in _LOCKED_STATUSES


async def confirm_packet_surety(
    packet_id: str,
    raw_surety: object,
    *,
    actor: str,
    source: str,
    get_collection=None,
) -> dict[str, Any]:
    """Validate and persist the staff-picked surety. Returns
    ``{"surety_id", "previous_surety_id", "changed"}`` or raises SuretyConfirmError."""
    if get_collection is None:
        from dashboard.extensions import get_collection  # noqa: WPS442

    sid = validate_surety_choice(raw_surety)
    packets = get_collection("paperwork_packets")
    packet: Optional[dict] = await packets.find_one({"packet_id": packet_id}, {"_id": 0})
    if not packet:
        raise SuretyConfirmError("packet_not_found", 404, "Packet not found.")

    previous = _current_surety(packet)
    if previous == sid:
        return {"surety_id": sid, "previous_surety_id": previous or None, "changed": False}
    if _is_locked(packet):
        raise SuretyConfirmError(
            "surety_locked", 409,
            "This packet was already sent or voided; its surety cannot be changed.",
        )
    poa = str(packet.get("poa_number") or "").strip()
    if poa:
        from dashboard.services.surety_registry import surety_for_poa_prefix

        poa_surety = surety_for_poa_prefix(poa)
        if poa_surety and poa_surety != sid:
            raise SuretyConfirmError(
                "surety_poa_mismatch", 409,
                "The assigned POA belongs to a different surety. Change the POA first.",
            )

    now = datetime.now(timezone.utc)
    res = await packets.update_one(
        # Compare-and-set on the stored value (None matches an unset surety).
        {"packet_id": packet_id, "surety_id": packet.get("surety_id")},
        {"$set": {
            "surety_id": sid,
            "template": sid,
            "surety_confirmed_by": actor,
            "surety_confirmed_at": now,
            "surety_confirmed_source": source,
            "updated_at": now,
        }},
    )
    if not getattr(res, "matched_count", 0):
        raise SuretyConfirmError(
            "surety_conflict", 409, "The packet's surety changed while you were confirming. Reload and retry.",
        )
    try:
        await get_collection("audit_events").insert_one({
            "Event_ID": str(uuid.uuid4()),
            "event_type": "packet_surety_confirmed",
            "packet_id": packet_id,
            "surety_id": sid,
            "previous_surety_id": previous or None,
            "staged_by_agent": bool(packet.get("staged_by_agent")),
            "source": source,
            "actor": actor,
            "timestamp": now,
        })
    except Exception as exc:
        # The packet itself carries surety_confirmed_by / _at; log and continue.
        logger.warning("[surety-confirm] audit insert failed (%s)", type(exc).__name__)
    logger.info("[surety-confirm] packet %s surety %s -> %s by %s", packet_id, previous or "unset", sid, actor)
    return {"surety_id": sid, "previous_surety_id": previous or None, "changed": True}
