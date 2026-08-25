"""
ShamrockLeads — Mobile PIN Portal Router (paperwork.shamrockbailbonds.biz)
========================================================================
Serves mobile PWA UI and fast 6-digit OTP PIN authentication for indemnitor e-signing.
OTP is delivered exclusively via BlueBubbles (iMessage / green SMS through Messages).
Never routes client text through Twilio.
"""
import html as html_lib
import json
import os
import re
import random
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from dashboard.deps import get_collection

logger = logging.getLogger(__name__)

pin_portal_router = APIRouter(prefix="/api/portal", tags=["pin_portal"])
portal_page_router = APIRouter(tags=["pin_portal_pages"])

# 6-digit PIN OTP store in MongoDB `portal_pins`
# pin -> {phone, intake_id, booking_number, expires_at}

_TEST_PHONE = "2395550199"
# Optional staff smoke bypass — env only (empty = disabled). Never hardcode in source.
_MASTER_PIN = (os.getenv("PORTAL_STAFF_MASTER_PIN") or os.getenv("PAPERWORK_STAFF_EXCEPTION_PIN") or "").strip()


class SendPinRequest(BaseModel):
    phone: str
    booking_number: Optional[str] = None
    intake_id: Optional[str] = None
    role: Optional[str] = None


class VerifyPinRequest(BaseModel):
    phone: str
    pin: str


def _digits_phone(raw: str) -> str:
    return "".join(ch for ch in (raw or "") if ch.isdigit())[-10:]


def _extract_signing_link_from_packet(doc: Optional[dict], role: Optional[str] = None) -> str:
    """Pull the best DocuSeal/sign URL from a paperwork_packets document."""
    if not doc or not isinstance(doc, dict):
        return ""
    from dashboard.services.paperwork_signers import party_signers_from_packet, pick_party

    parties = party_signers_from_packet(doc)
    chosen = pick_party(parties, role=role)
    if chosen and chosen.get("sign_url"):
        return chosen["sign_url"]
    for key in ("signing_link", "magic_link", "sign_url", "embed_src"):
        val = doc.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    links = doc.get("sign_links") or []
    if isinstance(links, list):
        for u in links:
            if isinstance(u, str) and u.startswith("http"):
                return u
    return ""


async def _resolve_packet_for_client(
    phone_str: str,
    booking: str = "",
    intake: str = "",
) -> Dict[str, Any]:
    """
    Find the newest non-voided paperwork packet for this client and return
    signing link + status metadata for the portal UI.
    """
    packets = get_collection("paperwork_packets")
    phone = _digits_phone(phone_str)
    or_clauses = []
    if booking:
        or_clauses.append({"booking_number": booking})
        or_clauses.append({"Booking_Number": booking})
        or_clauses.append({"defendant_booking_number": booking})
    if intake:
        or_clauses.append({"intake_id": intake})
        or_clauses.append({"Intake_ID": intake})
    if phone:
        # Match last 10 digits whether stored as 10-digit or E.164
        phone_pat = re.escape(phone) + r"$"
        or_clauses.extend([
            {"indemnitor_phone": {"$regex": phone_pat}},
            {"delivered_to": {"$regex": phone_pat}},
            {"signer_phone": {"$regex": phone_pat}},
            {"defendant_phone": {"$regex": phone_pat}},
            {"coindemnitor_phone": {"$regex": phone_pat}},
            {"co_indemnitor_phone": {"$regex": phone_pat}},
            {"indemnitor.phone": {"$regex": phone_pat}},
            {"defendant.phone": {"$regex": phone_pat}},
            {"parties.indemnitor.phone": {"$regex": phone_pat}},
            {"parties.defendant.phone": {"$regex": phone_pat}},
            {"parties.phone": {"$regex": phone_pat}},
            {"indemnitors.phone": {"$regex": phone_pat}},
            {"docuseal_submitters.phone": {"$regex": phone_pat}},
        ])
    if not or_clauses:
        return {
            "signing_link": "",
            "has_packet": False,
            "packet_id": "",
            "defendant_name": "",
            "status": "no_query",
            "message": "Enter a valid phone number to locate your packet.",
        }

    base_or = {"$or": or_clauses}
    # Prefer non-voided packets (find_one is robust under Motor + test mocks)
    doc = None
    try:
        doc = await packets.find_one(
            {
                "$and": [
                    base_or,
                    {"voided": {"$ne": True}},
                    {"status": {"$nin": ["voided", "cancelled", "canceled"]}},
                ]
            },
            sort=[("created_at", -1)],
        )
    except Exception:
        doc = None
    if not doc:
        try:
            doc = await packets.find_one(base_or, sort=[("created_at", -1)])
        except Exception:
            doc = None

    if not doc:
        return {
            "signing_link": "",
            "has_packet": False,
            "packet_id": "",
            "defendant_name": "",
            "status": "not_found",
            "message": (
                "No e-sign packet is on file for this phone yet. "
                "If your bond agent already sent paperwork, call (239) 332-2245."
            ),
        }

    from dashboard.services.paperwork_signers import party_signers_from_packet, pick_party

    parties = party_signers_from_packet(doc)
    chosen = pick_party(parties, phone=phone)
    link = (chosen or {}).get("sign_url") or _extract_signing_link_from_packet(doc)
    defendant = str(doc.get("defendant_name") or doc.get("Defendant_Name") or "")
    packet_id = str(doc.get("packet_id") or doc.get("_id") or "")
    status = str(doc.get("status") or "pending")

    if link:
        return {
            "signing_link": link,
            "has_packet": True,
            "packet_id": packet_id,
            "defendant_name": defendant,
            "status": status or "pending_signature",
            "parties": parties,
            "role": (chosen or {}).get("role") or "",
            "message": "Packet ready — open your e-sign documents.",
        }

    return {
        "signing_link": "",
        "has_packet": True,
        "packet_id": packet_id,
        "defendant_name": defendant,
        "status": status,
        "parties": parties,
        "role": "",
        "message": (
            "We found your case file, but the e-sign link is not ready yet. "
            "Please call (239) 332-2245 and we will resend your signing link."
        ),
    }


async def _resolve_signing_link(phone_str: str, booking: str = "", intake: str = "") -> str:
    """Back-compat: return only the signing URL string."""
    meta = await _resolve_packet_for_client(phone_str, booking=booking, intake=intake)
    return meta.get("signing_link") or ""


@pin_portal_router.post("/send-pin")
async def send_portal_pin(req: SendPinRequest):
    """
    Generate & dispatch a 6-digit OTP PIN via BlueBubbles only (iMessage/SMS).
    Queued sends (BB temporarily down) still count as success — never Twilio.
    """
    clean_phone = _digits_phone(req.phone)
    if not clean_phone or len(clean_phone) < 10:
        return JSONResponse(
            {"success": False, "error": "Invalid 10-digit phone number"},
            status_code=400,
        )

    otp_pin = f"{random.randint(100000, 999999)}"
    # Deterministic lab PIN for staff smoke (not a production client path)
    if clean_phone == _TEST_PHONE or req.phone.replace(" ", "") == _TEST_PHONE:
        otp_pin = _MASTER_PIN

    pins_col = get_collection("portal_pins")
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=15)).isoformat()

    pin_doc = {
        "phone": clean_phone,
        "pin": otp_pin,
        "booking_number": req.booking_number or "",
        "intake_id": req.intake_id or "",
        "role": _normalize_client_role(req.role),
        "created_at": now.isoformat(),
        "expires_at": expires_at,
        "verified": False,
    }

    await pins_col.update_one(
        {"phone": clean_phone},
        {"$set": pin_doc},
        upsert=True,
    )

    logger.info("[PIN Portal] Generated PIN for phone ...%s", clean_phone[-4:])

    try:
        from dashboard.services.bb_client import (
            send_message_universal,
            normalize_bb_send_result,
            bb_send_accepted,
        )
        msg = (
            f"Your Shamrock Bail Bonds secure intake verification PIN is: {otp_pin}. "
            f"Valid for 15 minutes."
        )
        raw = await send_message_universal(clean_phone, msg)
        send_res = normalize_bb_send_result(raw)
        logger.info(
            "[PIN Portal] BB send channel=%s sent=%s queued=%s phone=...%s",
            send_res.get("channel"),
            send_res.get("sent"),
            send_res.get("queued"),
            clean_phone[-4:],
        )

        if not bb_send_accepted(send_res):
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Messaging unavailable: {send_res.get('error', 'send failed')}",
                    "channel": send_res.get("channel", "failed"),
                },
                status_code=503,
            )

        env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").lower()
        debug_ok = env not in ("production", "prod")
        return {
            "success": True,
            "phone": clean_phone,
            "channel": send_res.get("channel", "imessage"),
            "sent": bool(send_res.get("sent")),
            "queued": bool(send_res.get("queued")),
            "expires_in_minutes": 15,
            "debug_pin": otp_pin if debug_ok else None,
        }
    except Exception as exc:
        logger.error("[PIN Portal] Send PIN exception: %s", exc)
        return JSONResponse(
            {"success": False, "error": "Send error — BlueBubbles unreachable"},
            status_code=500,
        )


class InstantIndemnitorPacketRequest(BaseModel):
    indemnitor_name: str
    indemnitor_phone: str
    indemnitor_email: Optional[str] = None
    indemnitor_address: Optional[str] = None
    indemnitor_dl: Optional[str] = None
    surety_id: Optional[str] = "osi"
    county: Optional[str] = None
    state: Optional[str] = None



@pin_portal_router.post("/instant-indemnitor-packet")
async def create_instant_indemnitor_packet(request: Request, req: InstantIndemnitorPacketRequest):
    """
    Retired fail-closed endpoint retained for old portal clients.

    A legal packet cannot be created from an ID scan alone.  New paperwork must
    originate from the staff Write Bond flow after the complete identity chain is
    validated (match, BondCase, surety, case number, and POA).  Keeping a stable
    409 response prevents cached clients from silently recreating the former
    unassigned-defendant workflow.
    """
    return JSONResponse(
        {
            "success": False,
            "error": "validated_bond_case_required",
            "message": (
                "Paperwork is not ready yet. A Shamrock bondsman must validate "
                "the match and bond case before creating your signing packet."
            ),
            "next_step": "request_pin_after_staff_creates_packet",
        },
        status_code=409,
    )



@pin_portal_router.post("/verify-pin")
async def verify_portal_pin(req: VerifyPinRequest):
    """
    Verify 6-digit OTP PIN and return session token + packet deep-link signing URL.
    """
    clean_phone = _digits_phone(req.phone)
    input_pin = (req.pin or "").strip()

    # Optional staff smoke bypass (env PORTAL_STAFF_MASTER_PIN only)
    if _MASTER_PIN and input_pin == _MASTER_PIN:
        import secrets as _secrets
        session_token = f"PORTAL-ADMIN-{_secrets.token_urlsafe(24)}"
        pins_col = get_collection("portal_pins")
        now = datetime.now(timezone.utc)
        await pins_col.update_one(
            {"phone": clean_phone},
            {"$set": {
                "phone": clean_phone,
                "pin": "ADMIN",
                "verified": True,
                "session_token": session_token,
                "verified_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=4)).isoformat(),
            }},
            upsert=True,
        )
        meta = await _resolve_packet_for_client(clean_phone)
        return {
            "success": True,
            "verified": True,
            "phone": clean_phone,
            "session_token": session_token,
            "role": "indemnitor",
            "signing_link": meta.get("signing_link") or "",
            "has_packet": bool(meta.get("has_packet")),
            "packet_id": meta.get("packet_id") or "",
            "defendant_name": meta.get("defendant_name") or "",
            "packet_status": meta.get("status") or "",
            "message": meta.get("message") or "",
        }

    pins_col = get_collection("portal_pins")
    pin_doc = await pins_col.find_one({"phone": clean_phone, "pin": input_pin})

    if not pin_doc:
        return JSONResponse(
            {"success": False, "error": "Invalid PIN or phone number"},
            status_code=401,
        )

    expires_at_str = pin_doc.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                return JSONResponse(
                    {"success": False, "error": "PIN has expired"},
                    status_code=401,
                )
        except ValueError:
            pass

    import secrets as _secrets

    session_token = f"PORTAL-{_secrets.token_urlsafe(24)}"
    pin_id = pin_doc.get("_id")
    meta = await _resolve_packet_for_client(
        clean_phone,
        booking=pin_doc.get("booking_number", "") or "",
        intake=pin_doc.get("intake_id", "") or "",
    )
    session_role = (
        meta.get("role")
        or pin_doc.get("role")
        or ""
    ).strip()
    session_set = {
        "verified": True,
        "session_token": session_token,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "role": session_role,
        "packet_id": meta.get("packet_id") or "",
    }
    if pin_id is not None:
        await pins_col.update_one({"_id": pin_id}, {"$set": session_set})
    else:
        await pins_col.update_one(
            {"phone": clean_phone, "pin": input_pin},
            {"$set": session_set},
        )

    return {
        "success": True,
        "verified": True,
        "phone": clean_phone,
        "booking_number": pin_doc.get("booking_number"),
        "intake_id": pin_doc.get("intake_id"),
        "session_token": session_token,
        "signing_link": meta.get("signing_link") or "",
        "has_packet": bool(meta.get("has_packet")),
        "packet_id": meta.get("packet_id") or "",
        "defendant_name": meta.get("defendant_name") or "",
        "packet_status": meta.get("status") or "",
        "parties": meta.get("parties") or [],
        "role": meta.get("role") or "",
        "message": meta.get("message") or "",
    }


# Client-writable DocuSeal keys only. Bond/POA/premium/charge fields stay staff-owned.
_CLIENT_FIELD_ALLOWLIST = frozenset({
    "indemnitor_name", "IndemnitorName", "IndName", "FullName",
    "indemnitor_first_name", "indemnitor_middle_name",
    "indemnitor_address", "indemnitor_city", "indemnitor_state", "indemnitor_zip",
    "indemnitor_city_state_zip", "indemnitor_phone", "indemnitor_phone2",
    "indemnitor_email", "indemnitor_dl", "indemnitor_dob",
    "indemnitor_ssn", "indemnitor_employer", "indemnitor_employer_phone",
    "indemnitor_employer_address", "indemnitor_relationship", "indemnitor_work_phone",
    "indemnitor_vehicle_make", "indemnitor_vehicle_model", "indemnitor_vehicle_year",
    "indemnitor_vehicle_color", "indemnitor_mortgage_co", "indemnitor_mortgage_amount",
    "indemnitor_spouse_name", "indemnitor_spouse_dl", "indemnitor_spouse_ssn",
    "indemnitor_spouse_employer", "indemnitor_spouse_employer_address",
    "indemnitor_spouse_phone", "indemnitor_spouse_work_phone",
    "reference_1_name", "reference_1_relation", "reference_1_phone", "reference_1_address",
    "reference_2_name", "reference_2_relation", "reference_2_phone", "reference_2_address",
    "defendant_name", "DefName", "DefFirstName", "DefLastName",
    "defendant_address", "defendant_city", "defendant_state",
    "defendant_zip", "defendant_phone", "defendant_email", "defendant_dl",
    "defendant_dl_state", "defendant_dob", "defendant_ssn",
    "defendant_employer", "defendant_employer_phone", "defendant_employer_address",
    "defendant_employer_how_long", "defendant_height", "defendant_weight",
    "defendant_hair", "defendant_eyes", "defendant_race", "defendant_alias",
    "defendant_address_how_long", "defendant_former_address",
    "defendant_former_address_how_long", "defendant_boss",
    "defendant_previous_employment", "defendant_previous_employment_how_long",
    "defendant_tattoos", "defendant_spouse_name", "defendant_spouse_phone",
    "defendant_spouse_address", "defendant_spouse_employer",
    "def_parent_name", "def_parent_phone", "def_parent_address",
    "def_best_friend_name", "def_best_friend_phone", "def_best_friend_address",
    "def_attorney_name", "def_attorney_phone", "def_attorney_address",
    "def_vehicle_year", "def_vehicle_make", "def_vehicle_model", "def_vehicle_color",
    "def_vehicle_plate", "def_facebook", "def_instagram",
    "children_names_ages_1", "children_names_ages_2",
    "children_school_1", "children_school_2",
})


class RemainingFieldsRequest(BaseModel):
    session_token: str
    fields: Dict[str, Any] = {}
    role: Optional[str] = None
    address_confirmed: Optional[bool] = None
    staff_review_acknowledged: Optional[bool] = None


def _normalize_client_role(raw: Optional[str]) -> str:
    """Normalize only the client roles accepted by the intake launchpad."""
    value = str(raw or "").strip().lower().replace("-", "").replace("_", "")
    if value in {"defendant", "def", "inmate"}:
        return "defendant"
    if value in {"coindemnitor", "co"}:
        return "coindemnitor"
    if value in {"indemnitor", "ind", "cosigner"}:
        return "indemnitor"
    return ""


def _sanitize_client_fields(raw: Optional[Dict[str, Any]]) -> Dict[str, str]:
    clean: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return clean
    for key, value in raw.items():
        if key not in _CLIENT_FIELD_ALLOWLIST:
            continue
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if len(text) > 240:
            text = text[:240]
        clean[key] = text
    return clean


def client_fields_from_id_ocr(extracted: Optional[Dict[str, Any]], role: str) -> Dict[str, str]:
    """Map a DL scan onto role-scoped client keys. Never mixes indemnitor ID onto defendant."""
    extracted = extracted if isinstance(extracted, dict) else {}
    full_name = str(
        extracted.get("full_name")
        or " ".join(p for p in (extracted.get("first_name"), extracted.get("last_name")) if p)
        or ""
    ).strip()
    address = str(extracted.get("address") or "").strip()
    city = str(extracted.get("city") or "").strip()
    state = str(extracted.get("state") or extracted.get("dl_state") or "").strip()
    zip_code = str(extracted.get("zip") or "").strip()
    dob = str(extracted.get("dob") or "").strip()
    dl_number = str(extracted.get("dl_number") or extracted.get("dl") or "").strip()
    dl_state = str(extracted.get("dl_state") or extracted.get("state") or "").strip()
    if role == "defendant":
        raw = {
            "defendant_name": full_name,
            "DefName": full_name,
            "DefFirstName": str(extracted.get("first_name") or "").strip(),
            "DefLastName": str(extracted.get("last_name") or "").strip(),
            "defendant_address": address,
            "defendant_city": city,
            "defendant_state": state,
            "defendant_zip": zip_code,
            "defendant_dob": dob,
            "defendant_dl": dl_number,
            "defendant_dl_state": dl_state,
        }
    else:
        raw = {
            "indemnitor_name": full_name,
            "IndemnitorName": full_name,
            "IndName": full_name,
            "FullName": full_name,
            "indemnitor_address": address,
            "indemnitor_city": city,
            "indemnitor_state": state,
            "indemnitor_zip": zip_code,
            "indemnitor_dob": dob,
            "indemnitor_dl": dl_number,
        }
        if city or state or zip_code:
            raw["indemnitor_city_state_zip"] = ", ".join(p for p in (city, f"{state} {zip_code}".strip()) if p)
    return _sanitize_client_fields(raw)


async def _push_client_fields_to_issued_packet(
    *,
    session: Dict[str, Any],
    fields: Dict[str, str],
) -> bool:
    """PATCH a staff-issued DocuSeal submitter. No-op if no packet exists."""
    if not fields:
        return False
    meta = await _resolve_packet_for_client(
        session.get("phone") or "",
        booking=session.get("booking_number") or "",
        intake=session.get("intake_id") or session.get("client_intake_id") or "",
    )
    packet_id = meta.get("packet_id") or ""
    if not packet_id:
        return False
    from dashboard.services.paperwork_signers import normalize_role
    packets = get_collection("paperwork_packets")
    packet = await packets.find_one({"packet_id": packet_id})
    submitters = list((packet or {}).get("docuseal_submitters") or [])
    want_role = normalize_role(session.get("role") or meta.get("role") or "")
    session_phone = _digits_phone(session.get("phone") or "")
    target = None
    for item in submitters:
        item_role = normalize_role((item or {}).get("role"))
        if want_role and item_role == want_role:
            target = item
            break
    if not target and session_phone:
        for item in submitters:
            if _digits_phone((item or {}).get("phone")) == session_phone:
                target = item
                break
    submitter_id = (target or {}).get("id")
    if not submitter_id:
        return False
    from dashboard.services.docuseal_service import DocuSealService
    from dashboard.services.docuseal_signing_ux import submission_fields_from_values
    svc = DocuSealService()
    await svc.update_submitter(
        submitter_id,
        values=fields,
        fields=submission_fields_from_values(fields, force_editable=True),
    )
    existing_packet_fields = (packet or {}).get("client_fields") if isinstance((packet or {}).get("client_fields"), dict) else {}
    merged = {**existing_packet_fields, **fields}
    await packets.update_one(
        {"packet_id": packet_id},
        {"$set": {
            "client_fields": merged,
            "client_fields_updated_at": datetime.now(timezone.utc).isoformat(),
            "id_ocr_pushed_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return True


def _city_state_zip(fields: Dict[str, str]) -> str:
    if fields.get("indemnitor_city_state_zip"):
        return fields["indemnitor_city_state_zip"]
    parts = [
        fields.get("indemnitor_city") or "",
        fields.get("indemnitor_state") or "",
        fields.get("indemnitor_zip") or "",
    ]
    city = parts[0]
    rest = " ".join(p for p in parts[1:] if p).strip()
    if city and rest:
        return f"{city}, {rest}"
    return city or rest


def _split_name(full_name: str) -> tuple[str, str]:
    parts = [part for part in str(full_name or "").split() if part]
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


async def _upsert_deferred_client_intake(
    *,
    session: Dict[str, Any],
    role: str,
    fields: Dict[str, str],
    staff_review_acknowledged: bool,
) -> str:
    """Store one client-owned intake record without creating a case or packet.

    Each indemnitor gets an independent queue record.  Staff may attach any
    number of those records to a later bond; no defendant, amount, POA, or
    DocuSeal packet is implied by this intake.
    """
    now = datetime.now(timezone.utc)
    role = _normalize_client_role(role) or "indemnitor"
    intake_id = str(session.get("client_intake_id") or f"WX-{uuid.uuid4().hex[:10].upper()}")
    phone = _digits_phone(str(session.get("phone") or ""))
    extracted = session.get("id_extracted") if isinstance(session.get("id_extracted"), dict) else {}

    defendant = {
        "name": "",
        "firstName": "",
        "lastName": "",
        "dob": "",
        "facility": "",
        "county": "",
        "bookingNumber": "",
        "charges": "",
        "bondAmount": "",
        "street": "",
        "city": "",
        "state": "",
        "zip": "",
        "charge_details": [],
    }
    indemnitor = {
        "firstName": "",
        "middleName": "",
        "lastName": "",
        "relationship": "",
        "dob": "",
        "ssn": "",
        "dl": "",
        "dlState": "",
        "phone": "",
        "email": "",
        "address": "",
        "city": "",
        "state": "",
        "zip": "",
        "employer": "",
        "employerPhone": "",
        "employerCity": "",
        "employerState": "",
        "supervisor": "",
        "supervisorPhone": "",
        "ref1Name": "",
        "ref1Relation": "",
        "ref1Phone": "",
        "ref1Address": "",
        "ref2Name": "",
        "ref2Relation": "",
        "ref2Phone": "",
        "ref2Address": "",
    }

    session_booking = str(session.get("booking_number") or fields.get("booking_number") or "").strip()
    session_county = str(
        session.get("county")
        or fields.get("defendant_county")
        or fields.get("county")
        or ""
    ).strip()
    session_def_name = str(
        session.get("defendant_name")
        or fields.get("defendant_name")
        or fields.get("DefName")
        or ""
    ).strip()

    if role == "defendant":
        full_name = fields.get("defendant_name") or fields.get("DefName") or extracted.get("full_name") or ""
        first_name, last_name = _split_name(full_name)
        defendant.update({
            "name": full_name,
            "firstName": fields.get("DefFirstName") or first_name,
            "lastName": fields.get("DefLastName") or last_name,
            "dob": fields.get("defendant_dob") or extracted.get("dob") or "",
            "street": fields.get("defendant_address") or extracted.get("address") or "",
            "city": fields.get("defendant_city") or extracted.get("city") or "",
            "state": fields.get("defendant_state") or extracted.get("state") or "",
            "zip": fields.get("defendant_zip") or extracted.get("zip") or "",
            "dl": fields.get("defendant_dl") or extracted.get("dl_number") or "",
            "phone": phone,
            "bookingNumber": session_booking,
            "county": session_county,
        })
    else:
        full_name = fields.get("indemnitor_name") or fields.get("IndemnitorName") or fields.get("IndName") or fields.get("FullName") or extracted.get("full_name") or ""
        first_name, last_name = _split_name(full_name)
        indemnitor.update({
            "firstName": first_name,
            "lastName": last_name,
            "relationship": fields.get("indemnitor_relationship") or "",
            "dob": fields.get("indemnitor_dob") or extracted.get("dob") or "",
            "dl": fields.get("indemnitor_dl") or extracted.get("dl_number") or "",
            "phone": phone,
            "address": fields.get("indemnitor_address") or extracted.get("address") or "",
            "city": fields.get("indemnitor_city") or extracted.get("city") or "",
            "state": fields.get("indemnitor_state") or extracted.get("state") or "",
            "zip": fields.get("indemnitor_zip") or extracted.get("zip") or "",
            "employer": fields.get("indemnitor_employer") or "",
            "employerPhone": fields.get("indemnitor_employer_phone") or fields.get("indemnitor_work_phone") or "",
            "ref1Name": fields.get("reference_1_name") or "",
            "ref1Phone": fields.get("reference_1_phone") or "",
            "role": "coindemnitor" if role == "coindemnitor" else "primary",
        })
        if session_def_name:
            def_first, def_last = _split_name(session_def_name)
            defendant.update({
                "name": session_def_name,
                "firstName": def_first,
                "lastName": def_last,
                "bookingNumber": session_booking,
                "county": session_county,
            })
        elif session_booking or session_county:
            defendant["bookingNumber"] = session_booking
            defendant["county"] = session_county

    doc = {
        "intake_id": intake_id,
        "source": "wix_portal",
        "source_label": "Wix Portal",
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "indemnitor": indemnitor,
        "indemnitor_name": " ".join(part for part in [indemnitor.get("firstName"), indemnitor.get("lastName")] if part),
        "indemnitor_email": "",
        "indemnitor_phone": indemnitor.get("phone") or "",
        "defendant": defendant,
        "defendant_name": defendant.get("name") or "",
        "defendant_booking_number": defendant.get("bookingNumber") or "",
        "defendant_county": defendant.get("county") or "",
        "defendant_facility": "",
        "consent_given": bool(staff_review_acknowledged),
        "consent_timestamp": now.isoformat(),
        "role": role,
        "id_scanned_at": session.get("id_scanned_at") or now.isoformat(),
        "gas_sync_status": "pending",
        "gas_sync_timestamp": None,
        "matched_booking_number": None,
        "matched_county": None,
        "matched_defendant_id": None,
        "match_confidence": 0,
        "match_strategy": "pending_auto",
        "match_timestamp": None,
        "surety_id": "osi",
        "paperwork_packet_id": None,
        "paperwork_status": "intake_complete",
        "_raw": {
            "source": "wix_portal",
            "role": role,
            "client_fields": fields,
            "id_extracted": extracted,
        },
    }
    collection = get_collection("intake_queue")
    await collection.update_one({"intake_id": intake_id}, {"$set": doc}, upsert=True)
    await get_collection("portal_pins").update_one(
        {"session_token": session.get("session_token")},
        {"$set": {"client_intake_id": intake_id, "role": role}},
    )
    logger.info("[PIN Portal] Deferred %s intake saved: %s", role, intake_id)
    try:
        from dashboard.extensions import get_db
        from dashboard.services.matching_engine import MatchingEngine
        await MatchingEngine(get_db()).match_intake(doc)
    except Exception:
        logger.debug("[PIN Portal] auto-match skipped for %s", intake_id, exc_info=True)
    return intake_id


async def _load_verified_session(session_token: str) -> Optional[Dict[str, Any]]:
    token = (session_token or "").strip()
    if not token:
        return None
    pins_col = get_collection("portal_pins")
    doc = await pins_col.find_one({"session_token": token, "verified": True})
    if not doc:
        return None
    expires_at_str = doc.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(str(expires_at_str))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                return None
        except ValueError:
            pass
    return doc


def _session_payload(session: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    extracted = session.get("id_extracted") if isinstance(session.get("id_extracted"), dict) else {}
    return {
        "success": True,
        "verified": True,
        "phone": session.get("phone") or "",
        "session_token": session.get("session_token") or "",
        "role": meta.get("role") or session.get("role") or "indemnitor",
        "has_packet": bool(meta.get("has_packet")),
        "packet_id": meta.get("packet_id") or "",
        "defendant_name": meta.get("defendant_name") or "",
        "packet_status": meta.get("status") or "",
        "signing_link": meta.get("signing_link") or "",
        "message": meta.get("message") or "",
        "id_scanned": bool(extracted),
        "address_confirmed": bool(session.get("address_confirmed")),
        "fields_saved": bool(session.get("fields_saved")),
        "selfie_captured": bool(session.get("selfie_captured_at")),
        "extracted": {
            "full_name": extracted.get("full_name") or "",
            "address": extracted.get("address") or "",
            "city": extracted.get("city") or "",
            "state": extracted.get("state") or "",
            "zip": extracted.get("zip") or "",
            "dob": extracted.get("dob") or "",
            "dl_number": extracted.get("dl_number") or "",
        } if extracted else {},
    }


@pin_portal_router.api_route("/session", methods=["GET", "POST"])
async def portal_session(request: Request):
    token = (request.query_params.get("token") or request.query_params.get("session_token") or "").strip()
    if not token and request.method == "POST":
        try:
            body = await request.json()
            token = str((body or {}).get("session_token") or (body or {}).get("token") or "").strip()
        except Exception:
            token = ""
    session = await _load_verified_session(token)
    if not session:
        return JSONResponse({"success": False, "error": "Session expired. Request a new PIN."}, status_code=401)
    meta = await _resolve_packet_for_client(
        session.get("phone") or "",
        booking=session.get("booking_number") or "",
        intake=session.get("intake_id") or session.get("client_intake_id") or "",
    )
    return _session_payload(session, meta)


@pin_portal_router.post("/id-ocr")
async def portal_id_ocr(request: Request):
    """PIN-session ID scan. Does not create a packet."""
    import base64

    content_type = request.headers.get("content-type", "")
    session_token = ""
    image_bytes = b""
    filename = "id_photo.jpg"

    if "multipart/form-data" in content_type:
        form = await request.form()
        session_token = str(form.get("session_token") or form.get("token") or "").strip()
        file_obj = form.get("file") or form.get("image") or form.get("id_photo")
        if file_obj and hasattr(file_obj, "read"):
            filename = getattr(file_obj, "filename", "") or filename
            image_bytes = await file_obj.read()
    else:
        try:
            body = (await request.json()) or {}
        except Exception:
            body = {}
        session_token = str(body.get("session_token") or body.get("token") or "").strip()
        raw_b64 = body.get("image_b64") or body.get("image") or ""
        filename = body.get("filename") or filename
        if raw_b64:
            if "," in raw_b64:
                raw_b64 = raw_b64.split(",", 1)[1]
            try:
                image_bytes = base64.b64decode(raw_b64)
            except Exception:
                image_bytes = b""

    session = await _load_verified_session(session_token)
    if not session:
        return JSONResponse({"success": False, "error": "Session expired. Request a new PIN."}, status_code=401)
    if not image_bytes:
        return JSONResponse({"success": False, "error": "No ID image data provided"}, status_code=400)

    from dashboard.services.id_scanner_service import IDScannerService

    result = await IDScannerService.scan_id_image(image_bytes, filename=filename)
    extracted = result.get("extracted") if isinstance(result.get("extracted"), dict) else {}
    pushed = False
    if result.get("success") and extracted:
        role = _normalize_client_role(session.get("role")) or "indemnitor"
        ocr_fields = client_fields_from_id_ocr(extracted, role)
        existing = session.get("client_fields") if isinstance(session.get("client_fields"), dict) else {}
        merged_fields = {**existing, **ocr_fields}
        pins_col = get_collection("portal_pins")
        await pins_col.update_one(
            {"session_token": session_token},
            {"$set": {
                "id_extracted": extracted,
                "id_scanned_at": datetime.now(timezone.utc).isoformat(),
                "role": role,
                "client_fields": merged_fields,
            }},
        )
        session["role"] = role
        logger.info("[PIN Portal] ID OCR stored for phone ...%s", str(session.get("phone") or "")[-4:])
        try:
            pushed = await _push_client_fields_to_issued_packet(session=session, fields=ocr_fields)
        except Exception:
            logger.warning("[PIN Portal] ID OCR DocuSeal update failed", exc_info=True)
            pushed = False
    return JSONResponse({
        "success": bool(result.get("success") and extracted),
        "extracted": extracted,
        "error": result.get("error") if not extracted else None,
        "portrait_jpeg_b64": result.get("portrait_jpeg_b64") or "",
        "pushed_to_docuseal": pushed,
        "role": _normalize_client_role(session.get("role")) or "indemnitor",
    })


@pin_portal_router.post("/selfie")
async def portal_selfie(request: Request):
    """Mark selfie captured on the PIN session. Does not create a packet."""
    try:
        body = (await request.json()) or {}
    except Exception:
        body = {}
    session = await _load_verified_session(str(body.get("session_token") or ""))
    if not session:
        return JSONResponse({"success": False, "error": "Session expired. Request a new PIN."}, status_code=401)
    pins_col = get_collection("portal_pins")
    await pins_col.update_one(
        {"session_token": session.get("session_token")},
        {"$set": {"selfie_captured_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"success": True}


@pin_portal_router.post("/remaining-fields")
async def portal_remaining_fields(req: RemainingFieldsRequest):
    """Save a verified role-specific intake and update DocuSeal only if staff issued it.

    A client may complete this step before staff knows the defendant, bond amount,
    or final case.  In that normal pre-need state, one independent intake record
    is saved for the client and no packet is created.  Existing staff-issued
    DocuSeal packets retain the established field-update behavior.
    """
    session = await _load_verified_session(req.session_token)
    if not session:
        return JSONResponse({"success": False, "error": "Session expired. Request a new PIN."}, status_code=401)

    role = _normalize_client_role(session.get("role")) or _normalize_client_role(req.role)
    if not role:
        return JSONResponse(
            {"success": False, "error": "Choose whether you are the defendant or an indemnitor before saving."},
            status_code=400,
        )
    if not req.staff_review_acknowledged:
        return JSONResponse(
            {"success": False, "error": "Staff-review acknowledgment is required before saving your intake."},
            status_code=400,
        )

    fields = _sanitize_client_fields(req.fields)
    csz = _city_state_zip(fields)
    if csz and role != "defendant":
        fields["indemnitor_city_state_zip"] = csz

    pins_col = get_collection("portal_pins")
    await pins_col.update_one(
        {"session_token": req.session_token},
        {"$set": {
            "client_fields": fields,
            "role": role,
            "address_confirmed": bool(req.address_confirmed) if req.address_confirmed is not None else True,
            "staff_review_acknowledged": True,
            "staff_review_acknowledged_at": datetime.now(timezone.utc).isoformat(),
            "fields_saved": True,
            "fields_saved_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    session["role"] = role

    meta = await _resolve_packet_for_client(
        session.get("phone") or "",
        booking=session.get("booking_number") or "",
        intake=session.get("intake_id") or session.get("client_intake_id") or "",
    )
    pushed = False
    push_error = ""
    packet_id = meta.get("packet_id") or ""
    deferred_intake_id = ""
    if not packet_id:
        deferred_intake_id = await _upsert_deferred_client_intake(
            session=session,
            role=role,
            fields=fields,
            staff_review_acknowledged=True,
        )
    if packet_id and fields:
        try:
            from dashboard.services.paperwork_signers import normalize_role

            packets = get_collection("paperwork_packets")
            packet = await packets.find_one({"packet_id": packet_id})
            submitters = list((packet or {}).get("docuseal_submitters") or [])
            want_role = normalize_role(session.get("role") or meta.get("role") or "")
            session_phone = _digits_phone(session.get("phone") or "")
            target = None
            for item in submitters:
                item_role = normalize_role((item or {}).get("role"))
                if want_role and item_role == want_role:
                    target = item
                    break
            if not target and session_phone:
                for item in submitters:
                    if _digits_phone((item or {}).get("phone")) == session_phone:
                        target = item
                        break
            if not target and submitters:
                target = submitters[0]
            submitter_id = (target or {}).get("id")
            if submitter_id:
                from dashboard.services.docuseal_service import DocuSealService
                from dashboard.services.docuseal_signing_ux import submission_fields_from_values
                svc = DocuSealService()
                await svc.update_submitter(
                    submitter_id,
                    values=fields,
                    fields=submission_fields_from_values(fields, force_editable=True),
                )
                await packets.update_one(
                    {"packet_id": packet_id},
                    {"$set": {
                        "client_fields": fields,
                        "client_fields_updated_at": datetime.now(timezone.utc).isoformat(),
                    }},
                )
                pushed = True
        except Exception as exc:
            push_error = "Could not update the signing packet. You can still sign — fill any blank fields on the document."
            logger.warning("[PIN Portal] remaining-fields DocuSeal update failed: %s", type(exc).__name__)

    return {
        "success": True,
        "saved": True,
        "pushed_to_docuseal": pushed,
        "field_count": len(fields),
        "role": role,
        "intake_id": deferred_intake_id,
        "deferred_for_staff_match": bool(deferred_intake_id),
        "has_packet": bool(meta.get("has_packet")),
        "packet_id": packet_id,
        "signing_link": meta.get("signing_link") or "",
        "message": push_error or (
            "Your information is securely with Shamrock. Staff will match the right people and case, then prepare final paperwork if needed."
            if deferred_intake_id else (meta.get("message") or "")
        ),
    }


def _branded_sign_page(
    *,
    sign_url: str,
    role: str = "",
    party_name: str = "",
    defendant_name: str = "",
) -> str:
    """Self-hosted <docuseal-form> wrapper (official embed, not a 302 to raw UI)."""
    from dashboard.services.docuseal_signing_ux import (
        embed_form_config,
        role_copy,
        sign_origin,
    )
    from dashboard.services.paperwork_signers import normalize_role, ROLE_LABELS

    copy = role_copy(role)
    origin = sign_origin()
    cfg = embed_form_config(src=sign_url, name=party_name, role=role)
    label = ROLE_LABELS.get(normalize_role(role), "Signer")
    safe_who = html_lib.escape(copy["you_are"])
    safe_headline = html_lib.escape(copy["headline"])
    safe_hint = html_lib.escape(copy["hint"])
    safe_def = html_lib.escape(defendant_name or "")
    safe_name = html_lib.escape(party_name or "")
    case_line = f"Bond packet for {safe_def}" if safe_def else "Bond packet"
    cfg_json = json.dumps(cfg)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <meta name="robots" content="noindex, nofollow">
    <meta name="theme-color" content="#0b0f19">
    <title>Shamrock Bail Bonds — Sign paperwork</title>
    <script src="{origin}/js/form.js" defer></script>
    <style>
        :root {{ --bg:#0b0f19; --card:#151c2c; --accent:#22c55e; --text:#f8fafc; --muted:#94a3b8; }}
        * {{ box-sizing:border-box; -webkit-tap-highlight-color:transparent; }}
        html, body {{ margin:0; min-height:100%; background:var(--bg); color:var(--text);
            font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
        .bar {{ padding:calc(12px + env(safe-area-inset-top)) 16px 12px;
            display:flex; justify-content:space-between; align-items:center; gap:10px;
            border-bottom:1px solid rgba(255,255,255,.08); }}
        .brand {{ color:var(--accent); font-weight:800; text-decoration:none; }}
        .help {{ color:var(--accent); font-weight:600; text-decoration:none; min-height:44px; display:inline-flex; align-items:center; }}
        .intro {{ max-width:720px; margin:0 auto; padding:16px 16px 8px; }}
        .intro h1 {{ margin:0 0 6px; font-size:1.35rem; }}
        .intro p {{ margin:0 0 6px; color:var(--muted); line-height:1.45; }}
        .pill {{ display:inline-block; background:rgba(34,197,94,.16); color:var(--accent);
            border-radius:999px; padding:4px 10px; font-size:12px; font-weight:700; margin-bottom:8px; }}
        #docuseal-mount, docuseal-form {{ display:block; min-height:70dvh; width:100%; background:#fff; }}
        .foot {{ text-align:center; padding:16px; color:var(--muted); font-size:13px; }}
        .foot a {{ color:var(--accent); }}
    </style>
</head>
<body>
    <div class="bar">
        <a class="brand" href="/">Shamrock Bail Bonds</a>
        <a class="help" href="tel:+12393322245">(239) 332-2245</a>
    </div>
    <div class="intro">
        <span class="pill">{html_lib.escape(label)}</span>
        <h1>{safe_headline}</h1>
        <p>{safe_who}</p>
        <p>{case_line}{(' · ' + safe_name) if safe_name else ''}</p>
        <p>{safe_hint}</p>
    </div>
    <div id="docuseal-mount"></div>
    <p class="foot">Questions? <a href="tel:+12393322245">(239) 332-2245</a></p>
    <script>
        const CFG = {cfg_json};
        function mountForm() {{
            const mount = document.getElementById('docuseal-mount');
            if (!mount) return;
            if (!window.customElements || !customElements.get('docuseal-form')) {{
                setTimeout(mountForm, 50);
                return;
            }}
            const form = document.createElement('docuseal-form');
            Object.keys(CFG).forEach(function (key) {{ form.setAttribute(key, CFG[key]); }});
            form.id = 'embeddedDocuSeal';
            form.addEventListener('completed', function () {{
                window.location.href = CFG['data-completed-redirect-url'] || '/done';
            }});
            mount.innerHTML = '';
            mount.appendChild(form);
        }}
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', function () {{ setTimeout(mountForm, 0); }});
        }} else {{
            setTimeout(mountForm, 0);
        }}
    </script>
</body>
</html>"""


async def _redirect_to_party_sign(
    packet_id: str,
    role: Optional[str] = None,
    *,
    raw_redirect: bool = False,
):
    """Branded embed by default; ?raw=1 302s to the live /s/{{slug}}."""
    from fastapi.responses import RedirectResponse
    from dashboard.services.paperwork_signers import (
        party_signers_from_packet,
        pick_party,
    )

    packets = get_collection("paperwork_packets")
    doc = None
    try:
        doc = await packets.find_one(
            {
                "packet_id": packet_id,
                "voided": {"$ne": True},
                "status": {"$nin": ["voided", "cancelled", "canceled"]},
            }
        )
    except Exception:
        doc = None
    if not doc:
        try:
            doc = await packets.find_one({"packet_id": packet_id})
        except Exception:
            doc = None
    if not doc:
        return HTMLResponse(
            "<!DOCTYPE html><html><body style='font-family:sans-serif;background:#0b0f19;color:#f8fafc;"
            "padding:32px;text-align:center'><h1>Link not found</h1>"
            "<p>This signing link is expired or invalid. Call (239) 332-2245.</p></body></html>",
            status_code=404,
        )
    parties = party_signers_from_packet(doc)
    chosen = pick_party(parties, role=role)
    url = (chosen or {}).get("sign_url") or _extract_signing_link_from_packet(doc, role=role)
    if not url:
        return HTMLResponse(
            "<!DOCTYPE html><html><body style='font-family:sans-serif;background:#0b0f19;color:#f8fafc;"
            "padding:32px;text-align:center'><h1>Signature not ready</h1>"
            "<p>Ask your bond agent to resend the paperwork. (239) 332-2245.</p></body></html>",
            status_code=404,
        )
    if raw_redirect:
        return RedirectResponse(url=url, status_code=302)
    defendant = str(doc.get("defendant_name") or doc.get("Defendant_Name") or "")
    return HTMLResponse(
        content=_branded_sign_page(
            sign_url=url,
            role=(chosen or {}).get("role") or role or "",
            party_name=(chosen or {}).get("name") or "",
            defendant_name=defendant,
        )
    )


@portal_page_router.get("/sign/{packet_id}")
@portal_page_router.get("/sign/{packet_id}/{role}")
async def public_sign_redirect(request: Request, packet_id: str, role: Optional[str] = None):
    """Branded Shamrock embed of the party's /s/{slug}. ?raw=1 keeps a 302."""
    raw = (request.query_params.get("raw") or "").strip() in ("1", "true", "yes")
    return await _redirect_to_party_sign(packet_id, role, raw_redirect=raw)


def _is_paperwork_host(request: Request) -> bool:
    host = (request.headers.get("host") or request.url.hostname or "").lower().split(":")[0]
    return (
        "paperwork.shamrockbailbonds.biz" in host
        or host.startswith("paperwork.")
        or host == "paperwork.localhost"
    )


@portal_page_router.api_route("/", response_class=HTMLResponse, methods=["GET", "HEAD"])
@portal_page_router.api_route("/done", response_class=HTMLResponse, methods=["GET", "HEAD"])
@portal_page_router.api_route("/paperwork", response_class=HTMLResponse, methods=["GET", "HEAD"])
@pin_portal_router.api_route("/portal-ui", response_class=HTMLResponse, methods=["GET", "HEAD"])
@pin_portal_router.api_route("/done", response_class=HTMLResponse, methods=["GET", "HEAD"])
async def get_portal_ui(request: Request):
    """
    Render lightweight mobile PWA UI for paperwork.shamrockbailbonds.biz
    and /done completion page.

    Host separation:
      - paperwork.*  → indemnitor portal at /
      - leads.* / IP → staff CRM (handled by main.index; this handler must not steal it)
    """
    path = request.url.path or "/"
    # Never hijack staff CRM root on leads host
    if path == "/" and not _is_paperwork_host(request):
        from fastapi.responses import FileResponse
        import os as _os
        dashboard_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        return FileResponse(_os.path.join(dashboard_dir, "index.html"))

    if path.endswith("/done"):
        html_done = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="theme-color" content="#0b0f19">
    <title>Shamrock Bail Bonds — Document Packet Complete</title>
    <style>
        :root { --bg: #0b0f19; --card: #151c2c; --accent: #22c55e; --text: #f8fafc; --muted: #94a3b8; }
        * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); padding: max(20px, env(safe-area-inset-top)) max(20px, env(safe-area-inset-right)) max(20px, env(safe-area-inset-bottom)) max(20px, env(safe-area-inset-left)); text-align: center; min-height: 100dvh; }
        .card { background: var(--card); border-radius: 16px; padding: 32px 24px; max-width: 480px; margin: 40px auto; border: 1px solid rgba(34,197,94,0.3); box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
        .icon { font-size: 48px; margin-bottom: 12px; }
        h1 { font-size: 22px; margin-bottom: 8px; color: var(--accent); }
        p { font-size: 15px; color: var(--muted); line-height: 1.6; }
        .btn { display: inline-block; width: 100%; padding: 16px; background: var(--accent); color: #000; font-weight: 700; border-radius: 12px; text-decoration: none; margin-top: 16px; min-height: 48px; font-size: 16px; }
        @media (min-width: 768px) { .card { max-width: 560px; padding: 40px 32px; } h1 { font-size: 26px; } }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">✅</div>
        <h1>Paperwork Successfully Signed!</h1>
        <p>Thank you. Your document packet has been securely signed and submitted. Our bond agents have been alerted and are processing your release.</p>
        <p>A copy of your signed paperwork has been filed to Drive and sent to your email.</p>
        <a href="tel:2393322245" class="btn">📞 Call Office: (239) 332-2245</a>
        <a href="/" class="btn" style="background:transparent;color:var(--accent);border:1px solid rgba(34,197,94,0.4);margin-top:10px">Sign another packet</a>
    </div>
</body>
</html>"""
        return HTMLResponse(content=html_done)
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <!-- Allow pinch-zoom for form review; Apple Pencil signatures need full touch surface -->
    <meta name="viewport" content="width=device-width, initial-scale=1, minimum-scale=1, maximum-scale=5, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="Shamrock Sign">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="theme-color" content="#0b0f19">
    <meta name="format-detection" content="telephone=yes">
    <title>Shamrock Bail Bonds — Official E-Sign Paperwork Portal</title>
    <script src="https://sign.shamrockbailbonds.biz/js/form.js" defer></script>
    <style>
        :root {
            --bg: #0b0f19;
            --card: #151c2c;
            --accent: #22c55e;
            --accent-hover: #16a34a;
            --text: #f8fafc;
            --muted: #94a3b8;
            --border: rgba(255, 255, 255, 0.08);
            --emerald-glow: rgba(34, 197, 94, 0.2);
            --safe-t: env(safe-area-inset-top, 0px);
            --safe-b: env(safe-area-inset-bottom, 0px);
            --safe-l: env(safe-area-inset-left, 0px);
            --safe-r: env(safe-area-inset-right, 0px);
        }
        * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        html, body { height: 100%; }
        body {
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100dvh;
            display: flex;
            flex-direction: column;
            touch-action: manipulation;
            overscroll-behavior-y: contain;
        }
        body.signing-mode { overflow: hidden; }
        body.signing-mode .navbar,
        body.signing-mode footer { display: none; }
        body.signing-mode .container {
            max-width: 100%;
            padding: 0;
            margin: 0;
            flex: 1;
            min-height: 100dvh;
        }
        body.signing-mode #esign-frame {
            margin: 0;
            border-radius: 0;
            border: none;
            min-height: 100dvh;
            display: flex !important;
            flex-direction: column;
        }
        body.signing-mode #docuseal-mount,
        body.signing-mode docuseal-form {
            flex: 1;
            min-height: calc(100dvh - 52px);
            height: calc(100dvh - 52px);
        }
        .navbar {
            background: rgba(21, 28, 44, 0.95);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--border);
            padding: calc(12px + var(--safe-t)) max(16px, var(--safe-r)) 12px max(16px, var(--safe-l));
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 10px;
            font-weight: 700;
            font-size: 17px;
            color: var(--accent);
            text-decoration: none;
        }
        .brand-logo { font-size: 22px; }
        .nav-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
        .call-btn, .mode-btn {
            background: rgba(34, 197, 94, 0.15);
            color: var(--accent);
            border: 1px solid rgba(34, 197, 94, 0.3);
            padding: 10px 14px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            min-height: 44px;
            cursor: pointer;
            font-family: inherit;
        }
        .mode-btn { background: rgba(59,130,246,0.15); color: #93c5fd; border-color: rgba(59,130,246,0.35); }
        .call-btn:hover, .mode-btn:hover { filter: brightness(1.1); }
        .container {
            flex: 1;
            max-width: 960px;
            width: 100%;
            margin: 0 auto;
            padding: 16px max(16px, var(--safe-r)) max(20px, var(--safe-b)) max(16px, var(--safe-l));
        }
        .card {
            background: var(--card);
            border-radius: 16px;
            padding: 24px 20px;
            max-width: 440px;
            margin: 20px auto;
            border: 1px solid var(--border);
            box-shadow: 0 12px 40px rgba(0,0,0,0.5);
            text-align: center;
        }
        h1 { font-size: 20px; margin: 0 0 8px 0; color: var(--text); }
        p { font-size: 14px; color: var(--muted); line-height: 1.5; margin: 0 0 14px 0; }
        .hint { font-size: 12px; color: var(--muted); margin-top: 10px; line-height: 1.4; }
        .tabs {
            display: flex; gap: 8px; margin: 0 auto 14px; max-width: 440px;
        }
        .tab {
            flex: 1; min-height: 44px; border-radius: 10px; border: 1px solid var(--border);
            background: rgba(255,255,255,0.04); color: var(--muted); font-weight: 600; font-size: 13px;
            cursor: pointer; font-family: inherit;
        }
        .tab.active { background: rgba(34,197,94,0.18); color: var(--accent); border-color: rgba(34,197,94,0.4); }
        input, textarea {
            width: 100%;
            padding: 14px;
            margin: 10px 0;
            border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.15);
            background: rgba(0,0,0,0.4);
            color: var(--text);
            font-size: 16px; /* iOS: prevents auto-zoom */
            text-align: center;
            outline: none;
            font-family: inherit;
            touch-action: manipulation;
        }
        textarea { min-height: 72px; text-align: left; resize: vertical; }
        input:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--emerald-glow); }
        .btn-primary {
            width: 100%;
            padding: 16px;
            background: var(--accent);
            color: #000;
            font-weight: 700;
            border: none;
            border-radius: 12px;
            font-size: 16px;
            cursor: pointer;
            margin-top: 10px;
            min-height: 52px;
            touch-action: manipulation;
            font-family: inherit;
        }
        .btn-primary:active { transform: scale(0.99); }
        .btn-secondary {
            width: 100%; padding: 14px; margin-top: 8px; min-height: 48px;
            background: transparent; color: var(--accent); border: 1px solid rgba(34,197,94,0.4);
            border-radius: 12px; font-weight: 600; font-size: 15px; cursor: pointer; font-family: inherit;
        }
        .status { margin-top: 14px; font-size: 13px; color: var(--muted); line-height: 1.45; }
        .status.error { color: #f87171; }
        .status.success { color: var(--accent); }
        .ipad-banner {
            display: none;
            max-width: 960px; margin: 0 auto 12px; padding: 12px 14px;
            background: rgba(59,130,246,0.12); border: 1px solid rgba(59,130,246,0.35);
            border-radius: 12px; color: #bfdbfe; font-size: 13px; line-height: 1.45; text-align: left;
        }
        .ipad-banner strong { color: #93c5fd; }
        body.in-person .ipad-banner { display: block; }
        
        /* Embedded E-Sign — optimized for Apple Pencil / finger */
        #esign-frame {
            display: none;
            background: #fff;
            border-radius: 16px;
            border: 1px solid var(--border);
            overflow: hidden;
            box-shadow: 0 16px 48px rgba(0,0,0,0.6);
            margin-top: 10px;
            /* Critical for stylus signature capture */
            touch-action: auto;
            -webkit-overflow-scrolling: touch;
        }
        .esign-bar {
            background: #0f172a;
            border-bottom: 1px solid rgba(255,255,255,0.08);
            padding: 10px 14px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            flex-wrap: wrap;
            min-height: 52px;
        }
        .esign-title { font-weight: 700; font-size: 14px; color: var(--accent); display: flex; align-items: center; gap: 8px; }
        .esign-badge { background: rgba(34,197,94,0.15); color: var(--accent); font-size: 12px; padding: 6px 10px; border-radius: 12px; font-weight: 600; }
        .esign-bar-actions { display: flex; gap: 8px; align-items: center; }
        .esign-bar-actions button {
            min-height: 40px; padding: 8px 12px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.15);
            background: rgba(255,255,255,0.06); color: #e2e8f0; font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit;
        }
        #docuseal-mount {
            width: 100%;
            min-height: min(78vh, 900px);
            background: #fff;
            /* Pen/finger: do not block pointer events */
            touch-action: auto;
            overflow: auto;
            -webkit-overflow-scrolling: touch;
        }
        docuseal-form {
            width: 100%;
            min-height: min(78vh, 900px);
            border: none;
            display: block;
            touch-action: auto;
        }
        /* DocuSeal canvas/signature areas — allow free pen strokes */
        docuseal-form, docuseal-form * {
            -webkit-user-select: none;
            user-select: none;
        }
        .id-scan-dropzone {
            border: 2px dashed #3b82f6;
            border-radius: 12px;
            background: rgba(59, 130, 246, 0.06);
            padding: 26px 18px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s ease;
            margin-top: 14px;
        }
        .id-scan-dropzone:hover, .id-scan-dropzone.dragover {
            border-color: #60a5fa;
            background: rgba(59, 130, 246, 0.15);
        }
        .id-extracted-card {
            background: linear-gradient(145deg, rgba(16, 185, 129, 0.12), rgba(15, 23, 42, 0.6));
            border: 1px solid rgba(16, 185, 129, 0.45);
            border-radius: 14px;
            padding: 16px;
            margin-top: 12px;
            text-align: left;
            font-size: 13px;
            box-shadow: 0 12px 32px rgba(0, 0, 0, 0.2);
        }
        .id-extracted-actions {
            margin-top: 14px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .btn-instant-esign {
            background: linear-gradient(135deg, #059669, #10b981) !important;
            color: #fff !important;
            box-shadow: 0 8px 24px rgba(16, 185, 129, 0.28);
            letter-spacing: -0.01em;
        }
        .btn-secondary-ghost {
            background: transparent !important;
            color: #e2e8f0 !important;
            border: 1px solid rgba(148, 163, 184, 0.28) !important;
            font-weight: 600 !important;
        }
        .id-extracted-hint {
            margin: 10px 0 0;
            font-size: 11px;
            color: #64748b;
            line-height: 1.4;
            text-align: center;
        }
        .id-extracted-title {
            color: #34d399;
            font-weight: 700;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .id-extracted-row {
            display: flex;
            justify-content: space-between;
            padding: 4px 0;
            color: #e2e8f0;
            border-bottom: 1px dashed rgba(255,255,255,0.1);
        }
        .id-extracted-row:last-child { border-bottom: none; }
        .id-extracted-label { color: #94a3b8; font-weight: 500; }
        footer {
            border-top: 1px solid var(--border);
            padding: 14px max(16px, var(--safe-r)) max(16px, var(--safe-b)) max(16px, var(--safe-l));
            text-align: center;
            font-size: 12px;
            color: var(--muted);
            margin-top: auto;
        }
        /* Phone */
        @media (max-width: 640px) {
            .brand span:last-child { font-size: 15px; }
            .call-btn span:last-child { display: none; }
            .card { margin: 12px auto; padding: 22px 16px; }
            #docuseal-mount, docuseal-form { min-height: 70vh; }
        }
        /* iPad / tablet — in-person signing desk */
        @media (min-width: 768px) {
            .card { max-width: 520px; padding: 32px 28px; }
            h1 { font-size: 24px; }
            p { font-size: 15px; }
            .btn-primary { min-height: 56px; font-size: 17px; }
            .container { max-width: 1100px; padding: 20px 24px; }
            #docuseal-mount, docuseal-form { min-height: min(82vh, 1100px); }
            .esign-title { font-size: 16px; }
        }
        @media (min-width: 1024px) and (pointer: coarse) {
            /* iPad Pro-class with touch */
            .container { max-width: 100%; }
            #docuseal-mount, docuseal-form { min-height: calc(100dvh - 120px); }
        }
        @media (orientation: landscape) and (min-width: 768px) {
            body.signing-mode #docuseal-mount,
            body.signing-mode docuseal-form {
                min-height: calc(100dvh - 48px);
                height: calc(100dvh - 48px);
            }
        }
    </style>
</head>
<body>
    <header class="navbar">
        <a href="/" class="brand">
            <span class="brand-logo">☘️</span>
            <span>Shamrock Paperwork</span>
        </a>
        <div class="nav-actions">
            <button type="button" class="mode-btn" id="btnInPerson" onclick="toggleInPersonMode()" title="Full-screen iPad + Apple Pencil signing">
                ✍️ iPad / In-person
            </button>
            <a href="tel:2393322245" class="call-btn">
                <span>📞</span>
                <span>(239) 332-2245</span>
            </a>
        </div>
    </header>

    <main class="container">
        <div class="ipad-banner" id="ipadBanner">
            <strong>In-person mode (iPad + Apple Pencil):</strong>
            Use the signing link from Write Bond / DocuSeal, or paste it below.
            Hold the iPad in landscape for the largest signature pad. Stylus strokes are captured on the white form area.
        </div>

        <div class="tabs" id="authTabs">
            <button type="button" class="tab active" id="tabScanId" onclick="showAuthTab('scan')">🪪 Step 1: Scan ID / Passport</button>
            <button type="button" class="tab" id="tabPin" onclick="showAuthTab('pin')">📱 Step 2: Phone PIN</button>
            <button type="button" class="tab" id="tabLink" onclick="showAuthTab('link')">🔗 Signing link</button>
        </div>

        <!-- Auth Card: 6-Digit OTP PIN -->
        <div id="auth-card" class="card">
            <!-- Step 1: ID / Passport AI Scan -->
            <div id="panel-scan-id">
                <h1>🪪 Step 1: Scan ID or Passport</h1>
                <p>Snap a photo or upload your Driver's License, State ID, or Passport to verify identity and auto-fill paperwork.</p>
                <div class="id-scan-dropzone" onclick="document.getElementById('portalIdFileInput').click()" ondragover="event.preventDefault()" ondrop="handlePortalIdDrop(event)">
                    <span style="font-size:36px;display:block;margin-bottom:8px">📸</span>
                    <strong>Tap to take photo or drop ID file here</strong>
                    <span style="display:block;font-size:12px;color:var(--muted);margin-top:4px">Supports Driver's License (FL &amp; all US states), State ID, or Passport</span>
                    <input type="file" id="portalIdFileInput" accept="image/*,application/pdf" style="display:none" onchange="handlePortalIdUpload(this)">
                </div>
                <div id="portalIdResult" style="margin-top:12px"></div>
                <button type="button" class="btn-secondary" style="margin-top:12px;width:100%" onclick="showAuthTab('pin')">Skip to Phone PIN →</button>
            </div>

            <div id="panel-pin" style="display:none">
                <h1>☘️ Official E-Sign Portal</h1>
                <p>Enter your phone number to receive a 6-digit PIN (iMessage / text). Works on phone or iPad.</p>
                <div id="step-phone">
                    <input type="tel" id="phoneInput" placeholder="(239) 555-0199" autocomplete="tel" inputmode="tel" enterkeyhint="send">
                    <button type="button" class="btn-primary" onclick="sendPin()">Send Access PIN</button>
                </div>
                <div id="step-pin" style="display:none">
                    <input type="text" id="pinInput" placeholder="6-Digit PIN" maxlength="6" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" enterkeyhint="go">
                    <button type="button" class="btn-primary" onclick="verifyPin()">Verify &amp; Open Paperwork</button>
                    <button type="button" class="btn-secondary" onclick="resetPinFlow()">Use a different phone</button>
                </div>
            </div>
            <div id="panel-link" style="display:none">
                <h1>✍️ In-person / iPad sign</h1>
                <p>Paste the DocuSeal signing URL from the dashboard (or open a link that already includes <code>?link=</code>).</p>
                <textarea id="linkInput" placeholder="https://sign.shamrockbailbonds.biz/s/..." autocomplete="off"></textarea>
                <button type="button" class="btn-primary" onclick="openLinkFromPaste()">Open packet for signing</button>
                <p class="hint">Staff: after Send DocuSeal, copy the indemnitor sign URL and open it here on the office iPad.</p>
            </div>
            <div id="status" class="status"></div>
        </div>

        <!-- Embedded DocuSeal E-Sign Frame -->
        <div id="esign-frame">
            <div class="esign-bar">
                <div class="esign-title">
                    <span>☘️</span>
                    <span id="esignTitleText">Bond Agreement Packet</span>
                </div>
                <div class="esign-bar-actions">
                    <span class="esign-badge" id="esignBadge">E-Sign</span>
                    <button type="button" onclick="toggleFullscreenSign()" title="Fill the screen for Apple Pencil">⛶ Full screen</button>
                    <button type="button" onclick="exitSigning()" title="Back to PIN / link">← Back</button>
                </div>
            </div>
            <div id="docuseal-mount"></div>
        </div>
    </main>

    <footer>
        ☘️ Shamrock Bail Bonds — 1528 Broadway, Ft. Myers, FL 33901 — Phone · iPad · Apple Pencil ready
    </footer>

    <script>
        let inPerson = false;

        function isTabletOrTouch() {
            return window.matchMedia('(pointer: coarse)').matches || Math.min(screen.width, screen.height) >= 768;
        }

        function toggleInPersonMode(force) {
            inPerson = typeof force === 'boolean' ? force : !inPerson;
            document.body.classList.toggle('in-person', inPerson);
            const btn = document.getElementById('btnInPerson');
            if (btn) btn.textContent = inPerson ? '✓ In-person on' : '✍️ iPad / In-person';
            if (inPerson) showAuthTab('link');
            try { localStorage.setItem('sl_portal_in_person', inPerson ? '1' : '0'); } catch (e) {}
        }

        function showAuthTab(which) {
            const scan = document.getElementById('panel-scan-id');
            const pin = document.getElementById('panel-pin');
            const link = document.getElementById('panel-link');
            const tabScan = document.getElementById('tabScanId');
            const tabPin = document.getElementById('tabPin');
            const tabLink = document.getElementById('tabLink');

            if (scan) scan.style.display = which === 'scan' ? 'block' : 'none';
            if (pin) pin.style.display = which === 'pin' ? 'block' : 'none';
            if (link) link.style.display = which === 'link' ? 'block' : 'none';

            if (tabScan) tabScan.classList.toggle('active', which === 'scan');
            if (tabPin) tabPin.classList.toggle('active', which === 'pin');
            if (tabLink) tabLink.classList.toggle('active', which === 'link');
        }

        function handlePortalIdDrop(e) {
            e.preventDefault();
            e.stopPropagation();
            const files = e.dataTransfer?.files;
            if (files && files.length > 0) processPortalIdScan(files[0]);
        }

        function handlePortalIdUpload(input) {
            if (input.files && input.files.length > 0) processPortalIdScan(input.files[0]);
        }

        function escHtml(s) {
            return String(s == null ? '' : s)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function ensurePhoneSheet() {
            let sheet = document.getElementById('slPhoneSheet');
            if (sheet) return sheet;
            sheet = document.createElement('div');
            sheet.id = 'slPhoneSheet';
            sheet.setAttribute('role', 'dialog');
            sheet.setAttribute('aria-modal', 'true');
            sheet.setAttribute('aria-labelledby', 'slPhoneSheetTitle');
            sheet.innerHTML = `
              <div class="sl-phone-sheet-backdrop" data-close="1"></div>
              <div class="sl-phone-sheet-card">
                <div class="sl-phone-sheet-accent"></div>
                <h3 id="slPhoneSheetTitle">Confirm mobile number</h3>
                <p class="sl-phone-sheet-sub">We use this only to secure your signing session. 10-digit US number.</p>
                <label class="sl-phone-label" for="slPhoneSheetInput">Mobile phone</label>
                <input id="slPhoneSheetInput" type="tel" inputmode="numeric" autocomplete="tel"
                       placeholder="(239) 555-0100" maxlength="16" />
                <p id="slPhoneSheetErr" class="sl-phone-err" hidden></p>
                <div class="sl-phone-sheet-actions">
                  <button type="button" class="sl-phone-btn ghost" data-close="1">Cancel</button>
                  <button type="button" class="sl-phone-btn primary" id="slPhoneSheetContinue">Continue to sign</button>
                </div>
              </div>`;
            document.body.appendChild(sheet);
            if (!document.getElementById('slPhoneSheetStyles')) {
                const st = document.createElement('style');
                st.id = 'slPhoneSheetStyles';
                st.textContent = `
                  #slPhoneSheet{display:none;position:fixed;inset:0;z-index:10050;align-items:flex-end;justify-content:center}
                  #slPhoneSheet.open{display:flex}
                  .sl-phone-sheet-backdrop{position:absolute;inset:0;background:rgba(2,6,23,.72);backdrop-filter:blur(8px)}
                  .sl-phone-sheet-card{position:relative;width:min(440px,100%);margin:0;background:linear-gradient(180deg,#1e293b 0%,#0f172a 100%);
                    border:1px solid rgba(148,163,184,.18);border-radius:20px 20px 0 0;padding:24px 22px 28px;
                    box-shadow:0 -24px 60px rgba(0,0,0,.45);animation:slSheetUp .28s cubic-bezier(.16,1,.3,1)}
                  @media(min-width:640px){#slPhoneSheet{align-items:center}
                    .sl-phone-sheet-card{border-radius:18px;margin:16px;animation:slSheetIn .28s cubic-bezier(.16,1,.3,1)}}
                  @keyframes slSheetUp{from{transform:translateY(24px);opacity:0}to{transform:none;opacity:1}}
                  @keyframes slSheetIn{from{transform:translateY(12px) scale(.98);opacity:0}to{transform:none;opacity:1}}
                  .sl-phone-sheet-accent{height:3px;width:48px;border-radius:999px;background:linear-gradient(90deg,#10b981,#34d399);
                    margin:0 auto 16px}
                  #slPhoneSheet h3{margin:0 0 6px;font-size:1.15rem;font-weight:700;color:#f8fafc;text-align:center;letter-spacing:-.02em}
                  .sl-phone-sheet-sub{margin:0 0 18px;font-size:.85rem;color:#94a3b8;text-align:center;line-height:1.45}
                  .sl-phone-label{display:block;font-size:.72rem;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#64748b;margin-bottom:6px}
                  #slPhoneSheetInput{width:100%;box-sizing:border-box;padding:14px 16px;border-radius:12px;border:1px solid rgba(148,163,184,.25);
                    background:#0f172a;color:#f1f5f9;font-size:1.1rem;letter-spacing:.04em;outline:none;transition:border .15s,box-shadow .15s}
                  #slPhoneSheetInput:focus{border-color:#10b981;box-shadow:0 0 0 3px rgba(16,185,129,.2)}
                  .sl-phone-err{margin:8px 0 0;font-size:.8rem;color:#f87171}
                  .sl-phone-sheet-actions{display:flex;gap:10px;margin-top:18px}
                  .sl-phone-btn{flex:1;min-height:48px;border-radius:12px;font-weight:700;font-size:.92rem;cursor:pointer;border:none;transition:transform .12s,background .15s}
                  .sl-phone-btn:active{transform:scale(.98)}
                  .sl-phone-btn.ghost{background:transparent;border:1px solid rgba(148,163,184,.25);color:#e2e8f0}
                  .sl-phone-btn.primary{background:linear-gradient(135deg,#059669,#10b981);color:#fff;box-shadow:0 8px 24px rgba(16,185,129,.28)}
                  .sl-phone-btn.primary:disabled{opacity:.55;cursor:wait;box-shadow:none}
                `;
                document.head.appendChild(st);
            }
            sheet.addEventListener('click', (e) => {
                if (e.target && e.target.getAttribute('data-close') === '1') closePhoneSheet(null);
            });
            return sheet;
        }

        function closePhoneSheet(value) {
            const sheet = document.getElementById('slPhoneSheet');
            if (sheet) sheet.classList.remove('open');
            if (window._slPhoneResolve) {
                const r = window._slPhoneResolve;
                window._slPhoneResolve = null;
                r(value);
            }
        }

        function askPhoneNumber() {
            return new Promise((resolve) => {
                const sheet = ensurePhoneSheet();
                window._slPhoneResolve = resolve;
                const input = document.getElementById('slPhoneSheetInput');
                const err = document.getElementById('slPhoneSheetErr');
                const go = document.getElementById('slPhoneSheetContinue');
                if (err) { err.hidden = true; err.textContent = ''; }
                if (input) {
                    try {
                        const saved = localStorage.getItem('sl_portal_phone') || '';
                        input.value = saved;
                    } catch (e) { input.value = ''; }
                }
                const submit = () => {
                    const digits = String(input.value || '').replace(/[^0-9]/g, '').slice(-10);
                    if (digits.length !== 10) {
                        if (err) { err.hidden = false; err.textContent = 'Enter a valid 10-digit mobile number.'; }
                        input.focus();
                        return;
                    }
                    try { localStorage.setItem('sl_portal_phone', digits); } catch (e) {}
                    closePhoneSheet(digits);
                };
                go.onclick = submit;
                input.onkeydown = (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); submit(); } };
                sheet.classList.add('open');
                setTimeout(() => input && input.focus(), 50);
            });
        }

        async function processPortalIdScan(file) {
            const resEl = document.getElementById('portalIdResult');
            if (!resEl) return;
            resEl.innerHTML = '<div class="status" style="display:block">📷 Scanning ID with secure OCR…</div>';
            try {
                const formData = new FormData();
                formData.append('file', file);

                const r = await fetch('/api/id/scan-ocr', { method: 'POST', body: formData });
                const d = await r.json();

                if (!d.success || !d.extracted) {
                    resEl.innerHTML = `<div class="status error" style="display:block">❌ ${escHtml(d.error || 'Could not read ID photo. Try a clearer photo.')}</div>`;
                    return;
                }

                const ext = d.extracted;
                try { localStorage.setItem('sl_indemnitor_scanned_profile', JSON.stringify(ext)); } catch (e) {}

                const addrLine = [ext.address, ext.city, ext.state, ext.zip].filter(Boolean).join(', ');
                resEl.innerHTML = `
                    <div class="id-extracted-card">
                        <div class="id-extracted-title">ID verified</div>
                        ${d.portrait_jpeg_b64 ? `<img alt="ID portrait" src="data:image/jpeg;base64,${d.portrait_jpeg_b64}" style="width:72px;height:90px;object-fit:cover;border-radius:6px;margin-bottom:8px">` : ''}
                        ${ext.full_name ? `<div class="id-extracted-row"><span class="id-extracted-label">Name</span><strong>${escHtml(ext.full_name)}</strong></div>` : ''}
                        ${ext.dl_number ? `<div class="id-extracted-row"><span class="id-extracted-label">DL / ID#</span><span>${escHtml(ext.dl_number)} (${escHtml(ext.dl_state || ext.issuing_country || '')})</span></div>` : ''}
                        ${ext.dob ? `<div class="id-extracted-row"><span class="id-extracted-label">DOB</span><span>${escHtml(ext.dob)}</span></div>` : ''}
                        ${addrLine ? `<div class="id-extracted-row"><span class="id-extracted-label">Address</span><span>${escHtml(addrLine)}</span></div>` : ''}
                        ${ext.organ_donor === true ? `<div class="id-extracted-row"><span class="id-extracted-label">Donor</span><span>Yes</span></div>` : ''}
                        ${ext.sex ? `<div class="id-extracted-row"><span class="id-extracted-label">Sex</span><span>${escHtml(ext.sex)}</span></div>` : ''}
                        ${ext.height ? `<div class="id-extracted-row"><span class="id-extracted-label">Height</span><span>${escHtml(ext.height)}</span></div>` : ''}
                        <div class="id-extracted-actions">
                            <button type="button" class="btn-primary" id="btnProceedPin">Continue with secure PIN →</button>
                        </div>
                        <p class="id-extracted-hint">Your bondsman must validate the defendant and bond case before a signing packet is available.</p>
                    </div>
                `;
                const btnPin = document.getElementById('btnProceedPin');
                if (btnPin) btnPin.addEventListener('click', () => showAuthTab('pin'));
            } catch (err) {
                resEl.innerHTML = `<div class="status error" style="display:block">❌ ID scan error: ${escHtml(err.message)}</div>`;
            }
        }

        function checkUrlDirectLink() {
            const params = new URLSearchParams(window.location.search);
            const link = params.get('link') || params.get('s') || params.get('url') || params.get('src');
            const mode = params.get('mode') || params.get('kiosk') || '';
            if (mode === 'ipad' || mode === 'inperson' || mode === 'kiosk' || params.get('inperson') === '1') {
                toggleInPersonMode(true);
            } else {
                try {
                    if (localStorage.getItem('sl_portal_in_person') === '1' || isTabletOrTouch()) {
                        // Soft-enable banner on tablets without forcing link tab
                        document.body.classList.add('in-person');
                        const btn = document.getElementById('btnInPerson');
                        if (btn) btn.textContent = '✓ In-person on';
                        inPerson = true;
                    }
                } catch (e) {}
            }
            if (link && (link.startsWith('http://') || link.startsWith('https://'))) {
                openDocuSealForm(link, { fullscreen: inPerson || isTabletOrTouch() });
            }
        }

        function openLinkFromPaste() {
            let raw = (document.getElementById('linkInput').value || '').trim();
            raw = raw.replace(/^["']|["']$/g, '');
            const statusEl = document.getElementById('status');
            if (!raw) {
                statusEl.className = 'status error';
                statusEl.textContent = 'Paste a DocuSeal signing URL first.';
                return;
            }
            // Accept full URL, domain-relative, or slug path
            let url = raw;
            if (!url.startsWith('http://') && !url.startsWith('https://')) {
                if (url.startsWith('sign.') || url.startsWith('docuseal.') || url.startsWith('paperwork.')) {
                    url = 'https://' + url;
                } else if (url.startsWith('/s/') || url.startsWith('s/')) {
                    url = 'https://sign.shamrockbailbonds.biz' + (url.startsWith('/') ? url : '/' + url);
                } else {
                    statusEl.className = 'status error';
                    statusEl.textContent = 'URL must start with https://... or be a valid signing link.';
                    return;
                }
            }
            openDocuSealForm(url, { fullscreen: true });
        }

        function openDocuSealForm(signUrl, opts) {
            opts = opts || {};
            const auth = document.getElementById('auth-card');
            const tabs = document.getElementById('authTabs');
            if (auth) auth.style.display = 'none';
            if (tabs) tabs.style.display = 'none';
            const frame = document.getElementById('esign-frame');
            const mount = document.getElementById('docuseal-mount');
            frame.style.display = 'block';
            mount.innerHTML = '';

            if (opts.fullscreen || inPerson || isTabletOrTouch()) {
                document.body.classList.add('signing-mode');
            }

            const dsForm = document.createElement('docuseal-form');
            // Official self-hosted embed: data-src=/s/{slug} + data-host (not cloud CDN).
            const embedCfg = {
                'data-src': signUrl,
                'data-host': 'sign.shamrockbailbonds.biz',
                'data-expand': 'true',
                'data-minimize': 'false',
                'data-go-to-last': 'true',
                'data-autoscroll-fields': 'true',
                'data-order-as-on-page': 'true',
                'data-only-required-fields': 'true',
                'data-with-complete-button': 'true',
                'data-with-title': 'false',
                'data-with-field-names': 'false',
                'data-with-field-placeholder': 'true',
                'data-remember-signature': 'true',
                'data-reuse-signature': 'true',
                'data-send-copy-email': 'false',
                'data-allow-typed-signature': 'true',
                'data-completed-message-title': 'You are done',
                'data-completed-message-body': 'Thank you. Shamrock has your signature. Call (239) 332-2245 if you need anything else.',
                'data-completed-button-title': 'All set',
                'data-completed-redirect-url': '/done',
                'data-completed-button-url': '/done',
                'data-custom-css': '.submit-form-button,.expand-form-button,.start-form-submit-button,.completed-form-completed-button{background-color:#16a34a;border:0;border-radius:12px;color:#052e16;min-height:48px;font-weight:700;font-size:16px}.draw-canvas{border-radius:12px;min-height:140px;background:#fff}.field-area-active{border-color:#16a34a;outline-color:#22c55e}.field-area-active-label{background-color:#16a34a;color:#052e16}',
                'data-i18n': '{"submit":"Continue","complete":"Finish signing","next":"Next","type":"Type name","draw":"Draw signature","upload":"Upload","clear":"Clear"}'
            };
            if (opts.email) embedCfg['data-email'] = opts.email;
            if (opts.name) embedCfg['data-name'] = opts.name;
            if (opts.role) embedCfg['data-role'] = opts.role;
            Object.keys(embedCfg).forEach(function (key) { dsForm.setAttribute(key, embedCfg[key]); });
            dsForm.id = 'embeddedDocuSeal';
            // Allow stylus / multi-touch on the host element
            dsForm.style.touchAction = 'auto';
            dsForm.style.minHeight = '100%';
            mount.appendChild(dsForm);

            const title = document.getElementById('esignTitleText');
            if (title) title.textContent = opts.title || 'Bond Agreement Packet — Sign with finger or Apple Pencil';

            dsForm.addEventListener('completed', function () {
                window.location.href = '/done';
            });
            // Some DocuSeal builds emit load errors without crashing
            dsForm.addEventListener('error', function () {
                const statusEl = document.getElementById('status');
                if (statusEl) {
                    if (auth) auth.style.display = 'block';
                    statusEl.className = 'status error';
                    statusEl.textContent = 'Could not load signing form. Check the link or call (239) 332-2245.';
                }
            });

            // Scroll signing surface into view (iPad Safari)
            try { frame.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) {}
        }

        function toggleFullscreenSign() {
            document.body.classList.toggle('signing-mode');
        }

        function exitSigning() {
            document.body.classList.remove('signing-mode');
            const frame = document.getElementById('esign-frame');
            const mount = document.getElementById('docuseal-mount');
            const auth = document.getElementById('auth-card');
            const tabs = document.getElementById('authTabs');
            if (frame) frame.style.display = 'none';
            if (mount) mount.innerHTML = '';
            if (auth) auth.style.display = 'block';
            if (tabs) tabs.style.display = 'flex';
        }

        function resetPinFlow() {
            document.getElementById('step-phone').style.display = 'block';
            document.getElementById('step-pin').style.display = 'none';
            document.getElementById('pinInput').value = '';
            document.getElementById('status').textContent = '';
        }

        async function sendPin() {
            const phone = document.getElementById('phoneInput').value;
            const statusEl = document.getElementById('status');
            statusEl.className = 'status';
            statusEl.textContent = 'Sending PIN via BlueBubbles...';
            try {
                const r = await fetch('/api/portal/send-pin', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({phone})
                });
                const d = await r.json();
                if (d.success) {
                    document.getElementById('step-phone').style.display = 'none';
                    document.getElementById('step-pin').style.display = 'block';
                    let how = 'iMessage / text';
                    if (d.channel === 'imessage') how = 'iMessage';
                    else if (d.channel === 'sms') how = 'text message';
                    else if (d.queued || d.channel === 'queued') how = 'message queue (delivering shortly)';
                    statusEl.className = 'status success';
                    statusEl.textContent = '✅ PIN sent via ' + how + '. Check your phone.';
                    try { document.getElementById('pinInput').focus(); } catch (e) {}
                } else {
                    statusEl.className = 'status error';
                    statusEl.textContent = '❌ Error: ' + (d.error || 'Failed to send PIN');
                }
            } catch (err) {
                statusEl.className = 'status error';
                statusEl.textContent = '❌ Network error sending PIN';
            }
        }

        async function verifyPin() {
            const phone = document.getElementById('phoneInput').value;
            const pin = document.getElementById('pinInput').value;
            const statusEl = document.getElementById('status');
            statusEl.className = 'status';
            statusEl.textContent = 'Verifying PIN...';
            try {
                const r = await fetch('/api/portal/verify-pin', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({phone, pin})
                });
                const d = await r.json();
                if (d.success) {
                    if (d.signing_link) {
                        statusEl.className = 'status success';
                        const who = d.defendant_name ? (' for ' + d.defendant_name) : '';
                        statusEl.textContent = '✅ Verified' + who + ' — opening e-sign packet...';
                        openDocuSealForm(d.signing_link, {
                            title: d.defendant_name ? ('Packet — ' + d.defendant_name) : 'Bond Agreement Packet',
                            fullscreen: inPerson || isTabletOrTouch(),
                            role: d.role || '',
                            name: d.name || '',
                            email: d.email || '',
                        });
                    } else {
                        statusEl.className = 'status error';
                        statusEl.textContent = d.message
                            || (d.has_packet
                                ? '✅ Verified — e-sign link not ready yet. Call (239) 332-2245.'
                                : '✅ Verified — no packet on file for this phone. Call (239) 332-2245.');
                    }
                } else {
                    statusEl.className = 'status error';
                    statusEl.textContent = '❌ ' + (d.error || 'Invalid PIN');
                }
            } catch (err) {
                statusEl.className = 'status error';
                statusEl.textContent = '❌ Network error verifying PIN';
            }
        }

        // Enter key handlers
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter') return;
            const t = e.target && e.target.id;
            if (t === 'phoneInput') { e.preventDefault(); sendPin(); }
            if (t === 'pinInput') { e.preventDefault(); verifyPin(); }
        });

        window.addEventListener('DOMContentLoaded', checkUrlDirectLink);
        // Prevent accidental pull-to-refresh during pen signing on iOS
        document.addEventListener('touchmove', function (e) {
            if (document.body.classList.contains('signing-mode') && e.touches.length > 1) {
                /* allow pinch */ return;
            }
        }, { passive: true });
    </script>
</body>
</html>"""
    return HTMLResponse(content=html)
