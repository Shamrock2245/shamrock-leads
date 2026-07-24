from __future__ import annotations
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Request
from dashboard.deps import get_settings
"""
ShamrockLeads — Phase 6: Paperwork Generation API Blueprint

Generates, delivers, and tracks all bail bond paperwork:
  - Appearance Bond PDFs (one per charge, OSI or Palmetto template)
  - Indemnity Agreement
  - SSA Release (signed by all parties)
  - Power of Attorney (POA)

Endpoints:
  GET  /api/paperwork/config                        — Dashboard Paperwork Config tab (TEMPLATE_MAP + DOC_RULES)
  POST /api/paperwork/generate/<intake_id>          — Generate full packet for an intake
  POST /api/paperwork/generate/bond/<intake_id>     — Generate appearance bond PDFs only
  GET  /api/paperwork/<packet_id>                   — Get packet status + download links
  POST /api/paperwork/<packet_id>/deliver           — Deliver via BlueBubbles iMessage
  POST /api/paperwork/<packet_id>/signnow           — Push to SignNow for e-signature
  POST /api/paperwork/<packet_id>/void              — Void a packet (policy Rule 3)
  GET  /api/paperwork/list/<intake_id>              — List all packets for an intake
  GET  /api/paperwork/signnow/validate-templates    — Validate all TEMPLATE_MAP entries

Policy Compliance (docs/policies/signature-policy.md):
  Rule 1: Packet must be bound to Bond_Case_ID before SignNow push.
  Rule 2: Surety-specific template set (OSI vs Palmetto).
  Rule 3: No in-place mutation after send/sign — void + new version.
  Rule 4: Recipient verification before sending signing link.
  Rule 5: Completion tracking via webhook with Drive filing.
"""
import io
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from dashboard.extensions import get_collection
from dashboard.services.bb_client import get_bb_client

logger = logging.getLogger(__name__)
paperwork_bp = APIRouter(prefix="/api", tags=["paperwork"])
# ── Template paths ─────────────────────────────────────────────────────────
_DOCKER_TEMPLATES = Path("/app/templates")
_LOCAL_TEMPLATES = Path(__file__).resolve().parent.parent.parent / "templates"
TEMPLATES_DIR = _DOCKER_TEMPLATES if _DOCKER_TEMPLATES.exists() else _LOCAL_TEMPLATES

# ── Document type constants ────────────────────────────────────────────────
DOC_APPEARANCE_BOND = "appearance_bond"
DOC_INDEMNITY = "indemnity_agreement"
DOC_SSA_RELEASE = "ssa_release"
DOC_POA = "power_of_attorney"
DOC_RECEIPT = "receipt"

PACKET_TYPES = {
    "full": [DOC_APPEARANCE_BOND, DOC_INDEMNITY, DOC_SSA_RELEASE, DOC_POA],
    "bond_only": [DOC_APPEARANCE_BOND],
    "signing": [DOC_INDEMNITY, DOC_SSA_RELEASE],
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _get_bond_pdf_service():
    """Lazy import to avoid circular imports."""
    from dashboard.bond_pdf_service import generate_appearance_bonds
    return generate_appearance_bonds


async def _load_intake(intake_id: str) -> Optional[dict]:
    col = get_collection("intake_queue")
    return await col.find_one({"intake_id": intake_id}, {"_id": 0})


async def _load_packet(packet_id: str) -> Optional[dict]:
    col = get_collection("paperwork_packets")
    return await col.find_one({"packet_id": packet_id}, {"_id": 0})


@paperwork_bp.get("/paperwork/config")
async def paperwork_config():
    """Return TEMPLATE_MAP + DOC_RULES for the Paperwork Config dashboard tab.

    Shapes data for the frontend:
      template_map.osi / .palmetto  → { key: { label, template_id, rule } }
      doc_rules                     → raw DOC_RULES dict
    """
    try:
        from dashboard.services.signnow_packet_service import SignNowPacketService

        svc = SignNowPacketService
        doc_rules = getattr(svc, "DOC_RULES", {}) or {}
        tmpl = getattr(svc, "TEMPLATE_MAP", {}) or {}

        osi: dict = {}
        palmetto: dict = {}
        for key, template_id in tmpl.items():
            base_key = key.replace("-palmetto", "")
            rule_meta = doc_rules.get(base_key, {}) or {}
            entry = {
                "label": rule_meta.get("label") or base_key.replace("-", " ").title(),
                "template_id": template_id or "",
                "rule": rule_meta.get("rule", "static"),
                "configured": bool(template_id),
            }
            if key.endswith("-palmetto"):
                palmetto[base_key] = entry
            else:
                osi[key] = entry
                # Shared keys also appear under Palmetto unless overridden
                if base_key not in palmetto:
                    palmetto[base_key] = {
                        **entry,
                        "label": f"{entry['label']} (shared)",
                    }

        # Apply explicit Palmetto overrides from TEMPLATE_MAP
        for key, template_id in tmpl.items():
            if not key.endswith("-palmetto"):
                continue
            base_key = key.replace("-palmetto", "")
            rule_meta = doc_rules.get(base_key, {}) or {}
            palmetto[base_key] = {
                "label": rule_meta.get("label") or base_key.replace("-", " ").title(),
                "template_id": template_id or "(uses shared)",
                "rule": rule_meta.get("rule", "static"),
                "configured": bool(template_id),
            }

        return {
            "success": True,
            "template_map": {"osi": osi, "palmetto": palmetto},
            "doc_rules": doc_rules,
            "counts": {
                "osi": len(osi),
                "palmetto": len(palmetto),
                "rules": len(doc_rules),
                "configured_osi": sum(1 for v in osi.values() if v.get("configured")),
                "configured_palmetto": sum(
                    1 for v in palmetto.values() if v.get("configured") and v.get("template_id") not in ("", "(uses shared)")
                ),
            },
        }
    except Exception as exc:
        logger.exception("paperwork/config error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# Standard default drag-and-drop document rules categories
DEFAULT_DOC_RULES_CATEGORIES = {
    "universal": [
        "master_bail_application",
        "indemnity_agreement",
        "promissory_note",
        "disclosure_statement",
        "premium_receipt",
    ],
    "payment_plan": [
        "payment_plan_agreement",
        "credit_card_authorization",
        "promissory_note_schedule",
        "wage_assignment",
    ],
    "osi_surety": [
        "osi_appearance_bond",
        "osi_premium_receipt",
    ],
    "palmetto_surety": [
        "palmetto_power_certificate",
        "palmetto_appearance_bond",
    ],
    "conditional": [
        "cosigner_addendum",
        "out_of_state_waiver",
        "gps_checkin_consent",
    ],
}


@paperwork_bp.get("/paperwork/config/rules")
async def get_doc_rules_config():
    """Return document category allocations for the Drag-and-Drop Document Builder."""
    try:
        rules_col = get_collection("paperwork_rules")
        doc = await rules_col.find_one({"_id": "drag_drop_rules"}, {"_id": 0})
        categories = doc.get("categories") if doc else DEFAULT_DOC_RULES_CATEGORIES
        return {
            "success": True,
            "categories": categories or DEFAULT_DOC_RULES_CATEGORIES,
            "updated_at": doc.get("updated_at") if doc else None,
        }
    except Exception as exc:
        logger.exception("get_doc_rules_config error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@paperwork_bp.post("/paperwork/config/rules")
async def save_doc_rules_config(request: Request):
    """Save updated drag-and-drop document category allocations to MongoDB."""
    try:
        body = await request.json()
        categories = body.get("categories")
        if not isinstance(categories, dict):
            return JSONResponse({"success": False, "error": "Invalid payload: 'categories' dict required"}, status_code=400)

        rules_col = get_collection("paperwork_rules")
        now_iso = datetime.now(timezone.utc).isoformat()
        await rules_col.update_one(
            {"_id": "drag_drop_rules"},
            {"$set": {"categories": categories, "updated_at": now_iso}},
            upsert=True,
        )
        return {
            "success": True,
            "message": "Document category rules saved successfully",
            "categories": categories,
            "updated_at": now_iso,
        }
    except Exception as exc:
        logger.exception("save_doc_rules_config error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


def _build_bond_data(intake: dict) -> dict:
    """
    Build the data dict expected by bond_pdf_service.generate_appearance_bonds().
    Maps intake fields → PDF template fields.
    """
    ind = intake.get("indemnitor", {})
    def_ = intake.get("defendant", {})

    return {
        # Defendant
        "defendant_name": intake.get("defendant_name", def_.get("name", "")),
        "dob": def_.get("dob", ""),
        "booking_number": def_.get("bookingNumber", intake.get("defendant_booking_number", "")),
        "county": def_.get("county", intake.get("defendant_county", "")),
        "facility": def_.get("facility", intake.get("defendant_facility", "")),
        "charges": def_.get("charges", ""),
        "bond_amount": def_.get("bondAmount", ""),
        # Indemnitor
        "indemnitor_name": intake.get("indemnitor_name", ""),
        "indemnitor_address": ind.get("address", ""),
        "indemnitor_city": ind.get("city", ""),
        "indemnitor_state": ind.get("state", "FL"),
        "indemnitor_zip": ind.get("zip", ""),
        "indemnitor_phone": ind.get("phone", ""),
        "indemnitor_dob": ind.get("dob", ""),
        "indemnitor_dl": ind.get("dl", ""),
        "indemnitor_dl_state": ind.get("dlState", "FL"),
        # Meta
        "intake_id": intake.get("intake_id", ""),
        "source": intake.get("source", ""),
        "created_at": datetime.now(timezone.utc).strftime("%m/%d/%Y"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/paperwork/generate/<intake_id>
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.post("/paperwork/generate/{intake_id}")
async def generate_packet(request: Request, intake_id: str):
    """
    Generate the full paperwork packet (appearance bonds + indemnity + SSA + POA).
    Stores packet metadata in `paperwork_packets` collection.
    Returns packet_id and document list.
    """
    try:
        data = (await request.json()) or {}
        packet_type = data.get("packet_type", "full")
        template = data.get("template", "osi")  # "osi" or "palmetto"

        intake = await _load_intake(intake_id)
        if not intake:
            return JSONResponse({"error": f"Intake {intake_id} not found"}, status_code=404)

        packet_id = f"PKT-{uuid.uuid4().hex[:10].upper()}"
        now = datetime.now(timezone.utc)

        bond_data = _build_bond_data(intake)
        documents = []

        # ── Generate appearance bond PDFs ──────────────────────────────────
        if DOC_APPEARANCE_BOND in PACKET_TYPES.get(packet_type, [DOC_APPEARANCE_BOND]):
            try:
                generate_bonds = _get_bond_pdf_service()
                pdf_buffers = generate_bonds(bond_data, template=template)
                for i, buf in enumerate(pdf_buffers, 1):
                    doc_id = f"{packet_id}-BOND-{i:02d}"
                    documents.append({
                        "doc_id": doc_id,
                        "type": DOC_APPEARANCE_BOND,
                        "label": f"Appearance Bond #{i}",
                        "template": template,
                        "charge_index": i,
                        "status": "generated",
                        "size_bytes": len(buf),
                        "generated_at": now.isoformat(),
                    })
            except Exception as e:
                logger.warning("Bond PDF generation error: %s", e)
                documents.append({
                    "type": DOC_APPEARANCE_BOND,
                    "status": "error",
                    "error": str(e),
                })

        # ── Indemnity Agreement ────────────────────────────────────────────
        if DOC_INDEMNITY in PACKET_TYPES.get(packet_type, []):
            documents.append({
                "doc_id": f"{packet_id}-IND",
                "type": DOC_INDEMNITY,
                "label": "Indemnity Agreement",
                "status": "pending_signnow",
                "generated_at": now.isoformat(),
            })

        # ── SSA Release ────────────────────────────────────────────────────
        if DOC_SSA_RELEASE in PACKET_TYPES.get(packet_type, []):
            documents.append({
                "doc_id": f"{packet_id}-SSA",
                "type": DOC_SSA_RELEASE,
                "label": "SSA Release",
                "status": "pending_signnow",
                "generated_at": now.isoformat(),
            })

        # ── POA ────────────────────────────────────────────────────────────
        if DOC_POA in PACKET_TYPES.get(packet_type, []):
            documents.append({
                "doc_id": f"{packet_id}-POA",
                "type": DOC_POA,
                "label": "Power of Attorney",
                "status": "pending_signnow",
                "generated_at": now.isoformat(),
            })

        # ── Store packet metadata ──────────────────────────────────────────
        # Resolve bond_case_id from intake or bond_cases collection (policy Rule 1)
        bond_case_id = intake.get("bond_case_id") or data.get("bond_case_id")
        if not bond_case_id:
            # Try to look up by intake_id in bond_cases
            bond_cases_col = get_collection("bond_cases")
            bc = await bond_cases_col.find_one({"intake_id": intake_id}, {"bond_case_id": 1})
            if bc:
                bond_case_id = bc.get("bond_case_id")

        packet_doc = {
            "packet_id": packet_id,
            "intake_id": intake_id,
            "bond_case_id": bond_case_id,           # policy Rule 1
            "packet_type": packet_type,
            "template": template,
            "surety_id": template,                  # alias for SignNow service
            "status": "generated",
            "documents": documents,
            "defendant_name": intake.get("defendant_name", ""),
            "defendant_county": intake.get("defendant_county", ""),
            "defendant_booking_number": (
                intake.get("defendant_booking_number")
                or intake.get("defendant", {}).get("bookingNumber", "")
            ),
            "indemnitor_name": intake.get("indemnitor_name", ""),
            "indemnitor_email": (
                intake.get("indemnitor_email")
                or intake.get("indemnitor", {}).get("email", "")
            ),
            "indemnitor_phone": (
                intake.get("indemnitor_phone")
                or intake.get("indemnitor", {}).get("phone", "")
            ),
            "created_at": now,
            "updated_at": now,
            "delivered_via": None,
            "signnow_invite_id": None,
            "signnow_document_id": None,            # populated on SignNow push
            "signnow_status": None,
            "packet_version": 1,                    # policy Rule 3
            "voided": False,
        }

        packets_col = get_collection("paperwork_packets")
        await packets_col.insert_one(packet_doc)

        # ── Update intake record ───────────────────────────────────────────
        intake_col = get_collection("intake_queue")
        await intake_col.update_one(
            {"intake_id": intake_id},
            {"$set": {
                "paperwork_packet_id": packet_id,
                "paperwork_status": "generated",
                "updated_at": now,
            }},
        )

        logger.info("[paperwork] Packet %s generated for intake %s (bond_case_id=%s)",
                    packet_id, intake_id, bond_case_id or "not_yet_linked")
        return {
            "success": True,
            "packet_id": packet_id,
            "intake_id": intake_id,
            "bond_case_id": bond_case_id,
            "packet_type": packet_type,
            "documents": documents,
            "document_count": len(documents),
        }

    except Exception as exc:
        logger.exception("generate_packet error for intake %s", intake_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paperwork/all
# Twenty CRM style: list all document packets across all cases with filters & stats
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.get("/paperwork/all")
async def list_all_packets(
    status: Optional[str] = None,
    surety: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
):
    """Return all paperwork packets across cases for Twenty CRM style document hub."""
    try:
        packets_col = get_collection("paperwork_packets")
        query: dict = {}

        if status and status != "all":
            query["$or"] = [
                {"status": status},
                {"signnow_status": status},
            ]
        if surety and surety != "all":
            query["surety_id"] = surety.lower()

        if search:
            rx = {"$regex": search, "$options": "i"}
            query["$or"] = [
                {"defendant_name": rx},
                {"indemnitor_name": rx},
                {"case_number": rx},
                {"booking_number": rx},
                {"packet_id": rx},
            ]

        cursor = packets_col.find(query, {"_id": 0}).sort("created_at", -1)
        packets = await cursor.to_list(length=limit)

        from datetime import date
        for p in packets:
            for field in ("created_at", "updated_at", "delivered_at", "signnow_sent_at", "signed_at"):
                val = p.get(field)
                if isinstance(val, (datetime, date)):
                    p[field] = val.isoformat()

        # Summary KPIs
        total = await packets_col.count_documents({})
        pending = await packets_col.count_documents({"status": {"$in": ["sent", "signnow_pending", "partially_signed"]}})
        signed = await packets_col.count_documents({"status": {"$in": ["signed", "completed"]}})
        filed = await packets_col.count_documents({"drive_url": {"$exists": True, "$ne": None}})

        def _to_int(v):
            try:
                return int(v)
            except Exception:
                return 0

        return {
            "success": True,
            "packets": packets,
            "count": len(packets),
            "summary": {
                "total_packets": _to_int(total),
                "pending_signature": _to_int(pending),
                "signed_completed": _to_int(signed),
                "filed_to_drive": _to_int(filed),
            },
        }
    except Exception as exc:
        logger.exception("list_all_packets error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paperwork/<packet_id>/hydration-audit
# Twenty CRM style: field hydration audit for 14-doc packet before dispatch
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.get("/paperwork/{packet_id}/hydration-audit")
async def get_packet_hydration_audit(packet_id: str):
    """Audit field hydration completeness for a paperwork packet."""
    try:
        packets_col = get_collection("paperwork_packets")
        packet = await packets_col.find_one(
            {"$or": [{"packet_id": packet_id}, {"booking_number": packet_id}]},
            {"_id": 0},
        )
        if not packet:
            return JSONResponse({"success": False, "error": "Packet not found"}, status_code=404)

        required_fields = [
            ("defendant_name", "Defendant Full Name"),
            ("defendant_dob", "Defendant Date of Birth"),
            ("defendant_address", "Defendant Address"),
            ("indemnitor_name", "Indemnitor Full Name"),
            ("indemnitor_phone", "Indemnitor Phone"),
            ("indemnitor_address", "Indemnitor Address"),
            ("case_number", "Case Number"),
            ("booking_number", "Booking Number"),
            ("bond_amount", "Bond Amount ($)"),
            ("surety_id", "Surety Selection (OSI/Palmetto)"),
            ("poa_number", "Power of Attorney (POA) Number"),
        ]

        fields_audit = []
        hydrated_count = 0

        for key, label in required_fields:
            val = packet.get(key)
            is_present = val is not None and str(val).strip() != "" and str(val).strip() != "None"
            if is_present:
                hydrated_count += 1
            fields_audit.append({
                "key": key,
                "label": label,
                "val": str(val) if is_present else None,
                "hydrated": is_present,
            })

        score = round((hydrated_count / len(required_fields)) * 100, 1)

        return {
            "success": True,
            "packet_id": packet.get("packet_id"),
            "booking_number": packet.get("booking_number"),
            "surety_id": packet.get("surety_id"),
            "status": packet.get("status"),
            "hydration_score": score,
            "hydrated_count": hydrated_count,
            "total_required": len(required_fields),
            "fields": fields_audit,
        }
    except Exception as exc:
        logger.exception("hydration_audit error for %s", packet_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paperwork/<packet_id>
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.get("/paperwork/{packet_id}")
async def get_packet(packet_id: str):
    """Return packet metadata and document list."""
    try:
        packet = await _load_packet(packet_id)
        if not packet:
            return JSONResponse({"error": f"Packet {packet_id} not found"}, status_code=404)

        # Serialize datetimes
        from datetime import date
        for field in ("created_at", "updated_at"):
            val = packet.get(field)
            if isinstance(val, (datetime, date)):
                packet[field] = val.isoformat()

        return packet
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/paperwork/<packet_id>/deliver
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.post("/paperwork/{packet_id}/deliver")
async def deliver_packet(request: Request, packet_id: str):
    """
    Deliver the paperwork packet via BlueBubbles iMessage.
    Sends a message with a magic link to the packet's signing page.
    Includes a geolocator link as required by project standards.
    """
    try:
        data = (await request.json()) or {}
        phone = data.get("phone", "").strip()
        custom_message = data.get("message", "")
        include_geo = data.get("include_geo", True)

        if not phone:
            return JSONResponse({"error": "phone is required"}, status_code=400)

        packet = await _load_packet(packet_id)
        if not packet:
            return JSONResponse({"error": f"Packet {packet_id} not found"}, status_code=404)

        # Policy Rule 4: verify recipient phone matches stored indemnitor phone
        stored_phone = packet.get("indemnitor_phone", "")
        if stored_phone:
            # Normalize both to digits only for comparison
            def _digits(p: str) -> str:
                return "".join(c for c in p if c.isdigit())
            if _digits(stored_phone) and _digits(phone) and _digits(stored_phone) != _digits(phone):
                logger.warning(
                    "[paperwork] deliver_packet: phone mismatch for packet %s — "
                    "stored=%s, provided=%s. Proceeding but logging for audit.",
                    packet_id, stored_phone, phone,
                )
                audit_events = get_collection("audit_events")
                await audit_events.insert_one({
                    "source": "paperwork_deliver",
                    "event_type": "phone_mismatch_warning",
                    "packet_id": packet_id,
                    "stored_phone": stored_phone,
                    "provided_phone": phone,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        defendant_name = packet.get("defendant_name", "your defendant")
        intake_id = packet.get("intake_id", "")

        # Build the signing magic link
        _settings = get_settings()
        portal_base = _settings.portal_base_url
        dashboard_url = _settings.dashboard_public_url or portal_base
        magic_link = f"{portal_base}/sign/{packet_id}"

        # Build the message
        if custom_message:
            message = custom_message
        else:
            message = (
                f"Hi! Here is your Shamrock Bail Bonds paperwork for {defendant_name}.\n\n"
                f"Please review and sign here:\n{magic_link}\n\n"
                f"Questions? Call us: 239-332-2245\n"
                f"Shamrock Bail Bonds — Fort Myers, FL"
            )

        # NOTE: Geo-tracking links are not auto-appended to paperwork messages.
        # Use /api/tracking/<booking>/send-geo-link for explicit geo-link delivery.

        # Send via BlueBubbles (iMessage-first, universal bridge)
        bb = get_bb_client(phone)
        if not bb:
            return JSONResponse({"error": "BlueBubbles server not configured"}, status_code=503)
        chat_guid = f"iMessage;-;{phone}"
        result = await bb.send_text(chat_guid, message)

        now = datetime.now(timezone.utc)
        packets_col = get_collection("paperwork_packets")
        await packets_col.update_one(
            {"packet_id": packet_id},
            {"$set": {
                "delivered_via": "imessage",
                "delivered_to": phone,
                "delivered_at": now,
                "magic_link": magic_link,
                "status": "delivered",
                "updated_at": now,
            }},
        )

        # Update intake
        intake_col = get_collection("intake_queue")
        await intake_col.update_one(
            {"intake_id": intake_id},
            {"$set": {"paperwork_status": "delivered", "updated_at": now}},
        )

        from dashboard.routers.helpers import mask_phone
        logger.info("[paperwork] Packet %s delivered to %s", packet_id, mask_phone(phone))
        return {
            "success": result.get("success", False),
            "packet_id": packet_id,
            "delivered_to": phone,
            "magic_link": magic_link,
            "bb_result": result,
        }

    except Exception as exc:
        logger.exception("deliver_packet error for %s", packet_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/paperwork/<packet_id>/signnow
# Push the packet to SignNow for e-signature.
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.post("/paperwork/{packet_id}/signnow")
async def push_to_signnow(request: Request, packet_id: str):
    """
    Push the paperwork packet to SignNow for e-signature.

    Policy compliance:
      Rule 1: Warns if bond_case_id is not set on the packet.
      Rule 2: Passes surety_id (template set) to SignNowPacketService.
      Rule 3: Rejects if packet is already signed (no in-place mutation).
      Rule 4: Passes phase and signer_email explicitly.

    Body (all optional — defaults to packet/intake values):
      phase:          1 (indemnitor) or 2 (post-approval). Default: 1.
      surety_id:      "osi" or "palmetto". Default: packet.template.
      signer_email:   Override indemnitor email.
      poa_number:     Required for phase 2.
      telegram_chat_id: If set, also sends signing link via Telegram.
    """
    try:
        data = (await request.json()) or {}

        packet = await _load_packet(packet_id)
        if not packet:
            return JSONResponse({"error": f"Packet {packet_id} not found"}, status_code=404)

        # Policy Rule 3: reject if already signed
        if packet.get("status") == "signed":
            return JSONResponse(status_code=409, content={
                "error": "Packet is already signed. Create a new packet version (Rule 3).",
                "packet_id": packet_id,
                "status": "signed",
            })

        # Policy Rule 3: reject if voided
        if packet.get("voided"):
            return JSONResponse(status_code=409, content={
                "error": "Packet has been voided. Create a new packet.",
                "packet_id": packet_id,
            })

        # Policy Rule 1: warn if bond_case_id not set
        bond_case_id = packet.get("bond_case_id")
        if not bond_case_id:
            logger.warning(
                "[paperwork] push_to_signnow: packet %s has no bond_case_id — "
                "proceeding but this violates signature policy Rule 1.",
                packet_id,
            )

        intake_id = packet.get("intake_id", "")
        intake = await _load_intake(intake_id)
        if not intake:
            return JSONResponse({"error": f"Intake {intake_id} not found"}, status_code=404)

        # Resolve parameters — body overrides packet defaults
        phase = int(data.get("phase", 1))
        surety_id = data.get("surety_id") or packet.get("surety_id") or packet.get("template", "osi")
        poa_number = data.get("poa_number") or intake.get("poa_number", "")
        signer_email = (
            data.get("signer_email")
            or packet.get("indemnitor_email")
            or intake.get("indemnitor_email")
            or intake.get("indemnitor", {}).get("email", "")
        )
        signer_name = (
            packet.get("indemnitor_name")
            or intake.get("indemnitor_name", "Indemnitor")
        )
        telegram_chat_id = data.get("telegram_chat_id") or intake.get("telegram_chat_id")
        routing_scenario = data.get("routing_scenario", "phase_1")
        custom_manifest = data.get("custom_manifest")

        if (phase == 2 or routing_scenario == "all-in-one") and not poa_number:
            return JSONResponse(status_code=400, content={
                "error": f"Scenario {routing_scenario} requires a poa_number. "
                         "Provide it in the request body or set it on the intake record.",
            })

        from dashboard.services.signnow_packet_service import SignNowPacketService
        svc = SignNowPacketService()
        result = await svc.create_packet(
            intake_doc=intake,
            packet_id=packet_id,
            phase=phase,
            surety_id=surety_id,
            signer_email=signer_email,
            signer_name=signer_name,
            poa_number=poa_number or None,
            custom_manifest=custom_manifest,
            routing_scenario=routing_scenario,
        )

        # Store the primary SignNow document ID for webhook correlation
        # The first document_id is the primary signing document
        primary_doc_id = (result.get("document_ids") or [""])[0]
        signing_link = result.get("signing_link", "")

        now = datetime.now(timezone.utc)
        packets_col = get_collection("paperwork_packets")
        await packets_col.update_one(
            {"packet_id": packet_id},
            {"$set": {
                "signnow_invite_id": result.get("invite_id"),
                "signnow_document_id": primary_doc_id,   # KEY: enables webhook lookup
                "signnow_document_ids": result.get("document_ids", []),
                "signnow_group_id": result.get("group_id", ""),
                "signnow_status": "sent",
                "signnow_sent_at": now,
                "signnow_phase": phase,
                "signnow_surety_id": surety_id,
                "status": "pending_signature",
                "updated_at": now,
            }},
        )

        # Update intake
        intake_col = get_collection("intake_queue")
        await intake_col.update_one(
            {"intake_id": intake_id},
            {"$set": {"paperwork_status": "pending_signature", "updated_at": now}},
        )

        logger.info(
            "[paperwork] Packet %s pushed to SignNow: invite=%s doc=%s phase=%d surety=%s",
            packet_id, result.get("invite_id"), primary_doc_id, phase, surety_id,
        )

        # ── Telegram delivery (if indemnitor has a Telegram chat_id stored) ──
        if signing_link and telegram_chat_id:
            try:
                from dashboard.services.telegram_service import get_telegram_service
                tg = get_telegram_service()
                await tg.send_signing_link(
                    chat_id=telegram_chat_id,
                    defendant_name=intake.get("defendant_name", ""),
                    signing_link=signing_link,
                    indemnitor_name=signer_name,
                    phase=phase,
                )
                logger.info("[paperwork] Telegram signing link sent to chat_id=%s", telegram_chat_id)
            except Exception as tg_exc:
                logger.warning("[paperwork] Telegram delivery failed: %s", tg_exc)

        return {
            "success": True,
            "packet_id": packet_id,
            "bond_case_id": bond_case_id,
            "phase": phase,
            "surety_id": surety_id,
            "signnow_invite_id": result.get("invite_id"),
            "signnow_document_id": primary_doc_id,
            "signnow_document_ids": result.get("document_ids", []),
            "signnow_group_id": result.get("group_id", ""),
            "signnow_signing_link": signing_link,
            "manifest_size": result.get("manifest_size", 0),
        }

    except Exception as exc:
        logger.exception("push_to_signnow error for %s", packet_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/paperwork/<packet_id>/void
# Void a packet (policy Rule 3 — no in-place mutation after send/sign).
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.post("/paperwork/{packet_id}/void")
async def void_packet(request: Request, packet_id: str):
    """
    Void a paperwork packet.

    Policy Rule 3: Once a packet has been sent or signed, it must be voided
    (not mutated). A new packet version should be created.

    Body:
      reason: (required) Human-readable reason for voiding.
      voided_by: (optional) Staff member name/ID.
    """
    try:
        data = (await request.json()) or {}
        reason = data.get("reason", "").strip()
        voided_by = data.get("voided_by", "staff")

        if not reason:
            return JSONResponse({"error": "reason is required to void a packet"}, status_code=400)

        packet = await _load_packet(packet_id)
        if not packet:
            return JSONResponse({"error": f"Packet {packet_id} not found"}, status_code=404)

        if packet.get("voided"):
            return JSONResponse({"error": "Packet is already voided", "packet_id": packet_id}, status_code=409)

        now = datetime.now(timezone.utc)
        packets_col = get_collection("paperwork_packets")
        await packets_col.update_one(
            {"packet_id": packet_id},
            {"$set": {
                "voided": True,
                "voided_at": now.isoformat(),
                "voided_by": voided_by,
                "void_reason": reason,
                "status": "voided",
                "updated_at": now,
            }},
        )

        # Log to audit_events
        audit_events = get_collection("audit_events")
        await audit_events.insert_one({
            "source": "paperwork_void",
            "event_type": "packet_voided",
            "packet_id": packet_id,
            "bond_case_id": packet.get("bond_case_id"),
            "reason": reason,
            "voided_by": voided_by,
            "timestamp": now.isoformat(),
        })

        logger.info("[paperwork] Packet %s voided by %s: %s", packet_id, voided_by, reason)
        return {
            "success": True,
            "packet_id": packet_id,
            "voided": True,
            "void_reason": reason,
            "voided_at": now.isoformat(),
        }

    except Exception as exc:
        logger.exception("void_packet error for %s", packet_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paperwork/list/<intake_id>
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.get("/paperwork/list/{intake_id}")
async def list_packets(intake_id: str):
    """Return all paperwork packets for an intake record."""
    try:
        packets_col = get_collection("paperwork_packets")
        cursor = packets_col.find(
            {"intake_id": intake_id},
            {"_id": 0},
        ).sort("created_at", -1)
        packets = await cursor.to_list(length=50)

        for p in packets:
            for field in ("created_at", "updated_at", "delivered_at", "signnow_sent_at"):
                if hasattr(p.get(field), "isoformat"):
                    p[field] = p[field].isoformat()

        return {
            "success": True,
            "intake_id": intake_id,
            "packets": packets,
            "count": len(packets),
        }
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paperwork/all
# Twenty CRM style: list all document packets across all cases with filters & stats
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.get("/paperwork/all")
async def list_all_packets(
    status: Optional[str] = None,
    surety: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
):
    """Return all paperwork packets across cases for Twenty CRM style document hub."""
    try:
        packets_col = get_collection("paperwork_packets")
        query: dict = {}

        if status and status != "all":
            query["$or"] = [
                {"status": status},
                {"signnow_status": status},
            ]
        if surety and surety != "all":
            query["surety_id"] = surety.lower()

        if search:
            rx = {"$regex": search, "$options": "i"}
            query["$or"] = [
                {"defendant_name": rx},
                {"indemnitor_name": rx},
                {"case_number": rx},
                {"booking_number": rx},
                {"packet_id": rx},
            ]

        cursor = packets_col.find(query, {"_id": 0}).sort("created_at", -1)
        packets = await cursor.to_list(length=limit)

        from datetime import date
        for p in packets:
            for field in ("created_at", "updated_at", "delivered_at", "signnow_sent_at", "signed_at"):
                val = p.get(field)
                if isinstance(val, (datetime, date)):
                    p[field] = val.isoformat()

        # Summary KPIs
        total = await packets_col.count_documents({})
        pending = await packets_col.count_documents({"status": {"$in": ["sent", "signnow_pending", "partially_signed"]}})
        signed = await packets_col.count_documents({"status": {"$in": ["signed", "completed"]}})
        filed = await packets_col.count_documents({"drive_url": {"$exists": True, "$ne": None}})

        def _to_int(v):
            try:
                return int(v)
            except Exception:
                return 0

        return {
            "success": True,
            "packets": packets,
            "count": len(packets),
            "summary": {
                "total_packets": _to_int(total),
                "pending_signature": _to_int(pending),
                "signed_completed": _to_int(signed),
                "filed_to_drive": _to_int(filed),
            },
        }
    except Exception as exc:
        logger.exception("list_all_packets error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paperwork/{packet_id}/hydration-audit
# Twenty CRM style: field hydration audit for 14-doc packet before dispatch
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.get("/paperwork/{packet_id}/hydration-audit")
async def get_packet_hydration_audit(packet_id: str):
    """Audit field hydration completeness for a paperwork packet."""
    try:
        packets_col = get_collection("paperwork_packets")
        packet = await packets_col.find_one(
            {"$or": [{"packet_id": packet_id}, {"booking_number": packet_id}]},
            {"_id": 0},
        )
        if not packet:
            return JSONResponse({"success": False, "error": "Packet not found"}, status_code=404)

        required_fields = [
            ("defendant_name", "Defendant Full Name"),
            ("defendant_dob", "Defendant Date of Birth"),
            ("defendant_address", "Defendant Address"),
            ("indemnitor_name", "Indemnitor Full Name"),
            ("indemnitor_phone", "Indemnitor Phone"),
            ("indemnitor_address", "Indemnitor Address"),
            ("case_number", "Case Number"),
            ("booking_number", "Booking Number"),
            ("bond_amount", "Bond Amount ($)"),
            ("surety_id", "Surety Selection (OSI/Palmetto)"),
            ("poa_number", "Power of Attorney (POA) Number"),
        ]

        fields_audit = []
        hydrated_count = 0

        for key, label in required_fields:
            val = packet.get(key)
            is_present = val is not None and str(val).strip() != "" and str(val).strip() != "None"
            if is_present:
                hydrated_count += 1
            fields_audit.append({
                "key": key,
                "label": label,
                "val": str(val) if is_present else None,
                "hydrated": is_present,
            })

        score = round((hydrated_count / len(required_fields)) * 100, 1)

        return {
            "success": True,
            "packet_id": packet.get("packet_id"),
            "booking_number": packet.get("booking_number"),
            "surety_id": packet.get("surety_id"),
            "status": packet.get("status"),
            "hydration_score": score,
            "hydrated_count": hydrated_count,
            "total_required": len(required_fields),
            "fields": fields_audit,
        }
    except Exception as exc:
        logger.exception("hydration_audit error for %s", packet_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)



# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paperwork/signnow/validate-templates
# Diagnostic: validate every TEMPLATE_MAP entry against production SignNow.
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.get("/paperwork/signnow/validate-templates")
async def validate_signnow_templates():
    """
    Validate all SignNow TEMPLATE_MAP entries against the production account.
    For each template:
      - Calls GET /document/{id} to confirm it exists and is accessible
      - Reports template name, field count, role list, and page count
      - Flags any missing or inaccessible templates
      - Lists all field names for field-mapping verification

    Returns:
        {
            "success": true,
            "templates": [...],
            "valid_count": 12,
            "invalid_count": 1,
            "palmetto_todos": ["collateral-receipt-palmetto", ...]
        }
    """
    import httpx
    from dashboard.services.signnow_packet_service import SignNowPacketService

    svc = SignNowPacketService()
    if not svc.api_token:
        try:
            await svc._get_token()
        except Exception as exc:
            return JSONResponse(status_code=500, content={
                "success": False,
                "error": f"SignNow auth failed: {exc}",
            })

    results = []
    valid = 0
    invalid = 0
    palmetto_todos = []

    async with httpx.AsyncClient(timeout=15) as client:
        for slug, template_id in SignNowPacketService.TEMPLATE_MAP.items():
            if not template_id or template_id.startswith("<"):
                palmetto_todos.append(slug)
                results.append({
                    "slug": slug,
                    "template_id": template_id,
                    "status": "todo",
                    "message": "Template ID not yet configured",
                })
                continue

            try:
                resp = await client.get(
                    f"{svc.base_url}/document/{template_id}",
                    headers=svc._headers,
                )
                if resp.status_code == 200:
                    doc_data = resp.json()
                    fields = doc_data.get("fields", [])
                    field_names = [f.get("field_name", f.get("name", "")) for f in fields]
                    roles = list({
                        f.get("role", "")
                        for f in fields
                        if f.get("role")
                    })
                    results.append({
                        "slug": slug,
                        "template_id": template_id,
                        "status": "valid",
                        "document_name": doc_data.get("document_name", ""),
                        "field_count": len(fields),
                        "field_names": sorted(field_names),  # for field-mapping audit
                        "roles": sorted(roles),
                        "page_count": doc_data.get("page_count", 0),
                    })
                    valid += 1
                elif resp.status_code == 404:
                    results.append({
                        "slug": slug,
                        "template_id": template_id,
                        "status": "not_found",
                        "message": "Template does not exist in this SignNow account",
                    })
                    invalid += 1
                else:
                    results.append({
                        "slug": slug,
                        "template_id": template_id,
                        "status": "error",
                        "http_status": resp.status_code,
                        "message": resp.text[:200],
                    })
                    invalid += 1
            except Exception as exc:
                results.append({
                    "slug": slug,
                    "template_id": template_id,
                    "status": "error",
                    "message": str(exc),
                })
                invalid += 1

    return {
        "success": True,
        "valid_count": valid,
        "invalid_count": invalid,
        "todo_count": len(palmetto_todos),
        "palmetto_todos": palmetto_todos,
        "templates": results,
    }