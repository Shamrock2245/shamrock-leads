"""Verify and consume a paperwork handoff token.

The portal team owns minting (``POST /api/portal/handoff-token``). This
module is the paperwork side: it checks the signature, the claims, and
the signer, then consumes the ``jti`` once. It does not expose that mint
route.

Wire format (what mint must return as ``token``)::

    v1.<base64url(canonical-json)>.<base64url(HMAC-SHA256(secret, json-bytes))>

Canonical JSON is UTF-8, compact separators, and sorted keys.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from dashboard.deps import get_collection

logger = logging.getLogger(__name__)

AUDIENCE = "paperwork"
TTL_SECONDS = 15 * 60
SKEW_SECONDS = 30
FLAG_ENV = "PAPERWORK_HANDOFF_ENABLED"
SECRET_ENV = "PAPERWORK_HANDOFF_SECRET"
JTI_COLLECTION = "paperwork_handoff_jti"
SESSION_HOURS = 2

ROLES = frozenset({"indemnitor", "coindemnitor", "defendant"})
MODES = frozenset({"", "id-upload", "add-cosigner", "inperson", "ipad", "kiosk"})

_DEAD_STATUSES = frozenset({"voided", "cancelled", "canceled", "declined"})
_UNVERIFIED_STATUSES = frozenset({"unverified"})
_VOID_PACKET_STATUSES = frozenset({"voided", "cancelled", "canceled"})

_ROLE_PREFIX = {
    "indemnitor": "indemnitor_",
    "coindemnitor": "coindemnitor_",
    "defendant": "defendant_",
}
_ROLE_EXTRA_KEYS = {
    "indemnitor": frozenset({"IndemnitorName", "IndName", "FullName"}),
    "coindemnitor": frozenset({"Ind2Name"}),
    "defendant": frozenset({"DefName", "DefFirstName", "DefLastName"}),
}

_PIN_FALLBACK = {"ok": False, "fallback": "pin"}


class HandoffRejected(Exception):
    """The token does not grant a session. ``reason`` is for the audit only."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def handoff_enabled() -> bool:
    """Default off. Unset, empty, and any value other than the on-set are off."""
    return (os.getenv(FLAG_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_role(raw: Optional[str]) -> str:
    value = str(raw or "").strip().lower().replace("-", "").replace("_", "")
    if value in {"defendant", "def", "inmate"}:
        return "defendant"
    if value in {"coindemnitor", "co"}:
        return "coindemnitor"
    if value in {"indemnitor", "ind"}:
        return "indemnitor"
    return ""


def normalize_mode(raw: Optional[str]) -> str:
    return str(raw or "").strip().lower()


def route_for_mode(mode: str) -> str:
    if mode == "add-cosigner":
        return "add-cosigner"
    if mode == "id-upload":
        return "id-upload"
    if mode in {"inperson", "ipad", "kiosk"}:
        return "inperson"
    return "case"


def _secret() -> bytes:
    value = (os.getenv(SECRET_ENV) or "").strip()
    if not value:
        raise HandoffRejected("secret_missing")
    return value.encode("utf-8")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def encode_handoff_token(payload: Mapping[str, Any]) -> str:
    """Sign an already-built claim set. Mint should call this."""
    body = _canonical(payload)
    sig = hmac.new(_secret(), body, hashlib.sha256).digest()
    return "v1." + _b64url(body) + "." + _b64url(sig)


def sign_handoff_token(
    *,
    case_id: str,
    role: str,
    person_id: str,
    mode: str = "",
    ttl_seconds: int = TTL_SECONDS,
    now: Optional[int] = None,
    jti: Optional[str] = None,
) -> str:
    """Build the token the mint endpoint returns. Not an HTTP route."""
    issued = int(time.time() if now is None else now)
    payload = {
        "aud": AUDIENCE,
        "case_id": str(case_id or "").strip(),
        "role": normalize_role(role),
        "person_id": str(person_id or "").strip(),
        "mode": normalize_mode(mode),
        "iat": issued,
        "exp": issued + int(ttl_seconds),
        "jti": jti or uuid.uuid4().hex,
    }
    return encode_handoff_token(payload)


def _decode(token: str, now: int) -> dict:
    raw = str(token or "").strip()
    if not raw or len(raw) > 4000:
        raise HandoffRejected("malformed")
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != "v1" or not parts[1] or not parts[2]:
        raise HandoffRejected("malformed")
    try:
        body = _b64url_decode(parts[1])
        sig = _b64url_decode(parts[2])
    except Exception:
        raise HandoffRejected("malformed")
    try:
        expected = hmac.new(_secret(), body, hashlib.sha256).digest()
    except HandoffRejected:
        raise
    if len(sig) != len(expected) or not hmac.compare_digest(sig, expected):
        raise HandoffRejected("bad_signature")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HandoffRejected("malformed")
    if not isinstance(payload, dict):
        raise HandoffRejected("malformed")
    if payload.get("aud") != AUDIENCE:
        raise HandoffRejected("audience")
    try:
        iat = int(payload.get("iat"))
        exp = int(payload.get("exp"))
    except (TypeError, ValueError):
        raise HandoffRejected("malformed")
    if exp <= now or iat > now + SKEW_SECONDS or exp <= iat or exp - iat > TTL_SECONDS:
        raise HandoffRejected("expired")
    role = normalize_role(str(payload.get("role") or ""))
    mode = normalize_mode(payload.get("mode"))
    case_id = str(payload.get("case_id") or "").strip()
    person_id = str(payload.get("person_id") or "").strip()
    jti = str(payload.get("jti") or "").strip()
    if role not in ROLES or mode not in MODES or not case_id or not person_id or not jti:
        raise HandoffRejected("malformed")
    if mode == "add-cosigner" and role != "coindemnitor":
        raise HandoffRejected("mismatch")
    return {
        "aud": AUDIENCE,
        "case_id": case_id,
        "role": role,
        "person_id": person_id,
        "mode": mode,
        "iat": iat,
        "exp": exp,
        "jti": jti,
    }


def _hints_agree(claims: Mapping[str, Any], hint_role: str, hint_case: str) -> None:
    """Query role and case are untrusted. Empty hints are not a disagreement.

    Mode is not compared. The token's mode is authoritative.
    """
    role_hint = str(hint_role or "").strip()
    if role_hint and normalize_role(role_hint) != claims["role"]:
        raise HandoffRejected("mismatch")
    case_hint = str(hint_case or "").strip()
    if case_hint and case_hint != claims["case_id"]:
        raise HandoffRejected("mismatch")


def _status(value: Any) -> str:
    return str(value or "").strip().lower()


def _packet_voided(doc: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(doc, Mapping):
        return False
    if doc.get("voided") is True:
        return True
    return _status(doc.get("status")) in _VOID_PACKET_STATUSES


def _person_ids(doc: Mapping[str, Any], role: str) -> set[str]:
    found: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text:
            found.add(text)

    if role == "defendant":
        add(doc.get("defendant_id"))
        add(doc.get("Defendant_ID"))
        nested = doc.get("defendant")
        if isinstance(nested, Mapping):
            add(nested.get("defendant_id") or nested.get("Defendant_ID") or nested.get("id"))
        return found
    if role == "indemnitor":
        add(doc.get("indemnitor_id"))
        add(doc.get("Indemnitor_ID"))
        nested = doc.get("indemnitor")
        if isinstance(nested, Mapping):
            add(nested.get("indemnitor_id") or nested.get("Indemnitor_ID") or nested.get("id"))
        return found
    add(doc.get("co_indemnitor_id"))
    add(doc.get("coindemnitor_id"))
    add(doc.get("Co_Indemnitor_ID"))
    for key in ("coindemnitor", "co_indemnitor", "cosigner", "co_signer"):
        nested = doc.get(key)
        if isinstance(nested, Mapping):
            add(nested.get("indemnitor_id") or nested.get("Indemnitor_ID") or nested.get("id"))
    people = doc.get("indemnitors")
    if isinstance(people, list):
        for person in people:
            if not isinstance(person, Mapping):
                continue
            raw = str(person.get("role") or "").lower().replace("-", "").replace("_", "")
            if raw in {"coindemnitor", "co"}:
                add(person.get("indemnitor_id") or person.get("Indemnitor_ID") or person.get("id"))
    return found


def _identity_problem(doc: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(doc, Mapping):
        return "unverified"
    if doc.get("voided") is True:
        return "voided_signer"
    status = _status(doc.get("status"))
    if status in _DEAD_STATUSES:
        return "voided_signer"
    if status in _UNVERIFIED_STATUSES or doc.get("identity_verified") is False:
        return "unverified"
    return ""


def _scan_record_ok(value: Any) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    if value.get("success") is False or value.get("passed") is False:
        return False
    if value.get("success") is True or value.get("passed") is True:
        return True
    return bool(str(value.get("full_name") or "").strip())


def person_has_id_scan(person: Optional[Mapping[str, Any]], session: Optional[Mapping[str, Any]] = None) -> bool:
    doc = person if isinstance(person, Mapping) else {}
    if doc.get("id_verified") is True:
        return True
    if _scan_record_ok(doc.get("id_scan")):
        return True
    history = doc.get("id_scan_history")
    if isinstance(history, list) and any(_scan_record_ok(item) for item in history):
        return True
    if isinstance(session, Mapping) and _scan_record_ok(session.get("id_extracted")):
        return True
    return False


async def _find_all(name: str, query: dict) -> list[dict]:
    cursor = get_collection(name).find(query)
    docs = await cursor.to_list(length=20)
    return [doc for doc in docs if isinstance(doc, dict)]


async def _load_bonds(case_id: str) -> list[dict]:
    query = {"$or": [{"bond_case_id": case_id}, {"Bond_Case_ID": case_id}]}
    found: list[dict] = []
    for name in ("bond_cases", "active_bonds"):
        rows = await _find_all(name, query)
        if len(rows) > 1:
            raise HandoffRejected("mismatch")
        found.extend(rows)
    if not found:
        raise HandoffRejected("mismatch")
    return found


def _require_bound(bonds: list[dict], role: str, person_id: str) -> None:
    for bond in bonds:
        ids = _person_ids(bond, role)
        if ids != {person_id}:
            raise HandoffRejected("mismatch")


async def _load_person(role: str, person_id: str) -> dict:
    if role == "defendant":
        name = "defendants"
        query = {"$or": [{"defendant_id": person_id}, {"Defendant_ID": person_id}]}
    else:
        name = "indemnitors"
        query = {"$or": [{"indemnitor_id": person_id}, {"Indemnitor_ID": person_id}]}
    doc = await get_collection(name).find_one(query)
    if not isinstance(doc, dict):
        raise HandoffRejected("unverified")
    problem = _identity_problem(doc)
    if problem:
        raise HandoffRejected(problem)
    return doc


async def _load_packet(case_id: str) -> Optional[dict]:
    rows = await _find_all(
        "paperwork_packets",
        {"$or": [{"bond_case_id": case_id}, {"Bond_Case_ID": case_id}]},
    )
    open_rows = [row for row in rows if not _packet_voided(row)]
    open_rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return open_rows[0] if open_rows else None


def _submitter(packet: Optional[Mapping[str, Any]], role: str) -> Optional[dict]:
    if not isinstance(packet, Mapping):
        return None
    from dashboard.services.paperwork_signers import normalize_role

    want = normalize_role(role)
    for item in packet.get("docuseal_submitters") or []:
        if isinstance(item, Mapping) and normalize_role(item.get("role")) == want:
            return dict(item)
    return None


def _require_submitter_alive(packet: Optional[Mapping[str, Any]], role: str) -> None:
    item = _submitter(packet, role)
    if not item:
        return
    problem = _identity_problem(item)
    if problem:
        raise HandoffRejected(problem)


def signing_link_for(packet: Optional[Mapping[str, Any]], role: str, *, has_scan: bool) -> str:
    """DocuSeal URL only when this signer is alive and an ID scan is on file.

    A missing scan, a voided packet, or a dead submitter yields an empty
    string. No pay link is built here.
    """
    if not has_scan or _packet_voided(packet):
        return ""
    item = _submitter(packet, role)
    if not item or _identity_problem(item):
        return ""
    url = item.get("sign_url") or ""
    if isinstance(url, str) and url.startswith("https://"):
        return url
    slug = item.get("slug")
    if isinstance(slug, str) and slug and not slug.startswith("http"):
        return "https://sign.shamrockbailbonds.biz/s/" + slug
    return ""


async def _check_subject(claims: Mapping[str, Any]) -> dict:
    bonds = await _load_bonds(claims["case_id"])
    _require_bound(bonds, claims["role"], claims["person_id"])
    person = await _load_person(claims["role"], claims["person_id"])
    packet = await _load_packet(claims["case_id"])
    _require_submitter_alive(packet, claims["role"])
    return {"person": person, "packet": packet, "bonds": bonds}


async def _consume_jti(claims: Mapping[str, Any], now_iso: str) -> None:
    """First insert wins. A pre-existing ``_id`` is a replay and grants nothing.

    Mint must not write this row. The paperwork app records it at consume time.
    """
    jti = claims["jti"]
    prior = await get_collection(JTI_COLLECTION).find_one_and_update(
        {"_id": jti},
        {"$setOnInsert": {
            "_id": jti,
            "consumed_at": now_iso,
            "case_id": claims["case_id"],
            "role": claims["role"],
            "person_id": claims["person_id"],
            "mode": claims["mode"],
            "exp": claims["exp"],
            "aud": AUDIENCE,
        }},
        upsert=True,
        return_document=False,
    )
    if prior is not None:
        raise HandoffRejected("reused")


async def _audit(action: str, *, reason: str = "", claims: Optional[Mapping[str, Any]] = None) -> None:
    claims = claims or {}
    now_iso = datetime.now(timezone.utc).isoformat()
    case_id = str(claims.get("case_id") or "")
    doc = {
        "entity_type": "paperwork_handoff",
        "entity_id": case_id or "rejected",
        "action": action,
        "actor": "paperwork_portal",
        "actor_type": "system",
        "agent": "paperwork_handoff",
        "timestamp": now_iso,
        "old_state": "",
        "new_state": "consumed" if action == "handoff_consumed" else "rejected",
        "reason": reason,
        "details": {
            "reason": reason,
            "jti": str(claims.get("jti") or ""),
            "role": str(claims.get("role") or ""),
            "case_id": case_id,
            "person_id": str(claims.get("person_id") or ""),
            "mode": str(claims.get("mode") or ""),
            "aud": AUDIENCE if claims else "",
        },
    }
    await get_collection("audit_events").insert_one(doc)


async def _open_session(claims: Mapping[str, Any], now: datetime) -> str:
    import secrets

    token = "PORTAL-HT-" + secrets.token_urlsafe(24)
    await get_collection("portal_pins").insert_one({
        "session_token": token,
        "verified": True,
        "auth_method": "handoff",
        "role": claims["role"],
        "bond_case_id": claims["case_id"],
        "person_id": claims["person_id"],
        "mode": claims["mode"],
        "handoff_jti": claims["jti"],
        "verified_at": now.isoformat(),
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=SESSION_HOURS)).isoformat(),
    })
    return token


def _id_upload_delegate():
    """The other branch's handler, if that module is already importable."""
    import importlib

    try:
        module = importlib.import_module("dashboard.services.paperwork_id_upload")
    except ImportError:
        return None
    handler = getattr(module, "handle_id_upload_mode", None)
    return handler if callable(handler) else None


async def _maybe_delegate_id_upload(claims: Mapping[str, Any]) -> bool:
    if claims.get("mode") != "id-upload":
        return False
    handler = _id_upload_delegate()
    if handler is None:
        return False
    try:
        result = handler({
            "case_id": claims["case_id"],
            "role": claims["role"],
            "person_id": claims["person_id"],
            "mode": "id-upload",
        })
        if hasattr(result, "__await__"):
            await result
    except Exception:
        logger.info("[handoff] id-upload handler failed jti=%s", claims.get("jti") or "-")
        return False
    return True


def _success_body(*, session_token: str, claims: Mapping[str, Any], packet: Optional[dict], person: dict, delegated: bool) -> dict:
    has_scan = person_has_id_scan(person)
    link = signing_link_for(packet, claims["role"], has_scan=has_scan)
    body = {
        "ok": True,
        "fallback": None,
        "session_token": session_token,
        "role": claims["role"],
        "case_id": claims["case_id"],
        "mode": claims["mode"],
        "route": route_for_mode(claims["mode"]),
        "packet_id": str((packet or {}).get("packet_id") or ""),
        "signing_link": link,
        "id_upload_delegated": delegated,
    }
    return body


async def consume_handoff(
    *,
    token: str,
    hint_role: str = "",
    hint_case: str = "",
    hint_mode: str = "",
    now: Optional[int] = None,
) -> dict:
    """Validate ``ht`` and open a portal session, or return the PIN fallback.

    ``hint_mode`` is accepted and ignored. The signed mode wins. A failure
    never includes a session, a signing URL, or a pay link.
    """
    del hint_mode  # token mode is authoritative; the query mode is not a check
    if not handoff_enabled():
        return dict(_PIN_FALLBACK)
    if not str(token or "").strip():
        return dict(_PIN_FALLBACK)

    issued = int(time.time() if now is None else now)
    claims: Optional[dict] = None
    try:
        claims = _decode(token, issued)
        _hints_agree(claims, hint_role, hint_case)
        subject = await _check_subject(claims)
        now_dt = datetime.fromtimestamp(issued, tz=timezone.utc)
        await _consume_jti(claims, now_dt.isoformat())
        try:
            await _audit("handoff_consumed", reason="ok", claims=claims)
        except Exception:
            logger.info("[handoff] audit failed jti=%s", claims["jti"])
            return dict(_PIN_FALLBACK)
        try:
            session_token = await _open_session(claims, now_dt)
        except Exception:
            logger.info("[handoff] session failed jti=%s", claims["jti"])
            return dict(_PIN_FALLBACK)
        delegated = await _maybe_delegate_id_upload(claims)
        logger.info(
            "[handoff] consumed jti=%s role=%s mode=%s",
            claims["jti"],
            claims["role"],
            claims["mode"] or "-",
        )
        return _success_body(
            session_token=session_token,
            claims=claims,
            packet=subject.get("packet"),
            person=subject.get("person") or {},
            delegated=delegated,
        )
    except HandoffRejected as exc:
        logger.info("[handoff] rejected reason=%s", exc.reason)
        try:
            await _audit("handoff_rejected", reason=exc.reason, claims=claims)
        except Exception:
            logger.info("[handoff] reject audit failed reason=%s", exc.reason)
        return dict(_PIN_FALLBACK)


def filter_role_fields(role: str, fields: Optional[Mapping[str, Any]]) -> dict:
    """Keep only this role's keys so a co-indemnitor cannot write the primary."""
    role = normalize_role(role)
    prefix = _ROLE_PREFIX.get(role, "")
    extra = _ROLE_EXTRA_KEYS.get(role, frozenset())
    clean: dict[str, str] = {}
    if not prefix or not isinstance(fields, Mapping):
        return clean
    for key, value in fields.items():
        if not (str(key).startswith(prefix) or key in extra):
            continue
        text = str(value or "").strip()
        if text:
            clean[str(key)] = text[:240]
    return clean


def _scan_history_row(scan: Mapping[str, Any], *, role: str) -> dict:
    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "success": scan.get("success") is True,
        "passed": scan.get("passed"),
        "source": "paperwork_handoff",
    }


async def append_signer_scan(*, role: str, person_id: str, scan: Mapping[str, Any]) -> None:
    """Insert a scan. An existing ``id_scan`` is left in place."""
    role = normalize_role(role)
    person_id = str(person_id or "").strip()
    if role not in ROLES or not person_id or not isinstance(scan, Mapping):
        return
    if role == "defendant":
        collection = "defendants"
        query = {"$or": [{"defendant_id": person_id}, {"Defendant_ID": person_id}]}
    else:
        collection = "indemnitors"
        query = {"$or": [{"indemnitor_id": person_id}, {"Indemnitor_ID": person_id}]}
    row = _scan_history_row(scan, role=role)
    col = get_collection(collection)
    await col.update_one(query, {"$push": {"id_scan_history": row}})
    await col.update_one(
        {"$and": [query, {"id_scan": {"$exists": False}}]},
        {"$set": {"id_scan": row}},
    )


async def _person_by_id(role: str, person_id: str) -> Optional[dict]:
    if not person_id:
        return None
    if role == "defendant":
        doc = await get_collection("defendants").find_one(
            {"$or": [{"defendant_id": person_id}, {"Defendant_ID": person_id}]}
        )
    else:
        doc = await get_collection("indemnitors").find_one(
            {"$or": [{"indemnitor_id": person_id}, {"Indemnitor_ID": person_id}]}
        )
    return doc if isinstance(doc, dict) else None


async def save_handoff_fields(*, session: Mapping[str, Any], fields: Mapping[str, Any]) -> dict:
    """Store this role's confirmed fields without touching another signer.

    Does not void a packet, change a POA number, delete a co-signer link,
    or call DocuSeal unless this signer already has an ID scan. A
    co-indemnitor write sets ``coindemnitor_fields`` only.
    """
    role = normalize_role(session.get("role"))
    case_id = str(session.get("bond_case_id") or "").strip()
    person_id = str(session.get("person_id") or "").strip()
    kept = filter_role_fields(role, fields)
    packet = await _load_packet(case_id) if case_id else None
    person = await _person_by_id(role, person_id)
    pushed = False
    packet_id = str((packet or {}).get("packet_id") or "")
    if packet and kept and packet_id:
        update: dict[str, Any] = {}
        if role == "coindemnitor":
            current = packet.get("coindemnitor_fields") if isinstance(packet.get("coindemnitor_fields"), dict) else {}
            update["coindemnitor_fields"] = {**current, **kept}
            update["coindemnitor_fields_updated_at"] = datetime.now(timezone.utc).isoformat()
        else:
            current = packet.get("client_fields") if isinstance(packet.get("client_fields"), dict) else {}
            update["client_fields"] = {**current, **kept}
            update["client_fields_updated_at"] = datetime.now(timezone.utc).isoformat()
        await get_collection("paperwork_packets").update_one(
            {"packet_id": packet_id},
            {"$set": update},
        )
        item = _submitter(packet, role)
        if (
            item
            and item.get("id") is not None
            and person_has_id_scan(person, session)
            and not _identity_problem(item)
        ):
            values = dict(kept)
            if role == "coindemnitor":
                for key, value in kept.items():
                    if key.startswith("coindemnitor_"):
                        values["indemnitor_" + key[len("coindemnitor_"):]] = value
            try:
                from dashboard.services.docuseal_service import DocuSealService
                from dashboard.services.docuseal_signing_ux import submission_fields_from_values

                await DocuSealService().update_submitter(
                    item["id"],
                    values=values,
                    fields=submission_fields_from_values(values),
                    send_email=False,
                    send_sms=False,
                    metadata={"handoff": True, "party_role": role, "packet_id": packet_id},
                )
                pushed = True
            except Exception:
                logger.info("[handoff] docuseal update skipped role=%s", role)
    return {
        "role": role,
        "fields": kept,
        "packet_id": str((packet or {}).get("packet_id") or ""),
        "signing_link": signing_link_for(packet, role, has_scan=person_has_id_scan(person, session)),
        "pushed_to_docuseal": pushed,
        "message": "",
    }
