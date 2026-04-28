"""
ShamrockLeads — Webhooks API Blueprint
Handles inbound webhooks from SignNow, Twilio, and SwipeSimple.

Uses extensions.get_collection() to avoid circular imports from app.py.
"""

from quart import Blueprint, request, jsonify
import hmac
import hashlib
import os
from datetime import datetime, timezone

from dashboard.extensions import get_collection

webhooks_bp = Blueprint('webhooks', __name__)


def verify_signnow_signature(payload: bytes, signature: str) -> bool:
    secret = os.getenv('SIGNNOW_WEBHOOK_SECRET', '').encode('utf-8')
    if not secret:
        return True  # Skip verification if no secret configured

    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@webhooks_bp.route('/webhooks/signnow', methods=['POST'])
async def signnow_webhook():
    """Handle document.complete events from SignNow."""
    from dashboard.api.events import publish_event

    signature = request.headers.get('x-signnow-signature', '')
    payload = await request.get_data()

    if not verify_signnow_signature(payload, signature):
        return jsonify({"error": "Invalid signature"}), 401

    data = await request.get_json()
    audit_events = get_collection("audit_events")

    # Log to audit_events
    audit_doc = {
        "source": "signnow_webhook",
        "event_type": data.get('event', 'unknown'),
        "payload": data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await audit_events.insert_one(audit_doc)

    if data.get('event') == 'document.complete':
        doc_id = data.get('document_id') or data.get('content', {}).get('document_id')

        # Update relevant MongoDB document
        bond_cases = get_collection("bond_cases")
        await bond_cases.update_one(
            {"signnow_document_id": doc_id},
            {"$set": {"status": "signed", "signed_at": datetime.now(timezone.utc).isoformat()}}
        )

        # Publish SSE event
        await publish_event('document_signed', {"document_id": doc_id})

    return jsonify({"success": True}), 200


@webhooks_bp.route('/webhooks/twilio', methods=['POST'])
async def twilio_webhook():
    """Handle inbound SMS from Twilio."""
    from dashboard.api.events import publish_event

    # Twilio sends form data
    form_data = await request.form
    audit_events = get_collection("audit_events")

    # Log to audit_events
    audit_doc = {
        "source": "twilio_webhook",
        "event_type": "inbound_sms",
        "payload": dict(form_data),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await audit_events.insert_one(audit_doc)

    # Publish SSE event
    await publish_event('sms_received', {
        "from": form_data.get('From'),
        "body": form_data.get('Body')
    })

    return "<Response></Response>", 200, {'Content-Type': 'text/xml'}


@webhooks_bp.route('/webhooks/payment', methods=['POST'])
async def payment_webhook():
    """Handle SwipeSimple payment confirmation (future)."""
    from dashboard.api.events import publish_event

    data = await request.get_json()
    audit_events = get_collection("audit_events")

    # Log to audit_events
    audit_doc = {
        "source": "payment_webhook",
        "event_type": "payment_confirmation",
        "payload": data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await audit_events.insert_one(audit_doc)

    # Publish SSE event
    await publish_event('payment_confirmed', data)

    return jsonify({"success": True}), 200


# ═══════════════════════════════════════════════════════════════════════════════
#  Wix Intake Webhooks
#  Called by Wix Velo backend when a new IntakeQueue record is created.
#  Mirrors handleNewIntake() from GAS WixPortalIntegration.js.
# ═══════════════════════════════════════════════════════════════════════════════

@webhooks_bp.route('/webhooks/wix-intake', methods=['POST'])
async def wix_intake_webhook():
    """
    Receive a new intake from the Wix/Velo IntakeQueue CMS collection.
    Validates the API key, stores in intake_queue, and publishes SSE event.
    """
    from dashboard.api.events import publish_event
    from dashboard.api.intake import _extract_indemnitor, _extract_defendant, SOURCE_LABELS
    import uuid

    # Validate API key
    api_key = (
        request.headers.get("X-API-Key")
        or request.headers.get("Authorization", "").replace("Bearer ", "")
    )
    expected_key = os.getenv("WIX_WEBHOOK_SECRET") or os.getenv("GAS_API_KEY", "")
    if expected_key and api_key != expected_key:
        return jsonify({"error": "Unauthorized"}), 401

    data = await request.get_json(force=True) or {}
    data.setdefault("source", "wix_portal")

    try:
        indemnitor = _extract_indemnitor(data)
        defendant = _extract_defendant(data)

        ind_full = " ".join(filter(None, [indemnitor["firstName"], indemnitor["lastName"]])) or "Unknown"
        def_full = defendant["name"] or "Unknown"

        intake_id = (
            data.get("intakeId")
            or data.get("caseId")
            or data.get("_id")
            or f"WX-{uuid.uuid4().hex[:10].upper()}"
        )

        now = datetime.now(timezone.utc)
        doc = {
            "intake_id": intake_id,
            "source": "wix_portal",
            "source_label": SOURCE_LABELS.get("wix_portal", "🌐 Wix Portal"),
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "indemnitor": indemnitor,
            "indemnitor_name": ind_full,
            "indemnitor_email": indemnitor.get("email", ""),
            "indemnitor_phone": indemnitor.get("phone", ""),
            "defendant": defendant,
            "defendant_name": def_full,
            "defendant_booking_number": defendant.get("bookingNumber", ""),
            "defendant_county": defendant.get("county", ""),
            "defendant_facility": defendant.get("facility", ""),
            "consent_given": bool(data.get("consent") or data.get("consentGiven")),
            "consent_timestamp": data.get("consentTimestamp", now.isoformat()),
            "ai_risk": "",
            "ai_score": None,
            "ai_rationale": "",
            "gas_sync_status": "pending",
            "gas_sync_timestamp": None,
            "_raw": data,
        }

        intake_queue = get_collection("intake_queue")
        await intake_queue.update_one(
            {"intake_id": intake_id},
            {"$set": doc},
            upsert=True,
        )

        # Real-time SSE push to dashboard
        await publish_event('new_intake', {
            "intake_id": intake_id,
            "source": "wix_portal",
            "defendant_name": def_full,
            "indemnitor_name": ind_full,
        })

        # Audit log
        audit_events = get_collection("audit_events")
        await audit_events.insert_one({
            "source": "wix_intake_webhook",
            "event_type": "new_intake",
            "intake_id": intake_id,
            "defendant_name": def_full,
            "indemnitor_name": ind_full,
            "timestamp": now.isoformat(),
        })

        return jsonify({
            "success": True,
            "intake_id": intake_id,
            "message": f"Intake received for {def_full}",
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@webhooks_bp.route('/webhooks/wix-intake-update', methods=['POST'])
async def wix_intake_update_webhook():
    """
    Receive an intake status update from Wix (consent confirmed, status changed, AI score).
    """
    api_key = (
        request.headers.get("X-API-Key")
        or request.headers.get("Authorization", "").replace("Bearer ", "")
    )
    expected_key = os.getenv("WIX_WEBHOOK_SECRET") or os.getenv("GAS_API_KEY", "")
    if expected_key and api_key != expected_key:
        return jsonify({"error": "Unauthorized"}), 401

    data = await request.get_json(force=True) or {}
    intake_id = data.get("intakeId") or data.get("caseId") or data.get("intake_id")
    if not intake_id:
        return jsonify({"error": "intake_id required"}), 400

    allowed = {"status", "gas_sync_status", "ai_risk", "ai_score", "ai_rationale", "notes"}
    updates = {k: v for k, v in data.items() if k in allowed}
    updates["updated_at"] = datetime.now(timezone.utc)

    intake_queue = get_collection("intake_queue")
    result = await intake_queue.update_one({"intake_id": intake_id}, {"$set": updates})
    if result.matched_count == 0:
        return jsonify({"error": f"Intake {intake_id} not found"}), 404

    return jsonify({"success": True, "intake_id": intake_id})
