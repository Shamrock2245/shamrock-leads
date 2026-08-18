"""Fail-closed authorization evidence for automatic defendant signing notices.

This module intentionally stores no phone, email, address, signing URL, or free-form
staff note.  A packet receives only an opaque authorization ID and the canonical
defendant ID.  A future record correction or recipient change requires a new packet
and a new authorization; authorization evidence is never inferred from a phone field.
"""
from __future__ import annotations

from typing import Any, Dict


_REQUIRED_EVIDENCE_FIELDS = (
    "authorization_id",
    "defendant_id",
    "contact_verified_at",
    "contact_verified_by",
    "imessage_opt_in_at",
    "imessage_opt_in_by",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def authorized_defendant_delivery_snapshot(bond_data: Dict[str, Any]) -> Dict[str, str]:
    """Return a minimal immutable packet authorization snapshot, or an empty dict.

    The source must be the authoritative resolved case context.  This is deliberately
    not a request-body override and it does not prove a contact merely because a phone
    appears on a defendant, arrest, intake, or packet record.
    """
    data = bond_data if isinstance(bond_data, dict) else {}
    raw = data.get("defendant_delivery_authorization")
    if not isinstance(raw, dict):
        return {}
    if _text(raw.get("status")).lower() != "verified_opt_in":
        return {}

    expected_defendant_id = _text(data.get("defendant_id"))
    authorization_defendant_id = _text(raw.get("defendant_id"))
    if not expected_defendant_id or authorization_defendant_id != expected_defendant_id:
        return {}

    values = {key: _text(raw.get(key)) for key in _REQUIRED_EVIDENCE_FIELDS}
    if not all(values.values()):
        return {}

    return {
        "authorization_id": values["authorization_id"],
        "defendant_id": values["defendant_id"],
        "status": "verified_opt_in",
    }


def submitter_has_current_defendant_authorization(
    *, packet: Dict[str, Any], submitter: Dict[str, Any]
) -> bool:
    """Require an exact packet snapshot and explicit defendant recipient metadata."""
    packet_id = _text((packet or {}).get("packet_id"))
    defendant_id = _text((packet or {}).get("defendant_id"))
    metadata = (submitter or {}).get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    authorization = metadata.get("defendant_delivery_authorization")
    authorization = authorization if isinstance(authorization, dict) else {}

    return bool(
        packet_id
        and defendant_id
        and metadata.get("packet_id") == packet_id
        and _text(metadata.get("party_role")).lower() == "defendant"
        and _text(authorization.get("status")).lower() == "verified_opt_in"
        and _text(authorization.get("defendant_id")) == defendant_id
        and _text(authorization.get("authorization_id"))
    )


def public_authorization_status(bond_data: Dict[str, Any]) -> Dict[str, bool]:
    """Return a non-PII readiness status for the dashboard and API."""
    return {"defendant_initial_delivery_authorized": bool(authorized_defendant_delivery_snapshot(bond_data))}


__all__ = [
    "authorized_defendant_delivery_snapshot",
    "submitter_has_current_defendant_authorization",
    "public_authorization_status",
]
