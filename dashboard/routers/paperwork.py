from __future__ import annotations
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Request
from dashboard.deps import get_settings
import os
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
from pydantic import BaseModel
from dashboard.extensions import get_collection
from dashboard.services.bb_client import (
    get_bb_client,
    send_message_universal,
    normalize_bb_send_result,
    bb_send_accepted,
)

logger = logging.getLogger(__name__)
paperwork_bp = APIRouter(prefix="/api", tags=["paperwork"])


def _packet_lookup_filter(packet_id: str) -> dict:
    """Safe Mongo filter for packet_id / ObjectId without invalid _id casts."""
    clauses: list[dict] = [{"packet_id": packet_id}]
    try:
        from bson import ObjectId
        if ObjectId.is_valid(packet_id):
            clauses.append({"_id": ObjectId(packet_id)})
    except Exception:
        pass
    return {"$or": clauses} if len(clauses) > 1 else clauses[0]
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

        # Local PDF folder inventory (agnostic + surety) for flatten / Adobe path
        local_pdf: dict = {}
        try:
            from dashboard.paperwork_pdf_service import list_template_inventory, packet_composition

            local_pdf = {
                "inventory": list_template_inventory(),
                "composition": {
                    "osi": packet_composition("osi"),
                    "palmetto": packet_composition("palmetto"),
                },
                "rule": (
                    "OSI packet = surety-agnostic-shamrock + osi; "
                    "Palmetto packet = surety-agnostic-shamrock + palmetto"
                ),
            }
        except Exception as inv_exc:
            logger.warning("paperwork/config local inventory: %s", inv_exc)
            local_pdf = {"error": str(inv_exc)}

        return {
            "success": True,
            "template_map": {"osi": osi, "palmetto": palmetto},
            "doc_rules": doc_rules,
            "local_pdf": local_pdf,
            "esign_providers": ["docuseal", "none"],
            "esign_default": "docuseal",
            "esign_policy": "docuseal_only",
            "legacy_esign_allowed": os.getenv("ALLOW_LEGACY_ESIGN", "false").lower() in ("1", "true", "yes"),
            "docuseal": {
                "configured": bool(os.getenv("DOCUSEAL_API_KEY")),
                "url": os.getenv("DOCUSEAL_URL", "https://sign.shamrockbailbonds.biz"),
                "webhook": "/api/webhooks/docuseal",
                "template_id_osi": os.getenv("DOCUSEAL_TEMPLATE_ID_OSI") or os.getenv("DOCUSEAL_TEMPLATE_ID") or "",
            },
            "flatten_engines": ["adobe_pdf_services", "local_pymupdf"],
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
# universal ≈ templates/surety-agnostic-shamrock + shared legal
# osi_surety / palmetto_surety ≈ templates/osi or templates/palmetto
DEFAULT_DOC_RULES_CATEGORIES = {
    "universal": [
        "paperwork-header",
        "faq-cosigners",
        "faq-defendants",
        "master_bail_application",
        "indemnity_agreement",
        "promissory_note",
        "disclosure_statement",
        "master-waiver",
        "ssa-release",
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
        "surety-terms",
    ],
    "palmetto_surety": [
        "palmetto_power_certificate",
        "palmetto_appearance_bond",
        "surety-terms",
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


@paperwork_bp.get("/paperwork/preview/{bond_case_id}")
async def paperwork_preview(bond_case_id: str):
    """Generate an instant mobile PDF preview stream for a bond case or intake."""
    from fastapi.responses import Response
    from dashboard.bond_pdf_service import generate_appearance_bond

    def _id_clauses(value: str) -> list[dict]:
        clauses: list[dict] = [
            {"bond_case_id": value},
            {"intake_id": value},
            {"booking_number": value},
            {"_id": value},
        ]
        try:
            from bson import ObjectId
            if ObjectId.is_valid(value):
                clauses.append({"_id": ObjectId(value)})
        except Exception:
            pass
        return clauses

    try:
        cases_col = get_collection("active_bonds")
        case_doc = await cases_col.find_one({"$or": _id_clauses(bond_case_id)})
        source = "active_bonds"
        if not case_doc:
            intake_col = get_collection("intake_queue")
            case_doc = await intake_col.find_one({"$or": _id_clauses(bond_case_id)})
            source = "intake_queue"
            if not case_doc:
                return JSONResponse(
                    {"success": False, "error": "Case record not found"},
                    status_code=404,
                )
            bond_data = _build_bond_data(case_doc)
        else:
            # active_bonds already stores flat fields; normalize aliases for PDF fill
            bond_data = {
                "defendant_name": case_doc.get("defendant_name") or case_doc.get("name") or "",
                "booking_number": case_doc.get("booking_number") or "",
                "county": case_doc.get("county") or case_doc.get("defendant_county") or "",
                "charges": case_doc.get("charges") or case_doc.get("charge") or "",
                "bond_amount": case_doc.get("bond_amount") or case_doc.get("amount") or "",
                "poa_number": case_doc.get("poa_number") or "",
                "case_number": case_doc.get("case_number") or "",
                "indemnitor_name": case_doc.get("indemnitor_name") or "",
                "surety": (
                    case_doc.get("surety")
                    or case_doc.get("surety_id")
                    or case_doc.get("insuranceCompany")
                    or "osi"
                ),
                "court_date": case_doc.get("court_date") or "",
                "address": case_doc.get("defendant_address") or case_doc.get("address") or "",
            }

        pdf_bytes = generate_appearance_bond(bond_data)
        if not pdf_bytes:
            return JSONResponse(
                {"success": False, "error": "PDF generation returned empty output"},
                status_code=500,
            )
        filename = f"preview_{bond_case_id}.pdf".replace(" ", "_")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "X-Preview-Source": source,
            },
        )
    except Exception as exc:
        logger.exception("paperwork_preview error for %s", bond_case_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)



def _build_bond_data(intake: dict) -> dict:
    """
    Build the data dict expected by bond_pdf_service.generate_appearance_bonds().

    Identity (enforced by bond_pdf_service):
      - One appearance bond per charge
      - Each charge → case_number (defendant may have multiple cases)
      - Exactly one POA per charge
    """
    ind = intake.get("indemnitor", {})
    def_ = intake.get("defendant", {})

    # Prefer structured charge_details (per-charge bond + case + optional POA)
    charge_details = (
        intake.get("charge_details")
        or def_.get("charge_details")
        or intake.get("charge_list")
        or def_.get("charge_list")
        or []
    )
    poa_numbers = (
        intake.get("poa_numbers")
        or intake.get("poa_number")
        or def_.get("poa_numbers")
        or ""
    )
    case_numbers = (
        intake.get("case_numbers")
        or intake.get("case_number")
        or def_.get("case_number")
        or def_.get("caseNumber")
        or ""
    )

    return {
        # Defendant
        "defendant_name": intake.get("defendant_name", def_.get("name", "")),
        "dob": def_.get("dob", ""),
        "booking_number": def_.get("bookingNumber", intake.get("defendant_booking_number", "")),
        "county": def_.get("county", intake.get("defendant_county", "")),
        "facility": def_.get("facility", intake.get("defendant_facility", "")),
        "charges": def_.get("charges", ""),
        "bond_amount": def_.get("bondAmount", ""),
        "charge_details": charge_details if isinstance(charge_details, list) else [],
        "case_number": case_numbers if isinstance(case_numbers, str) else "",
        "case_numbers": case_numbers,
        "poa_number": poa_numbers if isinstance(poa_numbers, str) else "",
        "poa_numbers": poa_numbers,
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

        # ── Appearance bonds (one per charge) — UNSIGNED files for print ────
        # Procedure: store unsigned PDF → print → live wet-ink signature → jail.
        # Never e-sign (SignNow / Adobe Sign). See bond_pdf_service.appearance_bond_procedure_meta.
        if DOC_APPEARANCE_BOND in PACKET_TYPES.get(packet_type, [DOC_APPEARANCE_BOND]):
            try:
                from dashboard.bond_pdf_service import (
                    describe_appearance_bonds,
                    store_appearance_bond_pdfs,
                    appearance_bond_procedure_meta,
                )

                generate_bonds = _get_bond_pdf_service()
                plan = describe_appearance_bonds(bond_data)
                pdf_buffers = generate_bonds(bond_data, template=template)
                stored = store_appearance_bond_pdfs(
                    pdf_buffers,
                    bond_data=bond_data,
                    surety=template,
                    packet_id=packet_id,
                    booking_number=bond_data.get("booking_number"),
                )
                proc = appearance_bond_procedure_meta()
                for i, buf in enumerate(pdf_buffers):
                    meta = plan[i] if i < len(plan) else {}
                    file_meta = stored[i] if i < len(stored) else {}
                    charge_label = (meta.get("charge") or f"Charge {i + 1}")[:60]
                    case_no = meta.get("case_number") or ""
                    poa = meta.get("poa_number") or ""
                    doc_id = f"{packet_id}-BOND-{i + 1:02d}"
                    documents.append({
                        "doc_id": doc_id,
                        "type": DOC_APPEARANCE_BOND,
                        "label": f"Appearance Bond (UNSIGNED · print) — {charge_label}",
                        "template": template,
                        "charge_index": i,
                        "charge": meta.get("charge") or "",
                        "case_number": case_no,
                        "poa_number": poa,
                        "bond_amount": meta.get("bond_amount"),
                        "ready": bool(meta.get("ready")),
                        "status": "unsigned_stored",
                        "signed": False,
                        "size_bytes": len(buf),
                        "file_path": file_meta.get("file_path"),
                        "filename": file_meta.get("filename"),
                        "generated_at": now.isoformat(),
                        **proc,
                    })
            except Exception as e:
                logger.warning("Bond PDF generation error: %s", e)
                documents.append({
                    "type": DOC_APPEARANCE_BOND,
                    "status": "error",
                    "error": str(e),
                    "print_only": True,
                    "e_sign": False,
                })

        # ── Indemnity Agreement ────────────────────────────────────────────
        if DOC_INDEMNITY in PACKET_TYPES.get(packet_type, []):
            documents.append({
                "doc_id": f"{packet_id}-IND",
                "type": DOC_INDEMNITY,
                "label": "Indemnity Agreement",
                "status": "pending_esign",
                "generated_at": now.isoformat(),
            })

        # ── SSA Release ────────────────────────────────────────────────────
        if DOC_SSA_RELEASE in PACKET_TYPES.get(packet_type, []):
            documents.append({
                "doc_id": f"{packet_id}-SSA",
                "type": DOC_SSA_RELEASE,
                "label": "SSA Release",
                "status": "pending_esign",
                "generated_at": now.isoformat(),
            })

        # ── POA ────────────────────────────────────────────────────────────
        if DOC_POA in PACKET_TYPES.get(packet_type, []):
            documents.append({
                "doc_id": f"{packet_id}-POA",
                "type": DOC_POA,
                "label": "Power of Attorney",
                "status": "pending_esign",
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
            "esign_provider": "docuseal",
            "signnow_invite_id": None,
            "signnow_document_id": None,            # legacy SignNow
            "signnow_status": None,
            "docuseal_submission_id": None,         # populated on DocuSeal push
            "docuseal_status": None,
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
        from dashboard.services.paperwork_signers import party_signers_from_packet

        for p in packets:
            for field in ("created_at", "updated_at", "delivered_at", "signnow_sent_at", "signed_at"):
                val = p.get(field)
                if isinstance(val, (datetime, date)):
                    p[field] = val.isoformat()
            p["parties"] = party_signers_from_packet(p)

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

        from dashboard.services.paperwork_signers import party_signers_from_packet
        packet["parties"] = party_signers_from_packet(packet)
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
        try:
            data = (await request.json()) or {}
        except Exception:
            data = {}
        custom_message = data.get("message", "")
        include_geo = data.get("include_geo", True)
        role = (data.get("role") or "").strip().lower() or None

        packet = await _load_packet(packet_id)
        if not packet:
            return JSONResponse({"error": f"Packet {packet_id} not found"}, status_code=404)

        from dashboard.services.paperwork_signers import (
            party_signers_from_packet,
            pick_party,
            branded_sign_url,
        )

        parties = party_signers_from_packet(packet)
        chosen = pick_party(parties, role=role, phone=data.get("phone"))
        phone = (data.get("phone") or "").strip() or (chosen or {}).get("phone") or ""
        if not phone:
            phone = (packet.get("indemnitor_phone") or packet.get("defendant_phone") or "").strip()
        phone = "".join(c for c in phone if c.isdigit())[-10:]
        if not phone:
            return JSONResponse(
                {"error": "phone is required — add an indemnitor or defendant mobile first"},
                status_code=400,
            )

        # Policy: recipient must match a known party on this packet (fail closed).
        known_digits = {
            "".join(c for c in str(p.get("phone") or "") if c.isdigit())[-10:]
            for p in parties
            if p.get("phone")
        }
        for key in ("indemnitor_phone", "defendant_phone"):
            digits = "".join(c for c in str(packet.get(key) or "") if c.isdigit())[-10:]
            if digits:
                known_digits.add(digits)
        if known_digits and phone not in known_digits:
            logger.warning(
                "[paperwork] deliver_packet: phone mismatch for packet %s role=%s — blocked",
                packet_id,
                (chosen or {}).get("role") or role or "",
            )
            return JSONResponse(
                {"error": "Recipient phone does not match indemnitor or defendant on this packet"},
                status_code=403,
            )

        defendant_name = packet.get("defendant_name", "your defendant")
        intake_id = packet.get("intake_id", "")
        party_role = (chosen or {}).get("role") or role or "indemnitor"
        magic_link = (chosen or {}).get("share_url") or branded_sign_url(packet_id, party_role)

        # Build the message
        if custom_message:
            message = custom_message
        else:
            who = "your" if party_role == "defendant" else f"{defendant_name}'s"
            message = (
                f"Hi! Here is the Shamrock Bail Bonds paperwork for {who} bond.\n\n"
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
        sent_ok = bool(result and result.get("success"))
        if not sent_ok:
            return JSONResponse(
                {
                    "success": False,
                    "error": (result or {}).get("error") or "BlueBubbles send failed",
                    "packet_id": packet_id,
                    "role": party_role,
                },
                status_code=502,
            )

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
            "success": True,
            "packet_id": packet_id,
            "delivered_to": mask_phone(phone),
            "recipient": mask_phone(phone),
            "role": party_role,
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
    LEGACY — SignNow push. Disabled for new work unless ALLOW_LEGACY_ESIGN=true.
    Use POST /api/paperwork/{packet_id}/docuseal or packet/finalize with provider=docuseal.
    """
    if os.getenv("ALLOW_LEGACY_ESIGN", "false").lower() not in ("1", "true", "yes"):
        return JSONResponse(
            {
                "success": False,
                "error": "signnow_retired",
                "message": (
                    "SignNow is no longer used for new bond packets. "
                    "Use DocuSeal: POST /api/paperwork/packet/finalize with provider=docuseal "
                    "or POST /api/paperwork/{packet_id}/docuseal."
                ),
                "use": "docuseal",
            },
            status_code=410,
        )
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
        try:
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
        except ValueError as ve:
            # Hydration fail-closed / missing POA etc. → client error, not 500
            return JSONResponse(status_code=400, content={
                "error": str(ve),
                "packet_id": packet_id,
            })

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


# ─────────────────────────────────────────────────────────────────────────────
# SwipeSimple Link, Cash Payment & Post-Release Remedy Endpoints
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.post("/paperwork/payment/swipesimple-link")
async def generate_swipesimple_link(request: Request):
    """
    Generate SwipeSimple checkout URL and deliver via BlueBubbles (iMessage/SMS)
    and/or Gmail email once the bond's premium amount has been confirmed.

    Payload options:
        packet_id: Optional paperwork packet ID
        booking_number: Optional county booking/case number
        amount: Confirmed premium amount (numeric)
        phone: Recipient cell phone for iMessage/SMS
        email: Recipient email address for payment request email
        deliver: Bool (default True) — master toggle to dispatch link
        deliver_text: Bool (default True) — send BlueBubbles text
        deliver_email: Bool (default True) — send Gmail email
        defendant_name: Optional defendant full name
    """
    try:
        body = await request.json() if request else {}
        packet_id = (body.get("packet_id") or "").strip()
        booking_number = (body.get("booking_number") or "").strip()
        phone = (body.get("phone") or "").strip()
        email_addr = (body.get("email") or body.get("email_address") or "").strip()
        amount_val = body.get("amount")
        defendant_name = (body.get("defendant_name") or "").strip()

        deliver_master = body.get("deliver", True)
        deliver_text = body.get("deliver_text", deliver_master)
        deliver_email = body.get("deliver_email", deliver_master)

        # Context Fallback: Hydrate missing fields from MongoDB
        packet_doc = None
        if packet_id:
            pkts = get_collection("paperwork_packets")
            try:
                packet_doc = await pkts.find_one(_packet_lookup_filter(packet_id))
            except Exception as lookup_err:
                logger.warning("SwipeSimple packet lookup failed: %s", lookup_err)
                packet_doc = None

        bonds_col = get_collection("active_bonds")
        bond_doc = None
        if booking_number:
            bond_doc = await bonds_col.find_one({"booking_number": booking_number})
        elif packet_doc and packet_doc.get("booking_number"):
            booking_number = str(packet_doc.get("booking_number") or "").strip()
            if booking_number:
                bond_doc = await bonds_col.find_one({"booking_number": booking_number})

        intake_doc = None
        if packet_doc and packet_doc.get("intake_id"):
            intake_col = get_collection("intake_queue")
            intake_id = str(packet_doc.get("intake_id") or "").strip()
            intake_clauses: list[dict] = [{"intake_id": intake_id}]
            try:
                from bson import ObjectId
                if ObjectId.is_valid(intake_id):
                    intake_clauses.append({"_id": ObjectId(intake_id)})
            except Exception:
                pass
            try:
                intake_doc = await intake_col.find_one(
                    {"$or": intake_clauses} if len(intake_clauses) > 1 else intake_clauses[0]
                )
            except Exception:
                intake_doc = None

        # Resolve phone
        if not phone:
            phone = (
                (packet_doc.get("indemnitor_phone") if packet_doc else "")
                or (packet_doc.get("phone") if packet_doc else "")
                or (intake_doc.get("indemnitor_phone") if intake_doc else "")
                or (bond_doc.get("indemnitor_phone") if bond_doc else "")
                or ((bond_doc.get("indemnitor") or {}).get("phone") if bond_doc else "")
                or ""
            )
        phone = str(phone or "").strip()

        # Resolve email
        if not email_addr:
            email_addr = (
                (packet_doc.get("indemnitor_email") if packet_doc else "")
                or (packet_doc.get("email") if packet_doc else "")
                or (intake_doc.get("indemnitor_email") if intake_doc else "")
                or (bond_doc.get("indemnitor_email") if bond_doc else "")
                or ((bond_doc.get("indemnitor") or {}).get("email") if bond_doc else "")
                or ""
            )
        email_addr = str(email_addr or "").strip()

        # Resolve defendant name
        if not defendant_name:
            defendant_name = (
                (packet_doc.get("defendant_name") if packet_doc else "")
                or (bond_doc.get("defendant_name") if bond_doc else "")
                or (intake_doc.get("defendant_name") if intake_doc else "")
                or "Client"
            )
        defendant_name = str(defendant_name or "Client").strip() or "Client"

        # Resolve amount
        try:
            amount = float(amount_val) if amount_val is not None else 0.0
        except (TypeError, ValueError):
            amount = 0.0

        if amount <= 0.0:
            try:
                if packet_doc and (packet_doc.get("premium_amount") or packet_doc.get("numeric_premium_dollar")):
                    amount = float(packet_doc.get("premium_amount") or packet_doc.get("numeric_premium_dollar") or 0.0)
                elif bond_doc and (bond_doc.get("premium_amount") or bond_doc.get("total_premium")):
                    amount = float(bond_doc.get("premium_amount") or bond_doc.get("total_premium") or 0.0)
                elif intake_doc and intake_doc.get("premium_amount"):
                    amount = float(intake_doc.get("premium_amount") or 0.0)
            except (TypeError, ValueError):
                amount = 0.0

        swipesimple_url = (os.getenv("SWIPESIMPLE_PAYMENT_LINK") or "").strip()
        if not swipesimple_url:
            try:
                swipesimple_url = (getattr(get_settings(), "SWIPESIMPLE_PAYMENT_LINK", None) or "").strip()
            except Exception:
                swipesimple_url = ""
        if not swipesimple_url:
            swipesimple_url = "https://swipesimple.com/links/lnk_b6bf996f4c57bb340a150e297e769abd"

        text_delivered = False
        text_queued = False
        text_channel = None
        email_delivered = False
        text_error = None
        email_error = None

        # 1) Deliver text via BlueBubbles (queue + retry; never Twilio)
        if deliver_text and phone:
            clean_phone = "".join(ch for ch in phone if ch.isdigit())
            if len(clean_phone) >= 10:
                try:
                    amount_str = f"${amount:,.2f}" if amount > 0 else "Confirmed Amount"
                    msg = (
                        f"💳 Shamrock Bail Bonds — Payment Request\n"
                        f"Defendant: {defendant_name}\n"
                        f"Case / Booking: {booking_number or 'N/A'}\n"
                        f"Confirmed Premium Amount: {amount_str}\n\n"
                        f"Pay Online via SwipeSimple:\n{swipesimple_url}\n\n"
                        f"Questions? Call or text us 24/7 at (239) 224-5454."
                    )
                    raw = await send_message_universal(phone, msg)
                    send_res = normalize_bb_send_result(raw)
                    text_delivered = bb_send_accepted(send_res)
                    text_queued = bool(send_res.get("queued"))
                    text_channel = send_res.get("channel")
                    if not text_delivered:
                        text_error = send_res.get("error") or "bb_send_failed"
                except Exception as bb_err:
                    text_error = str(bb_err)[:200]
                    logger.warning("SwipeSimple BlueBubbles delivery warning: %s", bb_err)
            else:
                text_error = "invalid_phone"

        # 2) Deliver email via Gmail API
        if deliver_email and email_addr and "@" in email_addr:
            try:
                from dashboard.services.gmail_reader import GmailReaderService
                gmail_svc = GmailReaderService()
                if gmail_svc.is_configured:
                    amount_str = f"${amount:,.2f}" if amount > 0 else "Confirmed Amount"
                    subject = f"Shamrock Bail Bonds — Payment Request ({amount_str})"
                    body_text = (
                        f"Shamrock Bail Bonds — Payment Request\n\n"
                        f"Defendant Name: {defendant_name}\n"
                        f"Case / Booking Number: {booking_number or 'N/A'}\n"
                        f"Confirmed Premium Amount: {amount_str}\n\n"
                        f"Please click the link below to securely pay your bond premium online via SwipeSimple:\n"
                        f"{swipesimple_url}\n\n"
                        f"Shamrock Bail Bonds | 1528 Broadway, Ft. Myers, FL 33901\n"
                        f"24/7 Phone / Text: (239) 224-5454\n"
                    )
                    body_html = f"""
                    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 580px; margin: 0 auto; padding: 20px; background-color: #0f172a; color: #f8fafc; border-radius: 12px; border: 1px solid #1e293b;">
                      <div style="text-align: center; padding-bottom: 16px; border-bottom: 1px solid #334155;">
                        <h2 style="color: #10b981; margin: 0; font-size: 22px; letter-spacing: 0.5px;">☘️ SHAMROCK BAIL BONDS</h2>
                        <p style="color: #94a3b8; font-size: 13px; margin-top: 4px;">Fast. Frictionless. Everywhere.</p>
                      </div>
                      <div style="padding: 20px 0;">
                        <h3 style="color: #ffffff; margin-top: 0;">Payment Request — Confirmed Bond Premium</h3>
                        <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5;">
                          Your bond paperwork and premium amount have been verified. You can securely pay online using credit/debit card via SwipeSimple below:
                        </p>
                        <div style="background-color: #1e293b; border-radius: 8px; padding: 16px; margin: 16px 0; border: 1px solid #334155;">
                          <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                            <tr>
                              <td style="color: #94a3b8; padding: 4px 0;">Defendant:</td>
                              <td style="color: #ffffff; font-weight: 600; text-align: right;">{defendant_name}</td>
                            </tr>
                            <tr>
                              <td style="color: #94a3b8; padding: 4px 0;">Booking / Case #:</td>
                              <td style="color: #ffffff; font-weight: 600; text-align: right;">{booking_number or 'N/A'}</td>
                            </tr>
                            <tr style="border-top: 1px dashed #475569;">
                              <td style="color: #10b981; font-weight: 700; padding: 8px 0 0 0; font-size: 15px;">Confirmed Premium:</td>
                              <td style="color: #10b981; font-weight: 700; text-align: right; padding: 8px 0 0 0; font-size: 18px;">{amount_str}</td>
                            </tr>
                          </table>
                        </div>
                        <div style="text-align: center; margin: 24px 0;">
                          <a href="{swipesimple_url}" target="_blank" style="background-color: #10b981; color: #ffffff; padding: 14px 28px; font-size: 15px; font-weight: 700; text-decoration: none; border-radius: 8px; display: inline-block; box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3);">
                            💳 Pay {amount_str} Online Now
                          </a>
                        </div>
                        <p style="color: #64748b; font-size: 12px; text-align: center;">
                          Direct Link: <a href="{swipesimple_url}" style="color: #38bdf8; word-break: break-all;">{swipesimple_url}</a>
                        </p>
                      </div>
                      <div style="border-top: 1px solid #334155; padding-top: 14px; text-align: center; font-size: 12px; color: #64748b;">
                        <p style="margin: 2px 0;">Shamrock Bail Bonds | 1528 Broadway, Ft. Myers, FL 33901</p>
                        <p style="margin: 2px 0;">24/7 Support: (239) 224-5454 | admin@shamrockbailbonds.biz</p>
                      </div>
                    </div>
                    """
                    mail_res = gmail_svc.send_email(
                        to=email_addr,
                        subject=subject,
                        body_text=body_text,
                        body_html=body_html,
                    )
                    email_delivered = bool(mail_res and mail_res.get("success"))
                    if not email_delivered:
                        email_error = mail_res.get("error") if mail_res else "email_failed"
                else:
                    email_error = "gmail_not_configured"
            except Exception as mail_err:
                email_error = str(mail_err)[:200]
                logger.warning("SwipeSimple Gmail delivery warning: %s", mail_err)

        # Audit & Database Record Logging
        now_iso = datetime.now(timezone.utc).isoformat()
        if packet_id:
            try:
                pkts = get_collection("paperwork_packets")
                await pkts.update_one(
                    _packet_lookup_filter(packet_id),
                    {
                        "$set": {
                            "last_payment_link_sent_at": now_iso,
                            "last_payment_amount": amount,
                            "payment_link_delivered_text": text_delivered,
                            "payment_link_delivered_email": email_delivered,
                            "payment_link_text_channel": text_channel,
                            "payment_link_text_queued": text_queued,
                        },
                        "$inc": {"payment_link_sent_count": 1},
                    },
                )
            except Exception as db_err:
                logger.warning("Failed to update paperwork_packets record: %s", db_err)

        try:
            disp_col = get_collection("payment_dispatches")
            await disp_col.insert_one({
                "packet_id": packet_id,
                "booking_number": booking_number,
                "amount": amount,
                "phone": phone,
                "email": email_addr,
                "swipesimple_url": swipesimple_url,
                "text_delivered": text_delivered,
                "text_queued": text_queued,
                "text_channel": text_channel,
                "text_error": text_error,
                "email_delivered": email_delivered,
                "email_error": email_error,
                "created_at": now_iso,
            })
        except Exception as log_err:
            logger.warning("Failed to log payment dispatch record: %s", log_err)

        return {
            "success": True,
            "packet_id": packet_id,
            "booking_number": booking_number,
            "amount": amount,
            "payment_link": swipesimple_url,
            "delivered": (text_delivered or email_delivered),
            "text_delivered": text_delivered,
            "text_queued": text_queued,
            "text_channel": text_channel,
            "text_error": text_error,
            "email_delivered": email_delivered,
            "email_error": email_error,
            "recipient_phone": phone,
            "recipient_email": email_addr,
            "defendant_name": defendant_name,
        }
    except Exception as exc:
        logger.exception("swipesimple-link error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@paperwork_bp.post("/paperwork/payment/cash-log")
async def log_cash_payment(request: Request):
    """Log official cash premium payment and generate receipt record."""
    try:
        body = await request.json()
        packet_id = body.get("packet_id", "")
        amount = float(body.get("amount", 0.0))
        received_from = body.get("received_from", "Indemnitor")
        notes = body.get("notes", "")

        tx_col = get_collection("payments")
        receipt_id = f"CASH-{uuid.uuid4().hex[:8].upper()}"
        now_iso = datetime.now(timezone.utc).isoformat()

        record = {
            "receipt_id": receipt_id,
            "packet_id": packet_id,
            "amount": amount,
            "payment_method": "cash",
            "received_from": received_from,
            "notes": notes,
            "status": "completed",
            "created_at": now_iso,
        }
        await tx_col.insert_one(record)

        return {
            "success": True,
            "message": f"Cash payment of ${amount:,.2f} recorded successfully",
            "receipt_id": receipt_id,
            "record": {k: v for k, v in record.items() if k != "_id"},
        }
    except Exception as exc:
        logger.exception("cash-log error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@paperwork_bp.post("/paperwork/post-release/remedy-doc")
async def generate_post_release_remedy_doc(request: Request):
    """Generate post-release legal documents & forfeiture remedies."""
    try:
        body = await request.json()
        doc_type = body.get("doc_type", "")
        packet_id = body.get("packet_id", "")
        case_number = body.get("case_number", "TBD")
        defendant_name = body.get("defendant_name", "Unknown Defendant")
        county = body.get("county", "Lee")
        notes = body.get("notes", "")

        remedy_col = get_collection("forfeiture_remedies")
        doc_id = f"REMEDY-{uuid.uuid4().hex[:8].upper()}"
        now_iso = datetime.now(timezone.utc).isoformat()

        titles = {
            "motion_vacate_forfeiture": "Motion to Vacate Forfeiture & Discharge Bond",
            "affidavit_surrender": "Affidavit of Defendant Surrender & Notice of Custody",
            "indemnitor_recovery_demand": "Formal Indemnitor Recovery Demand & Notice of Forfeiture",
            "fugitive_recovery_warrant": "Fugitive Recovery Agent Warrant & Authorization",
        }
        title = titles.get(doc_type, "Post-Release Legal Pleading")

        doc_record = {
            "doc_id": doc_id,
            "doc_type": doc_type,
            "title": title,
            "packet_id": packet_id,
            "case_number": case_number,
            "defendant_name": defendant_name,
            "county": county,
            "notes": notes,
            "status": "draft_generated",
            "created_at": now_iso,
        }
        await remedy_col.insert_one(doc_record)

        return {
            "success": True,
            "message": f"Generated {title}",
            "doc_id": doc_id,
            "record": {k: v for k, v in doc_record.items() if k != "_id"},
        }
    except Exception as exc:
        logger.exception("remedy-doc error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

# ─────────────────────────────────────────────────────────────────────────────
# Adaptive Case Packet Builder
# Resolves match → defendant/indemnitor, assembles drag-drop manifest,
# flattens extras, sends via SignNow (primary) or Adobe Sign (optional).
# ─────────────────────────────────────────────────────────────────────────────

@paperwork_bp.post("/paperwork/packet/context")
async def packet_builder_context(request: Request):
    """Resolve adaptive case context (defendant + indemnitor + bond) for the modal."""
    try:
        body = (await request.json()) or {}
        from dashboard.services.packet_builder_service import (
            resolve_case_context,
            build_adaptive_field_map,
            hydration_score,
            apply_self_indemnitor,
            assemble_manifest,
            resolve_client_esign_provider,
            SMALL_BOND_MAX,
        )
        from dashboard.services.adobe_pdf_service import adobe_status

        ctx = await resolve_case_context(
            intake_id=body.get("intake_id"),
            match_id=body.get("match_id"),
            defendant_id=body.get("defendant_id"),
            booking_number=body.get("booking_number"),
            county=body.get("county"),
            bond_case_id=body.get("bond_case_id"),
            packet_id=body.get("packet_id"),
        )

        self_mode = bool(body.get("self_indemnitor"))
        if self_mode:
            pin = body.get("authorization_pin") or body.get("pin") or ""
            try:
                ctx = apply_self_indemnitor(ctx, pin)
            except PermissionError as pe:
                return JSONResponse({"success": False, "error": str(pe)}, status_code=403)

        fields = build_adaptive_field_map(ctx)
        audit = hydration_score(fields)

        # Load drag-drop rules
        rules_col = get_collection("paperwork_rules")
        rules_doc = await rules_col.find_one({"_id": "drag_drop_rules"}, {"_id": 0})
        categories = (rules_doc or {}).get("categories") or DEFAULT_DOC_RULES_CATEGORIES

        include_pp = bool(body.get("include_payment_plan", True))
        extra_keys = body.get("extra_doc_keys") or []
        manifest = assemble_manifest(
            categories,
            surety_id=ctx.get("surety_id") or "osi",
            include_payment_plan=include_pp,
            extra_catalog_keys=extra_keys,
            self_indemnitor=bool(ctx.get("self_indemnitor")),
        )

        # Per-client e-sign preference (not per PDF)
        client_provider = await resolve_client_esign_provider(
            preferred=body.get("provider"),
            indemnitor_id=ctx.get("indemnitor_id"),
            defendant_id=ctx.get("defendant_id"),
            bond_case_id=ctx.get("bond_case_id"),
        )
        ctx["esign_provider"] = client_provider
        astat = adobe_status()

        return {
            "success": True,
            "context": ctx,
            "fields": fields,
            "hydration": audit,
            "categories": categories,
            "manifest": manifest,
            "small_bond_max": SMALL_BOND_MAX,
            "esign_provider": client_provider,
            "providers": {
                "docuseal": bool(os.getenv("DOCUSEAL_API_KEY")),
                "signnow": True,
                "adobe_pdf_services": astat["pdf_services"]["configured"],
                "adobe_sign": astat["acrobat_sign"]["configured"],
                # legacy key for UI
                "adobe": astat["acrobat_sign"]["configured"],
            },
            "adobe": astat,
        }
    except Exception as exc:
        logger.exception("packet_builder_context error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@paperwork_bp.post("/paperwork/packet/finalize")
async def packet_builder_finalize(request: Request):
    """
    Finalize a case packet:
      - adaptive hydration from match/defendant/indemnitor
      - optional self-indemnitor (PIN 224545)
      - assemble docs from drag-drop rules + extra catalog keys
      - attach extra uploaded PDFs
      - flatten into a single PDF when possible
      - send to DocuSeal (default) and/or SignNow / Adobe for signature
    """
    try:
        body = (await request.json()) or {}
        from dashboard.services.packet_builder_service import (
            resolve_case_context,
            build_adaptive_field_map,
            hydration_score,
            apply_self_indemnitor,
            assemble_manifest,
            decode_extra_uploads,
            flatten_pdf_bytes,
            send_via_adobe,
            resolve_client_esign_provider,
            save_client_esign_provider,
            template_slug_for_catalog_key,
            SMALL_BOND_MAX,
        )
        from dashboard.services.adobe_pdf_service import get_adobe_pdf_client

        ctx = await resolve_case_context(
            intake_id=body.get("intake_id"),
            match_id=body.get("match_id"),
            defendant_id=body.get("defendant_id"),
            booking_number=body.get("booking_number"),
            county=body.get("county"),
            bond_case_id=body.get("bond_case_id"),
            packet_id=body.get("packet_id"),
        )
        
        user = getattr(request.state, "user", {})
        if user.get("agent_name"):
            ctx["agent_name"] = user.get("agent_name")
        if user.get("license_number"):
            ctx["license_number"] = user.get("license_number")

        if body.get("self_indemnitor"):
            pin = body.get("authorization_pin") or body.get("pin") or ""
            try:
                ctx = apply_self_indemnitor(ctx, pin)
            except PermissionError as pe:
                return JSONResponse({"success": False, "error": str(pe)}, status_code=403)
            # Discretionary small-bond policy note (PIN already gates)
            if ctx.get("bond_amount") and ctx["bond_amount"] > SMALL_BOND_MAX:
                ctx["self_indemnitor_large_bond_override"] = True

        # E-sign provider is per-client (whole packet / all docs), not per PDF
        provider = await resolve_client_esign_provider(
            preferred=(body.get("provider") or "").lower().strip() or None,
            indemnitor_id=ctx.get("indemnitor_id") or body.get("indemnitor_id"),
            defendant_id=ctx.get("defendant_id") or body.get("defendant_id"),
            bond_case_id=ctx.get("bond_case_id") or body.get("bond_case_id"),
        )
        # DocuSeal-only for new packets (maps legacy preferences to docuseal)
        from dashboard.services.packet_builder_service import _normalize_esign_provider
        provider = _normalize_esign_provider(provider)

        # Persist preference when staff explicitly chose a provider in the UI
        if body.get("provider") and body.get("save_esign_preference", True):
            try:
                await save_client_esign_provider(
                    provider=provider,
                    indemnitor_id=ctx.get("indemnitor_id"),
                    defendant_id=ctx.get("defendant_id"),
                    bond_case_id=ctx.get("bond_case_id"),
                )
            except Exception as pref_exc:
                logger.warning("save esign preference skipped: %s", pref_exc)

        # Allow UI field overrides (staff edits after auto-fill)
        overrides = body.get("field_overrides") or {}
        if isinstance(overrides, dict):
            def_ = ctx.setdefault("defendant", {})
            ind = ctx.setdefault("indemnitor", {})
            map_def = {
                "defendant_name": ("defendant", "name"),
                "defendant_dob": ("defendant", "dob"),
                "defendant_phone": ("defendant", "phone"),
                "defendant_email": ("defendant", "email"),
                "defendant_address": ("defendant", "address"),
                "indemnitor_name": ("indemnitor", "name"),
                "indemnitor_phone": ("indemnitor", "phone"),
                "indemnitor_email": ("indemnitor", "email"),
                "indemnitor_address": ("indemnitor", "address"),
                "indemnitor_dob": ("indemnitor", "dob"),
                "case_number": (None, "case_number"),
                "booking_number": (None, "booking_number"),
                "poa_number": (None, "poa_number"),
                "bond_amount": (None, "bond_amount"),
            }
            for k, v in overrides.items():
                if v is None or str(v).strip() == "":
                    continue
                target = map_def.get(k)
                if not target:
                    continue
                group, field = target
                if group is None:
                    if field == "bond_amount":
                        try:
                            ctx[field] = float(str(v).replace("$", "").replace(",", ""))
                        except ValueError:
                            ctx[field] = v
                    else:
                        ctx[field] = v
                elif group == "defendant":
                    def_[field] = v
                elif group == "indemnitor":
                    ind[field] = v

        fields = build_adaptive_field_map(ctx)
        audit = hydration_score(fields)

        rules_col = get_collection("paperwork_rules")
        rules_doc = await rules_col.find_one({"_id": "drag_drop_rules"}, {"_id": 0})
        categories = (rules_doc or {}).get("categories") or DEFAULT_DOC_RULES_CATEGORIES
        if isinstance(body.get("categories"), dict):
            # Per-case overrides from the modal drop zones
            categories = body["categories"]

        extra_keys = body.get("extra_doc_keys") or body.get("packet_doc_keys") or []
        manifest = assemble_manifest(
            categories,
            surety_id=ctx.get("surety_id") or body.get("surety_id") or "osi",
            include_payment_plan=bool(body.get("include_payment_plan", True)),
            extra_catalog_keys=extra_keys,
            self_indemnitor=bool(ctx.get("self_indemnitor")),
        )

        extras = decode_extra_uploads(body.get("extra_uploads") or [])

        now = datetime.now(timezone.utc)
        packet_id = body.get("packet_id") or f"PKT-{uuid.uuid4().hex[:10].upper()}"
        surety_id = (body.get("surety_id") or ctx.get("surety_id") or "osi").lower()

        # Build synthetic intake for SignNow service
        def_ = ctx.get("defendant") or {}
        ind = ctx.get("indemnitor") or {}
        intake_doc = {
            "intake_id": ctx.get("intake_id") or f"SYN-{uuid.uuid4().hex[:8]}",
            "defendant_name": def_.get("name") or "",
            "defendant_booking_number": ctx.get("booking_number") or "",
            "defendant_county": ctx.get("county") or "",
            "defendant_facility": ctx.get("facility") or "",
            "defendant_dob": def_.get("dob") or "",
            "case_number": ctx.get("case_number") or "",
            "bond_amount": ctx.get("bond_amount") or 0,
            "charges": ctx.get("charges") or "",
            "indemnitor_name": ind.get("name") or "",
            "indemnitor_phone": ind.get("phone") or "",
            "indemnitor_email": ind.get("email") or "",
            "poa_number": ctx.get("poa_number") or body.get("poa_number") or "",
            "surety_id": surety_id,
            "self_indemnitor": bool(ctx.get("self_indemnitor")),
            "defendant": {
                "name": def_.get("name"),
                "firstName": def_.get("first_name"),
                "lastName": def_.get("last_name"),
                "dob": def_.get("dob"),
                "phone": def_.get("phone"),
                "email": def_.get("email"),
                "address": def_.get("address"),
                "city": def_.get("city"),
                "state": def_.get("state"),
                "zip": def_.get("zip"),
                "dl": def_.get("dl"),
                "dlState": def_.get("dl_state"),
                "bookingNumber": ctx.get("booking_number"),
                "county": ctx.get("county"),
                "facility": ctx.get("facility"),
                "charges": ctx.get("charges"),
                "bondAmount": ctx.get("bond_amount"),
                "caseNumber": ctx.get("case_number"),
                "height": def_.get("height"),
                "weight": def_.get("weight"),
                "race": def_.get("race"),
                "sex": def_.get("sex"),
                "hair": def_.get("hair"),
                "eyes": def_.get("eyes"),
                "employer": def_.get("employer"),
            },
            "indemnitor": {
                "name": ind.get("name"),
                "firstName": ind.get("first_name"),
                "lastName": ind.get("last_name"),
                "dob": ind.get("dob"),
                "phone": ind.get("phone"),
                "email": ind.get("email"),
                "address": ind.get("address"),
                "city": ind.get("city"),
                "state": ind.get("state"),
                "zip": ind.get("zip"),
                "dl": ind.get("dl"),
                "dlState": ind.get("dl_state"),
                "ssn": ind.get("ssn"),
                "employer": ind.get("employer"),
                "relationship": ind.get("relationship") or ("Self" if ctx.get("self_indemnitor") else ""),
            },
        }

        # Fill + flatten via Adobe PDF Services (combine/compress) with local fallback.
        # DocuSeal uses its own template set — skip heavy stitch/flatten unless staff
        # forces it (force_flatten) or provider needs a merged PDF (signnow/adobe).
        flat_bytes = b""
        flat_b64 = ""
        adobe_pdf_meta: dict = {}
        need_flatten = (
            provider in ("signnow", "adobe", "both", "none")
            or bool(body.get("force_flatten") or body.get("flatten"))
            or bool(extras)
        )
        if not need_flatten and provider == "docuseal":
            adobe_pdf_meta = {
                "skipped": True,
                "reason": "docuseal_uses_template_prefill_not_local_stitch",
            }
        else:
            try:
                pdf_parts = []
                part_names = []
                try:
                    from dashboard.paperwork_pdf_service import generate_full_packet
                    stitched = generate_full_packet(intake_doc, surety=surety_id)
                    if isinstance(stitched, (bytes, bytearray)) and stitched:
                        pdf_parts.append(bytes(stitched))
                        part_names.append("core-packet.pdf")
                except Exception as stitch_exc:
                    logger.debug("local stitch unavailable: %s", stitch_exc)
                for ex in extras:
                    fname = (ex.get("filename") or "").lower()
                    ctype = (ex.get("content_type") or "").lower()
                    if fname.endswith(".pdf") or "pdf" in ctype:
                        pdf_parts.append(ex["bytes"])
                        part_names.append(ex.get("filename") or "extra.pdf")

                if pdf_parts:
                    adobe_pdf = get_adobe_pdf_client()
                    if adobe_pdf.configured:
                        def _opt_bool(key):
                            if key not in body:
                                return None
                            return bool(body.get(key))

                        built = await adobe_pdf.build_flattened_packet(
                            pdf_parts,
                            field_map=fields,
                            names=part_names,
                            autotag=_opt_bool("autotag"),
                            autotag_report=_opt_bool("autotag_report"),
                            autotag_shift_headings=_opt_bool("autotag_shift_headings"),
                        )
                        adobe_pdf_meta = {
                            k: v for k, v in built.items() if k != "pdf_bytes"
                        }
                        if built.get("success") and built.get("pdf_bytes"):
                            flat_bytes = built["pdf_bytes"]
                        else:
                            flat_bytes = flatten_pdf_bytes(pdf_parts)
                            adobe_pdf_meta["fallback"] = "local_flatten"
                            adobe_pdf_meta["adobe_error"] = built.get("error")
                    else:
                        filled_parts = []
                        for part in pdf_parts:
                            blob, _meta = await adobe_pdf.fill_and_flatten_local_first(part, fields)
                            filled_parts.append(blob or part)
                        flat_bytes = flatten_pdf_bytes(filled_parts)
                        adobe_pdf_meta = {"adobe_pdf_configured": False, "engine": "local"}
            except Exception as flat_exc:
                logger.warning("flatten failed: %s", flat_exc)
                adobe_pdf_meta = {"error": str(flat_exc)}

        if flat_bytes:
            import base64 as _b64
            flat_b64 = _b64.b64encode(flat_bytes).decode("ascii")

        send_results: dict = {}
        signnow_result: dict = {}
        adobe_result: dict = {}
        docuseal_result: dict = {}
        status = "finalized"
        signing_link = ""

        # ── DocuSeal (default / self-hosted OSS) ──
        if provider == "docuseal":
            try:
                from dashboard.services.docuseal_service import (
                    get_docuseal_service,
                    resolve_template_id_for_surety,
                )

                ds = get_docuseal_service()
                template_id = (
                    body.get("docuseal_template_id")
                    or body.get("template_id")
                    or resolve_template_id_for_surety(surety_id)
                )
                if not ds.is_configured:
                    send_results["docuseal"] = {
                        "success": False,
                        "error": "docuseal_not_configured",
                        "hint": "Set DOCUSEAL_URL + DOCUSEAL_API_KEY after admin login",
                    }
                elif not template_id:
                    send_results["docuseal"] = {
                        "success": False,
                        "error": "template_id_required",
                        "hint": (
                            "Set DOCUSEAL_TEMPLATE_ID_OSI / DOCUSEAL_TEMPLATE_ID_PALMETTO "
                            "or pass template_id in the request"
                        ),
                    }
                else:
                    from dashboard.services.docuseal_service import build_bond_data_from_dashboard

                    bond_data = build_bond_data_from_dashboard(
                        ctx=ctx,
                        intake_doc=intake_doc,
                        field_overrides=overrides if isinstance(overrides, dict) else {},
                        body=body,
                        surety_id=surety_id,
                    )
                    docuseal_result = await ds.create_submission_for_packet(
                        template_id=template_id,
                        packet_id=packet_id,
                        bond_data=bond_data,
                        indemnitors=bond_data.get("indemnitors"),
                        send_email=bool(body.get("send_email", False)),
                        include_defendant=bool(body.get("include_defendant", True)),
                    )
                    from dashboard.services.paperwork_signers import (
                        party_signers_from_submitters,
                        pick_party,
                    )

                    parties = party_signers_from_submitters(
                        docuseal_result.get("submitters") or [],
                        packet_id=packet_id,
                        indemnitor_name=ind.get("name") or "",
                        defendant_name=def_.get("name") or "",
                        indemnitor_phone=ind.get("phone") or "",
                        defendant_phone=def_.get("phone") or "",
                    )
                    links = [p.get("share_url") or p.get("sign_url") for p in parties if p.get("sign_url")]
                    signing_link = (pick_party(parties, role="indemnitor") or {}).get("share_url") or (
                        links[0] if links else ""
                    )
                    status = "pending_signature"
                    send_results["docuseal"] = {
                        "success": True,
                        "submission_id": docuseal_result.get("submission_id"),
                        "template_id": template_id,
                        "submitters": docuseal_result.get("submitters"),
                        "signing_link": signing_link,
                        "sign_links": links,
                        "parties": parties,
                    }
            except Exception as ds_exc:
                logger.exception("DocuSeal finalize failed for packet %s", packet_id)
                send_results["docuseal"] = {"success": False, "error": str(ds_exc)[:400]}
                return JSONResponse(
                    {
                        "success": False,
                        "error": f"DocuSeal failed: {ds_exc}",
                        "packet_id": packet_id,
                        "send_results": send_results,
                    },
                    status_code=502,
                )

        # ── SignNow (legacy) ──
        if provider in ("signnow", "both"):
            try:
                from dashboard.services.signnow_packet_service import SignNowPacketService
                svc = SignNowPacketService()
                # custom manifest: map our assembled docs to SignNow template keys
                custom_manifest = []
                for d in manifest:
                    slug = d.get("template_slug") or template_slug_for_catalog_key(d.get("catalog_key", ""))
                    if d.get("print_only"):
                        continue
                    custom_manifest.append(slug)

                routing = body.get("routing_scenario") or "all-in-one"
                phase = int(body.get("phase") or (2 if routing == "all-in-one" else 1))
                signer_email = (
                    body.get("signer_email")
                    or ind.get("email")
                    or def_.get("email")
                    or ""
                )
                signer_name = ind.get("name") or def_.get("name") or "Signer"
                poa_number = ctx.get("poa_number") or body.get("poa_number") or ""

                if phase == 2 and not poa_number and routing == "all-in-one":
                    # Soft fallback to phase 1 if POA missing
                    phase = 1
                    routing = "phase_1"

                signnow_result = await svc.create_packet(
                    intake_doc=intake_doc,
                    packet_id=packet_id,
                    phase=phase,
                    surety_id=surety_id,
                    signer_email=signer_email,
                    signer_name=signer_name,
                    poa_number=poa_number or None,
                    custom_manifest=custom_manifest or None,
                    routing_scenario=routing,
                )
                signing_link = signnow_result.get("signing_link") or ""
                status = "pending_signature"
                send_results["signnow"] = {
                    "success": True,
                    "document_ids": signnow_result.get("document_ids"),
                    "group_id": signnow_result.get("group_id"),
                    "invite_id": signnow_result.get("invite_id"),
                    "signing_link": signing_link,
                }
            except Exception as sn_exc:
                logger.exception("SignNow finalize failed — initiating Adobe Sign fallback")
                send_results["signnow"] = {"success": False, "error": str(sn_exc), "fallback_triggered": True}
                if flat_bytes:
                    try:
                        adobe_fallback = await send_via_adobe(
                            flattened_pdf=flat_bytes,
                            filename=f"{packet_id}.pdf",
                            signer_email=body.get("signer_email") or ind.get("email") or "",
                            signer_name=ind.get("name") or def_.get("name") or "Signer",
                            agreement_name=f"Shamrock Bond Packet — {def_.get('name') or packet_id}",
                        )
                        send_results["adobe"] = adobe_fallback
                        send_results["adobe"]["is_fallback"] = True
                        if adobe_fallback.get("success"):
                            status = "pending_signature"
                            signing_link = adobe_fallback.get("signing_link") or adobe_fallback.get("url") or ""
                            logger.info("✅ Adobe Sign fallback succeeded for packet %s", packet_id)
                    except Exception as ad_exc:
                        logger.exception("Adobe Sign fallback also failed")
                        send_results["adobe"] = {"success": False, "error": str(ad_exc)}
                if provider == "signnow" and not (send_results.get("adobe", {}).get("success")):
                    return JSONResponse(
                        {"success": False, "error": f"SignNow failed ({sn_exc}) and Adobe Sign fallback failed.", "packet_id": packet_id, "send_results": send_results},
                        status_code=502,
                    )

        # ── Adobe ──
        if provider in ("adobe", "both"):
            if not flat_bytes:
                adobe_result = {
                    "success": False,
                    "error": "No flattened PDF available for Adobe (add blank templates or upload PDFs).",
                }
            else:
                adobe_result = await send_via_adobe(
                    flattened_pdf=flat_bytes,
                    filename=f"{packet_id}.pdf",
                    signer_email=body.get("signer_email") or ind.get("email") or "",
                    signer_name=ind.get("name") or def_.get("name") or "Signer",
                    agreement_name=f"Shamrock Bond Packet — {def_.get('name') or packet_id}",
                )
            send_results["adobe"] = adobe_result
            if adobe_result.get("success"):
                status = "pending_signature"

        # Persist packet
        packet_doc = {
            "packet_id": packet_id,
            "intake_id": intake_doc.get("intake_id"),
            "bond_case_id": ctx.get("bond_case_id"),
            "match_id": ctx.get("match_id"),
            "defendant_id": ctx.get("defendant_id"),
            "indemnitor_id": ctx.get("indemnitor_id"),
            "packet_type": "adaptive_builder",
            "template": surety_id,
            "surety_id": surety_id,
            "status": status,
            "manifest": manifest,
            "documents": [
                {
                    "doc_id": f"{packet_id}-{i:02d}",
                    "catalog_key": d.get("catalog_key"),
                    "template_slug": d.get("template_slug"),
                    "label": d.get("label"),
                    "print_only": d.get("print_only"),
                    "status": "included",
                }
                for i, d in enumerate(manifest, 1)
            ],
            "extra_uploads": [
                {"filename": e["filename"], "size": e["size"], "content_type": e["content_type"]}
                for e in extras
            ],
            "flattened": bool(flat_bytes),
            "adobe_pdf_meta": adobe_pdf_meta,
            "flattened_size": len(flat_bytes) if flat_bytes else 0,
            "defendant_name": def_.get("name") or "",
            "defendant_dob": def_.get("dob") or "",
            "defendant_address": def_.get("address") or "",
            "indemnitor_name": ind.get("name") or "",
            "indemnitor_phone": ind.get("phone") or "",
            "indemnitor_email": ind.get("email") or "",
            "indemnitor_address": ind.get("address") or "",
            "case_number": ctx.get("case_number") or "",
            "booking_number": ctx.get("booking_number") or "",
            "bond_amount": ctx.get("bond_amount") or 0,
            "premium_amount": ctx.get("premium_amount") or 0,
            "poa_number": ctx.get("poa_number") or body.get("poa_number") or "",
            "self_indemnitor": bool(ctx.get("self_indemnitor")),
            "hydration_score": audit.get("hydration_score"),
            "field_map_keys": list(fields.keys())[:80],
            "send_results": send_results,
            "esign_provider": provider,
            "signnow_document_ids": (signnow_result or {}).get("document_ids") or [],
            "signnow_group_id": (signnow_result or {}).get("group_id") or "",
            "signnow_invite_id": (signnow_result or {}).get("invite_id"),
            "signnow_status": "sent" if (signnow_result or {}).get("document_ids") else None,
            "docuseal_submission_id": (docuseal_result or {}).get("submission_id"),
            "docuseal_template_id": (send_results.get("docuseal") or {}).get("template_id"),
            "docuseal_submitters": (docuseal_result or {}).get("submitters") or [],
            "parties": (send_results.get("docuseal") or {}).get("parties") or [],
            "defendant_phone": def_.get("phone") or "",
            "docuseal_status": "sent" if (docuseal_result or {}).get("submission_id") else None,
            "docuseal_sent_at": now.isoformat() if (docuseal_result or {}).get("submission_id") else None,
            "adobe_agreement_id": (adobe_result or {}).get("agreement_id"),
            "signing_link": signing_link,
            "provider": provider,
            "created_at": now,
            "updated_at": now,
            "packet_version": 1,
            "voided": False,
        }

        packets_col = get_collection("paperwork_packets")
        existing = await packets_col.find_one({"packet_id": packet_id})
        if existing:
            packet_doc["packet_version"] = int(existing.get("packet_version") or 1) + 1
            packet_doc["created_at"] = existing.get("created_at") or now
            await packets_col.replace_one({"packet_id": packet_id}, packet_doc)
        else:
            await packets_col.insert_one(packet_doc)

        # Soft audit event
        try:
            await get_collection("audit_events").insert_one({
                "Event_ID": str(uuid.uuid4()),
                "event_type": "packet_finalized",
                "packet_id": packet_id,
                "provider": provider,
                "self_indemnitor": bool(ctx.get("self_indemnitor")),
                "hydration_score": audit.get("hydration_score"),
                "actor": "packet_builder",
                "timestamp": now,
            })
        except Exception:
            pass

        return {
            "success": True,
            "packet_id": packet_id,
            "status": status,
            "provider": provider,
            "hydration": audit,
            "manifest": manifest,
            "self_indemnitor": bool(ctx.get("self_indemnitor")),
            "signing_link": signing_link,
            "parties": (send_results.get("docuseal") or {}).get("parties") or [],
            "send_results": send_results,
            "flattened": bool(flat_bytes),
            "adobe_pdf_meta": adobe_pdf_meta,
            "esign_provider": provider,
            "flattened_preview_b64": flat_b64[:200] + "…" if flat_b64 and len(flat_b64) > 200 else flat_b64,
            "context_summary": {
                "defendant_name": def_.get("name"),
                "indemnitor_name": ind.get("name"),
                "bond_amount": ctx.get("bond_amount"),
                "surety_id": surety_id,
                "match_status": ctx.get("match_status"),
                "sources": ctx.get("sources"),
            },
        }
    except Exception as exc:
        logger.exception("packet_builder_finalize error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@paperwork_bp.get("/paperwork/adobe/status")
async def paperwork_adobe_status():
    """Report Adobe PDF Services + Acrobat Sign configuration status (no secrets)."""
    try:
        from dashboard.services.adobe_pdf_service import adobe_status, get_adobe_pdf_client
        status = adobe_status()
        # Live token probe when configured
        pdf = get_adobe_pdf_client()
        if pdf.configured:
            try:
                await pdf.get_access_token()
                status["pdf_services"]["token_ok"] = True
            except Exception as exc:
                status["pdf_services"]["token_ok"] = False
                status["pdf_services"]["token_error"] = str(exc)[:200]
        return {"success": True, **status}
    except Exception as exc:
        logger.exception("adobe status error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@paperwork_bp.post("/paperwork/client/esign-provider")
async def set_client_esign_provider(request: Request):
    """
    Set e-sign provider for a client (indemnitor / defendant / bond).
    Applies to the whole packet — not per PDF.
    Body: { provider: signnow|adobe|both|none, indemnitor_id?, defendant_id?, bond_case_id? }
    """
    try:
        body = (await request.json()) or {}
        from dashboard.services.packet_builder_service import save_client_esign_provider
        result = await save_client_esign_provider(
            provider=body.get("provider") or "",
            indemnitor_id=body.get("indemnitor_id"),
            defendant_id=body.get("defendant_id"),
            bond_case_id=body.get("bond_case_id"),
        )
        if not result.get("updated"):
            return JSONResponse(
                {"success": False, "error": "No matching client records updated (need indemnitor_id, defendant_id, or bond_case_id)"},
                status_code=404,
            )
        return {"success": True, **result}
    except ValueError as ve:
        return JSONResponse({"success": False, "error": str(ve)}, status_code=400)
    except Exception as exc:
        logger.exception("set_client_esign_provider error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@paperwork_bp.post("/paperwork/pdf-to-markdown")
async def paperwork_pdf_to_markdown(request: Request):
    """
    Convert a PDF to LLM-friendly Markdown via Adobe PDF Services.

    Docs: https://developer.adobe.com/document-services/docs/overview/pdf-services-api/howtos/pdf-to-markdown-api

    Body (one of):
      - pdf_b64: base64 (or data URL) of PDF bytes
      - packet_id: load flattened packet if stored (best-effort)
      - text/plain not supported — PDF only

    Optional:
      - bake_forms: bool (default true) — bake AcroForm fields first (API rejects fillable forms)
      - store: bool — save markdown onto paperwork_packets when packet_id given
    """
    try:
        body = (await request.json()) or {}
        from dashboard.services.adobe_pdf_service import get_adobe_pdf_client
        import base64 as _b64

        pdf_bytes = b""
        packet_id = (body.get("packet_id") or "").strip()

        raw_b64 = body.get("pdf_b64") or body.get("data_b64") or body.get("data") or ""
        if raw_b64:
            if isinstance(raw_b64, str) and "," in raw_b64 and raw_b64.strip().startswith("data:"):
                raw_b64 = raw_b64.split(",", 1)[1]
            try:
                pdf_bytes = _b64.b64decode(raw_b64)
            except Exception:
                return JSONResponse({"success": False, "error": "invalid pdf_b64"}, status_code=400)

        if not pdf_bytes and packet_id:
            # Best-effort: regenerate from blank stitch if we don't store blobs
            pkt = await get_collection("paperwork_packets").find_one(
                {"packet_id": packet_id}, {"_id": 0}
            )
            if not pkt:
                return JSONResponse({"success": False, "error": "packet not found"}, status_code=404)
            # If prior markdown stored, return it
            if pkt.get("markdown") and body.get("force") is not True:
                return {
                    "success": True,
                    "packet_id": packet_id,
                    "markdown": pkt.get("markdown"),
                    "cached": True,
                    "engine": pkt.get("markdown_engine") or "cached",
                }
            return JSONResponse(
                {
                    "success": False,
                    "error": (
                        "No PDF bytes available for this packet. "
                        "Pass pdf_b64, or finalize a packet that produces a flattened PDF first."
                    ),
                },
                status_code=400,
            )

        if not pdf_bytes:
            return JSONResponse(
                {"success": False, "error": "pdf_b64 (or regenerable packet_id) required"},
                status_code=400,
            )

        client = get_adobe_pdf_client()
        result = await client.pdf_to_markdown(
            pdf_bytes,
            bake_forms=bool(body.get("bake_forms", True)),
        )
        if not result.get("success"):
            return JSONResponse(result, status_code=502)

        if packet_id and body.get("store", True):
            try:
                await get_collection("paperwork_packets").update_one(
                    {"packet_id": packet_id},
                    {
                        "$set": {
                            "markdown": result.get("markdown"),
                            "markdown_engine": result.get("engine"),
                            "markdown_size": result.get("size"),
                            "markdown_at": datetime.now(timezone.utc),
                        }
                    },
                )
            except Exception as exc:
                logger.warning("store markdown failed: %s", exc)

        return {
            "success": True,
            "packet_id": packet_id or None,
            "markdown": result.get("markdown"),
            "size": result.get("size"),
            "engine": result.get("engine"),
            "preflight": result.get("preflight"),
            "docs": result.get("docs"),
        }
    except Exception as exc:
        logger.exception("pdf-to-markdown error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@paperwork_bp.post("/paperwork/pdf-extract")
async def paperwork_pdf_extract(request: Request):
    """
    PDF Extract API — structured JSON (text + tables).
    https://developer.adobe.com/document-services/docs/overview/pdf-extract-api/
    Body: { "pdf_b64": "..." }
    """
    try:
        body = (await request.json()) or {}
        from dashboard.services.adobe_pdf_service import get_adobe_pdf_client
        import base64 as _b64
        raw_b64 = body.get("pdf_b64") or body.get("data_b64") or body.get("data") or ""
        if not raw_b64:
            return JSONResponse({"success": False, "error": "pdf_b64 required"}, status_code=400)
        if isinstance(raw_b64, str) and "," in raw_b64 and raw_b64.strip().startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        try:
            pdf_bytes = _b64.b64decode(raw_b64)
        except Exception:
            return JSONResponse({"success": False, "error": "invalid pdf_b64"}, status_code=400)
        result = await get_adobe_pdf_client().extract_pdf(
            pdf_bytes,
            extract_text=bool(body.get("extract_text", True)),
            extract_tables=bool(body.get("extract_tables", True)),
            table_xlsx=bool(body.get("table_xlsx", True)),
            styling_info=bool(body.get("styling_info", False)),
            add_char_info=bool(body.get("add_char_info", False)),
            extract_figure_renditions=bool(body.get("extract_figure_renditions", False)),
        )
        status = 200 if result.get("success") else 502
        return JSONResponse(result, status_code=status)
    except Exception as exc:
        logger.exception("pdf-extract error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# DocuSeal (self-hosted e-sign) — S1 service surface
# ─────────────────────────────────────────────────────────────────────────────

@paperwork_bp.get("/paperwork/docuseal/health")
async def docuseal_health(surety_id: str = "osi"):
    """Connectivity + OSI/Palmetto template readiness for dashboard prefill."""
    from dashboard.services.docuseal_service import get_docuseal_service, readiness_report

    svc = get_docuseal_service()
    result = await svc.health()
    ready = readiness_report(surety_id=(surety_id or "osi").lower())
    result["readiness"] = ready
    # Prefill preview only needs API key + template id (bond names checked at send)
    result["ready_to_prefill"] = bool(ready.get("configured") and ready.get("template_ready"))
    result["infra_ready"] = bool(svc.is_configured and ready.get("template_ready"))
    code = 200 if result.get("ok") or not result.get("configured") else 502
    if not result.get("configured"):
        code = 503
    return JSONResponse(result, status_code=code)


@paperwork_bp.post("/paperwork/docuseal/prefill-preview")
async def docuseal_prefill_preview(request: Request):
    """
    Dry-run: resolve case context → DocuSeal field map (no submission created).

    Staff uses this from Adaptive Packet "Preview DocuSeal Prefill" to confirm
    OSI template alignment before Flatten & Send.
    """
    try:
        body = (await request.json()) or {}
    except Exception:
        body = {}

    from dashboard.services.packet_builder_service import (
        resolve_case_context,
        apply_self_indemnitor,
        SMALL_BOND_MAX,
    )
    from dashboard.services.docuseal_service import (
        DocuSealService,
        build_bond_data_from_dashboard,
        resolve_template_id_for_surety,
        readiness_report,
        ROLE_INDEMNITOR,
        ROLE_CO_INDEMNITOR,
        ROLE_DEFENDANT,
    )

    surety_id = (body.get("surety_id") or "osi").lower().strip()
    if surety_id not in ("osi", "palmetto"):
        surety_id = "osi"

    ctx = await resolve_case_context(
        intake_id=body.get("intake_id"),
        match_id=body.get("match_id"),
        defendant_id=body.get("defendant_id"),
        booking_number=body.get("booking_number"),
        county=body.get("county"),
        bond_case_id=body.get("bond_case_id"),
        packet_id=body.get("packet_id"),
    )
    if body.get("self_indemnitor"):
        pin = body.get("authorization_pin") or body.get("pin") or ""
        try:
            ctx = apply_self_indemnitor(ctx, pin)
        except PermissionError as pe:
            return JSONResponse({"success": False, "error": str(pe)}, status_code=403)

    # Apply same field overrides as finalize
    overrides = body.get("field_overrides") or {}
    if isinstance(overrides, dict):
        def_ = ctx.setdefault("defendant", {})
        ind = ctx.setdefault("indemnitor", {})
        for k, v in overrides.items():
            if v is None or str(v).strip() == "":
                continue
            if k.startswith("defendant_") and k != "defendant_name":
                def_[k.replace("defendant_", "")] = v
            elif k == "defendant_name":
                def_["name"] = v
            elif k.startswith("indemnitor_") and k != "indemnitor_name":
                ind[k.replace("indemnitor_", "")] = v
            elif k == "indemnitor_name":
                ind["name"] = v
            elif k in ("case_number", "booking_number", "poa_number", "bond_amount", "court_date"):
                if k == "bond_amount":
                    try:
                        ctx[k] = float(str(v).replace("$", "").replace(",", ""))
                    except ValueError:
                        ctx[k] = v
                else:
                    ctx[k] = v

    bond_data = build_bond_data_from_dashboard(
        ctx=ctx,
        field_overrides=overrides if isinstance(overrides, dict) else {},
        body=body,
        surety_id=surety_id,
    )
    values = DocuSealService.prefill_values_from_bond(bond_data)
    template_id = (
        body.get("docuseal_template_id")
        or body.get("template_id")
        or resolve_template_id_for_surety(surety_id)
    )

    # Predicted submitter roles (must match live DocuSeal template names)
    inds = bond_data.get("indemnitors") or []
    roles = []
    if inds:
        roles.append(ROLE_INDEMNITOR)  # "indemnitor"
        if len(inds) > 1:
            roles.append(ROLE_CO_INDEMNITOR)  # "Coindemnitor"
    if body.get("include_defendant", True):
        roles.append(ROLE_DEFENDANT)  # "Defendant"

    ready = readiness_report(bond_data, surety_id=surety_id)
    return {
        "success": True,
        "dry_run": True,
        "surety_id": surety_id,
        "template_id": template_id,
        "template_ready": bool(template_id),
        "prefill": values,
        "prefill_key_count": len(values),
        "submitter_roles": roles,
        "readiness": ready,
        "infra_ready": ready.get("configured") and bool(template_id),
        "can_send": bool(
            ready.get("configured")
            and template_id
            and (values.get("indemnitor_name") or values.get("defendant_name"))
        ),
        "context_sources": ctx.get("sources") or [],
        "bond_amount": bond_data.get("bond_amount"),
        "premium_preview": values.get("numeric_premium_dollar") or values.get("premium_amount"),
        "charges_summary": values.get("charges_summary"),
        "hint": (
            "Prefill looks good — indemnitor can sign now (defendant can be matched later)."
            if (ready.get("configured") and template_id and (values.get("indemnitor_name") or values.get("defendant_name")))
            else "; ".join(ready.get("hints") or ["Review missing fields / env"])
        ),
        "small_bond_max": SMALL_BOND_MAX,
    }


@paperwork_bp.get("/paperwork/docuseal/templates")
async def docuseal_list_templates(q: str = "", limit: int = 50):
    """List templates from DocuSeal (for Write Bond template picker)."""
    from dashboard.services.docuseal_service import get_docuseal_service

    svc = get_docuseal_service()
    if not svc.is_configured:
        return JSONResponse(
            {"success": False, "error": "docuseal_not_configured"},
            status_code=503,
        )
    try:
        data = await svc.list_templates(q=q or None, limit=limit)
        return {"success": True, "templates": data}
    except Exception as exc:
        logger.exception("docuseal templates list failed")
        return JSONResponse({"success": False, "error": str(exc)[:300]}, status_code=502)


@paperwork_bp.get("/paperwork/docuseal/templates/{template_id}")
async def docuseal_get_template(template_id: str):
    """
    Retrieve one template (fields/roles) for agent field-audit vs prefill keys.
    CLI parity: `docuseal templates retrieve <id>`
    """
    from dashboard.services.docuseal_service import get_docuseal_service

    svc = get_docuseal_service()
    if not svc.is_configured:
        return JSONResponse(
            {"success": False, "error": "docuseal_not_configured"},
            status_code=503,
        )
    try:
        data = await svc.get_template(template_id)
        return {"success": True, "template": data}
    except Exception as exc:
        logger.exception("docuseal template retrieve failed id=%s", template_id)
        return JSONResponse({"success": False, "error": str(exc)[:300]}, status_code=502)


@paperwork_bp.get("/paperwork/docuseal/submissions")
async def docuseal_list_submissions(
    status: str = "",
    template_id: str = "",
    q: str = "",
    limit: int = 50,
):
    """
    List DocuSeal submissions (ops / agent chase).
    CLI parity: `docuseal submissions list --status pending`
    """
    from dashboard.services.docuseal_service import get_docuseal_service

    svc = get_docuseal_service()
    if not svc.is_configured:
        return JSONResponse(
            {"success": False, "error": "docuseal_not_configured"},
            status_code=503,
        )
    try:
        data = await svc.list_submissions(
            status=status or None,
            template_id=template_id or None,
            q=q or None,
            limit=limit,
        )
        return {"success": True, "submissions": data}
    except Exception as exc:
        logger.exception("docuseal submissions list failed")
        return JSONResponse({"success": False, "error": str(exc)[:300]}, status_code=502)


@paperwork_bp.get("/paperwork/{packet_id}/docuseal/status")
async def paperwork_docuseal_status(packet_id: str):
    """
    Refresh packet signing status from DocuSeal and sync submitter sign links.
    Does not mark completed (webhook/poller owns completion + Drive archive).
    """
    from dashboard.services.docuseal_service import get_docuseal_service

    packet = await _load_packet(packet_id)
    if not packet:
        return JSONResponse({"success": False, "error": "packet_not_found"}, status_code=404)

    sub_id = packet.get("docuseal_submission_id")
    if not sub_id:
        return JSONResponse(
            {
                "success": False,
                "error": "no_docuseal_submission",
                "hint": "Push packet first: POST /api/paperwork/{packet_id}/docuseal",
            },
            status_code=400,
        )

    svc = get_docuseal_service()
    if not svc.is_configured:
        return JSONResponse(
            {"success": False, "error": "docuseal_not_configured"},
            status_code=503,
        )

    try:
        sub = await svc.get_submission(sub_id)
        status = (sub.get("status") or "").lower() if isinstance(sub, dict) else ""
        raw_submitters = []
        if isinstance(sub, dict):
            raw_submitters = sub.get("submitters") or []
        if not raw_submitters:
            listed = await svc.list_submitters(submission_id=sub_id, limit=50)
            items = listed.get("data") if isinstance(listed, dict) else listed
            raw_submitters = items if isinstance(items, list) else []

        submitters = [
            svc.normalize_submitter_record(s) for s in raw_submitters if isinstance(s, dict)
        ]
        now_iso = datetime.now(timezone.utc).isoformat()
        await get_collection("paperwork_packets").update_one(
            {"packet_id": packet_id},
            {
                "$set": {
                    "docuseal_status": status or packet.get("docuseal_status") or "pending",
                    "docuseal_submitters": submitters or packet.get("docuseal_submitters"),
                    "docuseal_polled_at": now_iso,
                }
            },
        )
        return {
            "success": True,
            "packet_id": packet_id,
            "submission_id": sub_id,
            "docuseal_status": status,
            "submitters": submitters,
            "sign_links": [
                {
                    "role": s.get("role"),
                    "email": s.get("email"),
                    "status": s.get("status"),
                    "sign_url": s.get("sign_url"),
                }
                for s in submitters
            ],
        }
    except Exception as exc:
        logger.exception("docuseal status failed packet=%s", packet_id)
        return JSONResponse({"success": False, "error": str(exc)[:400]}, status_code=502)


class BindDefendantRequest(BaseModel):
    defendant_name: str
    booking_number: Optional[str] = None
    county: Optional[str] = None
    case_number: Optional[str] = None
    defendant_id: Optional[str] = None
    charges: Optional[str] = None


@paperwork_bp.post("/paperwork/packets/{packet_id}/bind-defendant")
async def bind_defendant_to_packet(request: Request, packet_id: str, req: BindDefendantRequest):
    """
    Bind or match a defendant to an existing paperwork packet.
    Allows indemnitors to sign paperwork first and attach the defendant later.

    Guards:
      - Refuse if packet already signed/completed/filed
      - Atomic update only while unassigned_defendant is true (or defendant is still TBN)
      - Audit records real staff session actor when available
    """
    packet_col = get_collection("paperwork_packets")
    # Prefer packet_id string key (ObjectId string in _id is not valid BSON without cast)
    packet = await packet_col.find_one({"packet_id": packet_id})
    if not packet:
        # Fallback via shared loader (handles ObjectId-shaped ids safely)
        packet = await _load_packet(packet_id)
    if not packet:
        return JSONResponse({"success": False, "error": "packet_not_found"}, status_code=404)

    status = (packet.get("status") or packet.get("docuseal_status") or "").lower()
    if status in ("signed", "completed", "complete", "filed", "archived"):
        return JSONResponse(
            {
                "success": False,
                "error": "packet_already_finalized",
                "message": f"Cannot rebind defendant on packet with status '{status}'.",
            },
            status_code=409,
        )

    def_name = (req.defendant_name or "").strip()
    if not def_name:
        return JSONResponse({"success": False, "error": "defendant_name_required"}, status_code=400)
    if def_name.lower() in ("to be named", "tbn", "unknown", "n/a"):
        return JSONResponse(
            {"success": False, "error": "invalid_defendant_name", "message": "Provide the real defendant name."},
            status_code=400,
        )

    actor = "staff_or_matching_engine"
    try:
        from dashboard.auth.pin_middleware import get_session_from_request

        sess = get_session_from_request(request)
        if sess:
            actor = sess.get("email") or sess.get("role") or actor
    except Exception:
        pass

    now_iso = datetime.now(timezone.utc).isoformat()
    update_fields = {
        "defendant_name": def_name,
        "unassigned_defendant": False,
        "defendant_bound_at": now_iso,
        "defendant_bound_by": actor,
        "updated_at": now_iso,
    }
    if req.booking_number:
        update_fields["booking_number"] = req.booking_number.strip()
    if req.county:
        update_fields["county"] = req.county.strip()
    if req.case_number:
        update_fields["case_number"] = req.case_number.strip()
    if req.defendant_id:
        update_fields["defendant_id"] = req.defendant_id.strip()
    if req.charges:
        update_fields["charges"] = req.charges.strip()

    filt = {
        "packet_id": packet.get("packet_id") or packet_id,
        "$or": [
            {"unassigned_defendant": True},
            {"defendant_name": {"$in": ["To Be Named", "TBN", "", None]}},
        ],
    }
    result = await packet_col.update_one(filt, {"$set": update_fields})
    if result.matched_count == 0:
        # Packet may have been bound by another request, or already has a real defendant
        current = await packet_col.find_one({"packet_id": packet.get("packet_id") or packet_id})
        if current and not current.get("unassigned_defendant") and (
            (current.get("defendant_name") or "").strip().lower()
            not in ("to be named", "tbn", "")
        ):
            return JSONResponse(
                {
                    "success": False,
                    "error": "defendant_already_bound",
                    "message": "Packet already has a bound defendant. Unbind or create a new packet.",
                    "defendant_name": current.get("defendant_name"),
                },
                status_code=409,
            )
        # Fallback: force-set if still open but filter missed (legacy docs)
        await packet_col.update_one(
            {"packet_id": packet.get("packet_id") or packet_id},
            {"$set": update_fields},
        )

    # Log immutable audit event (never raise if audit write fails)
    try:
        events = get_collection("audit_events")
        await events.insert_one({
            "event_id": f"evt_bind_def_{uuid.uuid4().hex[:8]}",
            "event_type": "packet_defendant_matched",
            "packet_id": packet.get("packet_id") or packet_id,
            "defendant_name": def_name,
            "booking_number": update_fields.get("booking_number"),
            "county": update_fields.get("county"),
            "timestamp": now_iso,
            "actor": actor,
        })
    except Exception as audit_exc:
        logger.warning("bind-defendant audit write failed: %s", audit_exc)

    return {
        "success": True,
        "packet_id": packet.get("packet_id") or packet_id,
        "defendant_name": def_name,
        "message": f"Successfully bound defendant '{def_name}' to packet {packet.get('packet_id') or packet_id}.",
    }



@paperwork_bp.post("/paperwork/{packet_id}/docuseal/resend")
async def paperwork_docuseal_resend(packet_id: str, request: Request):
    """
    Re-send DocuSeal signature request(s) for a packet's submitters.

    Body JSON (all optional):
      submitter_id: int — only this party (else all incomplete)
      role: str — filter by role name
      email: str — update email before send (single target only)
      send_email: bool (default true)
      send_sms: bool (default false)
    """
    from dashboard.services.docuseal_service import get_docuseal_service

    packet = await _load_packet(packet_id)
    if not packet:
        return JSONResponse({"success": False, "error": "packet_not_found"}, status_code=404)

    try:
        body = await request.json() or {}
    except Exception:
        body = {}

    submitters = list(packet.get("docuseal_submitters") or [])
    if not submitters and packet.get("docuseal_submission_id"):
        svc_probe = get_docuseal_service()
        if svc_probe.is_configured:
            try:
                listed = await svc_probe.list_submitters(
                    submission_id=packet["docuseal_submission_id"], limit=50
                )
                items = listed.get("data") if isinstance(listed, dict) else listed
                if isinstance(items, list):
                    submitters = [
                        svc_probe.normalize_submitter_record(s)
                        for s in items
                        if isinstance(s, dict)
                    ]
            except Exception:
                pass

    if not submitters:
        return JSONResponse(
            {
                "success": False,
                "error": "no_submitters",
                "hint": "No docuseal_submitters on packet",
            },
            status_code=400,
        )

    only_id = body.get("submitter_id")
    only_role = (body.get("role") or "").strip()
    new_email = body.get("email")
    send_email = bool(body.get("send_email", True))
    send_sms = bool(body.get("send_sms", False))

    targets = []
    for s in submitters:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        st = (s.get("status") or "").lower()
        if st in ("completed", "complete", "signed"):
            continue
        if only_id is not None and str(s.get("id")) != str(only_id):
            continue
        if only_role and (s.get("role") or "") != only_role:
            continue
        targets.append(s)

    if not targets:
        return JSONResponse(
            {"success": False, "error": "no_pending_submitters"},
            status_code=400,
        )

    svc = get_docuseal_service()
    if not svc.is_configured:
        return JSONResponse(
            {"success": False, "error": "docuseal_not_configured"},
            status_code=503,
        )

    updated = []
    errors = []
    for s in targets:
        sid = s.get("id")
        try:
            kwargs: dict = {"send_email": send_email, "send_sms": send_sms}
            if new_email and (only_id is not None or len(targets) == 1):
                kwargs["email"] = str(new_email).strip()
            raw = await svc.update_submitter(sid, **kwargs)
            norm = svc.normalize_submitter_record(raw if isinstance(raw, dict) else s)
            updated.append(norm)
        except Exception as exc:
            errors.append({"submitter_id": sid, "error": str(exc)[:200]})

    if updated:
        by_id = {str(u.get("id")): u for u in updated if u.get("id") is not None}
        merged = []
        for s in submitters:
            if not isinstance(s, dict):
                continue
            key = str(s.get("id")) if s.get("id") is not None else ""
            merged.append(by_id.get(key) or s)
        seen = {
            str(m.get("id")) for m in merged if isinstance(m, dict) and m.get("id") is not None
        }
        for u in updated:
            if u.get("id") is not None and str(u.get("id")) not in seen:
                merged.append(u)
        await get_collection("paperwork_packets").update_one(
            {"packet_id": packet_id},
            {
                "$set": {
                    "docuseal_submitters": merged,
                    "docuseal_resent_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    return {
        "success": len(errors) == 0,
        "packet_id": packet_id,
        "resent": len(updated),
        "updated": updated,
        "errors": errors,
    }


@paperwork_bp.post("/paperwork/{packet_id}/docuseal")
async def paperwork_push_docuseal(packet_id: str, request: Request):
    """
    Create a DocuSeal submission for an existing paperwork packet.

    Body JSON:
      template_id: int|str (required until packet stores default)
      send_email: bool (default false — portal/PIN owns delivery)
      include_defendant: bool (default true)
      indemnitors: optional [{name, email, phone}]
      defendant: optional {name, email, phone}
      bond_data: optional override fields for prefill
    """
    from dashboard.services.docuseal_service import get_docuseal_service

    packet = await _load_packet(packet_id)
    if not packet:
        return JSONResponse({"success": False, "error": "packet_not_found"}, status_code=404)

    try:
        body = await request.json() or {}
    except Exception:
        body = {}

    template_id = body.get("template_id") or packet.get("docuseal_template_id")
    if not template_id:
        return JSONResponse(
            {
                "success": False,
                "error": "template_id_required",
                "hint": "Upload templates in DocuSeal admin, then pass template_id",
            },
            status_code=400,
        )

    # Hydration source: explicit bond_data > packet > intake
    bond_data = dict(body.get("bond_data") or {})
    user = getattr(request.state, "user", {})
    if user.get("agent_name"):
        bond_data["agent_name"] = user.get("agent_name")
    if user.get("license_number"):
        bond_data["license_number"] = user.get("license_number")
    for k in (
        "defendant_name",
        "indemnitor_name",
        "indemnitor_email",
        "indemnitor_phone",
        "county",
        "case_number",
        "poa_number",
        "booking_number",
        "court_date",
        "surety_id",
    ):
        if k not in bond_data and packet.get(k):
            bond_data[k] = packet.get(k)

    if packet.get("intake_id") and not bond_data.get("defendant_name"):
        intake = await _load_intake(packet["intake_id"])
        if intake:
            bond_data.setdefault("defendant_name", intake.get("defendant_name"))
            bond_data.setdefault("indemnitor_name", intake.get("indemnitor_name"))
            bond_data.setdefault("indemnitor_email", intake.get("indemnitor_email"))
            bond_data.setdefault("indemnitor_phone", intake.get("indemnitor_phone"))
            bond_data.setdefault("county", intake.get("county"))
            bond_data.setdefault("booking_number", intake.get("booking_number"))

    svc = get_docuseal_service()
    if not svc.is_configured:
        return JSONResponse(
            {"success": False, "error": "docuseal_not_configured"},
            status_code=503,
        )

    try:
        result = await svc.create_submission_for_packet(
            template_id=template_id,
            packet_id=packet_id,
            bond_data=bond_data,
            indemnitors=body.get("indemnitors"),
            defendant=body.get("defendant"),
            send_email=bool(body.get("send_email", False)),
            include_defendant=bool(body.get("include_defendant", True)),
            completed_redirect_url=body.get("completed_redirect_url"),
        )
    except Exception as exc:
        logger.exception("docuseal create submission failed packet=%s", packet_id)
        return JSONResponse(
            {"success": False, "error": str(exc)[:400]},
            status_code=502,
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    await get_collection("paperwork_packets").update_one(
        {"packet_id": packet_id},
        {
            "$set": {
                "esign_provider": "docuseal",
                "docuseal_template_id": template_id,
                "docuseal_submission_id": result.get("submission_id"),
                "docuseal_submitters": result.get("submitters"),
                "docuseal_status": "sent",
                "docuseal_sent_at": now_iso,
                "status": packet.get("status") if packet.get("status") == "signed" else "sent",
            }
        },
    )

    return {
        "success": True,
        "packet_id": packet_id,
        "esign_provider": "docuseal",
        "submission_id": result.get("submission_id"),
        "submitters": result.get("submitters"),
        "sign_links": [
            {"role": s.get("role"), "email": s.get("email"), "sign_url": s.get("sign_url")}
            for s in (result.get("submitters") or [])
        ],
    }


@paperwork_bp.post("/paperwork/docuseal/poll-swipesimple")
async def paperwork_poll_swipesimple(request: Request):
    """
    Manual trigger: poll Gmail for SwipeSimple bond receipts
    (same inbox utility as Bail School GAS poller; school amounts skipped).
    """
    from dashboard.services.swipesimple_receipt_poller import run_swipesimple_receipt_poll

    try:
        body = await request.json() or {}
    except Exception:
        body = {}
    result = await run_swipesimple_receipt_poll(body)
    return result
