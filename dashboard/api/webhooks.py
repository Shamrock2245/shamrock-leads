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
        return True
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

    audit_doc = {
        "source": "signnow_webhook",
        "event_type": data.get('event', 'unknown'),
        "payload": data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await audit_events.insert_one(audit_doc)

    if data.get('event') == 'document.complete':
        doc_id = data.get('document_id') or data.get('content', {}).get('document_id')
        bond_cases = get_collection("bond_cases")
        await bond_cases.update_one(
            {"signnow_document_id": doc_id},
            {"$set": {"status": "signed", "signed_at": datetime.now(timezone.utc).isoformat()}}
        )
        await publish_event('document_signed', {"document_id": doc_id})

    return jsonify({"success": True}), 200


@webhooks_bp.route('/webhooks/twilio', methods=['POST'])
async def twilio_webhook():
    """Handle inbound SMS from Twilio."""
    from dashboard.api.events import publish_event

    form_data = await request.form
    audit_events = get_collection("audit_events")

    audit_doc = {
        "source": "twilio_webhook",
        "event_type": "inbound_sms",
        "payload": dict(form_data),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await audit_events.insert_one(audit_doc)

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

    audit_doc = {
        "source": "payment_webhook",
        "event_type": "payment_confirmation",
        "payload": data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await audit_events.insert_one(audit_doc)

    await publish_event('payment_confirmed', data)

    return jsonify({"success": True}), 200
