"""
DocuSeal signing UX — official embed + OpenAPI helpers.

Sources (do not invent attributes):
  https://console.docuseal.com/openapi.yml
  docuseal-code references/embed/signing-form-js.md
  docuseal-code references/embed/signing-form-hosts.md
  docuseal-code references/embed/signing-form-custom-css.md
  docuseal-code references/embed/signing-form-security-recommendations.md

Self-hosted Shamrock: form.js and data-host MUST be sign.shamrockbailbonds.biz.
Prefer /s/{slug} submitter URLs (OSS). JWT embed is the paid Pro path — do not use it.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

DEFAULT_SIGN_HOST = "sign.shamrockbailbonds.biz"
DEFAULT_SIGN_ORIGIN = f"https://{DEFAULT_SIGN_HOST}"
DEFAULT_PAPERWORK_ORIGIN = "https://paperwork.shamrockbailbonds.biz"

# Human labels for DocuSeal template field names. Title/description are official
# POST /submissions submitters[].fields[] properties (OpenAPI 3.1).
FIELD_TITLES: Dict[str, Dict[str, str]] = {
    "indemnitor_name": {"title": "Your full legal name", "description": "As it appears on your ID."},
    "IndemnitorName": {"title": "Your full legal name"},
    "IndName": {"title": "Your full legal name"},
    "FullName": {"title": "Your full legal name"},
    "indemnitor_address": {"title": "Your street address"},
    "indemnitor_city": {"title": "City"},
    "indemnitor_state": {"title": "State"},
    "indemnitor_zip": {"title": "ZIP code"},
    "indemnitor_city_state_zip": {"title": "City, state, ZIP"},
    "indemnitor_phone": {"title": "Your mobile number"},
    "indemnitor_dl": {"title": "Driver license or ID number"},
    "indemnitor_dob": {"title": "Date of birth"},
    "indemnitor_employer": {"title": "Employer"},
    "indemnitor_employer_phone": {"title": "Work phone"},
    "indemnitor_work_phone": {"title": "Work phone"},
    "indemnitor_employer_address": {"title": "Employer address"},
    "indemnitor_relationship": {"title": "How do you know the defendant?"},
    "indemnitor_vehicle_year": {"title": "Vehicle year"},
    "indemnitor_vehicle_make": {"title": "Vehicle make"},
    "indemnitor_vehicle_model": {"title": "Vehicle model"},
    "indemnitor_vehicle_color": {"title": "Vehicle color"},
    "reference_1_name": {"title": "Reference name"},
    "reference_1_phone": {"title": "Reference phone"},
    "defendant_name": {"title": "Defendant's full legal name", "description": "The person this bond is for."},
    "defendant_address": {"title": "Street address"},
    "defendant_city": {"title": "City"},
    "defendant_state": {"title": "State"},
    "defendant_zip": {"title": "ZIP code"},
    "defendant_phone": {"title": "Mobile number"},
    "defendant_dl": {"title": "Driver license or ID number"},
    "defendant_dob": {"title": "Date of birth"},
}

# Official <docuseal-form> i18n keys (submission_form/i18n.js). Keep short —
# stressed callers should never see vendor jargon.
SIGNING_I18N: Dict[str, str] = {
    "submit": "Continue",
    "complete": "Finish signing",
    "next": "Next",
    "type": "Type name",
    "draw": "Draw signature",
    "upload": "Upload",
    "clear": "Clear",
    "signed": "Signed",
    "email": "Email",
    "phone": "Mobile number",
}

# Light chrome, Shamrock green actions. Do NOT dark-theme the document canvas —
# families must be able to read the bond packet.
SIGNING_CUSTOM_CSS = """
.submit-form-button,
.expand-form-button,
.start-form-submit-button,
.completed-form-completed-button {
  background-color: #16a34a;
  border: 0;
  border-radius: 12px;
  color: #052e16;
  min-height: 48px;
  font-weight: 700;
  font-size: 16px;
}
.submit-form-button:hover,
.expand-form-button:hover,
.start-form-submit-button:hover {
  background-color: #15803d;
}
.submit-form-button:disabled {
  background-color: #86efac;
  color: #14532d;
}
.steps-progress-current {
  background-color: #16a34a !important;
}
.field-name-label,
.field-description-text {
  font-size: 15px;
}
.draw-canvas {
  border-radius: 12px;
  min-height: 140px;
  background-color: #ffffff;
  border: 1px solid #d1d5db;
}
.steps-form input,
.steps-form textarea,
.steps-form select {
  min-height: 44px;
  font-size: 16px;
  border-radius: 10px;
}
.field-area-active {
  border-color: #16a34a;
  outline-color: #22c55e;
}
.field-area-active-label {
  background-color: #16a34a;
  color: #052e16;
}
"""

ROLE_COPY = {
    "indemnitor": {
        "you_are": "You are signing as the indemnitor (co-signer).",
        "headline": "Sign your bond paperwork",
        "hint": "Most fields are already filled. Add your initials and signature to finish.",
        "fields_title": "A few details we still need",
        "fields_sub": "Bond amount, POA, and charges stay with your bondsman. Fill what you know.",
    },
    "coindemnitor": {
        "you_are": "You are signing as a co-indemnitor.",
        "headline": "Sign your bond paperwork",
        "hint": "Same packet as the primary co-signer — just your own initials and signature.",
        "fields_title": "A few details we still need",
        "fields_sub": "Confirm your name and how you know the defendant. Skip anything you do not know.",
    },
    "defendant": {
        "you_are": "You are signing as the defendant.",
        "headline": "Sign your bond paperwork",
        "hint": "Confirm your name and ID, then initial and sign. You do not fill the co-signer’s forms.",
        "fields_title": "Confirm your details",
        "fields_sub": "We only need your identity. Premium and court fields stay with the office.",
    },
    "bondsman": {
        "you_are": "You are signing as the bondsman.",
        "headline": "Agent signature",
        "hint": "Complete the agent blocks on this packet.",
        "fields_title": "Agent details",
        "fields_sub": "",
    },
}


def sign_host(public_url: Optional[str] = None) -> str:
    raw = (public_url or os.getenv("DOCUSEAL_URL") or DEFAULT_SIGN_ORIGIN).strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or DEFAULT_SIGN_HOST).lower()


def sign_origin(public_url: Optional[str] = None) -> str:
    return f"https://{sign_host(public_url)}"


def paperwork_done_url() -> str:
    base = (os.getenv("PAPERWORK_PUBLIC_URL") or DEFAULT_PAPERWORK_ORIGIN).rstrip("/")
    return f"{base}/done"


def role_copy(role: Optional[str]) -> Dict[str, str]:
    from dashboard.services.paperwork_signers import normalize_role

    key = normalize_role(role)
    return ROLE_COPY.get(key, ROLE_COPY["indemnitor"])


def friendly_fields_for_values(values: Optional[Any]) -> List[Dict[str, Any]]:
    """OpenAPI submitters[].fields[] — titles only, never invent values."""
    keys: List[str] = []
    if isinstance(values, dict):
        keys = [str(k) for k in values.keys()]
    elif isinstance(values, list):
        for item in values:
            if isinstance(item, dict) and item.get("name"):
                keys.append(str(item["name"]))
    out: List[Dict[str, Any]] = []
    seen = set()
    for key in keys:
        if key in seen or key not in FIELD_TITLES:
            continue
        seen.add(key)
        meta = FIELD_TITLES[key]
        row: Dict[str, Any] = {"name": key, "title": meta["title"]}
        if meta.get("description"):
            row["description"] = meta["description"]
        out.append(row)
    return out


def embed_form_config(
    *,
    src: str,
    email: str = "",
    name: str = "",
    role: str = "",
    completed_redirect_url: Optional[str] = None,
) -> Dict[str, str]:
    """
    Attribute map for <docuseal-form> (HTML data-* keys).

    Official required for self-host: data-src + data-host.
    data-src must be /s/{slug} (multi-party) not /d/{template}.
    """
    attrs = {
        "data-src": src,
        "data-host": sign_host(),
        "data-expand": "true",
        "data-minimize": "false",
        "data-go-to-last": "true",
        "data-autoscroll-fields": "true",
        "data-order-as-on-page": "true",
        "data-only-required-fields": "true",
        "data-with-complete-button": "true",
        "data-with-title": "false",
        "data-with-field-names": "false",
        "data-with-field-placeholder": "true",
        "data-remember-signature": "true",
        "data-reuse-signature": "true",
        "data-send-copy-email": "false",
        "data-allow-typed-signature": "true",
        "data-completed-message-title": "You are done",
        "data-completed-message-body": (
            "Thank you. Shamrock has your signature. Call (239) 332-2245 if you need anything else."
        ),
        "data-completed-button-title": "All set",
        "data-custom-css": SIGNING_CUSTOM_CSS.strip(),
        "data-i18n": json.dumps(SIGNING_I18N, separators=(",", ":")),
    }
    if email:
        attrs["data-email"] = email
    if name:
        attrs["data-name"] = name
    if role:
        attrs["data-role"] = role
    redirect = completed_redirect_url or paperwork_done_url()
    if redirect:
        attrs["data-completed-redirect-url"] = redirect
        attrs["data-completed-button-url"] = redirect
    return attrs
