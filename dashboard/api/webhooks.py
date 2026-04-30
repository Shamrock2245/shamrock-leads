"""
ShamrockLeads — Webhooks API Blueprint
Handles inbound webhooks from SignNow, Twilio, and SwipeSimple.

Uses extensions.get_collection() to avoid circular imports from app.py.
"""

from quart import Blueprint, request, jsonify
import hmac
import hashlib
import logging
import os
from datetime import datetime, timezone

from dashboard.extensions import get_collection

webhooks_bp = Blueprint('webhooks', __name__)
logger = logging.getLogger(__name__)


def verify_signnow_signature(payload: bytes, signature: str) -> bool:
    secret = os.getenv('SIGNNOW_WEBHOOK_SECRET', '').encode('utf-8')
    if not secret:
        return True  # Skip verification if no secret configured

    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@webhooks_bp.route('/webhooks/signnow', methods=['POST'])
async def signnow_webhook():
    """
    Handle document.complete (and other) events from SignNow.

    On document.complete:
      1. Verify HMAC signature.
      2. Log to audit_events.
      3. Look up the bond case by signnow_document_id.
      4. Download the signed PDF from SignNow.
      5. Upload the signed PDF to the Google Drive case folder.
      6. Update bond_cases: Packet_Status=signed, Signature_Status=signed, signed_at.
      7. Publish SSE event to dashboard.
      8. Fire Slack alert.
    """
    import httpx
    from dashboard.api.events import publish_event

    signature = request.headers.get('x-signnow-signature', '')
    payload = await request.get_data()

    if not verify_signnow_signature(payload, signature):
        return jsonify({"error": "Invalid signature"}), 401

    data = await request.get_json(force=True) or {}
    now_iso = datetime.now(timezone.utc).isoformat()

    # Step 2: Log to audit_events
    audit_events = get_collection("audit_events")
    await audit_events.insert_one({
        "source": "signnow_webhook",
        "event_type": data.get('event', 'unknown'),
        "payload": data,
        "timestamp": now_iso,
    })

    if data.get('event') == 'document.complete':
        doc_id = (
            data.get('document_id')
            or data.get('content', {}).get('document_id')
            or ""
        )
        logger.info("[signnow_webhook] document.complete for doc_id=%s", doc_id)

        # Step 3: Look up bond case
        bond_cases = get_collection("bond_cases")
        bond_case = await bond_cases.find_one({"signnow_document_id": doc_id})

        # Step 4: Download signed PDF
        pdf_bytes = None
        signnow_token = os.getenv("SIGNNOW_API_TOKEN", "")
        if doc_id and signnow_token:
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    dl_resp = await client.get(
                        f"https://api.signnow.com/document/{doc_id}/download?type=collapsed",
                        headers={"Authorization": f"Bearer {signnow_token}"},
                    )
                    dl_resp.raise_for_status()
                    pdf_bytes = dl_resp.content
                    logger.info(
                        "[signnow_webhook] Downloaded signed PDF (%d bytes) for doc %s",
                        len(pdf_bytes), doc_id,
                    )
            except Exception as exc:
                logger.error("[signnow_webhook] PDF download failed: %s", exc)

        # Step 5: Upload to Google Drive case folder
        drive_file_id = None
        drive_url = None
        if pdf_bytes and bond_case:
            try:
                from googleapiclient.discovery import build as gdrive_build
                from googleapiclient.http import MediaIoBaseUpload
                from google.oauth2 import service_account
                import io

                creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
                if creds_path and os.path.exists(creds_path):
                    creds = service_account.Credentials.from_service_account_file(
                        creds_path,
                        scopes=["https://www.googleapis.com/auth/drive"],
                    )
                    drive_svc = gdrive_build("drive", "v3", credentials=creds)

                    defendant_name = bond_case.get("defendant_name", "Unknown")
                    booking_number = bond_case.get("booking_number", doc_id[:8])
                    folder_id = bond_case.get("google_drive_folder_id", "")

                    file_name = f"SIGNED_{defendant_name}_{booking_number}_{doc_id[:8]}.pdf"
                    file_meta = {"name": file_name}
                    if folder_id:
                        file_meta["parents"] = [folder_id]

                    media = MediaIoBaseUpload(
                        io.BytesIO(pdf_bytes),
                        mimetype="application/pdf",
                        resumable=False,
                    )
                    uploaded = drive_svc.files().create(
                        body=file_meta,
                        media_body=media,
                        fields="id,webViewLink",
                    ).execute()
                    drive_file_id = uploaded.get("id")
                    drive_url = uploaded.get("webViewLink")
                    logger.info(
                        "[signnow_webhook] Filed signed PDF to Google Drive: %s", drive_url
                    )
                else:
                    logger.warning(
                        "[signnow_webhook] GOOGLE_APPLICATION_CREDENTIALS not set "
                        "-- skipping Drive upload"
                    )
            except Exception as exc:
                logger.error("[signnow_webhook] Google Drive upload failed: %s", exc)

        # Step 6: Update bond case
        update_fields = {
            "Packet_Status": "signed",
            "Signature_Status": "signed",
            "signed_at": now_iso,
        }
        if drive_file_id:
            update_fields["signed_pdf_drive_id"] = drive_file_id
            update_fields["signed_pdf_drive_url"] = drive_url

        await bond_cases.update_one(
            {"signnow_document_id": doc_id},
            {"$set": update_fields},
        )

        # Step 7: Publish SSE event
        defendant_name = (bond_case or {}).get("defendant_name", "Unknown")
        await publish_event('document_signed', {
            "document_id": doc_id,
            "defendant_name": defendant_name,
            "drive_url": drive_url,
            "signed_at": now_iso,
        })

        # Step 8: Slack alert
        slack_webhook = os.getenv("SLACK_WEBHOOK_URL", "")
        if slack_webhook:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    drive_link = f" -- <{drive_url}|View in Drive>" if drive_url else ""
                    await client.post(slack_webhook, json={
                        "text": (
                            f":white_check_mark: *SignNow Complete* -- "
                            f"{defendant_name} signed their paperwork.{drive_link}"
                        )
                    })
            except Exception as exc:
                logger.warning("[signnow_webhook] Slack alert failed: %s", exc)

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
