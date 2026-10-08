from __future__ import annotations
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Request, Query
"""
ShamrockLeads — Indemnitor Intake Queue API Blueprint
Receives indemnitor information from ALL sources that previously fed
Dashboard.html in the GAS project:
  Sources:
    1. wix_portal      — Wix/Velo indemnitor portal (IntakeQueue CMS collection)
    2. telegram        — Telegram bot
    2b. telegram_miniapp — Telegram mini-app intake form
    2c. shannon_voice — Shannon voice agent (727-295-2245)
    3. manual_entry    — Staff manually enters data in the dashboard
    4. walk_in         — Walk-in client (staff enters on their behalf)
    5. phone_call      — Phone intake (staff enters while on call)
    6. bookmarklet     — LCSO bookmarklet auto-scrape
  Endpoints:
    POST /api/intake/submit          — Accept new intake from any source
    GET  /api/intake/queue           — List pending intakes (staff dashboard queue)
    GET  /api/intake/<intake_id>     — Get single intake record
    POST /api/intake/<intake_id>/process  — Mark as processed / hydrate bond form
    POST /api/intake/<intake_id>/archive  — Archive / mark done
    PATCH /api/intake/<intake_id>    — Update intake fields
    GET  /api/intake/stats           — Queue stats (count by source, status)
    POST /api/intake/<intake_id>/match    — Phase 4: Run matching engine on this intake
    POST /api/intake/<intake_id>/promote  — Phase 5: Atomic intake → active_bonds promotion
  Indemnitor Field Schema (mirrors Dashboard.html addIndemnitor() exactly):
    Personal:   firstName, middleName, lastName, relationship, dob, ssn, dl, dlState
    Contact:    phone, email
    Address:    address, city, state, zip
    Employment: employer, employerPhone, employerCity, employerState, supervisor, supervisorPhone
    References: ref1Name, ref1Relation, ref1Phone, ref1Address
                ref2Name, ref2Relation, ref2Phone, ref2Address
    Defendant:  defendantName, defendantDOB, defendantFacility, defendantCounty,
                defendantBookingNumber, defendantCharges, defendantBondAmount
    Meta:       source, platform, timestamp, consentGiven, consentTimestamp,
                telegramUserId, telegramUsername, gpsLatitude, gpsLongitude
"""
import hmac
import os
import re
import uuid
import logging
from typing import Optional
from datetime import datetime, timezone
from dashboard.extensions import get_collection, get_db
logger = logging.getLogger(__name__)
intake_bp = APIRouter(prefix="/api", tags=["intake"])
# ── Valid intake sources ──────────────────────────────────────────────────────
VALID_SOURCES = {
    "wix_portal",
    "telegram",
    "telegram_mini_app",   # legacy alias used by older Telegram Mini App builds
    "telegram_miniapp",    # canonical mini-app tag
    "manual_entry",
    "walk_in",
    "phone_call",
    "bookmarklet",
    "shamrock-leads-dashboard",
    "elevenlabs_voice",
    "shannon",
    "shannon_voice",       # canonical Shannon voice tag
}

@intake_bp.get("/intake/by-booking/{booking_number}")
async def get_intake_by_booking(booking_number: str):
    """Fetch the latest intake record for a given booking number."""
    collection = get_collection("intake_queue")
    # Sort by descending _id to get the most recent submission if there are multiple
    intake = await collection.find_one({"defendant_booking_number": booking_number}, sort=[("_id", -1)])
    
    if not intake:
        return JSONResponse({'error': 'No intake found for this booking'}, status_code=404)
        
    return JSONResponse({
        "status": "success",
        "intake_id": str(intake.get("_id")) if "_id" in intake else intake.get("intake_id"),
        "booking_number": booking_number
    }, status_code=200)

SOURCE_LABELS = {
    "wix_portal": "🌐 Wix Portal",
    "telegram": "📱 Telegram",
    "telegram_mini_app": "📱 Telegram Mini App",
    "telegram_miniapp": "📱 Telegram Mini App",
    "manual_entry": "✏️ Manual Entry",
    "walk_in": "🚶 Walk-In",
    "phone_call": "📞 Phone Call",
    "bookmarklet": "🔖 Bookmarklet",
    "shamrock-leads-dashboard": "☘️ Dashboard",
    "elevenlabs_voice": "🎙 Shannon Voice",
    "shannon": "🎙 Shannon Voice",
    "shannon_voice": "🎙 Shannon Voice",
}
def _normalize_source(raw: str) -> str:
    """Normalize source string to a canonical value."""
    raw = (raw or "manual_entry").lower().strip().replace(" ", "_").replace("-", "_")
    if raw in VALID_SOURCES:
        return raw
    if "telegram" in raw:
        return "telegram"
    if "wix" in raw or "portal" in raw:
        return "wix_portal"
    if "walk" in raw:
        return "walk_in"
    if "phone" in raw or "call" in raw:
        return "phone_call"
    if "shannon" in raw or "elevenlabs" in raw or "voice" in raw:
        return "elevenlabs_voice"
    if "bookmarklet" in raw or "lcso" in raw:
        return "bookmarklet"
    if raw in ("shamrock_leads_dashboard", "dashboard"):
        return "shamrock-leads-dashboard"
    return "manual_entry"



def _intake_surety(data: dict):
    """Supported surety id when the payload names one; otherwise None.

    Intakes never default to OSI. An unrecognised label is kept separately in
    ``surety_unrecognized`` for staff instead of being coerced or dropping the lead.
    """
    from dashboard.services.surety_registry import normalize_surety, is_supported_surety

    raw = normalize_surety(data.get("surety_id") or data.get("SuretyID") or data.get("surety"))
    return raw if is_supported_surety(raw) else None


def _intake_surety_unrecognized(data: dict):
    from dashboard.services.surety_registry import normalize_surety, is_supported_surety

    raw = normalize_surety(data.get("surety_id") or data.get("SuretyID") or data.get("surety"))
    return raw if raw and not is_supported_surety(raw) else None


def _extract_indemnitor(data: dict, apply_defaults: bool = True) -> dict:
    """
    Extract and normalize indemnitor fields from any source payload.
    Handles all field-name variants used by Wix, Telegram, GAS, and manual entry.
    Mirrors the Queue.process() hydration logic in Dashboard.html exactly.
    """
    g = lambda *keys: next((str(data.get(k, "")).strip() for k in keys if data.get(k)), "")

    out = {
        # Personal
        "firstName":    g("IndFirstName", "indFirstName", "indemnitorFirstName", "firstName", "first_name", "FirstName"),
        "middleName":   g("IndMiddleName", "indemnitorMiddleName", "middleName", "middle_name"),
        "lastName":     g("IndLastName", "indLastName", "indemnitorLastName", "lastName", "last_name", "LastName"),
        "relationship": g("IndRelation", "indRelation", "indemnitorRelation", "relationship", "Relationship", "Role"),
        "dob":          g("IndDOB", "indemnitorDOB", "dob", "DOB"),
        "ssn":          g("IndSSN", "indemnitorSSN", "ssn", "SSN"),
        "dl":           g("IndDL", "indemnitorDL", "dlNumber", "dl", "DL"),
        "dlState":      g("IndDLState", "indemnitorDLState", "dlState", "DLState") or ("FL" if apply_defaults else ""),
        # Contact
        "phone":        g("IndPhone", "indPhone", "indemnitorPhone", "phone", "Phone"),
        "email":        g("IndEmail", "indEmail", "indemnitorEmail", "email", "Email"),
        # Address
        "address":      g("IndAddress", "indemnitorStreetAddress", "indemnitorAddress", "address", "Address"),
        "city":         g("IndCity", "indemnitorCity", "city", "City"),
        "state":        g("IndState", "indemnitorState", "state", "State") or ("FL" if apply_defaults else ""),
        "zip":          g("IndZip", "indemnitorZipCode", "indemnitorZip", "zip", "ZIP"),
        # Employment
        "employer":         g("IndEmployer", "indemnitorEmployerName", "employer", "Employer"),
        "employerPhone":    g("IndEmployerPhone", "indemnitorEmployerPhone", "employerPhone"),
        "employerAddress":  g("IndEmployerAddress", "indemnitorEmployerAddress"),
        "employerCity":     g("IndEmployerCity", "indemnitorEmployerCity", "employerCity"),
        "employerState":    g("IndEmployerState", "indemnitorEmployerState", "employerState"),
        "supervisor":       g("IndJobTitle", "indemnitorSupervisorName", "supervisor", "jobTitle"),
        "supervisorPhone":  g("IndSupervisorPhone", "indemnitorSupervisorPhone", "supervisorPhone"),
        # References
        "ref1Name":     g("Ref1Name", "ref1Name", "reference1Name", "IndRef1Name"),
        "ref1Relation": g("Ref1Relation", "ref1Relation", "reference1Relation"),
        "ref1Phone":    g("Ref1Phone", "ref1Phone", "reference1Phone"),
        "ref1Address":  g("Ref1Address", "ref1Address", "reference1Address"),
        "ref2Name":     g("Ref2Name", "ref2Name", "reference2Name", "IndRef2Name"),
        "ref2Relation": g("Ref2Relation", "ref2Relation", "reference2Relation"),
        "ref2Phone":    g("Ref2Phone", "ref2Phone", "reference2Phone"),
        "ref2Address":  g("Ref2Address", "ref2Address", "reference2Address"),
    }
    full = g(
        "indemnitorName",
        "indemnitor_name",
        "IndemnitorName",
        "caller_name",
        "callerName",
        "FullName",
    )
    if full and not out["firstName"] and not out["lastName"]:
        parts = [p for p in full.split() if p]
        if parts:
            out["firstName"] = parts[0]
            if len(parts) > 1:
                out["lastName"] = parts[-1]
            if len(parts) > 2:
                out["middleName"] = " ".join(parts[1:-1])
    return out


def _extract_defendant(data: dict, apply_defaults: bool = True) -> dict:
    """Extract defendant fields from any source payload (including bookmarklet)."""
    g = lambda *keys: next((str(data.get(k, "")).strip() for k in keys if data.get(k)), "")
    
    # Handle charges list vs string
    charges_raw = data.get("charges") or data.get("charge_details") or data.get("DefCharges") or data.get("charges_raw") or ""
    if isinstance(charges_raw, list):
        charge_str = " | ".join(
            (c.get("description") or c.get("charge") or str(c)) if isinstance(c, dict) else str(c)
            for c in charges_raw
        )
    else:
        charge_str = str(charges_raw).strip()

    # SSN and DL stay on prefixed keys. Bare ssn/dl would copy the cosigner
    # onto the defendant when both people are on one Wix payload. Street is
    # also stored as `address` because the packet builder and DocuSeal read
    # `address`. City/state/zip keep their old bare-key fallbacks.
    street = g("defendantStreetAddress", "street", "street_address", "address")
    return {
        "name":          g("defendantFullName", "DefName", "defName", "defendantName", "defendant_name", "DefendantName", "full_name"),
        "firstName":     g("DefFirstName", "defFirstName", "defendant_first_name", "firstName"),
        "lastName":      g("DefLastName", "defLastName", "defendant_last_name", "lastName"),
        "alias":         g("defendantAlias", "DefAlias", "defendant_alias"),
        "dob":           g("defendantDOB", "DefDOB", "defDOB", "defendant_dob", "dob", "DOB"),
        "ssn":           g("DefSSN", "defendantSSN", "defendant_ssn"),
        "dl":            g("DefDL", "defendantDL", "defendant_dl"),
        "dlState":       g("DefDLState", "defendantDLState", "defendant_dl_state"),
        "facility":      g("DefFacility", "defFacility", "jailFacility", "facility", "Facility"),
        # Website applicants don't pick a county; never assume Lee for them.
        "county":        g("DefCounty", "defCounty", "county", "County") or ("Lee" if apply_defaults else ""),
        "bookingNumber": g("defendantArrestNumber", "DefBookingNumber", "bookingNumber", "booking_number", "Booking_Number", "arrest_number"),
        "charges":       charge_str,
        "bondAmount":    g("DefBondAmount", "defBondAmount", "bondAmount", "bond_amount", "Bond_Amount", "totalBond"),
        "race":          g("defendantRace", "race", "Race"),
        "sex":           g("defendantSex", "sex", "Sex", "gender"),
        "height":        g("defendantHeight", "height", "Height"),
        "weight":        g("defendantWeight", "weight", "Weight"),
        "eyes":          g("defendantEyes", "defendant_eyes"),
        "hair":          g("defendantHair", "defendant_hair"),
        "street":        street,
        "address":       street,
        "city":          g("defendantCity", "city"),
        "state":         g("defendantState", "state") or ("FL" if apply_defaults else ""),
        "zip":           g("defendantZip", "zip", "zip_code"),
        "phone":         g("defendantPhone", "DefPhone", "defPhone", "defendant_phone"),
        "email":         g("defendantEmail", "DefEmail", "defEmail", "defendant_email"),
        # Secondary anchors — defendant-prefixed / vehicle-prefixed keys only.
        # Bare phone/email/employer/make/plate collide with indemnitor fields on Wix payloads.
        "employer":      g("defendantEmployer", "DefEmployer", "defEmployer", "defendant_employer"),
        "employerPhone": g("defendantEmployerPhone", "DefEmployerPhone", "defEmployerPhone", "defendant_employer_phone"),
        "employerAddress": g("defendantEmployerAddress", "DefEmployerAddress", "defEmployerAddress", "defendant_employer_address"),
        "vehicleMake":   g("defendantVehicleMake", "vehicleMake", "vehicle_make"),
        "vehicleModel":  g("defendantVehicleModel", "vehicleModel", "vehicle_model"),
        "vehicleYear":   g("defendantVehicleYear", "vehicleYear", "vehicle_year"),
        "vehicleColor":  g("defendantVehicleColor", "vehicleColor", "vehicle_color"),
        "vehiclePlate":  g("defendantVehiclePlate", "vehiclePlate", "vehicle_plate", "licensePlate", "license_plate"),
        "vehicleVIN":    g("defendantVehicleVIN", "vehicleVIN", "vehicle_vin"),
        "emergencyName": g("defendantEmergencyName", "emergencyContactName", "emergency_contact_name", "emergencyName"),
        "emergencyPhone": g("defendantEmergencyPhone", "emergencyContactPhone", "emergency_contact_phone", "emergencyPhone"),
        "emergencyRelation": g("defendantEmergencyRelation", "emergencyContactRelation", "emergency_contact_relation", "emergencyRelation"),
        "charge_details": charges_raw if isinstance(charges_raw, list) else [],
    }


async def _normalize_intake(
    data: dict,
    source: str = "wix_webhook",
    extra: Optional[dict] = None,
) -> tuple[str, dict]:
    """
    Normalize and persist an intake record from any source dictionary.
    Returns (intake_id, intake_doc).

    ``extra`` fields are merged into the stored doc (e.g. form_type,
    submitted_by_role, application subdoc from the Wix wizard adapter).
    Website (Wix) intakes get NO county / state / surety defaults.
    """
    source_canonical = _normalize_source(source)
    apply_defaults = not (source_canonical.startswith("wix") or "wix" in str(source).lower())
    indemnitor = _extract_indemnitor(data, apply_defaults=apply_defaults)
    defendant = _extract_defendant(data, apply_defaults=apply_defaults)

    ind_full_name = (
        " ".join(
            filter(
                None,
                [indemnitor.get("firstName"), indemnitor.get("middleName"), indemnitor.get("lastName")],
            )
        )
        or str(data.get("indemnitorName") or data.get("indemnitor_name") or data.get("caller_name") or "").strip()
        or "Unknown"
    )
    def_full_name = defendant["name"] or " ".join(filter(None, [defendant["firstName"], defendant["lastName"]])) or "Unknown"

    prefix_map = {
        "telegram": "TG",
        "telegram_mini_app": "TG",
        "telegram_miniapp": "TG",
        "wix_portal": "WX",
        "wix_webhook": "WX",
        "walk_in": "WI",
        "phone_call": "PC",
        "elevenlabs_voice": "SH",
        "shannon": "SH",
        "shannon_voice": "SH",
        "bookmarklet": "BK",
        "manual_entry": "ME",
        "shamrock-leads-dashboard": "SL",
    }
    prefix = prefix_map.get(source_canonical, "IN")
    intake_id = (
        data.get("intakeId")
        or data.get("caseId")
        or data.get("intake_id")
        or f"{prefix}-{uuid.uuid4().hex[:10].upper()}"
    )

    now = datetime.now(timezone.utc)

    doc = {
        "intake_id": intake_id,
        "source": source_canonical,
        "source_label": SOURCE_LABELS.get(source_canonical, source_canonical),
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "indemnitor": indemnitor,
        "indemnitor_name": ind_full_name,
        "indemnitor_email": indemnitor.get("email", ""),
        "indemnitor_phone": indemnitor.get("phone", ""),
        "defendant": defendant,
        "defendant_name": def_full_name,
        "defendant_booking_number": defendant.get("bookingNumber", ""),
        "defendant_county": defendant.get("county", ""),
        "defendant_facility": defendant.get("facility", ""),
        "consent_given": bool(data.get("consent") or data.get("consentGiven")),
        "consent_timestamp": data.get("consentTimestamp", now.isoformat()),
        "telegram_user_id": data.get("telegramUserId", ""),
        "telegram_username": data.get("telegramUsername", ""),
        "gps_latitude": data.get("gpsLatitude"),
        "gps_longitude": data.get("gpsLongitude"),
        "manual_location": data.get("manualLocation"),
        "ai_risk": "",
        "ai_score": None,
        "ai_rationale": "",
        "gas_sync_status": "pending",
        "gas_sync_timestamp": None,
        "matched_booking_number": None,
        "matched_county": None,
        "matched_defendant_id": None,
        "match_confidence": None,
        "match_strategy": None,
        "match_timestamp": None,
        # No OSI default: staff choose the surety at Write Bond (fail closed).
        "surety_id": _intake_surety(data),
        "surety_unrecognized": _intake_surety_unrecognized(data),
        "paperwork_packet_id": None,
        "paperwork_status": None,
        "_raw": _strip_secrets(data),
    }
    if extra:
        doc.update(extra)

    intake_queue = get_collection("intake_queue")
    await intake_queue.update_one(
        {"intake_id": intake_id},
        {"$set": doc},
        upsert=True,
    )
    return intake_id, doc


_RAW_SECRET_KEYS = ("apiKey", "secret", "api_key")


def _strip_secrets(data: dict) -> dict:
    """Drop credentials before the raw payload is stored on the intake."""
    return {k: v for k, v in data.items() if k not in _RAW_SECRET_KEYS}


def _anchor_present(value) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.lower() != "unknown"


def _has_intake_anchor(indemnitor: dict, defendant: dict) -> bool:
    """True when the payload names a person, phone, or booking. Empty leads are refused."""
    if any(_anchor_present(indemnitor.get(k)) for k in ("firstName", "lastName", "phone", "email")):
        return True
    return any(
        _anchor_present(defendant.get(k))
        for k in ("name", "firstName", "lastName", "bookingNumber", "phone")
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/intake/submit
#  Accept a new indemnitor intake from any source
# ═══════════════════════════════════════════════════════════════════════════════
@intake_bp.post("/intake/submit")
async def intake_submit(request: Request):
    """
    Accept indemnitor intake from any source (Wix, Telegram, manual, walk-in, phone).
    Stores in MongoDB `intake_queue` collection.
    Mirrors handleNewIntake() / storeIntakeInQueue() from GAS WixPortalIntegration.js.
    After storing, auto-triggers Phase 4 matching engine, then the same Sheets +
    Slack fan-out the website webhook uses. County, state, and surety are never
    invented here.
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid or empty JSON body"}, status_code=400)
    if not isinstance(data, dict) or not data:
        return JSONResponse({"success": False, "error": "Empty or invalid JSON body"}, status_code=400)

    source_raw = (
        data.get("source")
        or data.get("platform")
        or data.get("action", "")
        or "manual_entry"
    )
    source = _normalize_source(source_raw)

    # ── Auth Gate for external Wix Portal submissions ─────────────────────────
    if source == "wix_portal":
        wix_secret = (os.getenv("WIX_WEBHOOK_SECRET") or os.getenv("GAS_API_KEY") or "").strip()
        provided = (
            request.headers.get("X-Wix-Webhook-Secret", "")
            or request.headers.get("X-Api-Key", "")
            or data.get("apiKey", "")
            or data.get("secret", "")
            or ""
        )
        if not wix_secret:
            logger.error("[intake_submit] WIX_WEBHOOK_SECRET/GAS_API_KEY not configured")
            return JSONResponse({"success": False, "error": "Webhook auth not configured"}, status_code=503)
        if not hmac.compare_digest(str(provided).encode(), wix_secret.encode()):
            logger.warning("[intake_submit] Unauthorized wix_portal submission — invalid or missing secret")
            return JSONResponse({"success": False, "error": "Unauthorized: Invalid or missing webhook secret"}, status_code=401)

    # No county / state / DL-state defaults. Staff set those at Write Bond.
    indemnitor = _extract_indemnitor(data, apply_defaults=False)
    defendant = _extract_defendant(data, apply_defaults=False)
    if not _has_intake_anchor(indemnitor, defendant):
        return JSONResponse(
            {"success": False, "error": "A name, phone, or booking number is required"},
            status_code=400,
        )

    # Build a full name for display
    ind_full_name = (
        " ".join(
            filter(
                None,
                [indemnitor.get("firstName"), indemnitor.get("middleName"), indemnitor.get("lastName")],
            )
        )
        or str(data.get("indemnitorName") or data.get("indemnitor_name") or data.get("caller_name") or "").strip()
        or "Unknown"
    )
    def_full_name = defendant["name"] or " ".join(filter(None, [defendant["firstName"], defendant["lastName"]])) or "Unknown"

    # Generate unique intake ID (TG- prefix for Telegram, WX- for Wix, IN- for others)
    prefix_map = {
        "telegram": "TG",
        "telegram_mini_app": "TG",
        "telegram_miniapp": "TG",
        "wix_portal": "WX",
        "walk_in": "WI",
        "phone_call": "PC",
        "elevenlabs_voice": "SH",
        "shannon": "SH",
        "shannon_voice": "SH",
        "bookmarklet": "BK",
        "manual_entry": "ME",
        "shamrock-leads-dashboard": "SL",
    }
    prefix = prefix_map.get(source, "IN")
    # Allow caller to supply their own ID (e.g. Wix caseId, Telegram TG-xxx)
    intake_id = (
        data.get("intakeId")
        or data.get("caseId")
        or data.get("intake_id")
        or f"{prefix}-{uuid.uuid4().hex[:10].upper()}"
    )

    now = datetime.now(timezone.utc)

    doc = {
        "intake_id": intake_id,
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source),
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        # Indemnitor
        "indemnitor": indemnitor,
        "indemnitor_name": ind_full_name,
        "indemnitor_email": indemnitor.get("email", ""),
        "indemnitor_phone": indemnitor.get("phone", ""),
        # Defendant
        "defendant": defendant,
        "defendant_name": def_full_name,
        "defendant_booking_number": defendant.get("bookingNumber", ""),
        "defendant_county": defendant.get("county", ""),
        "defendant_facility": defendant.get("facility", ""),
        # Consent & Meta
        "consent_given": bool(data.get("consent") or data.get("consentGiven")),
        "consent_timestamp": data.get("consentTimestamp", now.isoformat()),
        "telegram_user_id": data.get("telegramUserId", ""),
        "telegram_username": data.get("telegramUsername", ""),
        "gps_latitude": data.get("gpsLatitude"),
        "gps_longitude": data.get("gpsLongitude"),
        "manual_location": data.get("manualLocation"),
        # AI fields (populated later by risk engine)
        "ai_risk": "",
        "ai_score": None,
        "ai_rationale": "",
        # GAS sync status
        "gas_sync_status": "pending",
        "gas_sync_timestamp": None,
        # Matching fields (populated by Phase 4 matching engine)
        "matched_booking_number": None,
        "matched_county": None,
        "matched_defendant_id": None,
        "match_confidence": None,
        "match_strategy": None,
        "match_timestamp": None,
        # Surety company routing — 'osi' or 'palmetto' only when the source
        # names one explicitly. Missing/unknown stays None; staff pick at Write Bond.
        "surety_id": _intake_surety(data),
        "surety_unrecognized": _intake_surety_unrecognized(data),
        # Paperwork fields (populated by Phase 6)
        "paperwork_packet_id": None,
        "paperwork_status": None,
        # Raw payload preserved for full hydration (credentials stripped)
        "_raw": _strip_secrets(data),
    }

    intake_queue = get_collection("intake_queue")
    try:
        # Upsert by intake_id to prevent duplicates
        await intake_queue.update_one(
            {"intake_id": intake_id},
            {"$set": doc},
            upsert=True,
        )
        logger.info("[intake] New intake stored: %s | source=%s", intake_id, source)

        # Shannon's create_intake webhook has a short ElevenLabs deadline.
        # Matching can run later from the desk; do not block the voice turn.
        match_result = None
        # Voice tools have a short turn budget. Match-review still runs from the
        # desk; these sources do not block the call on the matcher.
        skip_match = bool(data.get("skip_match")) or source in ("elevenlabs_voice", "shannon", "shannon_voice")
        if not skip_match:
            try:
                from dashboard.services.matching_engine import MatchingEngine
                engine = MatchingEngine(get_db())
                match_result = await engine.match_intake(doc)
                logger.info(
                    "[intake] Auto-match for %s: confidence=%s strategy=%s auto_linked=%s",
                    intake_id,
                    match_result.get("confidence"),
                    match_result.get("strategy"),
                    match_result.get("auto_linked"),
                )
            except Exception as match_err:
                logger.warning("[intake] Auto-match failed for %s: %s", intake_id, match_err)

        try:
            from dashboard.routers.events import publish_event
            await publish_event("new_intake", {
                "intake_id": intake_id,
                "defendant_name": def_full_name if def_full_name != "Unknown" else "",
                "county": doc.get("defendant_county", ""),
                "booking_number": doc.get("defendant_booking_number", ""),
                "source": source,
            })
        except Exception:
            pass

        # Same post-save copy as the website webhook: Sheets ledger + Slack.
        # Fire-and-forget; a fan-out failure never fails the intake.
        try:
            from dashboard.services.intake_fanout import intake_for_fanout, schedule_after_save
            fanout_doc = await intake_for_fanout(intake_queue, intake_id, doc, match_result)
            schedule_after_save(fanout_doc)
        except Exception as exc:
            logger.error("[intake] fan-out scheduling failed (non-fatal): %s", exc)

        from dashboard.services.payment_links import payment_link_for

        return {
            "success": True,
            "intake_id": intake_id,
            "source": source,
            "defendant_name": def_full_name,
            "indemnitor_name": ind_full_name,
            "message": f"Intake received from {SOURCE_LABELS.get(source, source)}",
            "match": match_result,
            "payment_link": payment_link_for(source),
        }
    except Exception as e:
        logger.error(f"[intake] Failed to store intake {intake_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/intake/queue
#  Return pending intakes for the staff dashboard queue
# ═══════════════════════════════════════════════════════════════════════════════
@intake_bp.get("/intake/queue")
async def intake_queue_list(
    status: str = Query(default="pending"),
    limit: int = Query(default=50),
    source: str = Query(default=""),
    counties: str = Query(default=""),
):
