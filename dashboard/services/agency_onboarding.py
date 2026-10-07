"""Agency onboarding records. No email, no SMS, no payment calls.

Invites are stored on the tenant document with a token so a later mailer can
send them. This module does not import a mailer or an HTTP client.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any

from dashboard.tenancy.constants import SHAMROCK_TENANT_ID
from dashboard.tenancy.context import bind_platform_job, normalize_slug
from dashboard.tenancy.flag import multi_tenant_enabled

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LICENSE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{2,39}$")
_SECRET_REF = re.compile(r"^env:[A-Z][A-Z0-9_]{2,60}$")
_ROLES = frozenset({"owner", "admin", "staff", "sub_agent", "recovery"})
_TEXTING = frozenset({"", "none", "bluebubbles", "twilio"})
_PAYMENTS = frozenset({"", "none", "swipesimple", "later"})
_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "password",
        "secret",
        "token",
        "card",
        "pan",
        "smtp",
    }
)


class OnboardingError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class OnboardingDisabled(OnboardingError):
    def __init__(self):
        super().__init__("disabled")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _reject_secret_fields(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in _FORBIDDEN_KEYS:
                raise OnboardingError("secret_ref_only")
            _reject_secret_fields(value)
    elif isinstance(payload, list):
        for item in payload:
            _reject_secret_fields(item)


def _email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if not _EMAIL.match(email) or len(email) > 120:
        raise OnboardingError("owner_email_invalid")
    return email


def _text(value: Any, *, code: str, limit: int = 120) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or "\n" in text or "\r" in text:
        raise OnboardingError(code)
    return text


def _slug_for(payload: dict, legal_name: str) -> str:
    raw = payload.get("slug") or legal_name
    slug = normalize_slug(re.sub(r"[^a-z0-9]+", "_", str(raw).strip().lower()).strip("_"))
    if not slug or slug == SHAMROCK_TENANT_ID:
        raise OnboardingError("slug_invalid")
    return slug


def _licenses(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise OnboardingError("licenses_invalid")
    rows = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            raise OnboardingError("licenses_invalid")
        number = str(item.get("number") or "").strip()
        state = str(item.get("state") or "").strip().upper()
        name = str(item.get("name_on_license") or "").strip()
        if not _LICENSE.match(number) or not re.fullmatch(r"[A-Z]{2}", state):
            raise OnboardingError("licenses_invalid")
        if len(name) > 120:
            raise OnboardingError("licenses_invalid")
        rows.append({"number": number, "state": state, "name_on_license": name})
    return rows


def _branding(raw: Any, display_name: str) -> dict[str, str]:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise OnboardingError("branding_invalid")
    color = str(raw.get("primary_color") or "#0b6b3a").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise OnboardingError("branding_invalid")
    logo = str(raw.get("logo_url") or "").strip()
    if len(logo) > 100_000:
        raise OnboardingError("logo_too_large")
    if logo and not (logo.startswith("https://") or logo.startswith("data:image/")):
        raise OnboardingError("branding_invalid")
    return {
        "display_name": str(raw.get("display_name") or display_name).strip()[:120],
        "primary_color": color,
        "logo_url": logo,
    }


def _invites(raw: Any, owner_email: str) -> list[dict[str, str]]:
    raw = raw or []
    if not isinstance(raw, list):
        raise OnboardingError("invites_invalid")
    rows = []
    seen = set()
    for item in raw[:30]:
        if not isinstance(item, dict):
            raise OnboardingError("invites_invalid")
        email = _email(item.get("email"))
        role = str(item.get("role") or "").strip().lower()
        if role not in _ROLES or email in seen:
            raise OnboardingError("invites_invalid")
        seen.add(email)
        rows.append(
            {
                "email": email,
                "role": role,
                "token": secrets.token_urlsafe(24),
                "status": "recorded",
            }
        )
    if owner_email not in seen:
        rows.insert(
            0,
            {
                "email": owner_email,
                "role": "owner",
                "token": secrets.token_urlsafe(24),
                "status": "recorded",
            },
        )
    return rows


def _integration(raw: Any, *, allowed: frozenset[str], code: str) -> dict[str, str]:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise OnboardingError(code)
    provider = str(raw.get("provider") or "none").strip().lower()
    if provider not in allowed:
        raise OnboardingError(code)
    secret_ref = str(raw.get("secret_ref") or "").strip()
    if secret_ref and not _SECRET_REF.match(secret_ref):
        raise OnboardingError("secret_ref_only")
    status = "connected" if secret_ref else "skipped"
    return {"provider": provider or "none", "secret_ref": secret_ref, "status": status}


def build_agency_record(
    payload: dict,
    *,
    source: str,
    actor: str,
    approve_now: bool,
) -> dict[str, Any]:
    """Validate a wizard payload into a tenant document. Does not write."""
    if not isinstance(payload, dict):
        raise OnboardingError("invalid")
    _reject_secret_fields(payload)
    legal_name = _text(payload.get("legal_name"), code="legal_name_required")
    owner = payload.get("owner") or {}
    if not isinstance(owner, dict):
        raise OnboardingError("owner_email_invalid")
    owner_name = _text(owner.get("name"), code="owner_name_required")
    owner_email = _email(owner.get("email"))
    phone = str(owner.get("phone") or "").strip()
    if len(phone) > 32 or "\n" in phone:
        raise OnboardingError("owner_phone_invalid")
    home_state = str(payload.get("home_state") or "FL").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", home_state):
        raise OnboardingError("home_state_invalid")
    integrations = payload.get("integrations") or {}
    if not isinstance(integrations, dict):
        raise OnboardingError("integrations_invalid")
    now = _now()
    status = "pending_approval"
    approved_at = ""
    if approve_now and source == "super_admin":
        status = "active"
        approved_at = now
    return {
        "tenant_id": _slug_for(payload, legal_name),
        "legal_name": legal_name,
        "status": status,
        "plan": "unbilled",
        "home_state": home_state,
        "owner": {"name": owner_name, "email": owner_email, "phone": phone},
        "licenses": _licenses(payload.get("licenses")),
        "branding": _branding(payload.get("branding"), legal_name),
        "invites": _invites(payload.get("invites"), owner_email),
        "integrations": {
            "texting": _integration(
                integrations.get("texting"), allowed=_TEXTING, code="integrations_invalid"
            ),
            "payments": _integration(
                integrations.get("payments"), allowed=_PAYMENTS, code="integrations_invalid"
            ),
        },
        "signup_source": source,
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
        "approved_at": approved_at,
        "approved_by": actor if status == "active" else "",
        "rejected_reason": "",
        "invites_sent": 0,
    }


def public_agency_view(doc: dict, *, include_invites: bool) -> dict[str, Any]:
    view = {
        "tenant_id": doc.get("tenant_id"),
        "legal_name": doc.get("legal_name"),
        "status": doc.get("status"),
        "home_state": doc.get("home_state"),
        "plan": doc.get("plan"),
        "signup_source": doc.get("signup_source"),
        "owner": {"name": (doc.get("owner") or {}).get("name"), "email": (doc.get("owner") or {}).get("email")},
        "license_count": len(doc.get("licenses") or []),
        "branding": {
            "display_name": (doc.get("branding") or {}).get("display_name"),
            "primary_color": (doc.get("branding") or {}).get("primary_color"),
            "has_logo": bool((doc.get("branding") or {}).get("logo_url")),
        },
        "integrations": doc.get("integrations") or {},
        "invites_sent": 0,
        "created_at": doc.get("created_at"),
    }
    if include_invites:
        view["invites"] = [
            {"email": row.get("email"), "role": row.get("role"), "status": row.get("status")}
            for row in doc.get("invites") or []
        ]
    return view


async def _tenants():
    from dashboard.extensions import get_collection

    return get_collection("tenants")


async def create_agency(payload: dict, *, source: str, actor: str, approve_now: bool = False) -> dict:
    if not multi_tenant_enabled():
        raise OnboardingDisabled()
    if source not in {"self_serve", "super_admin"}:
        raise OnboardingError("invalid")
    if source != "super_admin":
        approve_now = False
    record = build_agency_record(payload, source=source, actor=actor, approve_now=approve_now)
    with bind_platform_job("agency_onboarding"):
        col = await _tenants()
        existing = await col.find_one({"tenant_id": record["tenant_id"]})
        if existing:
            raise OnboardingError("slug_taken")
        await col.insert_one(record)
    return public_agency_view(record, include_invites=source == "super_admin")


async def list_agencies() -> list[dict]:
    if not multi_tenant_enabled():
        raise OnboardingDisabled()
    with bind_platform_job("agency_onboarding"):
        col = await _tenants()
        cursor = col.find({})
        rows = []
        if hasattr(cursor, "__aiter__"):
            async for doc in cursor:
                rows.append(doc)
        else:
            rows = list(cursor)
    return [public_agency_view(doc, include_invites=True) for doc in rows]


async def decide_agency(tenant_id: str, *, approve: bool, actor: str, reason: str = "") -> dict:
    if not multi_tenant_enabled():
        raise OnboardingDisabled()
    slug = normalize_slug(tenant_id)
    if not slug or slug == SHAMROCK_TENANT_ID:
        raise OnboardingError("slug_invalid")
    reason = str(reason or "").strip()
    if not approve and not reason:
        raise OnboardingError("reason_required")
    if len(reason) > 300:
        raise OnboardingError("reason_required")
    now = _now()
    update = {
        "status": "active" if approve else "rejected",
        "updated_at": now,
        "approved_at": now if approve else "",
        "approved_by": actor if approve else "",
        "rejected_reason": "" if approve else reason,
    }
    with bind_platform_job("agency_onboarding"):
        col = await _tenants()
        found = await col.find_one({"tenant_id": slug})
        if not found:
            raise OnboardingError("not_found")
        if found.get("status") != "pending_approval":
            raise OnboardingError("not_pending")
        await col.update_one({"tenant_id": slug}, {"$set": update})
        found.update(update)
    return public_agency_view(found, include_invites=True)
