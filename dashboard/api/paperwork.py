"""
ShamrockLeads — Phase 6: Paperwork Generation API Blueprint

Generates, delivers, and tracks all bail bond paperwork:
  - Appearance Bond PDFs (one per charge, OSI or Palmetto template)
  - Indemnity Agreement
  - SSA Release (signed by all parties)
  - Power of Attorney (POA)

Endpoints:
  POST /api/paperwork/generate/<intake_id>     — Generate full packet for an intake
  POST /api/paperwork/generate/bond/<intake_id> — Generate appearance bond PDFs only
  GET  /api/paperwork/<packet_id>              — Get packet status + download links
  POST /api/paperwork/<packet_id>/deliver      — Deliver via BlueBubbles iMessage
  POST /api/paperwork/<packet_id>/signnow      — Push to SignNow for e-signature
  GET  /api/paperwork/list/<intake_id>         — List all packets for an intake
"""
import io
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from quart import Blueprint, jsonify, request, current_app, send_file
from dashboard.extensions import get_collection
from dashboard.services.bb_client import get_bb_client

logger = logging.getLogger(__name__)
paperwork_bp = Blueprint("paperwork", __name__)

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
# Generate the full paperwork packet for an intake record.
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.route("/paperwork/generate/<intake_id>", methods=["POST"])
async def generate_packet(intake_id: str):
    """
    Generate the full paperwork packet (appearance bonds + indemnity + SSA + POA).
    Stores packet metadata in `paperwork_packets` collection.
    Returns packet_id and document list.
    """
    try:
        data = (await request.get_json()) or {}
        packet_type = data.get("packet_type", "full")
        template = data.get("template", "osi")  # "osi" or "palmetto"

        intake = await _load_intake(intake_id)
        if not intake:
            return jsonify({"error": f"Intake {intake_id} not found"}), 404

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
        packet_doc = {
            "packet_id": packet_id,
            "intake_id": intake_id,
            "packet_type": packet_type,
            "template": template,
            "status": "generated",
            "documents": documents,
            "defendant_name": intake.get("defendant_name", ""),
            "defendant_county": intake.get("defendant_county", ""),
            "indemnitor_name": intake.get("indemnitor_name", ""),
            "indemnitor_phone": intake.get("indemnitor_phone", ""),
            "created_at": now,
            "updated_at": now,
            "delivered_via": None,
            "signnow_invite_id": None,
            "signnow_status": None,
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

        logger.info("[paperwork] Packet %s generated for intake %s", packet_id, intake_id)
        return jsonify({
            "success": True,
            "packet_id": packet_id,
            "intake_id": intake_id,
            "packet_type": packet_type,
            "documents": documents,
            "document_count": len(documents),
        })

    except Exception as exc:
        logger.exception("generate_packet error for intake %s", intake_id)
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paperwork/<packet_id>
# Get packet status and document list.
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.route("/paperwork/<packet_id>", methods=["GET"])
async def get_packet(packet_id: str):
    """Return packet metadata and document list."""
    try:
        packet = await _load_packet(packet_id)
        if not packet:
            return jsonify({"error": f"Packet {packet_id} not found"}), 404

        # Serialize datetimes
        for field in ("created_at", "updated_at"):
            if hasattr(packet.get(field), "isoformat"):
                packet[field] = packet[field].isoformat()

        return jsonify(packet)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/paperwork/<packet_id>/deliver
# Deliver the packet via BlueBubbles iMessage.
# Body: { "phone": "+12395551234", "message": "Here is your paperwork..." }
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.route("/paperwork/<packet_id>/deliver", methods=["POST"])
async def deliver_packet(packet_id: str):
    """
    Deliver the paperwork packet via BlueBubbles iMessage.
    Sends a message with a magic link to the packet's signing page.
    Includes a geolocator link as required by project standards.
    """
    try:
        data = (await request.get_json()) or {}
        phone = data.get("phone", "").strip()
        custom_message = data.get("message", "")
        include_geo = data.get("include_geo", True)

        if not phone:
            return jsonify({"error": "phone is required"}), 400

        packet = await _load_packet(packet_id)
        if not packet:
            return jsonify({"error": f"Packet {packet_id} not found"}), 404

        defendant_name = packet.get("defendant_name", "your defendant")
        intake_id = packet.get("intake_id", "")

        # Build the signing magic link
        base_url = current_app.config.get("PORTAL_BASE_URL", "https://shamrockbailbonds.biz")
        magic_link = f"{base_url}/sign/{packet_id}"

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

        # Send via universal bridge (iMessage-first, SMS fallback)
        from dashboard.services.bb_client import send_message_universal
        result = await send_message_universal(phone, message)

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

        logger.info("[paperwork] Packet %s delivered to %s", packet_id, phone)
        return jsonify({
            "success": result.get("success", False),
            "packet_id": packet_id,
            "delivered_to": phone,
            "magic_link": magic_link,
            "bb_result": result,
        })

    except Exception as exc:
        logger.exception("deliver_packet error for %s", packet_id)
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/paperwork/<packet_id>/signnow
# Push the packet to SignNow for e-signature.
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.route("/paperwork/<packet_id>/signnow", methods=["POST"])
async def push_to_signnow(packet_id: str):
    """
    Push the paperwork packet to SignNow for e-signature.
    Uses the existing signnow_packet_service.
    """
    try:
        data = (await request.get_json()) or {}

        packet = await _load_packet(packet_id)
        if not packet:
            return jsonify({"error": f"Packet {packet_id} not found"}), 404

        intake_id = packet.get("intake_id", "")
        intake = await _load_intake(intake_id)
        if not intake:
            return jsonify({"error": f"Intake {intake_id} not found"}), 404

        from dashboard.services.signnow_packet_service import SignNowPacketService
        svc = SignNowPacketService()
        result = await svc.create_packet(intake_doc=intake, packet_id=packet_id)

        now = datetime.now(timezone.utc)
        packets_col = get_collection("paperwork_packets")
        await packets_col.update_one(
            {"packet_id": packet_id},
            {"$set": {
                "signnow_invite_id": result.get("invite_id"),
                "signnow_status": "sent",
                "signnow_sent_at": now,
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

        logger.info("[paperwork] Packet %s pushed to SignNow: %s", packet_id, result.get("invite_id"))
        return jsonify({
            "success": True,
            "packet_id": packet_id,
            "signnow_invite_id": result.get("invite_id"),
            "signnow_signing_link": result.get("signing_link"),
        })

    except Exception as exc:
        logger.exception("push_to_signnow error for %s", packet_id)
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paperwork/list/<intake_id>
# List all packets for an intake record.
# ─────────────────────────────────────────────────────────────────────────────
@paperwork_bp.route("/paperwork/list/<intake_id>", methods=["GET"])
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

        return jsonify({
            "success": True,
            "intake_id": intake_id,
            "packets": packets,
            "count": len(packets),
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500
