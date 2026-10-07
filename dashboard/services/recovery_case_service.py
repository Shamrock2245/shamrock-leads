"""
BailSafe recovery case share — conservative field allowlist.

Staff share a forfeiture file into ``recovery_case_shares``. Recovery sessions
read a projected card only. Indemnitor phones/emails, premiums, ledgers,
payment plans, POA numbers, and DocuSeal ids are never copied onto the card.

Single-agency Shamrock. No tenant id.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dashboard.extensions import get_collection

logger = logging.getLogger("shamrock.recovery")

DISPOSITIONS = frozenset({"contacted", "located", "surrendered"})
MAX_NOTE_CHARS = 4000
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_UPLOAD_EXT = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".webp"})

# Top-level keys a recovery response may carry. Anything else is dropped.
RECOVERY_CARD_KEYS = frozenset({
    "share_id",
    "share_status",
    "shared_at",
    "expires_at",
    "recovery_agent_id",
    "booking_number",
    "case_number",
    "court_case_number",
    "defendant_name",
    "date_of_birth",
    "county",
    "state",
    "bond_status",
    "forfeiture_order_date",
    "forfeited_at",
    "known_addresses",
    "staff_notes",
    "court_date",
    "court_location",
    "remittitur",
    "disposition",
    "disposition_at",
    "disposition_by_label",
    "notes",
    "documents",
})

REMITTITUR_KEYS = frozenset({
    "clock_known",
    "forfeiture_order_date",
    "days_elapsed",
    "deadline_60d",
    "days_left_60d",
    "deadline_180d",
    "days_left_180d",
    "motion_status",
    "urgency_level",
})

CANDIDATE_KEYS = frozenset({
    "booking_number",
    "bond_case_id",
    "defendant_name",
    "county",
    "state",
    "bond_status",
    "forfeiture_order_date",
    "court_case_number",
    "case_number",
    "already_shared",
})

# Key fragments that must never survive onto a recovery payload.
_DENY_KEY_FRAGMENTS = (
    "phone",
    "email",
    "premium",
    "ssn",
    "social_security",
    "indemnitor",
    "ledger",
    "payment",
    "docuseal",
    "poa",
    "password",
    "token",
    "accounting",
    "commission",
    "bond_amount",
    "buf",
    "mugshot",
    "raw_html",
    "charge",
    "surety",
)

_RECOVERY_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,31}$")


class RecoveryError(Exception):
    def __init__(self, code: str, status: int, message: str):
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


def upload_root() -> Path:
    """Recovery files live outside the dashboard static tree."""
    raw = (os.getenv("RECOVERY_UPLOAD_DIR") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "var" / "recovery_uploads"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _key_denied(key: str) -> bool:
    lowered = str(key or "").lower()
    return any(fragment in lowered for fragment in _DENY_KEY_FRAGMENTS)


def drop_denied_keys(value: Any) -> Any:
    """Recursive key scrub. Values are kept; denied keys are removed."""
    if isinstance(value, list):
        return [drop_denied_keys(item) for item in value]
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if _key_denied(str(key)):
                continue
            cleaned[str(key)] = drop_denied_keys(item)
        return cleaned
    return value


def _norm_recovery_id(value: Any) -> str:
    rid = re.sub(r"\s+", "", str(value or "")).upper()
    if not _RECOVERY_ID_RE.fullmatch(rid):
        return ""
    return rid


def _clean_name(value: Any) -> str:
    name = " ".join(str(value or "").split())
    if len(name) < 2 or len(name) > 80:
        return ""
    return name


def _text(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _date_text(value: Any) -> str:
    parsed = _parse_dt(value)
    if parsed:
        return parsed.date().isoformat()
    return _text(value, 40)


async def _collect(cursor, limit: int = 500) -> list[dict]:
    if hasattr(cursor, "to_list"):
        rows = await cursor.to_list(length=limit)
        return list(rows or [])
    rows = []
    async for doc in cursor:
        rows.append(doc)
        if len(rows) >= limit:
            break
    return rows


async def _audit(
    *,
    action: str,
    entity_id: str,
    actor: str,
    actor_type: str,
    details: dict | None = None,
) -> None:
    safe_details = drop_denied_keys(details or {})
    # Notes and disposition text stay on the case file, not the audit row.
    if isinstance(safe_details, dict):
        safe_details.pop("text", None)
        safe_details.pop("staff_notes", None)
    await get_collection("audit_events").insert_one({
        "event_id": str(uuid.uuid4()),
        "entity_type": "recovery_case_share",
        "entity_id": entity_id,
        "action": action,
        "details": safe_details,
        "actor": actor or "unknown",
        "actor_type": actor_type or "system",
        "event_context": "recovery",
        "timestamp": _now(),
    })


def _is_forfeiture(bond: dict) -> bool:
    status = str(bond.get("status") or "").strip().lower()
    if status == "forfeited":
        return True
    if _text(bond.get("forfeiture_order_date"), 80) or _text(bond.get("forfeited_at"), 80):
        return True
    if _parse_dt(bond.get("forfeiture_order_date")) or _parse_dt(bond.get("forfeited_at")):
        return True
    return False


async def _find_bond(booking_number: str = "", bond_case_id: str = "") -> dict | None:
    col = get_collection("active_bonds")
    booking_number = booking_number.strip()
    bond_case_id = bond_case_id.strip()
    if booking_number:
        doc = await col.find_one({"booking_number": booking_number})
        if doc:
            return doc
    if bond_case_id:
        for key in ("Bond_Case_ID", "bond_case_id"):
            doc = await col.find_one({key: bond_case_id})
            if doc:
                return doc
    return None


async def _find_arrest(booking_number: str, county: str = "") -> dict | None:
    if not booking_number:
        return None
    col = get_collection("arrests")
    if county:
        doc = await col.find_one({"booking_number": booking_number, "county": county})
        if doc:
            return doc
    return await col.find_one({"booking_number": booking_number})


def _defendant_name(bond: dict, arrest: dict | None) -> str:
    for key in ("defendant_name", "full_name"):
        found = _text(bond.get(key), 160)
        if found:
            return found
    arrest = arrest or {}
    found = _text(arrest.get("full_name"), 160)
    if found:
        return found
    first = _text(arrest.get("first_name"), 80)
    last = _text(arrest.get("last_name"), 80)
    return " ".join(part for part in (first, last) if part)


def _dob(bond: dict, arrest: dict | None) -> str:
    for key in ("defendant_dob", "date_of_birth"):
        found = _text(bond.get(key), 32)
        if found:
            return found
    arrest = arrest or {}
    for key in ("date_of_birth", "dob"):
        found = _text(arrest.get(key), 32)
        if found:
            return found
    return ""


def _known_addresses(bond: dict, arrest: dict | None) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()

    def push(label: str, line: str) -> None:
        cleaned = " ".join(str(line or "").split())
        if not cleaned:
            return
        marker = cleaned.lower()
        if marker in seen:
            return
        seen.add(marker)
        rows.append({"label": label, "line": cleaned[:240]})

    push("defendant", _text(bond.get("defendant_address"), 240))
    arrest = arrest or {}
    street = _text(arrest.get("address"), 160)
    city = _text(arrest.get("city"), 80)
    state = _text(arrest.get("state"), 40)
    zip_code = _text(arrest.get("zip_code") or arrest.get("zip"), 16)
    locality = " ".join(part for part in (state, zip_code) if part)
    arrest_line = ", ".join(part for part in (street, city, locality) if part)
    push("booking", arrest_line)

    extras = bond.get("known_addresses") or bond.get("defendant_addresses") or []
    if isinstance(extras, list):
        for item in extras:
            if isinstance(item, str):
                push("defendant", item)
            elif isinstance(item, dict):
                label = str(item.get("label") or "defendant").lower()
                if "indemnitor" in label or "cosigner" in label:
                    continue
                push("defendant", str(item.get("line") or item.get("address") or ""))
    return rows


def _remittitur_clock(bond: dict) -> dict:
    """Date clock only. Dollar amounts and percentages stay off the card."""
    order = _parse_dt(bond.get("forfeiture_order_date")) or _parse_dt(bond.get("forfeited_at"))
    motion = _text(bond.get("motion_to_vacate_status"), 40)
    if order is None:
        return {
            "clock_known": False,
            "forfeiture_order_date": "",
            "days_elapsed": None,
            "deadline_60d": "",
            "days_left_60d": None,
            "deadline_180d": "",
            "days_left_180d": None,
            "motion_status": motion,
            "urgency_level": "",
        }
    now = _now()
    days_elapsed = max(0, (now - order).days)
    days_left_60 = max(0, 60 - days_elapsed)
    days_left_180 = max(0, 180 - days_elapsed)
    if days_left_60 <= 7:
        urgency = "CRITICAL"
    elif days_left_60 <= 21:
        urgency = "HIGH"
    else:
        urgency = "MEDIUM"
    return {
        "clock_known": True,
        "forfeiture_order_date": order.date().isoformat(),
        "days_elapsed": days_elapsed,
        "deadline_60d": (order + timedelta(days=60)).date().isoformat(),
        "days_left_60d": days_left_60,
        "deadline_180d": (order + timedelta(days=180)).date().isoformat(),
        "days_left_180d": days_left_180,
        "motion_status": motion,
        "urgency_level": urgency,
    }


def project_candidate(bond: dict, *, already_shared: bool) -> dict:
    arrest_case = _text(bond.get("court_case_number"), 80)
    card = {
        "booking_number": _text(bond.get("booking_number"), 80),
        "bond_case_id": _text(bond.get("Bond_Case_ID") or bond.get("bond_case_id"), 80),
        "defendant_name": _defendant_name(bond, None),
        "county": _text(bond.get("county"), 80),
        "state": _text(bond.get("state") or bond.get("defendant_state"), 8),
        "bond_status": _text(bond.get("status"), 40),
        "forfeiture_order_date": _date_text(bond.get("forfeiture_order_date")),
        "court_case_number": arrest_case or _text(bond.get("case_number"), 80),
        "case_number": _text(bond.get("case_number"), 80),
        "already_shared": bool(already_shared),
    }
    return {key: card[key] for key in CANDIDATE_KEYS}


def project_case(
    share: dict,
    bond: dict,
    arrest: dict | None,
    notes: list[dict],
    documents: list[dict],
) -> dict:
    clock = {key: _remittitur_clock(bond)[key] for key in REMITTITUR_KEYS}
    order = _parse_dt(bond.get("forfeiture_order_date"))
    forfeited = _parse_dt(bond.get("forfeited_at"))
    card = {
        "share_id": share.get("share_id") or "",
        "share_status": share.get("status") or "",
        "shared_at": share.get("shared_at") or "",
        "expires_at": share.get("expires_at") or "",
        "recovery_agent_id": share.get("recovery_agent_id") or "",
        "booking_number": _text(bond.get("booking_number") or share.get("booking_number"), 80),
        "case_number": _text(bond.get("case_number") or (arrest or {}).get("case_number"), 80),
        "court_case_number": _text(
            bond.get("court_case_number") or bond.get("case_number") or (arrest or {}).get("case_number"),
            80,
        ),
        "defendant_name": _defendant_name(bond, arrest),
        "date_of_birth": _dob(bond, arrest),
        "county": _text(bond.get("county") or (arrest or {}).get("county") or share.get("county"), 80),
        "state": _text(bond.get("state") or (arrest or {}).get("state") or share.get("state"), 8),
        "bond_status": _text(bond.get("status"), 40),
        "forfeiture_order_date": order.date().isoformat() if order else _text(bond.get("forfeiture_order_date"), 40),
        "forfeited_at": forfeited.date().isoformat() if forfeited else _text(bond.get("forfeited_at"), 40),
        "known_addresses": _known_addresses(bond, arrest),
        "staff_notes": _text(share.get("staff_notes"), MAX_NOTE_CHARS),
        "court_date": _text(bond.get("court_date") or (arrest or {}).get("court_date"), 40),
        "court_location": _text(bond.get("court_location"), 160),
        "remittitur": clock,
        "disposition": share.get("disposition") or "",
        "disposition_at": share.get("disposition_at") or "",
        "disposition_by_label": share.get("disposition_by_label") or "",
        "notes": notes,
        "documents": documents,
    }
    sealed = {key: card[key] for key in RECOVERY_CARD_KEYS}
    return drop_denied_keys(sealed)


def _share_open(share: dict, now: datetime | None = None) -> tuple[bool, str]:
    status = str(share.get("status") or "")
    if status == "revoked":
        return False, "revoked"
    if status == "expired":
        return False, "expired"
    if status not in ("active", ""):
        return False, "inactive"
    exp = _parse_dt(share.get("expires_at"))
    if exp and exp <= (now or _now()):
        return False, "expired"
    return True, "active"


def _agent_can_see(share: dict, recovery_id: str) -> bool:
    assigned = str(share.get("recovery_agent_id") or "").strip().upper()
    if not assigned:
        return True
    return assigned == str(recovery_id or "").strip().upper()


async def authenticate_recovery_agent(recovery_id: str) -> dict | None:
    rid = _norm_recovery_id(recovery_id)
    if not rid:
        return None
    doc = await get_collection("recovery_agents").find_one({"recovery_id": rid, "is_active": True})
    if not doc:
        return None
    return {
        "recovery_id": rid,
        "display_name": _clean_name(doc.get("display_name")) or rid,
        "email": f"recovery-{rid.lower()}@agents.shamrockbailbonds.biz",
    }


async def provision_recovery_agent(recovery_id: str, display_name: str, *, actor: str) -> dict:
    rid = _norm_recovery_id(recovery_id)
    name = _clean_name(display_name)
    if not rid or not name:
        raise RecoveryError(
            "agent_invalid",
            400,
            "A recovery id (letters, numbers, hyphen) and display name are required.",
        )
    col = get_collection("recovery_agents")
    existing = await col.find_one({"recovery_id": rid})
    now = _iso(_now())
    if existing and existing.get("is_active"):
        return {
            "success": True,
            "recovery_id": rid,
            "display_name": existing.get("display_name") or name,
            "already_active": True,
        }
    if existing:
        await col.update_one(
            {"recovery_id": rid},
            {"$set": {"is_active": True, "display_name": name, "reactivated_at": now, "reactivated_by": actor}},
        )
    else:
        await col.insert_one({
            "recovery_id": rid,
            "display_name": name,
            "is_active": True,
            "created_at": now,
            "created_by": actor,
        })
    await _audit(
        action="recovery_agent_provisioned",
        entity_id=rid,
        actor=actor,
        actor_type="staff",
        details={"recovery_id": rid},
    )
    return {"success": True, "recovery_id": rid, "display_name": name, "already_active": False}


async def list_recovery_agents() -> list[dict]:
    rows = await _collect(get_collection("recovery_agents").find({}))
    out = []
    for doc in rows:
        out.append({
            "recovery_id": doc.get("recovery_id") or "",
            "display_name": doc.get("display_name") or "",
            "is_active": bool(doc.get("is_active")),
        })
    out.sort(key=lambda row: row["recovery_id"])
    return out


async def _active_share_for_booking(booking_number: str) -> dict | None:
    rows = await _collect(get_collection("recovery_case_shares").find({"booking_number": booking_number}))
    now = _now()
    for doc in rows:
        open_, _reason = _share_open(doc, now)
        if open_:
            return doc
    return None


def _expiry_from_days(raw: Any) -> str:
    if raw is None or raw == "":
        return ""
    try:
        days = int(raw)
    except (TypeError, ValueError):
        raise RecoveryError("expiry_invalid", 400, "expires_in_days must be a whole number of days.")
    if days < 1 or days > 366:
        raise RecoveryError("expiry_invalid", 400, "expires_in_days must be between 1 and 366.")
    return _iso(_now() + timedelta(days=days))


async def share_forfeiture_case(
    *,
    booking_number: str = "",
    bond_case_id: str = "",
    staff_notes: str = "",
    expires_in_days: Any = None,
    recovery_agent_id: str = "",
    actor: str,
) -> dict:
    booking_number = booking_number.strip()
    bond_case_id = bond_case_id.strip()
    if not booking_number and not bond_case_id:
        raise RecoveryError("booking_required", 400, "booking_number is required.")
    bond = await _find_bond(booking_number, bond_case_id)
    if not bond:
        raise RecoveryError("forfeiture_not_found", 404, "Forfeiture case not found.")
    if not _is_forfeiture(bond):
        raise RecoveryError("not_forfeiture", 409, "Only a forfeiture file can be shared to Recovery.")
    found_case = str(bond.get("Bond_Case_ID") or bond.get("bond_case_id") or "")
    if bond_case_id and found_case and found_case != bond_case_id:
        raise RecoveryError("identity_conflict", 409, "Booking and bond case do not match.")
    canonical_booking = _text(bond.get("booking_number"), 80)
    if not canonical_booking:
        raise RecoveryError("booking_required", 400, "The forfeiture file has no booking number.")

    assigned = ""
    if str(recovery_agent_id or "").strip():
        agent = await authenticate_recovery_agent(recovery_agent_id)
        if not agent:
            raise RecoveryError("recovery_agent_unknown", 400, "That recovery id is not active.")
        assigned = agent["recovery_id"]

    notes = _text(staff_notes, MAX_NOTE_CHARS)
    expires_at = _expiry_from_days(expires_in_days)
    existing = await _active_share_for_booking(canonical_booking)
    if existing:
        updates: dict[str, Any] = {}
        if notes and notes != (existing.get("staff_notes") or ""):
            updates["staff_notes"] = notes
        if assigned and assigned != (existing.get("recovery_agent_id") or ""):
            updates["recovery_agent_id"] = assigned
        if expires_at:
            updates["expires_at"] = expires_at
        if updates:
            await get_collection("recovery_case_shares").update_one(
                {"share_id": existing["share_id"]},
                {"$set": updates},
            )
            existing.update(updates)
            await _audit(
                action="recovery_case_share_updated",
                entity_id=existing["share_id"],
                actor=actor,
                actor_type="staff",
                details={"booking_number": canonical_booking, "share_id": existing["share_id"]},
            )
        case = await _project_share(existing)
        return {"success": True, "already_shared": True, "share_id": existing["share_id"], "case": case}

    share_id = str(uuid.uuid4())
    share = {
        "share_id": share_id,
        "booking_number": canonical_booking,
        "bond_case_id": found_case,
        "county": _text(bond.get("county"), 80),
        "state": _text(bond.get("state"), 8),
        "status": "active",
        "staff_notes": notes,
        "recovery_agent_id": assigned,
        "shared_by": actor,
        "shared_at": _iso(_now()),
        "expires_at": expires_at,
        "revoked_at": "",
        "revoked_by": "",
        "disposition": "",
        "disposition_at": "",
        "disposition_by_label": "",
    }
    await get_collection("recovery_case_shares").insert_one(share)
    await _audit(
        action="recovery_case_shared",
        entity_id=share_id,
        actor=actor,
        actor_type="staff",
        details={
            "booking_number": canonical_booking,
            "share_id": share_id,
            "recovery_agent_id": assigned,
            "expires_at": expires_at,
        },
    )
    case = await _project_share(share)
    return {"success": True, "already_shared": False, "share_id": share_id, "case": case}


async def revoke_share(share_id: str, *, actor: str) -> dict:
    share = await get_collection("recovery_case_shares").find_one({"share_id": share_id})
    if not share:
        raise RecoveryError("share_not_found", 404, "Share not found.")
    if share.get("status") == "revoked":
        return {"success": True, "share_id": share_id, "share_status": "revoked", "already_revoked": True}
    now = _iso(_now())
    await get_collection("recovery_case_shares").update_one(
        {"share_id": share_id},
        {"$set": {"status": "revoked", "revoked_at": now, "revoked_by": actor}},
    )
    await _audit(
        action="recovery_case_unshared",
        entity_id=share_id,
        actor=actor,
        actor_type="staff",
        details={"share_id": share_id, "booking_number": share.get("booking_number") or ""},
    )
    return {"success": True, "share_id": share_id, "share_status": "revoked", "already_revoked": False}


async def _mark_expired(share: dict) -> None:
    if share.get("status") == "expired":
        return
    await get_collection("recovery_case_shares").update_one(
        {"share_id": share["share_id"]},
        {"$set": {"status": "expired", "expired_at": _iso(_now())}},
    )
    await _audit(
        action="recovery_case_expired",
        entity_id=share["share_id"],
        actor="system",
        actor_type="system",
        details={"share_id": share["share_id"], "booking_number": share.get("booking_number") or ""},
    )


async def _notes_for(share_id: str) -> list[dict]:
    rows = await _collect(get_collection("recovery_case_notes").find({"share_id": share_id}))
    projected = []
    for doc in rows:
        projected.append({
            "note_id": doc.get("note_id") or "",
            "text": _text(doc.get("text"), MAX_NOTE_CHARS),
            "kind": doc.get("kind") or "note",
            "disposition": doc.get("disposition") or "",
            "actor_label": doc.get("actor_label") or "",
            "actor_role": doc.get("actor_role") or "",
            "created_at": doc.get("created_at") or "",
        })
    projected.sort(key=lambda row: row.get("created_at") or "")
    return projected


async def _documents_for(share_id: str) -> list[dict]:
    rows = await _collect(get_collection("recovery_case_documents").find({"share_id": share_id}))
    projected = []
    for doc in rows:
        projected.append({
            "doc_id": doc.get("doc_id") or "",
            "filename": _text(doc.get("filename"), 120),
            "content_type": doc.get("content_type") or "",
            "byte_size": int(doc.get("byte_size") or 0),
            "uploaded_at": doc.get("uploaded_at") or "",
            "uploaded_by_label": doc.get("uploaded_by_label") or "",
            "uploaded_by_role": doc.get("uploaded_by_role") or "",
        })
    projected.sort(key=lambda row: row.get("uploaded_at") or "")
    return projected


async def _project_share(share: dict) -> dict:
    bond = await _find_bond(share.get("booking_number") or "", share.get("bond_case_id") or "") or {}
    arrest = await _find_arrest(share.get("booking_number") or "", share.get("county") or "")
    notes = await _notes_for(share["share_id"])
    documents = await _documents_for(share["share_id"])
    if not bond:
        bond = {
            "booking_number": share.get("booking_number") or "",
            "county": share.get("county") or "",
            "state": share.get("state") or "",
            "status": "forfeited",
        }
    return project_case(share, bond, arrest, notes, documents)


async def _visible_shares(*, recovery_id: str | None, include_closed: bool) -> list[dict]:
    rows = await _collect(get_collection("recovery_case_shares").find({}))
    now = _now()
    visible = []
    for share in rows:
        if recovery_id is not None and not _agent_can_see(share, recovery_id):
            continue
        open_, reason = _share_open(share, now)
        if reason == "expired" and share.get("status") != "expired":
            await _mark_expired(share)
            share["status"] = "expired"
            open_ = False
        if not open_ and not include_closed:
            continue
        if not open_ and recovery_id is not None:
            continue
        visible.append(share)
    visible.sort(key=lambda row: row.get("shared_at") or "", reverse=True)
    return visible


async def list_cases_for_recovery(recovery_id: str) -> list[dict]:
    shares = await _visible_shares(recovery_id=recovery_id, include_closed=False)
    return [await _project_share(share) for share in shares]


async def list_cases_for_staff(*, include_closed: bool = False) -> list[dict]:
    shares = await _visible_shares(recovery_id=None, include_closed=include_closed)
    return [await _project_share(share) for share in shares]


async def list_forfeiture_candidates() -> list[dict]:
    rows = await _collect(get_collection("active_bonds").find({
        "$or": [
            {"status": "forfeited"},
            {"forfeiture_order_date": {"$nin": [None, ""]}},
            {"forfeited_at": {"$nin": [None, ""]}},
        ]
    }))
    out = []
    for bond in rows:
        if not _is_forfeiture(bond):
            continue
        booking = _text(bond.get("booking_number"), 80)
        existing = await _active_share_for_booking(booking) if booking else None
        out.append(project_candidate(bond, already_shared=bool(existing)))
    out.sort(key=lambda row: (row.get("defendant_name") or "", row.get("booking_number") or ""))
    return out[:200]


async def get_case_for_actor(
    share_id: str,
    *,
    recovery_id: str | None,
    allow_closed: bool,
) -> dict:
    share = await get_collection("recovery_case_shares").find_one({"share_id": share_id})
    if not share:
        raise RecoveryError("share_not_found", 404, "Shared case not found.")
    if recovery_id is not None and not _agent_can_see(share, recovery_id):
        raise RecoveryError("share_not_found", 404, "Shared case not found.")
    open_, reason = _share_open(share)
    if reason == "expired" and share.get("status") != "expired":
        await _mark_expired(share)
        share["status"] = "expired"
        open_ = False
    if not open_ and (recovery_id is not None or not allow_closed):
        raise RecoveryError("share_not_found", 404, "Shared case not found.")
    return await _project_share(share)


async def _require_open_share(share_id: str, *, recovery_id: str | None) -> dict:
    share = await get_collection("recovery_case_shares").find_one({"share_id": share_id})
    if not share:
        raise RecoveryError("share_not_found", 404, "Shared case not found.")
    if recovery_id is not None and not _agent_can_see(share, recovery_id):
        raise RecoveryError("share_not_found", 404, "Shared case not found.")
    open_, reason = _share_open(share)
    if reason == "expired" and share.get("status") != "expired":
        await _mark_expired(share)
    if not open_:
        raise RecoveryError("share_closed", 409, "This share is closed.")
    return share


async def add_case_note(
    share_id: str,
    text: str,
    *,
    actor: str,
    actor_label: str,
    actor_role: str,
    recovery_id: str | None,
) -> dict:
    share = await _require_open_share(share_id, recovery_id=recovery_id)
    body = _text(text, MAX_NOTE_CHARS)
    if not body:
        raise RecoveryError("note_required", 400, "A note is required.")
    note_id = str(uuid.uuid4())
    await get_collection("recovery_case_notes").insert_one({
        "note_id": note_id,
        "share_id": share_id,
        "booking_number": share.get("booking_number") or "",
        "text": body,
        "kind": "note",
        "disposition": "",
        "actor": actor,
        "actor_label": actor_label,
        "actor_role": actor_role,
        "created_at": _iso(_now()),
    })
    await _audit(
        action="recovery_note_added",
        entity_id=share_id,
        actor=actor,
        actor_type=actor_role,
        details={"share_id": share_id, "note_id": note_id, "booking_number": share.get("booking_number") or ""},
    )
    return await _project_share(share)


async def set_disposition(
    share_id: str,
    disposition: str,
    *,
    actor: str,
    actor_label: str,
    actor_role: str,
    recovery_id: str | None,
) -> dict:
    share = await _require_open_share(share_id, recovery_id=recovery_id)
    choice = str(disposition or "").strip().lower()
    if choice not in DISPOSITIONS:
        raise RecoveryError(
            "invalid_disposition",
            400,
            "Disposition must be contacted, located, or surrendered.",
        )
    now = _iso(_now())
    await get_collection("recovery_case_shares").update_one(
        {"share_id": share_id},
        {"$set": {
            "disposition": choice,
            "disposition_at": now,
            "disposition_by_label": actor_label,
        }},
    )
    share.update({
        "disposition": choice,
        "disposition_at": now,
        "disposition_by_label": actor_label,
    })
    await get_collection("recovery_case_notes").insert_one({
        "note_id": str(uuid.uuid4()),
        "share_id": share_id,
        "booking_number": share.get("booking_number") or "",
        "text": f"Marked {choice}.",
        "kind": "disposition",
        "disposition": choice,
        "actor": actor,
        "actor_label": actor_label,
        "actor_role": actor_role,
        "created_at": now,
    })
    await _audit(
        action="recovery_disposition_set",
        entity_id=share_id,
        actor=actor,
        actor_type=actor_role,
        details={
            "share_id": share_id,
            "booking_number": share.get("booking_number") or "",
            "disposition": choice,
        },
    )
    return await _project_share(share)


def _magic_ok(ext: str, data: bytes) -> bool:
    if ext == ".pdf":
        return data.startswith(b"%PDF")
    if ext in (".jpg", ".jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == ".webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    return False


def _safe_filename(name: str, ext: str) -> str:
    base = Path(str(name or "document")).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "document"
    if not base.lower().endswith(ext):
        base = f"{base}{ext}"
    return base[:120]


async def add_case_document(
    share_id: str,
    *,
    filename: str,
    data: bytes,
    actor: str,
    actor_label: str,
    actor_role: str,
    recovery_id: str | None,
) -> dict:
    share = await _require_open_share(share_id, recovery_id=recovery_id)
    ext = Path(str(filename or "")).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise RecoveryError("document_type_denied", 400, "Upload a PDF or image (JPG, PNG, WEBP).")
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise RecoveryError("document_size_denied", 400, "File must be between 1 byte and 8 MB.")
    if not _magic_ok(ext, data):
        raise RecoveryError("document_type_denied", 400, "File contents do not match the allowed type.")
    try:
        uuid.UUID(share_id)
    except ValueError:
        raise RecoveryError("share_not_found", 404, "Shared case not found.")
    doc_id = str(uuid.uuid4())
    content_type = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }[ext]
    root = upload_root().resolve()
    dest_dir = (root / share_id).resolve()
    if dest_dir.parent != root:
        raise RecoveryError("document_type_denied", 400, "Upload path rejected.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored = dest_dir / f"{doc_id}{ext}"
    stored.write_bytes(data)
    await get_collection("recovery_case_documents").insert_one({
        "doc_id": doc_id,
        "share_id": share_id,
        "booking_number": share.get("booking_number") or "",
        "filename": _safe_filename(filename, ext),
        "ext": ext,
        "content_type": content_type,
        "byte_size": len(data),
        "stored_name": stored.name,
        "uploaded_at": _iso(_now()),
        "uploaded_by": actor,
        "uploaded_by_label": actor_label,
        "uploaded_by_role": actor_role,
    })
    await _audit(
        action="recovery_document_uploaded",
        entity_id=share_id,
        actor=actor,
        actor_type=actor_role,
        details={
            "share_id": share_id,
            "doc_id": doc_id,
            "booking_number": share.get("booking_number") or "",
            "content_type": content_type,
            "byte_size": len(data),
        },
    )
    logger.info("recovery document stored share=%s doc=%s bytes=%s", share_id, doc_id, len(data))
    return await _project_share(share)


async def resolve_document(
    share_id: str,
    doc_id: str,
    *,
    recovery_id: str | None,
) -> tuple[Path, dict]:
    share = await get_collection("recovery_case_shares").find_one({"share_id": share_id})
    if not share:
        raise RecoveryError("share_not_found", 404, "Shared case not found.")
    if recovery_id is not None and not _agent_can_see(share, recovery_id):
        raise RecoveryError("share_not_found", 404, "Shared case not found.")
    open_, reason = _share_open(share)
    if reason == "expired" and share.get("status") != "expired":
        await _mark_expired(share)
        open_ = False
    if not open_ and recovery_id is not None:
        raise RecoveryError("share_not_found", 404, "Shared case not found.")
    meta = await get_collection("recovery_case_documents").find_one({
        "share_id": share_id,
        "doc_id": doc_id,
    })
    if not meta:
        raise RecoveryError("document_not_found", 404, "Document not found.")
    ext = meta.get("ext") or ""
    if ext not in ALLOWED_UPLOAD_EXT:
        raise RecoveryError("document_not_found", 404, "Document not found.")
    root = upload_root().resolve()
    path = (root / share_id / f"{doc_id}{ext}").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise RecoveryError("document_not_found", 404, "Document not found.")
    if not path.is_file():
        raise RecoveryError("document_not_found", 404, "Document not found.")
    return path, meta
