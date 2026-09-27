"""
Adapter: Wix website wizards (nested JSON) → the flat keys that
``routers/intake.py`` ``_extract_defendant`` / ``_extract_indemnitor`` read.

Accepted envelopes (all optional wrappers are unwrapped):
  * {formType, role, source, clientNonce, payload: {...wizard data...}}  ← leadsIntake.jsw
  * {action: 'submitDefendantApplication', payload: {...}}               ← legacy embed
  * {type: 'indemnitor-submit-phase1', wizardData: {...}, formData: {...}}
  * the bare wizard object itself

Wizard shapes (shamrock-bail-portal-site src/public/*-wizard.html):
  Defendant  → defendant{first/last/alias/address/city/state/zip/phone/email/dob/ssn},
               physical{}, employment{}, family{}, references{parent,inlaws,…},
               legal{}, vehicle{}, identification{dl,dlState}, consents{},
               formType: 'defendant_appearance_bond'
  Indemnitor → defendant{firstName,lastName,county,dob,phone,address,bookingNumber},
               indemnitor{…,street,city,state,zip,dl,dlState,ssn,employer…},
               vehicle{}, homeowner{}, spouse{}, references[ {name,relation,phone,address} ],
               meta{source:'indemnitor-wizard'}

Nothing here defaults a surety or a county: web applicants don't pick either,
staff do at Write Bond.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional, Tuple

DEFENDANT_FORM = "defendant_application"
INDEMNITOR_FORM = "indemnitor_application"

# Sensitive keys never copied into audit / ledger / Slack copies.
SENSITIVE_KEYS = {"ssn", "IndSSN", "DefSSN", "defendantSSN", "indemnitorSSN"}


def _s(v: Any) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    if isinstance(v, bool):
        return "yes" if v else ""
    return str(v).strip()


def _d(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def unwrap(data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (wizard_data, envelope)."""
    env = dict(data or {})
    inner = data
    for key in ("payload", "wizardData", "data"):
        if isinstance(env.get(key), dict) and (
            isinstance(env[key].get("defendant"), dict) or isinstance(env[key].get("indemnitor"), dict)
        ):
            inner = env[key]
            break
    return inner, env


def is_wizard_payload(data: Dict[str, Any]) -> bool:
    inner, _ = unwrap(data or {})
    return isinstance(inner.get("defendant"), dict) or isinstance(inner.get("indemnitor"), dict)


def detect_form(inner: Dict[str, Any], env: Dict[str, Any]) -> Tuple[str, str]:
    """Return (form_type, role) — role is who filled the form out."""
    hint = " ".join(
        _s(x).lower()
        for x in (
            env.get("formType"), env.get("form_type"), env.get("role"), env.get("action"),
            env.get("type"), inner.get("formType"), _d(inner.get("meta")).get("source"),
        )
    )
    if "indemnitor" in hint or "phase1" in hint or isinstance(inner.get("indemnitor"), dict):
        return INDEMNITOR_FORM, "indemnitor"
    return DEFENDANT_FORM, "defendant"


def client_nonce(env: Dict[str, Any], inner: Dict[str, Any]) -> str:
    for key in ("clientNonce", "client_nonce", "submissionId", "caseId", "intakeId"):
        v = _s(env.get(key)) or _s(inner.get(key))
        if v:
            return v[:128]
    return ""


def intake_id_for_nonce(nonce: str) -> Optional[str]:
    if not nonce:
        return None
    return "WX-" + hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:12].upper()


def _put(out: Dict[str, Any], key: str, value: Any) -> None:
    v = _s(value)
    if v:
        out[key] = v


def flatten(data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (flat_payload, meta). meta = {form_type, role, nonce, application}."""
    inner, env = unwrap(data or {})
    form_type, role = detect_form(inner, env)
    d = _d(inner.get("defendant"))
    ind = _d(inner.get("indemnitor"))
    out: Dict[str, Any] = {}

    # ── Defendant (both forms carry a defendant block) ──
    _put(out, "DefFirstName", d.get("firstName"))
    _put(out, "DefLastName", d.get("lastName"))
    full = " ".join(x for x in (_s(d.get("firstName")), _s(d.get("lastName"))) if x)
    _put(out, "defendantFullName", full)
    _put(out, "defendantDOB", d.get("dob"))
    _put(out, "defendantPhone", d.get("phone"))
    _put(out, "defendantEmail", d.get("email"))
    _put(out, "defendantStreetAddress", d.get("address") or d.get("street"))
    _put(out, "defendantCity", d.get("city"))
    _put(out, "defendantState", d.get("state"))
    _put(out, "defendantZip", d.get("zip"))
    _put(out, "DefCounty", d.get("county"))
    _put(out, "defendantArrestNumber", d.get("bookingNumber"))
    _put(out, "DefFacility", d.get("facility"))

    phys = _d(inner.get("physical"))
    _put(out, "defendantRace", phys.get("race"))
    _put(out, "defendantHeight", phys.get("height"))
    _put(out, "defendantWeight", phys.get("weight"))

    if role == "defendant":
        emp = _d(inner.get("employment"))
        _put(out, "defendantEmployer", emp.get("employer"))
        _put(out, "defendantEmployerPhone", emp.get("phone"))
        _put(out, "defendantEmployerAddress", emp.get("address"))
        veh = _d(inner.get("vehicle"))
        _put(out, "defendantVehicleMake", veh.get("make"))
        _put(out, "defendantVehicleModel", veh.get("model"))
        _put(out, "defendantVehicleYear", veh.get("year"))
        _put(out, "defendantVehicleColor", veh.get("color"))
        _put(out, "defendantVehiclePlate", veh.get("plate"))
        refs = _d(inner.get("references"))
        parent = _d(refs.get("parent"))
        _put(out, "defendantEmergencyName", parent.get("name"))
        _put(out, "defendantEmergencyPhone", parent.get("phone"))
        if parent.get("name"):
            out["defendantEmergencyRelation"] = "Parent"

    # ── Indemnitor (indemnitor form only) ──
    if ind:
        _put(out, "IndFirstName", ind.get("firstName"))
        _put(out, "IndMiddleName", ind.get("middleName"))
        _put(out, "IndLastName", ind.get("lastName"))
        _put(out, "IndRelation", ind.get("relationship"))
        _put(out, "IndDOB", ind.get("dob"))
        _put(out, "IndSSN", ind.get("ssn"))
        _put(out, "IndDL", ind.get("dl"))
        _put(out, "IndDLState", ind.get("dlState"))
        _put(out, "IndPhone", ind.get("phone"))
        _put(out, "IndEmail", ind.get("email"))
        _put(out, "IndAddress", ind.get("street") or ind.get("address"))
        _put(out, "IndCity", ind.get("city"))
        _put(out, "IndState", ind.get("state"))
        _put(out, "IndZip", ind.get("zip"))
        _put(out, "IndEmployer", ind.get("employer"))
        _put(out, "IndEmployerPhone", ind.get("employerPhone"))
        refs = inner.get("references")
        if isinstance(refs, list):
            for i, r in enumerate(refs[:2], start=1):
                r = _d(r)
                _put(out, f"Ref{i}Name", r.get("name"))
                _put(out, f"Ref{i}Relation", r.get("relation"))
                _put(out, f"Ref{i}Phone", r.get("phone"))
                _put(out, f"Ref{i}Address", r.get("address"))
        # Indemnitor's vehicle / spouse / homeowner stay in the application subdoc.

    consents = _d(inner.get("consents"))
    if consents:
        out["consent"] = all(_d(v).get("granted") for v in consents.values()) if consents else False
    elif inner.get("consent") is not None:
        out["consent"] = bool(inner.get("consent"))

    # Explicit surety only if the envelope names one (wizards don't). Never defaulted.
    for key in ("surety_id", "surety"):
        if _s(env.get(key)):
            out["surety_id"] = _s(env.get(key))
            break

    nonce = client_nonce(env, inner)
    iid = intake_id_for_nonce(nonce)
    if iid:
        out["intakeId"] = iid

    meta = {
        "form_type": form_type,
        "role": role,
        "nonce": nonce,
        "application": redact(inner),
        "landing": {k: _s(env.get(k)) for k in ("utm_source", "utm_medium", "utm_campaign", "county_hint", "page") if _s(env.get(k))},
    }
    return out, meta


def redact(obj: Any) -> Any:
    """Deep copy with SSNs reduced to last-4 and uploads/base64 dropped."""
    if isinstance(obj, dict):
        res = {}
        for k, v in obj.items():
            if k in ("uploadData", "documents", "userAgent"):
                continue
            if k in SENSITIVE_KEYS or k.lower() == "ssn":
                digits = "".join(ch for ch in _s(v) if ch.isdigit())
                res[k] = ("***-**-" + digits[-4:]) if len(digits) >= 4 else ""
                continue
            res[k] = redact(v)
        return res
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, str) and len(obj) > 2000:
        return obj[:200] + "…[truncated]"
    return obj
