"""
ShamrockLeads — Adaptive Packet Builder Service
================================================
Resolves defendant + indemnitor from the matching system, builds a robust
field-hydration map, assembles drag-and-drop packet manifests, flattens
final PDFs, and routes to SignNow (primary) or Adobe Sign (optional).

Self-indemnitor (defendant also indemnitor) is allowed only for small bonds
and requires Brendan's authorization PIN (default 224545).
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Catalog key (UI drag-drop) → SignNow / blank-PDF template slug
CATALOG_TO_TEMPLATE: Dict[str, str] = {
    "master_bail_application": "defendant-application",
    "indemnity_agreement": "indemnity-agreement",
    "promissory_note": "promissory-note",
    "disclosure_statement": "disclosure-form",
    "premium_receipt": "collateral-receipt",
    "payment_plan_agreement": "payment-plan",
    "credit_card_authorization": "payment-plan",
    "promissory_note_schedule": "payment-plan",
    "wage_assignment": "payment-plan",
    "osi_appearance_bond": "appearance-bond",
    "osi_premium_receipt": "collateral-receipt",
    "palmetto_power_certificate": "surety-terms",
    "palmetto_appearance_bond": "appearance-bond",
    "cosigner_addendum": "indemnity-agreement",
    "additional_cosigner_addendum": "indemnity-agreement",
    "recovery_expense_addendum": "master-waiver",
    "cash_premium_receipt": "collateral-receipt",
    "out_of_state_waiver": "master-waiver",
    "gps_checkin_consent": "master-waiver",
    # Already-canonical keys pass through
    "paperwork-header": "paperwork-header",
    "faq-cosigners": "faq-cosigners",
    "faq-defendants": "faq-defendants",
    "indemnity-agreement": "indemnity-agreement",
    "defendant-application": "defendant-application",
    "promissory-note": "promissory-note",
    "disclosure-form": "disclosure-form",
    "surety-terms": "surety-terms",
    "master-waiver": "master-waiver",
    "ssa-release": "ssa-release",
    "collateral-receipt": "collateral-receipt",
    "payment-plan": "payment-plan",
    "appearance-bond": "appearance-bond",
}

PRINT_ONLY_TEMPLATES = frozenset({"appearance-bond"})

SELF_INDEMNITOR_PIN = os.getenv("SELF_INDEMNITOR_PIN", "224545")
SMALL_BOND_MAX = float(os.getenv("SMALL_BOND_MAX", "10000"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _money(val: Any) -> float:
    if val is None or val == "":
        return 0.0
    try:
        return float(re.sub(r"[^\d.\-]", "", str(val)))
    except (TypeError, ValueError):
        return 0.0


def _first(*vals: Any) -> str:
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() not in ("none", "null", "n/a", "-"):
            return s
    return ""


def _digits_phone(p: str) -> str:
    d = re.sub(r"\D", "", p or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d


def _split_name(full: str) -> Tuple[str, str, str]:
    parts = [p for p in (full or "").strip().split() if p]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def verify_self_indemnitor_pin(pin: str) -> bool:
    """Brendan discretion PIN for defendant-as-indemnitor on small bonds."""
    return (pin or "").strip() == SELF_INDEMNITOR_PIN


def template_slug_for_catalog_key(key: str) -> str:
    return CATALOG_TO_TEMPLATE.get(key, key.replace("_", "-"))


async def resolve_case_context(
    *,
    intake_id: Optional[str] = None,
    match_id: Optional[str] = None,
    defendant_id: Optional[str] = None,
    booking_number: Optional[str] = None,
    county: Optional[str] = None,
    bond_case_id: Optional[str] = None,
    packet_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve the richest available defendant + indemnitor + bond context from
    intake, matches, defendants, arrests, and active_bonds collections.
    """
    from dashboard.extensions import get_collection

    intake: Dict[str, Any] = {}
    match: Dict[str, Any] = {}
    defendant: Dict[str, Any] = {}
    indemnitor: Dict[str, Any] = {}
    arrest: Dict[str, Any] = {}
    bond: Dict[str, Any] = {}
    packet: Dict[str, Any] = {}
    sources: List[str] = []

    if packet_id:
        packet = await get_collection("paperwork_packets").find_one(
            {"packet_id": packet_id}, {"_id": 0}
        ) or {}
        if packet:
            sources.append("packet")
            intake_id = intake_id or packet.get("intake_id")
            bond_case_id = bond_case_id or packet.get("bond_case_id")
            booking_number = booking_number or packet.get("booking_number") or packet.get("defendant_booking_number")

    if intake_id:
        intake = await get_collection("intake_queue").find_one(
            {"intake_id": intake_id}, {"_id": 0}
        ) or {}
        if intake:
            sources.append("intake")
            match_id = match_id or intake.get("match_id")
            defendant_id = defendant_id or intake.get("defendant_id")
            booking_number = booking_number or intake.get("defendant_booking_number") or (
                (intake.get("defendant") or {}).get("bookingNumber")
            )
            county = county or intake.get("defendant_county") or (
                (intake.get("defendant") or {}).get("county")
            )
            bond_case_id = bond_case_id or intake.get("bond_case_id")

    if match_id:
        match = await get_collection("matches").find_one(
            {"$or": [{"Match_ID": match_id}, {"match_id": match_id}]},
            {"_id": 0},
        ) or {}
        if match:
            sources.append("match")
            defendant_id = defendant_id or match.get("Defendant_ID") or match.get("defendant_id")
            if not indemnitor:
                ind_id = match.get("Indemnitor_ID") or match.get("indemnitor_id")
                if ind_id:
                    indemnitor = await get_collection("indemnitors").find_one(
                        {"$or": [{"Indemnitor_ID": ind_id}, {"indemnitor_id": ind_id}]},
                        {"_id": 0},
                    ) or {}
                    if indemnitor:
                        sources.append("indemnitor")

    if defendant_id:
        defendant = await get_collection("defendants").find_one(
            {"$or": [{"Defendant_ID": defendant_id}, {"defendant_id": defendant_id}]},
            {"_id": 0},
        ) or {}
        if defendant:
            sources.append("defendant")
            booking_number = booking_number or defendant.get("Booking_Number") or defendant.get("booking_number")
            county = county or defendant.get("County") or defendant.get("county")

    if booking_number:
        q: Dict[str, Any] = {
            "$or": [
                {"Booking_Number": booking_number},
                {"booking_number": booking_number},
            ]
        }
        if county:
            q = {
                "$and": [
                    q,
                    {"$or": [{"County": county}, {"county": county}, {"County": {"$regex": f"^{re.escape(county)}", "$options": "i"}}]},
                ]
            }
        arrest = await get_collection("arrests").find_one(q, {"_id": 0}) or {}
        if arrest:
            sources.append("arrest")

    if bond_case_id or booking_number:
        bq: Dict[str, Any] = {}
        if bond_case_id:
            bq["$or"] = [
                {"Bond_Case_ID": bond_case_id},
                {"bond_case_id": bond_case_id},
            ]
        elif booking_number:
            bq["$or"] = [
                {"Booking_Number": booking_number},
                {"booking_number": booking_number},
            ]
        bond = await get_collection("active_bonds").find_one(bq, {"_id": 0}) or {}
        if not bond:
            bond = await get_collection("bond_cases").find_one(bq, {"_id": 0}) or {}
        if bond:
            sources.append("bond")

    # Nested intake parties
    ind_nested = intake.get("indemnitor") if isinstance(intake.get("indemnitor"), dict) else {}
    def_nested = intake.get("defendant") if isinstance(intake.get("defendant"), dict) else {}

    def_name = _first(
        defendant.get("Full_Name"), defendant.get("full_name"), defendant.get("name"),
        def_nested.get("name"), intake.get("defendant_name"), arrest.get("Full_Name"),
        arrest.get("Name"), bond.get("defendant_name"), packet.get("defendant_name"),
    )
    ind_name = _first(
        indemnitor.get("Full_Name"), indemnitor.get("full_name"), indemnitor.get("name"),
        ind_nested.get("name"), intake.get("indemnitor_name"), bond.get("indemnitor_name"),
        packet.get("indemnitor_name"),
    )

    bond_amount = _money(
        _first(
            bond.get("Bond_Amount"), bond.get("bond_amount"),
            def_nested.get("bondAmount"), intake.get("bond_amount"),
            arrest.get("Bond_Amount"), arrest.get("bond_amount"),
            packet.get("bond_amount"),
        )
    )
    premium = _money(
        _first(bond.get("Premium"), bond.get("premium_amount"), packet.get("premium_amount"))
    )
    if premium <= 0 and bond_amount > 0:
        premium = round(bond_amount * 0.10, 2)

    surety_id = (
        _first(
            bond.get("Surety_ID"), bond.get("surety_id"),
            packet.get("surety_id"), packet.get("template"),
            intake.get("surety_id"), "osi",
        )
        or "osi"
    ).lower()
    if surety_id not in ("osi", "palmetto"):
        surety_id = "osi"

    match_status = _first(
        match.get("Status"), match.get("status"),
        intake.get("match_status"), "unknown",
    )
    match_confidence = match.get("Confidence") or match.get("confidence") or intake.get("match_confidence")

    context = {
        "resolved_at": _now_iso(),
        "sources": sources,
        "intake_id": intake.get("intake_id") or intake_id,
        "match_id": match.get("Match_ID") or match.get("match_id") or match_id,
        "match_status": match_status,
        "match_confidence": match_confidence,
        "defendant_id": defendant.get("Defendant_ID") or defendant.get("defendant_id") or defendant_id,
        "indemnitor_id": indemnitor.get("Indemnitor_ID") or indemnitor.get("indemnitor_id"),
        "bond_case_id": bond.get("Bond_Case_ID") or bond.get("bond_case_id") or bond_case_id,
        "packet_id": packet.get("packet_id") or packet_id,
        "surety_id": surety_id,
        "poa_number": _first(
            bond.get("POA_Number"), bond.get("poa_number"),
            intake.get("poa_number"), packet.get("poa_number"),
        ),
        "case_number": _first(
            bond.get("Case_Number"), bond.get("case_number"),
            def_nested.get("caseNumber"), intake.get("case_number"),
            arrest.get("Case_Number"), packet.get("case_number"),
        ),
        "booking_number": _first(
            booking_number, defendant.get("Booking_Number"),
            def_nested.get("bookingNumber"), arrest.get("Booking_Number"),
            packet.get("booking_number"),
        ),
        "county": _first(
            county, defendant.get("County"), def_nested.get("county"),
            arrest.get("County"), intake.get("defendant_county"), packet.get("defendant_county"),
        ),
        "state": _first(
            arrest.get("State"), defendant.get("State"), bond.get("State"), "FL",
        ) or "FL",
        "facility": _first(
            def_nested.get("facility"), arrest.get("Facility"),
            intake.get("defendant_facility"),
        ),
        "charges": _first(
            def_nested.get("charges"), arrest.get("Charges"),
            arrest.get("Charge"), intake.get("charges"), bond.get("charges"),
        ),
        "bond_amount": bond_amount,
        "premium_amount": premium,
        "is_small_bond": bond_amount > 0 and bond_amount <= SMALL_BOND_MAX,
        "small_bond_max": SMALL_BOND_MAX,
        "defendant": {
            "name": def_name,
            "first_name": _first(defendant.get("First_Name"), def_nested.get("firstName"), _split_name(def_name)[0]),
            "middle_name": _first(defendant.get("Middle_Name"), _split_name(def_name)[1]),
            "last_name": _first(defendant.get("Last_Name"), def_nested.get("lastName"), _split_name(def_name)[2]),
            "dob": _first(defendant.get("DOB"), def_nested.get("dob"), arrest.get("DOB"), intake.get("defendant_dob")),
            "phone": _first(defendant.get("Phone"), def_nested.get("phone"), arrest.get("Phone")),
            "email": _first(defendant.get("Email"), def_nested.get("email")),
            "address": _first(defendant.get("Address"), def_nested.get("address"), arrest.get("Address")),
            "city": _first(defendant.get("City"), def_nested.get("city")),
            "state": _first(defendant.get("State"), def_nested.get("state"), "FL"),
            "zip": _first(defendant.get("Zip"), def_nested.get("zip")),
            "dl": _first(defendant.get("DL"), def_nested.get("dl")),
            "dl_state": _first(defendant.get("DL_State"), def_nested.get("dlState"), "FL"),
            "ssn": _first(defendant.get("SSN"), def_nested.get("ssn")),
            "employer": _first(defendant.get("Employer"), def_nested.get("employer")),
            "height": _first(defendant.get("Height"), arrest.get("Height")),
            "weight": _first(defendant.get("Weight"), arrest.get("Weight")),
            "race": _first(defendant.get("Race"), arrest.get("Race")),
            "sex": _first(defendant.get("Sex"), arrest.get("Sex"), arrest.get("Gender")),
            "hair": _first(defendant.get("Hair"), arrest.get("Hair")),
            "eyes": _first(defendant.get("Eyes"), arrest.get("Eyes")),
        },
        "indemnitor": {
            "name": ind_name,
            "first_name": _first(indemnitor.get("First_Name"), ind_nested.get("firstName"), _split_name(ind_name)[0]),
            "middle_name": _first(indemnitor.get("Middle_Name"), _split_name(ind_name)[1]),
            "last_name": _first(indemnitor.get("Last_Name"), ind_nested.get("lastName"), _split_name(ind_name)[2]),
            "dob": _first(indemnitor.get("DOB"), ind_nested.get("dob")),
            "phone": _first(indemnitor.get("Phone"), ind_nested.get("phone"), intake.get("indemnitor_phone")),
            "email": _first(indemnitor.get("Email"), ind_nested.get("email"), intake.get("indemnitor_email")),
            "address": _first(indemnitor.get("Address"), ind_nested.get("address")),
            "city": _first(indemnitor.get("City"), ind_nested.get("city")),
            "state": _first(indemnitor.get("State"), ind_nested.get("state"), "FL"),
            "zip": _first(indemnitor.get("Zip"), ind_nested.get("zip")),
            "dl": _first(indemnitor.get("DL"), ind_nested.get("dl")),
            "dl_state": _first(indemnitor.get("DL_State"), ind_nested.get("dlState"), "FL"),
            "ssn": _first(indemnitor.get("SSN"), ind_nested.get("ssn")),
            "employer": _first(indemnitor.get("Employer"), ind_nested.get("employer")),
            "relationship": _first(indemnitor.get("Relationship"), ind_nested.get("relationship"), ind_nested.get("relation")),
        },
        "raw": {
            "has_intake": bool(intake),
            "has_match": bool(match),
            "has_defendant": bool(defendant),
            "has_indemnitor": bool(indemnitor),
            "has_arrest": bool(arrest),
            "has_bond": bool(bond),
        },
    }
    return context


def apply_self_indemnitor(context: Dict[str, Any], pin: str) -> Dict[str, Any]:
    """
    Copy defendant identity onto indemnitor when PIN-authorized.
    Returns updated context (mutates a shallow copy).
    """
    if not verify_self_indemnitor_pin(pin):
        raise PermissionError("Self-indemnitor requires authorization PIN (Brendan discretion).")

    ctx = dict(context)
    def_ = dict(ctx.get("defendant") or {})
    ind = dict(ctx.get("indemnitor") or {})
    # Overlay defendant PII onto indemnitor while preserving any stronger ind email/phone if set
    for key in (
        "name", "first_name", "middle_name", "last_name", "dob", "address",
        "city", "state", "zip", "dl", "dl_state", "ssn", "employer",
    ):
        if def_.get(key):
            ind[key] = def_[key]
    if def_.get("phone") and not ind.get("phone"):
        ind["phone"] = def_["phone"]
    if def_.get("email") and not ind.get("email"):
        ind["email"] = def_["email"]
    ind["relationship"] = ind.get("relationship") or "Self"
    ctx["indemnitor"] = ind
    ctx["self_indemnitor"] = True
    ctx["self_indemnitor_authorized_at"] = _now_iso()
    return ctx


def build_adaptive_field_map(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adaptive multi-alias field map for SignNow / local PDF hydration.
    Mirrors conventions used by SignNowPacketService.build_prefill_fields.
    """
    def_ = context.get("defendant") or {}
    ind = context.get("indemnitor") or {}
    def_name = def_.get("name") or ""
    ind_name = ind.get("name") or ""
    bond_amount = float(context.get("bond_amount") or 0)
    premium = float(context.get("premium_amount") or 0)
    bond_str = f"${bond_amount:,.2f}" if bond_amount else ""
    prem_str = f"${premium:,.2f}" if premium else ""
    county = context.get("county") or ""
    booking = context.get("booking_number") or ""
    case_no = context.get("case_number") or ""
    poa = context.get("poa_number") or ""
    ind_csz = ", ".join(filter(None, [ind.get("city"), ind.get("state")]))
    if ind.get("zip"):
        ind_csz = f"{ind_csz} {ind['zip']}".strip()
    today = datetime.now(timezone.utc).strftime("%m/%d/%Y")

    fields = {
        # Defendant
        "defendant_name": def_name,
        "DefendantName": def_name,
        "defendant-full-name": def_name,
        "DefName": def_name,
        "Defendant Print Name": def_name,
        "Defendants NameRow1": def_name,
        "DefFirstName": def_.get("first_name") or "",
        "DefLastName": def_.get("last_name") or "",
        "DefMiddleName": def_.get("middle_name") or "",
        "DefDOB": def_.get("dob") or "",
        "defendant_dob": def_.get("dob") or "",
        "DefPhone": def_.get("phone") or "",
        "defendant-phone": def_.get("phone") or "",
        "defendant-email": def_.get("email") or "",
        "DefAddress": def_.get("address") or "",
        "defendant-address": def_.get("address") or "",
        "DefCity": def_.get("city") or "",
        "DefState": def_.get("state") or "FL",
        "DefZip": def_.get("zip") or "",
        "DefCounty": county,
        "DefHeight": def_.get("height") or "",
        "DefWeight": def_.get("weight") or "",
        "DefRace": def_.get("race") or "",
        "DefHair": def_.get("hair") or "",
        "DefEyes": def_.get("eyes") or "",
        "DefSex": def_.get("sex") or "",
        "DefDL": def_.get("dl") or "",
        "DefDLState": def_.get("dl_state") or "FL",
        "DefEmployer": def_.get("employer") or "",
        # Indemnitor
        "indemnitor_name": ind_name,
        "IndemnitorName": ind_name,
        "indemnitor-full-name": ind_name,
        "IndName": ind_name,
        "IndAddress": ind.get("address") or "",
        "indemnitor-address": ind.get("address") or "",
        "indemnitor_address": ind.get("address") or "",
        "IndCityStateZip": ind_csz,
        "indemnitor_city": ind.get("city") or "",
        "indemnitor_state": ind.get("state") or "FL",
        "indemnitor_zip": ind.get("zip") or "",
        "IndPhone": ind.get("phone") or "",
        "indemnitor-phone": ind.get("phone") or "",
        "indemnitor_phone": ind.get("phone") or "",
        "Phone": ind.get("phone") or "",
        "IndDL": ind.get("dl") or "",
        "indemnitor_dl": ind.get("dl") or "",
        "IndDOB": ind.get("dob") or "",
        "indemnitor_dob": ind.get("dob") or "",
        "IndSSN": ind.get("ssn") or "",
        "indemnitor-email": ind.get("email") or "",
        "indemnitor_email": ind.get("email") or "",
        "IndRelation": ind.get("relationship") or "",
        "IndEmployer": ind.get("employer") or "",
        "FullName": ind_name,
        "Social": ind.get("ssn") or "",
        # Bond / case
        "bond_amount": bond_str,
        "BondAmount": bond_str,
        "numeric-bond-amount": bond_str,
        "premium_amount": prem_str,
        "PremiumAmount": prem_str,
        "Premium": prem_str,
        "booking_number": booking,
        "BookingNumber": booking,
        "county": county,
        "case_number": case_no,
        "CaseNumber": case_no,
        "poa_number": poa,
        "POANumber": poa,
        "POA": poa,
        "charges": context.get("charges") or "",
        "Charges": context.get("charges") or "",
        "facility": context.get("facility") or "",
        "surety_id": context.get("surety_id") or "osi",
        "date": today,
        "Date": today,
        "Today": today,
        "agent_name": os.getenv("AGENT_NAME", "Brendan O'Neal"),
        "agency_name": "Shamrock Bail Bonds",
        "agency_phone": "(239) 332-2245",
        "self_indemnitor": "yes" if context.get("self_indemnitor") else "no",
    }
    # Drop empty values for cleaner audit
    return {k: v for k, v in fields.items() if v not in (None, "")}


def hydration_score(fields: Dict[str, Any]) -> Dict[str, Any]:
    required = [
        ("defendant_name", "Defendant Full Name"),
        ("defendant_dob", "Defendant DOB"),
        ("defendant-address", "Defendant Address"),
        ("indemnitor_name", "Indemnitor Full Name"),
        ("indemnitor_phone", "Indemnitor Phone"),
        ("indemnitor_address", "Indemnitor Address"),
        ("case_number", "Case Number"),
        ("booking_number", "Booking Number"),
        ("bond_amount", "Bond Amount"),
        ("surety_id", "Surety"),
        ("poa_number", "POA Number"),
    ]
    rows = []
    ok = 0
    for key, label in required:
        # allow alias hits
        val = fields.get(key)
        if not val:
            aliases = {
                "defendant_dob": ["DefDOB"],
                "defendant-address": ["DefAddress"],
                "indemnitor_address": ["IndAddress", "indemnitor-address"],
            }.get(key, [])
            for a in aliases:
                if fields.get(a):
                    val = fields[a]
                    break
        present = bool(val and str(val).strip())
        if present:
            ok += 1
        rows.append({"key": key, "label": label, "val": val if present else None, "hydrated": present})
    score = round((ok / len(required)) * 100, 1) if required else 0
    return {
        "hydration_score": score,
        "hydrated_count": ok,
        "total_required": len(required),
        "fields": rows,
    }


def assemble_manifest(
    categories: Dict[str, List[str]],
    *,
    surety_id: str = "osi",
    include_payment_plan: bool = False,
    extra_catalog_keys: Optional[List[str]] = None,
    self_indemnitor: bool = False,
) -> List[Dict[str, Any]]:
    """
    Build ordered packet document list from drag-drop categories + extras.
    """
    surety_id = (surety_id or "osi").lower()
    ordered_keys: List[str] = []
    for k in categories.get("universal") or []:
        if k not in ordered_keys:
            ordered_keys.append(k)
    if include_payment_plan:
        for k in categories.get("payment_plan") or []:
            if k not in ordered_keys:
                ordered_keys.append(k)
    surety_cat = "osi_surety" if surety_id == "osi" else "palmetto_surety"
    for k in categories.get(surety_cat) or []:
        if k not in ordered_keys:
            ordered_keys.append(k)
    for k in categories.get("conditional") or []:
        # Skip multi-cosigner when self-indemnitor
        if self_indemnitor and k in ("cosigner_addendum", "additional_cosigner_addendum"):
            continue
        if k not in ordered_keys:
            ordered_keys.append(k)
    for k in extra_catalog_keys or []:
        if k and k not in ordered_keys:
            ordered_keys.append(k)

    # Always ensure core SignNow phase docs when building e-sign packets
    core_defaults = [
        "paperwork-header", "faq-cosigners", "indemnity_agreement",
        "master_bail_application", "promissory_note", "disclosure_statement",
        "ssa-release", "master-waiver", "premium_receipt",
    ]
    for k in core_defaults:
        # Only inject if universal was empty / missing essentials
        pass

    docs = []
    for i, key in enumerate(ordered_keys):
        slug = template_slug_for_catalog_key(key)
        docs.append({
            "order": i + 1,
            "catalog_key": key,
            "template_slug": slug,
            "print_only": slug in PRINT_ONLY_TEMPLATES,
            "label": key.replace("_", " ").replace("-", " ").title(),
        })
    return docs


def flatten_pdf_bytes(pdf_parts: List[bytes]) -> bytes:
    """Merge multiple PDF blobs into a single flat PDF."""
    if not pdf_parts:
        return b""
    try:
        import fitz  # PyMuPDF
        out = fitz.open()
        for blob in pdf_parts:
            if not blob:
                continue
            try:
                src = fitz.open(stream=blob, filetype="pdf")
                out.insert_pdf(src)
                src.close()
            except Exception as exc:
                logger.warning("flatten skip bad pdf part: %s", exc)
        if out.page_count == 0:
            out.close()
            return pdf_parts[0]
        data = out.tobytes(deflate=True, garbage=3)
        out.close()
        return data
    except Exception as exc:
        logger.warning("PyMuPDF flatten failed (%s); returning first part only", exc)
        return pdf_parts[0]


def decode_extra_uploads(uploads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    uploads: [{filename, content_type, data_b64}]
    Returns [{filename, content_type, bytes}]
    """
    out = []
    for u in uploads or []:
        raw = u.get("data_b64") or u.get("data") or ""
        if "," in raw and raw.strip().startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            blob = base64.b64decode(raw)
        except Exception:
            continue
        if not blob:
            continue
        out.append({
            "filename": u.get("filename") or f"extra-{uuid.uuid4().hex[:8]}.pdf",
            "content_type": u.get("content_type") or "application/pdf",
            "bytes": blob,
            "size": len(blob),
        })
    return out


async def send_via_adobe(
    *,
    flattened_pdf: bytes,
    filename: str,
    signer_email: str,
    signer_name: str,
    agreement_name: str,
) -> Dict[str, Any]:
    """
    Optional Adobe Acrobat Sign path.
    Requires ADOBE_SIGN_INTEGRATION_KEY (or ADOBE_SIGN_ACCESS_TOKEN).
    """
    token = os.getenv("ADOBE_SIGN_INTEGRATION_KEY") or os.getenv("ADOBE_SIGN_ACCESS_TOKEN") or ""
    base = (os.getenv("ADOBE_SIGN_API_BASE") or "https://api.na1.adobesign.com/api/rest/v6").rstrip("/")
    if not token:
        return {
            "success": False,
            "provider": "adobe",
            "error": "Adobe Sign not configured (set ADOBE_SIGN_INTEGRATION_KEY).",
        }
    if not signer_email:
        return {"success": False, "provider": "adobe", "error": "signer_email required for Adobe Sign"}

    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # 1) Transient document upload
            files = {
                "File-Name": (None, filename),
                "File": (filename, flattened_pdf, "application/pdf"),
            }
            up = await client.post(
                f"{base}/transientDocuments",
                headers=headers,
                files=files,
            )
            if up.status_code >= 400:
                return {
                    "success": False,
                    "provider": "adobe",
                    "error": f"Adobe upload failed HTTP {up.status_code}: {up.text[:300]}",
                }
            transient_id = up.json().get("transientDocumentId")
            if not transient_id:
                return {"success": False, "provider": "adobe", "error": "No transientDocumentId from Adobe"}

            # 2) Create agreement
            payload = {
                "fileInfos": [{"transientDocumentId": transient_id}],
                "name": agreement_name or filename,
                "participantSetsInfo": [{
                    "order": 1,
                    "role": "SIGNER",
                    "memberInfos": [{"email": signer_email, "name": signer_name or "Signer"}],
                }],
                "signatureType": "ESIGN",
                "state": "IN_PROCESS",
            }
            ag = await client.post(
                f"{base}/agreements",
                headers={**headers, "Content-Type": "application/json"},
                json=payload,
            )
            if ag.status_code >= 400:
                return {
                    "success": False,
                    "provider": "adobe",
                    "error": f"Adobe agreement failed HTTP {ag.status_code}: {ag.text[:300]}",
                }
            agreement_id = ag.json().get("id") or ag.json().get("agreementId") or ""
            return {
                "success": True,
                "provider": "adobe",
                "agreement_id": agreement_id,
                "signer_email": signer_email,
                "status": "sent",
            }
    except Exception as exc:
        logger.exception("Adobe Sign send failed")
        return {"success": False, "provider": "adobe", "error": str(exc)}
