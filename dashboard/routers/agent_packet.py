"""
ShamrockLeads — Chat Agent Packet Staging API
=============================================
Allows authorized AI chat agents (Grok, Antigravity, Custom GPT Actions) to
stage a bond paperwork packet in Super CRM from:
  1. Defendant booking URL (auto-parsed via url_ingest_service for verbatim charges & bond)
  2. Indemnitor contact/PII fields
  3. Optional base64 ID photo (auto-parsed via IDScannerService)

Safety & Compliance:
  - Never issues a DocuSeal submission or sends client messages directly.
  - Stages an intake + paperwork_packet (`status="staged"`) and returns a
    1-click Super CRM URL (`/?tab=paperwork&packet_id=PKT-...`) so staff can
    assign the POA, edit any fields, and hit Send inside Super CRM.
  - Protected by machine auth (`X-API-Key` / `Authorization: Bearer`) or staff session.
  - Rejects private/loopback/metadata URLs to prevent SSRF: the host is resolved
    and every address must be public, and every fetch hop (redirects included)
    is re-checked (``public_url_guard``).
  - Never writes the ``arrests`` collection. Arrest records come only from
    source scrapers; agent-supplied booking facts stay on the intake/packet.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import ipaddress
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from dashboard.auth.pin_middleware import (
    bearer_token,
    get_session_from_request,
    is_machine_auth_valid,
    machine_key_name,
)
from dashboard.extensions import get_collection
from dashboard.services.id_scanner_service import IDScannerService
from dashboard.services.public_url_guard import check_public_url
from dashboard.services.url_ingest_service import ingest_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent-packet"])

# Largest accepted base64 ID photo (characters). ~12 MB of image bytes.
MAX_ID_IMAGE_B64_CHARS = 16_000_000


class AgentIndemnitorInput(BaseModel):
    full_name: Optional[str] = Field(None, description="Indemnitor full legal name")
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = Field(None, description="Indemnitor mobile phone")
    email: Optional[str] = Field(None, description="Indemnitor email address")
    dob: Optional[str] = Field(None, description="Date of birth (YYYY-MM-DD or MM/DD/YYYY)")
    dl_number: Optional[str] = Field(None, description="Driver's license / state ID number")
    dl_state: Optional[str] = Field("FL", description="2-letter issuing state")
    address: Optional[str] = Field(None, description="Street address")
    city: Optional[str] = None
    state: Optional[str] = "FL"
    zip: Optional[str] = None
    relationship: Optional[str] = Field(None, description="Relationship to defendant (e.g. Mother, Spouse)")


class AgentDefendantInput(BaseModel):
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    booking_number: Optional[str] = None
    county: Optional[str] = None
    state: Optional[str] = "FL"
    dob: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    bond_amount: Optional[float] = None
    charges: Optional[Any] = None
    case_number: Optional[str] = None


class AgentStagePacketRequest(BaseModel):
    booking_url: Optional[str] = Field(
        None,
        description="Official county jail booking URL containing defendant & bond details",
    )
    indemnitor: AgentIndemnitorInput = Field(default_factory=AgentIndemnitorInput)
    defendant: AgentDefendantInput = Field(default_factory=AgentDefendantInput)
    id_image_base64: Optional[str] = Field(
        None,
        description="Optional base64 or data-URI photo of the indemnitor's front ID",
    )
    id_back_image_base64: Optional[str] = Field(
        None,
        description="Optional base64 or data-URI photo of the indemnitor's back ID",
    )
    surety: Optional[str] = Field(
        None,
        description="Surety code ('osi' or 'palmetto'). When omitted the packet is staged with no "
        "surety and staff must confirm one in Super CRM (never defaults to OSI).",
    )
    notes: Optional[str] = Field(None, description="Optional notes from the chat agent or bondsman")
    idempotency_key: Optional[str] = Field(
        None,
        description="Optional retry key (same as the Idempotency-Key header). A repeat with the "
        "same key and payload returns the original ids; a different payload returns 409.",
    )


class AgentParseBookingRequest(BaseModel):
    booking_url: str = Field(..., description="County jail booking URL to parse")
    county: Optional[str] = None
    state: Optional[str] = "FL"


def _caller_scope(request: Request) -> Optional[str]:
    """Authenticated caller id used to scope idempotency keys, or None (unauthorized).

    Machine callers are scoped by which configured key they used (env name, never
    the value); staff by tenant + email from the session.
    """
    if is_machine_auth_valid(request):
        provided = (
            request.headers.get("X-API-Key")
            or request.headers.get("X-Api-Key")
            or request.headers.get("X-Internal-Token")
            or request.query_params.get("api_key")
            or ""
        )
        return f"machine:{machine_key_name(provided) or 'key'}"
    bearer_key = machine_key_name(bearer_token(request))
    if bearer_key:
        return f"machine:{bearer_key}"
    sess = get_session_from_request(request)
    if sess:
        tenant = str(sess.get("tenant_id") or "").strip()
        who = str(sess.get("email") or sess.get("agent_name") or sess.get("role") or "staff").strip().lower()
        return f"staff:{tenant}:{who}"
    return None


def _is_authorized(request: Request) -> bool:
    return _caller_scope(request) is not None


_MAX_IDEMPOTENCY_KEY_LEN = 200


def _idempotency_key(request: Request, body: "AgentStagePacketRequest") -> tuple[Optional[str], Optional[str]]:
    """(key, error). Header ``Idempotency-Key`` or body ``idempotency_key``; both must agree."""
    header = (request.headers.get("Idempotency-Key") or "").strip()
    field = (body.idempotency_key or "").strip()
    if header and field and header != field:
        return None, "Idempotency-Key header and idempotency_key field differ"
    key = header or field
    if not key:
        return None, None
    if len(key) > _MAX_IDEMPOTENCY_KEY_LEN or not key.isprintable():
        return None, "Idempotency key must be 1-200 printable characters"
    return key, None


def _payload_fingerprint(body: "AgentStagePacketRequest") -> str:
    payload = body.model_dump(exclude={"idempotency_key"})
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _scoped_key_hash(scope: str, key: str) -> str:
    return hashlib.sha256(f"agent-stage-packet|{scope}|{key}".encode("utf-8")).hexdigest()


# A ``pending`` claim older than this is treated as abandoned and may be retaken.
IDEMPOTENCY_PENDING_STALE_S = 120
SURETY_UNCONFIRMED_WARNING = (
    "surety_unconfirmed: no surety was supplied; staff must confirm OSI or Palmetto in Super CRM."
)
IDEMPOTENCY_RETRY_AFTER_S = 5


def _conflict_response():
    return JSONResponse(
        {
            "success": False,
            "error": "Idempotency key was already used with a different payload.",
            "code": "idempotency_key_conflict",
        },
        status_code=409,
    )


def _in_progress_response():
    return JSONResponse(
        {
            "success": False,
            "error": "A request with this idempotency key is still in progress. Retry shortly.",
            "code": "idempotency_in_progress",
        },
        status_code=409,
        headers={"Retry-After": str(IDEMPOTENCY_RETRY_AFTER_S)},
    )


def _replay_response(intake: dict, *, recovered: bool = False) -> dict:
    packet_id = intake.get("paperwork_packet_id")
    out = {
        "success": True,
        "status": "staged_ready_for_staff",
        "idempotent_replay": True,
        "packet_id": packet_id,
        "intake_id": intake.get("intake_id"),
        "super_crm_url": f"{_super_crm_base_url()}/?tab=paperwork&packet_id={packet_id}",
        "surety": intake.get("agent_surety"),
        "surety_confirmed": bool(intake.get("agent_surety")),
        "warnings": [] if intake.get("agent_surety") else [SURETY_UNCONFIRMED_WARNING],
    }
    if recovered:
        out["idempotency_recovered"] = True
    return out


def _as_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    # pymongo returns naive UTC datetimes unless the client is tz_aware.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _packet_doc_from_intake(intake: dict, surety: Optional[str], now: datetime) -> dict:
    """Staged paperwork_packets doc built only from the stored intake (never DocuSeal).

    ``surety`` None leaves ``template`` / ``surety_id`` unset so the CRM readiness
    chain keeps "Surety Confirmed" red and DocuSeal paths fail closed
    (``require_surety``) until staff confirm one.
    """
    doc = {
        "packet_id": intake.get("paperwork_packet_id"),
        "intake_id": intake.get("intake_id"),
        "bond_case_id": None,
        "packet_type": "full",
        "template": surety,
        "surety_id": surety,
        "status": "staged",
        "staged_by_agent": True,
        "booking_url": intake.get("booking_url"),
        "documents": [],
        "defendant_name": intake.get("defendant_name"),
        "defendant_county": intake.get("defendant_county"),
        "defendant_state": intake.get("defendant_state"),
        "defendant_booking_number": intake.get("defendant_booking_number"),
        "bond_amount": intake.get("bond_amount"),
        "charges": intake.get("charges"),
        "indemnitor_name": intake.get("indemnitor_name"),
        "indemnitor_email": intake.get("indemnitor_email"),
        "indemnitor_phone": intake.get("indemnitor_phone"),
        "created_at": now,
        "updated_at": now,
        "delivered_via": None,
        "esign_provider": "docuseal",
        "docuseal_submission_id": None,
        "docuseal_status": None,
        "packet_version": 1,
        "voided": False,
    }
    if intake.get("agent_idempotency_key"):
        doc["agent_idempotency_key"] = intake["agent_idempotency_key"]
    return doc


async def _packet_exists(packet_id: Optional[str]) -> bool:
    if not packet_id:
        return False
    found = await get_collection("paperwork_packets").find_one(
        {"packet_id": packet_id}, {"_id": 0, "packet_id": 1}
    )
    return bool(found)


async def _write_claimed_packet(intake_col, intake: dict, attempt: str, surety: Optional[str], now: datetime):
    """Write the packet for a claim this request holds. Returns an error response or None.

    The packet is upserted on its packet_id with ``$setOnInsert`` after an
    existence check, so a write that actually landed before an error was raised
    is never duplicated by a retry. Claim state moves pending -> complete only
    after the write; on failure it is marked failed (never deleted).
    """
    claim_filter = {"intake_id": intake.get("intake_id"), "idempotency_attempt": attempt}
    packet_id = intake.get("paperwork_packet_id")
    try:
        if not await _packet_exists(packet_id):
            await get_collection("paperwork_packets").update_one(
                {"packet_id": packet_id},
                {"$setOnInsert": _packet_doc_from_intake(intake, surety, now)},
                upsert=True,
            )
    except Exception as exc:
        logger.warning("[agent-packet] packet write failed (%s)", type(exc).__name__)
        try:
            await intake_col.update_one(
                claim_filter,
                {"$set": {
                    "idempotency_state": "failed",
                    "idempotency_error": "packet_write_failed",
                    "idempotency_failed_at": datetime.now(timezone.utc),
                }},
            )
        except Exception as mark_exc:
            # Claim stays pending; it becomes retakeable once stale.
            logger.warning("[agent-packet] could not mark claim failed (%s)", type(mark_exc).__name__)
        return JSONResponse(
            {
                "success": False,
                "error": "The packet could not be saved. Retry with the same idempotency key.",
                "code": "packet_write_failed",
                "retryable": True,
            },
            status_code=503,
            headers={"Retry-After": str(IDEMPOTENCY_RETRY_AFTER_S)},
        )
    try:
        await intake_col.update_one(
            claim_filter,
            {"$set": {
                "idempotency_state": "complete",
                "idempotency_completed_at": datetime.now(timezone.utc),
            }},
        )
    except Exception as exc:
        # Packet exists; a later replay re-verifies it once the pending claim is stale.
        logger.warning("[agent-packet] could not mark claim complete (%s)", type(exc).__name__)
    return None


async def _resolve_existing_claim(intake_col, prior: dict, fingerprint: str):
    """Answer a request whose idempotency key already has an intake claim."""
    if prior.get("agent_idempotency_fingerprint") != fingerprint:
        return _conflict_response()
    now = datetime.now(timezone.utc)
    raw_state = prior.get("idempotency_state")
    state = raw_state or "complete"
    if state == "complete":
        if await _packet_exists(prior.get("paperwork_packet_id")):
            return _replay_response(prior)
        # Claim says complete but the packet is missing: treat as failed.
    elif state == "pending":
        claimed_at = _as_utc(prior.get("idempotency_claimed_at"))
        if claimed_at and (now - claimed_at).total_seconds() <= IDEMPOTENCY_PENDING_STALE_S:
            return _in_progress_response()
        # Stale (or undated) pending: treat as failed.
    # failed / stale pending / complete-without-packet: retake the claim atomically.
    from pymongo import ReturnDocument

    retake_filter = {
        "intake_id": prior.get("intake_id"),
        "agent_idempotency_key": prior.get("agent_idempotency_key"),
        "agent_idempotency_fingerprint": fingerprint,
        "idempotency_attempt": prior.get("idempotency_attempt"),
    }
    if raw_state is not None:
        retake_filter["idempotency_state"] = raw_state
    new_attempt = uuid.uuid4().hex
    retaken = await intake_col.find_one_and_update(
        retake_filter,
        {"$set": {
            "idempotency_state": "pending",
            "idempotency_attempt": new_attempt,
            "idempotency_claimed_at": now,
            "idempotency_retaken_at": now,
            "idempotency_retaken_from": state,
        }},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if not retaken:
        # Another request changed the claim first.
        return _in_progress_response()
    err = await _write_claimed_packet(
        intake_col, retaken, new_attempt, retaken.get("agent_surety"), now
    )
    if err is not None:
        return err
    return _replay_response(retaken, recovered=True)


def _resolve_surety(raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(canonical surety id or None, error). Missing stays None: agent packets never default to OSI."""
    from dashboard.services.surety_registry import UnsupportedSuretyError, optional_surety

    try:
        sid = optional_surety(raw)
    except UnsupportedSuretyError:
        return None, "Unknown or inactive surety. Use a configured surety (e.g. 'osi' or 'palmetto')."
    return sid, None


async def _validate_public_booking_url(raw_url: str) -> Optional[str]:
    """Return an error string if URL is unsafe (SSRF / non-HTTP), else None."""
    try:
        parsed = urlsplit(raw_url.strip())
    except ValueError:
        return "Malformed booking_url"
    if parsed.scheme.lower() not in ("http", "https"):
        return "booking_url must use http or https"
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return "booking_url is missing a hostname"
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".local") or host.endswith(".internal"):
        return "booking_url host is not allowed"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return "booking_url IP address is not allowed"
    except ValueError:
        pass  # Domain name, not a raw IP literal
    # Resolve the host: decimal/hex/short IPv4, wildcard DNS (127.0.0.1.nip.io)
    # and other non-global ranges only show up after resolution.
    resolved_err = await check_public_url(raw_url)
    if resolved_err:
        return "booking_url host is not allowed"
    return None


def _decode_base64_image(raw: str) -> bytes:
    text = (raw or "").strip()
    if len(text) > MAX_ID_IMAGE_B64_CHARS:
        raise ValueError("ID image is too large")
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1].strip()
    return base64.b64decode(text, validate=True)


def _first_non_empty(*vals: Any) -> str:
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _split_name(full_name: str, first: str = "", last: str = "") -> tuple[str, str, str]:
    f = (first or "").strip()
    l = (last or "").strip()
    full = (full_name or "").strip()
    if not full and (f or l):
        full = f"{f} {l}".strip()
    if full and (not f or not l):
        parts = full.split()
        if not f and parts:
            f = parts[0]
        if not l and len(parts) > 1:
            l = " ".join(parts[1:])
    return full, f, l


def _super_crm_base_url() -> str:
    base = (
        os.getenv("SUPER_CRM_URL")
        or os.getenv("PUBLIC_BASE_URL")
        or "https://leads.shamrockbailbonds.biz"
    ).strip().rstrip("/")
    return base


@router.post("/parse-booking-url")
async def parse_booking_url(request: Request, body: AgentParseBookingRequest):
    """Parse a county jail booking URL and return verbatim defendant/charge/bond data."""
    if not _is_authorized(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
    url_err = await _validate_public_booking_url(body.booking_url)
    if url_err:
        return JSONResponse({"success": False, "error": url_err}, status_code=400)
    result = await ingest_url(
        body.booking_url.strip(), county=body.county, state=body.state, public_only=True
    )
    if not result.get("success"):
        return JSONResponse({"success": False, "error": result.get("error", "Failed to parse booking URL")}, status_code=422)
    return result


@router.post("/stage-packet")
@router.post("/issue-packet")
async def stage_packet_for_staff(request: Request, body: AgentStagePacketRequest):
    """Stage an intake + paperwork packet from booking URL, indemnitor info, and optional ID photo.

    Returns a 1-click Super CRM URL so staff can assign the POA, edit any details, and send DocuSeal.
    """
    scope = _caller_scope(request)
    if scope is None:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    surety, surety_err = _resolve_surety(body.surety)
    if surety_err:
        return JSONResponse(
            {"success": False, "error": surety_err, "code": "surety_unsupported"}, status_code=422
        )

    idem_key, idem_err = _idempotency_key(request, body)
    if idem_err:
        return JSONResponse({"success": False, "error": idem_err}, status_code=400)
    idem_hash = _scoped_key_hash(scope, idem_key) if idem_key else None
    fingerprint = _payload_fingerprint(body) if idem_key else None
    if idem_hash:
        intake_col = get_collection("intake_queue")
        # Fast replay path: no booking fetch or OCR for a repeat.
        prior = await intake_col.find_one({"agent_idempotency_key": idem_hash}, {"_id": 0})
        if prior:
            return await _resolve_existing_claim(intake_col, prior, fingerprint)

    warnings: list[str] = []
    parsed_booking: dict[str, Any] = {}
    parse_method = ""

    # 1. Parse defendant booking URL if provided
    booking_url = (body.booking_url or "").strip()
    if booking_url:
        url_err = await _validate_public_booking_url(booking_url)
        if url_err:
            return JSONResponse({"success": False, "error": url_err}, status_code=400)
        try:
            ingest_res = await ingest_url(
                booking_url,
                county=body.defendant.county,
                state=body.defendant.state,
                public_only=True,
            )
            if ingest_res.get("success") and isinstance(ingest_res.get("data"), dict):
                parsed_booking = ingest_res["data"]
                parse_method = str(ingest_res.get("parse_method") or "")
            else:
                warnings.append(
                    f"Booking URL could not be fully parsed ({ingest_res.get('error') or 'unknown error'}); "
                    "staged with supplied defendant fields."
                )
        except Exception as exc:
            logger.warning("[agent-packet] ingest_url failed (%s)", type(exc).__name__)
            warnings.append("Booking URL could not be fetched; staged with supplied defendant fields.")

    # 2. Scan indemnitor ID image if provided
    id_ocr: dict[str, Any] = {}
    if body.id_image_base64:
        try:
            img_bytes = _decode_base64_image(body.id_image_base64)
            scan_res = await IDScannerService.scan_id_image(img_bytes, filename="indemnitor_id_front.jpg")
            if isinstance(scan_res, dict) and scan_res.get("success"):
                id_ocr = scan_res.get("data") or {}
            else:
                warnings.append(f"ID OCR did not extract fields: {(scan_res or {}).get('error', 'unknown')}")
        except (binascii.Error, ValueError) as exc:
            logger.warning("[agent-packet] ID image rejected (%s)", type(exc).__name__)
            warnings.append("ID image could not be decoded (invalid or too large); not scanned.")
        except Exception as exc:
            logger.warning("[agent-packet] ID scan failed (%s)", type(exc).__name__)
            warnings.append("ID image could not be scanned; enter indemnitor ID fields in Super CRM.")

    # 3. Merge defendant fields (verbatim charges & booking facts preserved from source)
    def_in = body.defendant
    def_full, def_first, def_last = _split_name(
        _first_non_empty(parsed_booking.get("full_name"), def_in.full_name),
        _first_non_empty(parsed_booking.get("first_name"), def_in.first_name),
        _first_non_empty(parsed_booking.get("last_name"), def_in.last_name),
    )
    def_booking = _first_non_empty(parsed_booking.get("booking_number"), def_in.booking_number)
    def_county = _first_non_empty(parsed_booking.get("county"), def_in.county)
    def_state = _first_non_empty(parsed_booking.get("state"), def_in.state, "FL")
    def_dob = _first_non_empty(parsed_booking.get("dob"), def_in.dob)
    def_address = _first_non_empty(parsed_booking.get("address"), def_in.address)
    def_phone = _first_non_empty(def_in.phone, parsed_booking.get("phone"))
    def_case_no = _first_non_empty(parsed_booking.get("case_number"), def_in.case_number)

    raw_charges = parsed_booking.get("charges")
    if not raw_charges:
        raw_charges = def_in.charges or []

    raw_bond = parsed_booking.get("total_bond") or parsed_booking.get("bond_amount") or def_in.bond_amount or 0
    try:
        bond_amount = round(float(str(raw_bond).replace("$", "").replace(",", "").strip() or 0), 2)
    except (TypeError, ValueError):
        bond_amount = 0.0

    if not def_full and not def_booking:
        return JSONResponse(
            {
                "success": False,
                "error": "Could not determine defendant name or booking number from booking_url or defendant input.",
                "warnings": warnings,
            },
            status_code=422,
        )

    # 4. Merge indemnitor fields (explicit input wins over ID OCR; OCR fills missing gaps)
    ind_in = body.indemnitor
    ind_full, ind_first, ind_last = _split_name(
        _first_non_empty(ind_in.full_name, id_ocr.get("full_name")),
        _first_non_empty(ind_in.first_name, id_ocr.get("first_name")),
        _first_non_empty(ind_in.last_name, id_ocr.get("last_name")),
    )
    ind_phone = _first_non_empty(ind_in.phone)
    ind_email = _first_non_empty(ind_in.email)
    ind_dob = _first_non_empty(ind_in.dob, id_ocr.get("dob"))
    ind_dl = _first_non_empty(ind_in.dl_number, id_ocr.get("dl_number"), id_ocr.get("document_number"))
    ind_dl_state = _first_non_empty(ind_in.dl_state, id_ocr.get("dl_state"), "FL")
    ind_address = _first_non_empty(ind_in.address, id_ocr.get("address"))
    ind_city = _first_non_empty(ind_in.city, id_ocr.get("city"))
    ind_state = _first_non_empty(ind_in.state, id_ocr.get("state"), "FL")
    ind_zip = _first_non_empty(ind_in.zip, id_ocr.get("zip"))
    ind_rel = _first_non_empty(ind_in.relationship)

    if not ind_full:
        warnings.append("Indemnitor name is missing.")
    if not ind_email and not ind_phone:
        warnings.append("Indemnitor phone and email are both missing.")
    if not surety:
        warnings.append(SURETY_UNCONFIRMED_WARNING)

    now = datetime.now(timezone.utc)
    intake_id = f"INT-AGT-{uuid.uuid4().hex[:10].upper()}"
    packet_id = f"PKT-{uuid.uuid4().hex[:10].upper()}"

    # 5. No arrests write. ``arrests`` is the scraper source of truth (dedup
    # key state + county + booking_number) and packet context treats an arrests
    # row as scraper-recorded bond/charges. Agent-supplied booking facts stay on
    # the intake and packet below; staff verify them in Super CRM.

    # 6. Insert intake_queue record
    intake_doc = {
        "intake_id": intake_id,
        "source": "chat_agent",
        "status": "staged_by_agent",
        "paperwork_packet_id": packet_id,
        "paperwork_status": "staged",
        "booking_url": booking_url,
        "defendant_name": def_full,
        "defendant_county": def_county,
        "defendant_state": def_state,
        "defendant_booking_number": def_booking,
        "defendant_dob": def_dob,
        "defendant_address": def_address,
        "defendant_phone": def_phone,
        "case_number": def_case_no,
        "bond_amount": bond_amount,
        "charges": raw_charges,
        "defendant": {
            "fullName": def_full,
            "firstName": def_first,
            "lastName": def_last,
            "bookingNumber": def_booking,
            "county": def_county,
            "state": def_state,
            "dob": def_dob,
            "address": def_address,
            "phone": def_phone,
            "caseNumber": def_case_no,
            "bondAmount": bond_amount,
            "charges": raw_charges,
        },
        "indemnitor_name": ind_full,
        "indemnitor_email": ind_email,
        "indemnitor_phone": ind_phone,
        "indemnitor_dob": ind_dob,
        "indemnitor_dl_number": ind_dl,
        "indemnitor_address": ind_address,
        "indemnitor": {
            "fullName": ind_full,
            "firstName": ind_first,
            "lastName": ind_last,
            "phone": ind_phone,
            "email": ind_email,
            "dob": ind_dob,
            "dlNumber": ind_dl,
            "dlState": ind_dl_state,
            "address": ind_address,
            "city": ind_city,
            "state": ind_state,
            "zip": ind_zip,
            "relationship": ind_rel,
            "idUploaded": bool(body.id_image_base64),
        },
        "id_ocr": id_ocr or None,
        "notes": _first_non_empty(body.notes),
        "created_at": now,
        "updated_at": now,
    }
    intake_col = get_collection("intake_queue")
    attempt = None
    if idem_hash:
        attempt = uuid.uuid4().hex
        intake_doc["agent_idempotency_key"] = idem_hash
        intake_doc["agent_idempotency_fingerprint"] = fingerprint
        intake_doc["agent_surety"] = surety
        # Claim lifecycle: pending (now) -> complete after the packet write, or
        # failed if it raises. Docs are never deleted.
        intake_doc["idempotency_state"] = "pending"
        intake_doc["idempotency_attempt"] = attempt
        intake_doc["idempotency_claimed_at"] = now
        # Atomic claim: the intake doc itself is the claim. Only the request whose
        # upsert inserted it (prior is None) goes on to write the packet.
        from pymongo import ReturnDocument

        prior = await intake_col.find_one_and_update(
            {"agent_idempotency_key": idem_hash},
            {"$setOnInsert": intake_doc},
            upsert=True,
            return_document=ReturnDocument.BEFORE,
            projection={"_id": 0},
        )
        if prior:
            return await _resolve_existing_claim(intake_col, prior, fingerprint)
    else:
        await intake_col.insert_one(intake_doc)

    # 7. Insert staged paperwork_packets record (never creates DocuSeal submission here)
    if idem_hash:
        err = await _write_claimed_packet(intake_col, intake_doc, attempt, surety, now)
        if err is not None:
            return err
    else:
        await get_collection("paperwork_packets").insert_one(
            _packet_doc_from_intake(intake_doc, surety, now)
        )

    super_crm_url = f"{_super_crm_base_url()}/?tab=paperwork&packet_id={packet_id}"
    logger.info(
        "[agent-packet] Staged packet %s (intake=%s, booking=%s, county=%s)",
        packet_id, intake_id, def_booking or "n/a", def_county or "n/a",
    )

    return {
        "success": True,
        "status": "staged_ready_for_staff",
        "packet_id": packet_id,
        "intake_id": intake_id,
        "super_crm_url": super_crm_url,
        "surety": surety,
        "surety_confirmed": bool(surety),
        "defendant": {
            "full_name": def_full,
            "booking_number": def_booking,
            "county": def_county,
            "state": def_state,
            "dob": def_dob,
            "case_number": def_case_no,
            "bond_amount": bond_amount,
            "charges": raw_charges,
        },
        "indemnitor": {
            "full_name": ind_full,
            "phone": ind_phone,
            "email": ind_email,
            "dob": ind_dob,
            "dl_number": ind_dl,
            "dl_state": ind_dl_state,
            "address": ind_address,
            "city": ind_city,
            "state": ind_state,
            "zip": ind_zip,
            "relationship": ind_rel,
        },
        "id_ocr_extracted": bool(id_ocr),
        "warnings": warnings,
    }
