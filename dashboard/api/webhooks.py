"""
ShamrockLeads — Webhooks API Blueprint
Handles inbound webhooks from SignNow, Twilio, and SwipeSimple.

Uses extensions.get_collection() to avoid circular imports from app.py.
"""
from __future__ import annotations

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

        # Step 9: Telegram staff alert
        try:
            from dashboard.services.telegram_service import get_telegram_service
            tg = get_telegram_service()
            booking_number = (bond_case or {}).get("booking_number", doc_id[:8])
            await tg.send_document_signed_alert(
                defendant_name=defendant_name,
                booking_number=booking_number,
                drive_url=drive_url or "",
            )
        except Exception as tg_exc:
            logger.warning("[signnow_webhook] Telegram alert failed: %s", tg_exc)

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
    """
    Handle SwipeSimple payment confirmation webhook.

    SwipeSimple sends a POST with JSON payload on payment events.
    Expected fields (SwipeSimple standard webhook schema):
        event_type:       "payment.completed" | "payment.failed" | "payment.refunded"
        transaction_id:   unique SwipeSimple transaction ID
        amount:           payment amount in dollars (float)
        status:           "approved" | "declined" | "refunded"
        card_last4:       last 4 digits of card
        card_brand:       "Visa" | "Mastercard" etc.
        customer_name:    cardholder name
        custom_fields:    { booking_number, county, indemnitor_name, indemnitor_phone }
        created_at:       ISO timestamp

    On success:
      1. Validate HMAC signature (if SWIPESIMPLE_WEBHOOK_SECRET is set)
      2. Parse booking_number from custom_fields or query params
      3. Update bond case payment status in active_bonds / prospective_bonds
      4. Log to payments collection
      5. Send BlueBubbles receipt to indemnitor
      6. Fire Slack alert
      7. Publish SSE event
      8. Log audit event
    """
    import httpx
    from dashboard.api.events import publish_event
    from dashboard.services.bb_client import send_message_universal

    now = datetime.now(timezone.utc)

    # -- 1. HMAC signature validation (optional -- skip if secret not set) -----
    webhook_secret = os.getenv("SWIPESIMPLE_WEBHOOK_SECRET", "")
    if webhook_secret:
        raw_body = await request.get_data()
        sig_header = request.headers.get("X-SwipeSimple-Signature", "")
        expected_sig = hmac.new(
            webhook_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(f"sha256={expected_sig}", sig_header):
            logger.warning("[payment_webhook] Invalid HMAC signature -- rejecting")
            return jsonify({"error": "Invalid signature"}), 401

    data = await request.get_json(silent=True) or {}
    event_type = data.get("event_type", "payment.completed")
    transaction_id = data.get("transaction_id", "")
    amount = float(data.get("amount", 0))
    status = data.get("status", "approved").lower()
    card_last4 = data.get("card_last4", "")
    card_brand = data.get("card_brand", "")
    customer_name = data.get("customer_name", "")
    created_at = data.get("created_at", now.isoformat())

    # Extract booking_number from custom_fields or top-level
    custom = data.get("custom_fields", {})
    booking_number = (
        custom.get("booking_number")
        or data.get("booking_number")
        or request.args.get("booking_number", "")
    )
    indemnitor_name = custom.get("indemnitor_name", customer_name)
    indemnitor_phone = custom.get("indemnitor_phone", "")
    county = custom.get("county", "")

    logger.info(
        "[payment_webhook] event=%s txn=%s amount=$%.2f status=%s booking=%s",
        event_type, transaction_id, amount, status, booking_number
    )

    # -- 2. Determine payment outcome -----------------------------------------
    is_success = status in ("approved", "completed", "success")
    is_refund = event_type == "payment.refunded" or status == "refunded"
    is_failed = status in ("declined", "failed")

    # -- 3. Update bond case ---------------------------------------------------
    if booking_number:
        active_bonds_col = get_collection("active_bonds")
        prospective_bonds_col = get_collection("prospective_bonds")

        payment_update = {
            "last_payment_amount": amount,
            "last_payment_date": now,
            "last_payment_txn": transaction_id,
            "last_payment_status": status,
            "updated_at": now,
        }
        if is_success:
            payment_update["payment_status"] = "paid"
            payment_update["payment_received"] = True
        elif is_refund:
            payment_update["payment_status"] = "refunded"
        elif is_failed:
            payment_update["payment_status"] = "failed"

        # Try active_bonds first, then prospective_bonds
        result = await active_bonds_col.update_one(
            {"booking_number": booking_number},
            {"$set": payment_update, "$inc": {"total_paid": amount if is_success else 0}},
        )
        if result.matched_count == 0:
            await prospective_bonds_col.update_one(
                {"booking_number": booking_number},
                {"$set": payment_update, "$inc": {"total_paid": amount if is_success else 0}},
            )

    # -- 4. Log to payments collection ----------------------------------------
    payments_col = get_collection("payments")
    payment_doc = {
        "booking_number": booking_number,
        "transaction_id": transaction_id,
        "amount": amount,
        "status": status,
        "event_type": event_type,
        "card_last4": card_last4,
        "card_brand": card_brand,
        "customer_name": customer_name,
        "indemnitor_name": indemnitor_name,
        "indemnitor_phone": indemnitor_phone,
        "county": county,
        "method": "swipesimple",
        "source": "webhook",
        "created_at": created_at,
        "recorded_at": now.isoformat(),
    }
    await payments_col.insert_one(payment_doc)

    # -- 5. BlueBubbles receipt to indemnitor ---------------------------------
    if is_success and indemnitor_phone:
        first_name = indemnitor_name.split()[0] if indemnitor_name else "there"
        defendant_name = ""
        if booking_number:
            bond_doc = await get_collection("active_bonds").find_one(
                {"booking_number": booking_number}, {"defendant_name": 1}
            )
            if bond_doc:
                defendant_name = bond_doc.get("defendant_name", "")

        payment_date = now.strftime("%B %d, %Y")
        card_info = f"{card_brand} ending in {card_last4}" if card_last4 else "card on file"
        receipt_msg = (
            f"Hi {first_name}! \u2705 We received your payment of ${amount:,.2f} "
            f"on {payment_date}"
            + (f" for {defendant_name}'s bond" if defendant_name else "")
            + f" via {card_info}.\n\n"
            f"Thank you! Your receipt has been recorded. "
            f"Reply anytime if you need anything. \u2014 Shamrock Bail Bonds \U0001f340"
        )
        try:
            bb_result = await send_message_universal(indemnitor_phone, receipt_msg)
            logger.info(
                "[payment_webhook] Receipt sent to %s: %s",
                indemnitor_phone, bb_result.get("success")
            )
        except Exception as exc:
            logger.warning("[payment_webhook] BB receipt failed: %s", exc)

    # -- 6. Slack alert -------------------------------------------------------
    slack_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if slack_url:
        import httpx as _httpx
        emoji = "\u2705" if is_success else ("\U0001f504" if is_refund else "\u274c")
        slack_text = (
            f"{emoji} *SwipeSimple Payment {status.title()}*\n"
            f"Amount: *${amount:,.2f}*\n"
            f"Booking: `{booking_number or 'N/A'}`\n"
            f"Indemnitor: {indemnitor_name or 'Unknown'}\n"
            f"Card: {card_brand} ****{card_last4}\n"
            f"TXN: `{transaction_id}`"
        )
        try:
            async with _httpx.AsyncClient(timeout=5) as client:
                await client.post(slack_url, json={"text": slack_text})
        except Exception as exc:
            logger.warning("[payment_webhook] Slack alert failed: %s", exc)

    # -- 7. SSE event ---------------------------------------------------------
    await publish_event("payment_confirmed", {
        "booking_number": booking_number,
        "amount": amount,
        "status": status,
        "transaction_id": transaction_id,
        "indemnitor_name": indemnitor_name,
    })

    # -- 8. Audit log ---------------------------------------------------------
    audit_events = get_collection("audit_events")
    await audit_events.insert_one({
        "source": "payment_webhook",
        "event_type": event_type,
        "booking_number": booking_number,
        "amount": amount,
        "status": status,
        "transaction_id": transaction_id,
        "indemnitor_name": indemnitor_name,
        "timestamp": now.isoformat(),
    })

    # -- 9. Telegram staff alert ----------------------------------------------
    try:
        from dashboard.services.telegram_service import get_telegram_service
        tg = get_telegram_service()
        emoji = "\u2705" if is_success else ("\U0001f504" if is_refund else "\u274c")
        tg_msg = (
            f"{emoji} *SwipeSimple {status.title()}*\n"
            f"Amount: *${amount:,.2f}*\n"
            f"Booking: `{booking_number or 'N/A'}`\n"
            f"Indemnitor: {indemnitor_name or 'Unknown'}\n"
            f"TXN: `{transaction_id}`"
        )
        await tg.send_staff_alert(tg_msg)
    except Exception as tg_exc:
        logger.warning("[payment_webhook] Telegram alert failed: %s", tg_exc)

    return jsonify({"success": True, "recorded": True, "booking_number": booking_number}), 200


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
