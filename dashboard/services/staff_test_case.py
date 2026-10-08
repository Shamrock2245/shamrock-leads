"""Staff-only Write Bond smoke cases.

Off unless ``STAFF_TEST_CASE_MODE=1`` and the request sets ``test_case`` true.
A test case uses a ``TEST-`` booking and a ``PKT-TEST-`` packet. It never
matches or updates a real arrest, bond, defendant, or POA inventory row.

DocuSeal submitters are forced to one allowlisted staff address, with phones
removed and ``send_email`` / ``send_sms`` false. Real inventory powers are
not read unless ``STAFF_TEST_CASE_REAL_POWER=1`` and the request also sets
``allow_real_power`` true.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from dashboard.auth.pin_middleware import (
    DASHBOARD_PIN,
    get_session_from_request,
    is_machine_auth_valid,
    session_is_admin,
    session_is_god_admin,
)
from dashboard.auth.recovery_scope import session_is_staff

logger = logging.getLogger(__name__)

ENV_MODE = "STAFF_TEST_CASE_MODE"
ENV_REAL_POWER = "STAFF_TEST_CASE_REAL_POWER"
ENV_SIGNER_EMAIL = "STAFF_TEST_CASE_SIGNER_EMAIL"
ENV_EMAIL_ALLOWLIST = "STAFF_TEST_CASE_EMAIL_ALLOWLIST"

DEFAULT_SIGNER_EMAIL = "admin@shamrockbailbonds.biz"
_STAFF_EMAIL_DOMAIN = "@shamrockbailbonds.biz"

# Uppercase reserved namespace. Lowercase fixtures such as pkt-test-0002 are
# not staff test cases.
_BOOKING_RE = re.compile(r"^TEST-[A-Z0-9][A-Z0-9-]{0,48}$")
_PACKET_RE = re.compile(r"^PKT-TEST-[A-Z0-9][A-Z0-9-]{0,48}$")
_CASE_RE = _BOOKING_RE
_POA_RE = _BOOKING_RE

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_PHONE_KEY = re.compile(r"phone", re.IGNORECASE)
_EMAIL_KEY = re.compile(r"email", re.IGNORECASE)
_PAY_KEY = re.compile(r"(payment|swipesimple|premium|invoice|pay_link)", re.IGNORECASE)

_IDENTITY_COLLECTIONS = (
    ("arrests", "real_arrest_refused", "Test mode cannot use or modify a real arrest record."),
    ("active_bonds", "real_bond_refused", "Test mode cannot use or modify a real bond."),
    ("bond_cases", "real_bond_refused", "Test mode cannot use or modify a real bond case."),
    ("defendants", "real_defendant_refused", "Test mode cannot use or modify a real defendant."),
)


class StaffTestCaseError(Exception):
    """Fail closed. ``code`` is the stable machine error."""

    def __init__(self, code: str, message: str, status_code: int = 403):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class StaffTestCasePlan:
    """Synthetic case the finalize route may bind. Not an inventory power."""

    context: dict
    signer_email: str
    packet_id: str
    use_real_power: bool
    poa_record: dict
    actor: str


def _truthy(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return False


def _env_on(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in _TRUTHY


def staff_test_case_mode_enabled() -> bool:
    return _env_on(ENV_MODE)


def real_power_mode_enabled() -> bool:
    return _env_on(ENV_REAL_POWER)


def request_asks_for_test_case(body: Any) -> bool:
    """True only when this request explicitly asks for a staff test case."""
    if not isinstance(body, Mapping):
        return False
    return _truthy(body.get("test_case"))


def is_test_booking(value: Any) -> bool:
    return bool(_BOOKING_RE.match(str(value or "").strip()))


def is_test_packet_id(value: Any) -> bool:
    return bool(_PACKET_RE.match(str(value or "").strip()))


def packet_is_staff_test(packet: Optional[Mapping[str, Any]]) -> bool:
    """True for a packet this mode created, or a PKT-TEST- id.

    A booking that merely starts with TEST- is not enough. Existing fixtures
    use bookings such as TEST-BK-0002 on ordinary packets.
    """
    if not isinstance(packet, Mapping):
        return False
    if packet.get("is_test") is True:
        return True
    return is_test_packet_id(packet.get("packet_id"))


def audit_test_marker(packet_id: str, booking_number: str = "") -> dict:
    """Fields every test-mode audit_events row must carry."""
    marker = {
        "is_test": True,
        "test_case": True,
        "packet_id": str(packet_id or "").strip(),
    }
    booking = str(booking_number or "").strip()
    if booking:
        marker["booking_number"] = booking
    return marker


def docuseal_event_is_test(
    packet: Optional[Mapping[str, Any]],
    packet_id_hint: str = "",
) -> bool:
    if packet_is_staff_test(packet):
        return True
    return is_test_packet_id(packet_id_hint)


def resolve_signer_email() -> str:
    """Staff inbox for every DocuSeal submitter. Default is the office admin.

    ``STAFF_TEST_CASE_SIGNER_EMAIL`` is accepted only when it is on
    ``STAFF_TEST_CASE_EMAIL_ALLOWLIST`` and is a @shamrockbailbonds.biz address.
    The office admin address is always allowlisted.
    """
    configured = (os.getenv(ENV_SIGNER_EMAIL) or "").strip().lower()
    allow = {DEFAULT_SIGNER_EMAIL}
    extra = os.getenv(ENV_EMAIL_ALLOWLIST) or ""
    for part in extra.split(","):
        item = part.strip().lower()
        if item:
            allow.add(item)
    if not configured or configured == DEFAULT_SIGNER_EMAIL:
        return DEFAULT_SIGNER_EMAIL
    if configured not in allow or not configured.endswith(_STAFF_EMAIL_DOMAIN):
        raise StaffTestCaseError(
            "signer_email_not_allowlisted",
            "Staff test cases can only email an allowlisted @shamrockbailbonds.biz address.",
            403,
        )
    return configured


def scrub_test_contacts(value: Any, signer_email: str) -> Any:
    """Blank phone and payment fields. Force email fields to the staff inbox."""
    if isinstance(value, dict):
        out: dict = {}
        for key, item in value.items():
            if _PHONE_KEY.search(str(key)):
                out[key] = "" if not isinstance(item, (dict, list)) else scrub_test_contacts(item, signer_email)
            elif _EMAIL_KEY.search(str(key)) and not isinstance(item, (dict, list)):
                out[key] = signer_email
            elif _PAY_KEY.search(str(key)) and not isinstance(item, (dict, list)):
                out[key] = 0 if isinstance(item, (int, float)) and not isinstance(item, bool) else ""
            elif isinstance(item, (dict, list)):
                out[key] = scrub_test_contacts(item, signer_email)
            else:
                out[key] = item
        return out
    if isinstance(value, list):
        return [scrub_test_contacts(item, signer_email) if isinstance(item, (dict, list)) else item for item in value]
    return value


def apply_staff_test_contacts(bond_data: dict, signer_email: str) -> dict:
    """Rewrite a packet payload so DocuSeal cannot contact a real person."""
    if not isinstance(bond_data, dict):
        return bond_data
    scrubbed = scrub_test_contacts(bond_data, signer_email)
    bond_data.clear()
    bond_data.update(scrubbed)
    bond_data["is_test"] = True
    bond_data["test_case"] = True
    bond_data["send_email"] = False
    bond_data["send_sms"] = False
    bond_data["staff_test_signer_email"] = signer_email
    bond_data["premium_amount"] = 0
    for party_key in ("defendant", "indemnitor"):
        party = bond_data.get(party_key)
        if isinstance(party, dict):
            party["email"] = signer_email
            party["phone"] = ""
    parties = bond_data.get("indemnitors")
    if isinstance(parties, list):
        for party in parties:
            if isinstance(party, dict):
                party["email"] = signer_email
                party["phone"] = ""
    return bond_data


def force_test_submitter(submitter: Mapping[str, Any], signer_email: str) -> dict:
    """One DocuSeal submitter: staff email, no phone, send flags false."""
    out = dict(submitter)
    out["email"] = signer_email
    out.pop("phone", None)
    out["send_email"] = False
    out["send_sms"] = False
    values = out.get("values")
    if isinstance(values, (dict, list)):
        out["values"] = scrub_test_contacts(values, signer_email)
    fields = out.get("fields")
    if isinstance(fields, list):
        cleaned = []
        for field in fields:
            if not isinstance(field, dict):
                cleaned.append(field)
                continue
            name = str(field.get("name") or "")
            if _PHONE_KEY.search(name) or _PAY_KEY.search(name):
                continue
            if _EMAIL_KEY.search(name):
                field = dict(field)
                if "default_value" in field:
                    field["default_value"] = signer_email
                if "value" in field:
                    field["value"] = signer_email
            cleaned.append(field)
        out["fields"] = cleaned
    meta = out.get("metadata")
    if isinstance(meta, dict):
        out["metadata"] = scrub_test_contacts(meta, signer_email)
    return out


def _authorize(request, body: Mapping[str, Any]) -> str:
    """PIN admin/staff session, machine token, or dashboard PIN header."""
    sess = get_session_from_request(request)
    if sess and sess.get("auth"):
        role = str(sess.get("role") or "").strip().lower()
        if role != "sub_agent" and (
            session_is_staff(request)
            or session_is_god_admin(request)
            or session_is_admin(request)
            or role in ("staff", "admin", "god_admin")
        ):
            return str(sess.get("email") or sess.get("agent_name") or "staff_session")

    if is_machine_auth_valid(request):
        return request.headers.get("X-Staff-Email", "").strip() or "machine_key"

    admin_token = (
        request.headers.get("X-Admin-Token")
        or request.headers.get("X-PIN")
        or request.headers.get("X-Staff-PIN")
        or ""
    ).strip()
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer_val = auth_header[7:].strip()
        if bearer_val:
            admin_token = bearer_val
    active_pin = (os.getenv("DASHBOARD_PIN") or DASHBOARD_PIN or "").strip()
    if active_pin and admin_token and secrets.compare_digest(admin_token, active_pin):
        return request.headers.get("X-Staff-Email", "").strip() or "god_admin"
    raise StaffTestCaseError(
        "staff_session_required",
        "Staff test cases require a PIN admin or staff session.",
        401,
    )


def _text(body: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = body.get(key)
        if value is None or isinstance(value, (dict, list, bool)):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _packet_id(body: Mapping[str, Any]) -> str:
    raw = _text(body, "packet_id")
    if not raw:
        return f"PKT-TEST-{uuid.uuid4().hex[:10].upper()}"
    if not is_test_packet_id(raw):
        raise StaffTestCaseError(
            "test_packet_id_required",
            "Staff test packets must use a packet_id prefixed PKT-TEST-.",
            409,
        )
    return raw


def _amount(body: Mapping[str, Any]) -> float:
    raw = body.get("bond_amount")
    if raw is None or raw == "":
        return 5000.0
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        raise StaffTestCaseError(
            "test_bond_amount_invalid",
            "Staff test cases need a numeric bond_amount.",
            400,
        )
    if amount <= 0:
        raise StaffTestCaseError(
            "test_bond_amount_invalid",
            "Staff test cases need a bond_amount greater than zero.",
            400,
        )
    return amount


async def _refuse_real_identity(booking: str) -> None:
    """Fail closed when this TEST- booking is already a real record.

    Matching documents are only read. Nothing in those collections is updated.
    """
    from dashboard.extensions import get_collection

    booking_filter = {
        "$or": [
            {"Booking_Number": booking},
            {"booking_number": booking},
        ]
    }
    for name, code, message in _IDENTITY_COLLECTIONS:
        try:
            col = get_collection(name)
            cursor = col.find(booking_filter, {"_id": 0, "is_test": 1, "booking_number": 1, "Booking_Number": 1})
            docs = await cursor.to_list(length=5)
        except Exception as exc:
            logger.warning(
                "[staff-test-case] identity lookup failed closed collection=%s error_type=%s",
                name,
                type(exc).__name__,
            )
            raise StaffTestCaseError(
                "test_case_lookup_failed",
                "Test mode could not verify this booking is not a real record.",
                503,
            ) from exc
        for doc in docs or []:
            if not isinstance(doc, Mapping) or doc.get("is_test") is not True:
                raise StaffTestCaseError(code, message, 409)
            stored = str(doc.get("booking_number") or doc.get("Booking_Number") or booking).strip()
            if not is_test_booking(stored):
                raise StaffTestCaseError(
                    "real_booking_not_testable",
                    "A real booking cannot be flagged as a test case.",
                    409,
                )


def _synthetic_context(
    body: Mapping[str, Any],
    *,
    booking: str,
    case_number: str,
    poa_number: str,
    signer_email: str,
    bond_amount: float,
) -> dict:
    suffix = booking[5:]
    defendant_name = _text(body, "defendant_name") or "Sample Party One"
    indemnitor_name = _text(body, "indemnitor_name") or "Sample Party Two"
    license_no = _text(body, "license_number", "writing_agent_license", "agent_license")
    agent_name = _text(body, "agent_name", "writing_agent_name", "bondsman_name")
    ctx = {
        "is_test": True,
        "test_case": True,
        "sources": ["staff_test_case"],
        "booking_number": booking,
        "case_number": case_number,
        "county": _text(body, "county") or "Lee",
        "state": _text(body, "state") or "FL",
        "surety_id": (_text(body, "surety_id") or "osi").lower(),
        "bond_amount": bond_amount,
        "premium_amount": 0,
        "poa_number": poa_number,
        "match_status": "validated",
        "match_id": f"TEST-MATCH-{suffix}",
        "bond_case_id": f"TEST-BOND-{suffix}",
        "defendant_id": f"TEST-DEF-{suffix}",
        "indemnitor_id": f"TEST-IND-{suffix}",
        "charges": _text(body, "charges") or "Sample charge",
        "defendant": {
            "name": defendant_name,
            "email": signer_email,
            "phone": "",
        },
        "indemnitor": {
            "name": indemnitor_name,
            "email": signer_email,
            "phone": "",
        },
        "indemnitors": [
            {"name": indemnitor_name, "email": signer_email, "phone": ""},
        ],
    }
    # License-only records resolve through the existing agent pair. A name is
    # copied only when the request actually sent one.
    if license_no and not agent_name:
        ctx["license_number"] = license_no
    elif agent_name or license_no:
        if agent_name:
            ctx["agent_name"] = agent_name
        if license_no:
            ctx["license_number"] = license_no
    return ctx


async def prepare_staff_test_case(request, body: Mapping[str, Any]) -> StaffTestCasePlan:
    """Authorize and build a synthetic TEST- case, or fail closed.

    Callers must already know the request asked for test mode. This never
    falls through to a real booking.
    """
    if not staff_test_case_mode_enabled():
        raise StaffTestCaseError(
            "staff_test_case_disabled",
            "Staff test cases are off. Set STAFF_TEST_CASE_MODE=1 to enable them.",
            403,
        )
    actor = _authorize(request, body)
    signer_email = resolve_signer_email()

    booking = _text(body, "booking_number", "booking")
    if not is_test_booking(booking):
        raise StaffTestCaseError(
            "test_booking_required",
            "Test mode only accepts booking numbers prefixed TEST-.",
            409,
        )
    case_number = _text(body, "case_number") or f"TEST-CASE-{booking[5:]}"
    if not is_test_booking(case_number):
        raise StaffTestCaseError(
            "test_case_number_required",
            "Test mode only accepts case numbers prefixed TEST-.",
            409,
        )

    poa_number = _text(body, "poa_number") or "TEST-POA-0001"
    use_real_power = False
    if is_test_booking(poa_number):
        use_real_power = False
    elif _truthy(body.get("allow_real_power")) and real_power_mode_enabled():
        use_real_power = True
    else:
        raise StaffTestCaseError(
            "real_power_not_authorized",
            "Test mode does not use a real power unless STAFF_TEST_CASE_REAL_POWER=1 "
            "and allow_real_power is true. Default is a TEST- power that is not in inventory.",
            409,
        )

    await _refuse_real_identity(booking)
    bond_amount = _amount(body)
    ctx = _synthetic_context(
        body,
        booking=booking,
        case_number=case_number,
        poa_number=poa_number,
        signer_email=signer_email,
        bond_amount=bond_amount,
    )
    # A phone on the request is kept only until the DocuSeal payload is scrubbed.
    supplied_phone = _text(body, "indemnitor_phone", "defendant_phone", "phone")
    if supplied_phone:
        ctx["defendant"]["phone"] = supplied_phone
        ctx["indemnitor"]["phone"] = supplied_phone
        ctx["indemnitors"][0]["phone"] = supplied_phone
    poa_record = {
        "poa_number": poa_number,
        "is_test": True,
        "source": "staff_test_case",
        "max_bond_value": bond_amount,
    }
    return StaffTestCasePlan(
        context=ctx,
        signer_email=signer_email,
        packet_id=_packet_id(body),
        use_real_power=use_real_power,
        poa_record=poa_record,
        actor=actor,
    )
