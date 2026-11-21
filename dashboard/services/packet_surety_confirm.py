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
  - The packet update is compare-and-set on the previous ``surety_id`` AND on
    the packet still being editable (not voided, no DocuSeal submission or
    status, not in a sent/signed/completed/voided status). Two staff confirming
    at once get 409 ``surety_conflict``; a send or void that lands between the
    read and the write gets 409 ``surety_locked``. The packet is never mutated
    in either case.
  - No surety change without its audit. The audit entry is ``$push``-ed onto
    the packet's ``surety_audit`` array in the same single-document update, so
    the two are atomic on any MongoDB. On a replica set / Atlas the update and
    the ``audit_events`` row also run in one multi-document transaction; if
    either fails, nothing is saved (503 ``surety_confirm_failed``). Without
    transaction support the ``audit_events`` row is a secondary copy of the
    embedded entry (same ``Event_ID``). Nothing is deleted.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Any of these means the packet left the editable (unsent) stage. The same
# set is used for the read-time check and inside the compare-and-set filter.
_LOCKED_STATUSES = (
    "sent", "delivered", "pending_signature", "partially_signed", "signed",
    "completed", "voided", "cancelled", "canceled", "signnow_pending", "filed",
)
_LOCKED_STATUS_VARIANTS = sorted({v for s in _LOCKED_STATUSES for v in (s, s.upper(), s.title())})


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
    if packet.get("voided") or packet.get("docuseal_submission_id") or packet.get("docuseal_status"):
        return True
    return str(packet.get("status") or "").strip().lower() in _LOCKED_STATUSES


def _editable_filter() -> dict:
    """Mongo predicate mirroring ``not _is_locked(packet)`` for the CAS."""
    return {
        "voided": {"$nin": [True]},
        "docuseal_submission_id": {"$in": [None, ""]},
        "docuseal_status": {"$in": [None, ""]},
        "status": {"$nin": _LOCKED_STATUS_VARIANTS},
    }


def _transaction_client(packets) -> Any:
    """The Motor client behind ``packets`` when it can run a transaction, else None."""
    database = getattr(packets, "database", None)
    client = getattr(database, "client", None)
    if client is None or not hasattr(client, "start_session"):
        return None
    return client


async def _supports_transactions(client) -> bool:
    try:
        from dashboard.routers.payment_plans import _supports_transactions as check

        return bool(await check(client))
    except Exception as exc:
        logger.warning("[surety-confirm] transaction check failed (%s)", type(exc).__name__)
        return False


def _locked_error() -> SuretyConfirmError:
    return SuretyConfirmError(
        "surety_locked", 409,
        "This packet was already sent or voided; its surety cannot be changed.",
    )


def _conflict_error() -> SuretyConfirmError:
    return SuretyConfirmError(
        "surety_conflict", 409, "The packet's surety changed while you were confirming. Reload and retry.",
    )


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
        raise _locked_error()
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
    event_id = str(uuid.uuid4())
    audit_entry = {
        "Event_ID": event_id,
        "event_type": "packet_surety_confirmed",
        "surety_id": sid,
        "previous_surety_id": previous or None,
        "staged_by_agent": bool(packet.get("staged_by_agent")),
        "source": source,
        "actor": actor,
        "timestamp": now,
    }
    # Compare-and-set: the stored surety (None matches an unset surety) and
    # the packet still being unsent / not voided at write time.
    cas_filter = {"packet_id": packet_id, "surety_id": packet.get("surety_id"), **_editable_filter()}
    update = {
        "$set": {
            "surety_id": sid,
            "template": sid,
            "surety_confirmed_by": actor,
            "surety_confirmed_at": now,
            "surety_confirmed_source": source,
            "updated_at": now,
        },
        # Same document, same write: the surety never changes without this.
        "$push": {"surety_audit": audit_entry},
    }
    audit_row = {**audit_entry, "packet_id": packet_id}
    audit_events = get_collection("audit_events")

    async def _write(session=None) -> None:
        kwargs = {"session": session} if session is not None else {}
        res = await packets.update_one(cas_filter, update, **kwargs)
        if not getattr(res, "matched_count", 0):
            raise await _cas_miss_error(packets, packet_id)
        if session is not None:
            await audit_events.insert_one(dict(audit_row), session=session)

    client = _transaction_client(packets)
    if client is not None and await _supports_transactions(client):
        started = client.start_session()
        if hasattr(started, "__await__"):
            started = await started
        try:
            async with started as session:
                await session.with_transaction(_write)
        except SuretyConfirmError:
            raise
        except Exception as exc:
            logger.warning("[surety-confirm] transaction failed (%s); nothing saved", type(exc).__name__)
            raise SuretyConfirmError(
                "surety_confirm_failed", 503, "The surety could not be confirmed. Nothing was saved. Retry.",
            ) from None
    else:
        # No multi-document transactions (standalone Mongo): the packet update
        # above already carries its audit entry atomically. audit_events gets
        # a copy with the same Event_ID.
        try:
            await _write()
        except SuretyConfirmError:
            raise
        except Exception as exc:
            logger.warning("[surety-confirm] packet update failed (%s); nothing saved", type(exc).__name__)
            raise SuretyConfirmError(
                "surety_confirm_failed", 503, "The surety could not be confirmed. Nothing was saved. Retry.",
            ) from None
        try:
            await audit_events.insert_one(dict(audit_row))
        except Exception as exc:
            logger.warning(
                "[surety-confirm] audit_events copy failed (%s); packet.surety_audit holds Event_ID %s",
                type(exc).__name__, event_id,
            )
    logger.info("[surety-confirm] packet %s surety %s -> %s by %s", packet_id, previous or "unset", sid, actor)
    return {"surety_id": sid, "previous_surety_id": previous or None, "changed": True}


async def _cas_miss_error(packets, packet_id: str) -> SuretyConfirmError:
    """Why the compare-and-set matched nothing: sent/voided wins over a race."""
    try:
        current = await packets.find_one({"packet_id": packet_id}, {"_id": 0})
    except Exception:
        current = None
    if not current:
        return SuretyConfirmError("packet_not_found", 404, "Packet not found.")
    if _is_locked(current):
        return _locked_error()
    return _conflict_error()
