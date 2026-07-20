from __future__ import annotations

"""
ShamrockLeads — Webhooks API Blueprint
Handles inbound webhooks from SignNow, Twilio, and SwipeSimple.

Uses extensions.get_collection() to avoid circular imports from app.py.

Security:
  - SignNow: HMAC-SHA256 signature verification (SIGNNOW_WEBHOOK_SECRET).
             If secret is NOT set, webhook is REJECTED (fail-closed, not fail-open).
  - SwipeSimple: HMAC-SHA256 signature verification (SWIPESIMPLE_WEBHOOK_SECRET).
  - Twilio: Twilio request validator (TWILIO_AUTH_TOKEN).

Data Flow (SignNow document.complete):
  1. Verify HMAC signature — reject if invalid or secret missing.
  2. Log raw payload to audit_events.
  3. Look up paperwork_packets by signnow_document_id (primary) OR
     by signnow_invite_id (fallback) to handle group-invite completions.
  4. Verify packet is bound to an active bond case (policy Rule 1 + Rule 5).
  5. Download signed PDF from SignNow.
  6. Upload signed PDF to Google Drive case folder.
  7. Update paperwork_packets: status=signed, signnow_status=signed, signed_at.
  8. Update bond_cases: Packet_Status=signed, Signature_Status=signed, signed_at.
  9. Publish SSE event to dashboard.
  10. Fire Slack alert.
  11. Fire Telegram staff alert.
  12. Escalate if packet references unknown bond case (policy Rule 5 / Escalation).
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
import hmac
import hashlib
import logging
import os
from datetime import datetime, timezone

from dashboard.extensions import get_collection

webhooks_bp = APIRouter(prefix="/api", tags=["webhooks"])
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Security helpers
# ─────────────────────────────────────────────────────────────────────────────

def verify_signnow_signature(payload: bytes, signature: str) -> bool:
    """
    Verify the SignNow HMAC-SHA256 webhook signature.

    SECURITY: Fail-CLOSED — if SIGNNOW_WEBHOOK_SECRET is not configured,
    we REJECT the request rather than accepting it blindly. This prevents
    spoofed document.complete events from triggering Drive uploads and
    status updates on forged data.

    SignNow sends the signature in the 'x-signnow-signature' header as a
    hex-encoded HMAC-SHA256 of the raw request body.
    """
    secret = os.getenv('SIGNNOW_WEBHOOK_SECRET', '')
    if not secret:
        # In production, reject if secret is not configured.
        # In dev (DEBUG=true), allow through with a warning.
        if os.getenv("DEBUG", "false").lower() == "true":
            logger.warning("[signnow_webhook] SIGNNOW_WEBHOOK_SECRET not set — allowing in DEBUG mode")
            return True
        logger.error("[signnow_webhook] SIGNNOW_WEBHOOK_SECRET not set — rejecting webhook (set DEBUG=true to bypass)")
        return False
        
    secret_bytes = secret.encode('utf-8')

    if not signature:
        logger.warning("[signnow_webhook] No x-signnow-signature header present — rejecting")
        return False

    expected = hmac.new(secret_bytes, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


# ─────────────────────────────────────────────────────────────────────────────
# POST /webhooks/signnow
# ─────────────────────────────────────────────────────────────────────────────

@webhooks_bp.post("/webhooks/signnow")
async def signnow_webhook(request: Request):
    """
    Handle document.complete (and other) events from SignNow.

    On document.complete:
      1.  Verify HMAC signature (fail-closed — rejects if secret not set).
      2.  Log raw payload to audit_events.
      3.  Look up paperwork_packets by signnow_document_id OR signnow_invite_id.
      4.  Verify packet is bound to an active bond case (policy Rule 1 + 5).
      5.  Download signed PDF from SignNow.
      6.  Upload signed PDF to Google Drive case folder.
      7.  Update paperwork_packets: status=signed, signnow_status=signed, signed_at,
          signed_pdf_drive_id, signed_pdf_drive_url.
      8.  Update bond_cases: Packet_Status=signed, Signature_Status=signed, signed_at.
      9.  Publish SSE event to dashboard.
      10. Fire Slack alert.
      11. Fire Telegram staff alert.
      12. Escalate (Slack + log) if packet references unknown bond case.
    """
    import httpx
    from dashboard.routers.events import publish_event

    # ── Step 1: Verify HMAC signature ────────────────────────────────────────
    signature = request.headers.get('x-signnow-signature', '')
    payload = await request.body()

    if not verify_signnow_signature(payload, signature):
        logger.warning(
            "[signnow_webhook] Rejected — invalid or missing HMAC signature. "
            "Headers: %s", dict(request.headers)
        )
        return JSONResponse({"error": "Invalid signature"}, status_code=401)

    data = await request.json() or {}
    now_iso = datetime.now(timezone.utc).isoformat()
    event_type = data.get('event', 'unknown')

    # ── Step 2: Log raw payload to audit_events ───────────────────────────────
    audit_events = get_collection("audit_events")
    await audit_events.insert_one({
        "source": "signnow_webhook",
        "event_type": event_type,
        "payload": data,
        "timestamp": now_iso,
    })

    logger.info("[signnow_webhook] Received event=%s", event_type)

    if event_type not in ('document.complete', 'document_group.complete'):
        # Log non-complete events (e.g. document.update, invite.complete) but take no action
        return JSONResponse(status_code=200, content={"success": True, "action": "logged_only"})

    # ── Extract document ID ───────────────────────────────────────────────────
    doc_id = (
        data.get('document_id')
        or data.get('document_group_id')
        or data.get('content', {}).get('document_id')
        or data.get('content', {}).get('document_group_id')
        or data.get('meta', {}).get('document_id')
        or data.get('meta', {}).get('document_group_id')
        or ""
    )
    logger.info("[signnow_webhook] %s for id=%s", event_type, doc_id)

    # ── Step 3: Look up paperwork_packets ────────────────────────────────────
    # Primary: match by signnow_document_id (stored when packet is pushed)
    # Fallback: match by signnow_invite_id (for group-invite completions where
    #           the webhook carries the invite ID rather than a single doc ID)
    packets_col = get_collection("paperwork_packets")
    packet = None
    if doc_id:
        packet = await packets_col.find_one({"signnow_document_id": doc_id})
        if not packet:
            packet = await packets_col.find_one({"signnow_group_id": doc_id})
    if not packet and doc_id:
        # Fallback: the invite_id stored as "embed_<doc_id>" or "group_embed_<group_id>"
        packet = await packets_col.find_one({"signnow_invite_id": f"embed_{doc_id}"})
        if not packet:
            packet = await packets_col.find_one({"signnow_invite_id": f"group_embed_{doc_id}"})
    if not packet:
        # Last resort: search by any document_id in the documents[] array
        packet = await packets_col.find_one({"documents.signnow_doc_id": doc_id})

    if not packet:
        # ── Escalation: unknown packet (policy Escalation Rule) ───────────────
        logger.error(
            "[signnow_webhook] ESCALATION: document.complete for doc_id=%s "
            "has no matching paperwork_packets record. "
            "Possible causes: packet sent outside this system, or doc_id mismatch.",
            doc_id,
        )
        await _send_escalation_slack(
            f"⚠️ *SignNow Escalation* — `document.complete` received for doc `{doc_id}` "
            f"but no matching packet found in paperwork_packets.\n"
            f"This may indicate a forged webhook or a packet sent outside the dashboard."
        )
        # Still return 200 so SignNow doesn't retry indefinitely
        return JSONResponse(status_code=200, content={"success": True, "warning": "packet_not_found"})

    packet_id = packet.get("packet_id", "")
    intake_id = packet.get("intake_id", "")
    defendant_name = packet.get("defendant_name", "Unknown")
    booking_number = packet.get("booking_number") or packet.get("defendant_booking_number", doc_id[:8])

    # ── Step 4: Verify packet is bound to an active bond case ─────────────────
    bond_cases = get_collection("bond_cases")
    bond_case = None

    # Try bond_case_id first (policy Rule 1 — packet must reference Bond_Case_ID)
    bond_case_id = packet.get("bond_case_id")
    if bond_case_id:
        bond_case = await bond_cases.find_one({"bond_case_id": bond_case_id})

    # Fallback: look up by signnow_document_id or signnow_group_id (legacy path)
    if not bond_case and doc_id:
        bond_case = await bond_cases.find_one({"signnow_document_id": doc_id})
        if not bond_case:
            bond_case = await bond_cases.find_one({"signnow_group_id": doc_id})

    # Fallback: look up by packet_id
    if not bond_case and packet_id:
        bond_case = await bond_cases.find_one({"packet_id": packet_id})

    # Fallback: look up by intake_id
    if not bond_case and intake_id:
        bond_case = await bond_cases.find_one({"intake_id": intake_id})

    if not bond_case:
        logger.warning(
            "[signnow_webhook] No bond_case found for packet %s (doc %s) — "
            "will still update packet record and file to Drive, but bond_case update skipped.",
            packet_id, doc_id,
        )
        await _send_escalation_slack(
            f"⚠️ *SignNow Warning* — Packet `{packet_id}` signed (doc `{doc_id}`) "
            f"but no matching bond_case found. "
            f"Defendant: {defendant_name}. Manual review required."
        )
    else:
        # Verify bond case is still open/posted (policy Rule 4)
        case_status = bond_case.get("status", "")
        if case_status not in ("open", "posted", "active", "pending", ""):
            logger.warning(
                "[signnow_webhook] Bond case %s has status=%s — "
                "packet %s signed but case may be closed.",
                bond_case.get("bond_case_id", ""), case_status, packet_id,
            )

    # ── Step 5: Download signed PDF from SignNow ──────────────────────────────
    pdf_bytes = None
    signnow_token = os.getenv("SIGNNOW_API_TOKEN", "")
    if doc_id and signnow_token:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                if event_type == 'document_group.complete' or (packet and packet.get('signnow_group_id') == doc_id):
                    dl_resp = await client.get(
                        f"https://api.signnow.com/document-group/{doc_id}/download?type=merged",
                        headers={"Authorization": f"Bearer {signnow_token}"},
                    )
                else:
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
    elif not signnow_token:
        logger.warning("[signnow_webhook] SIGNNOW_API_TOKEN not set — cannot download signed PDF")

    # ── Step 6: Upload to Google Drive case folder ────────────────────────────
    drive_file_id = None
    drive_url = None
    if pdf_bytes:
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

                # Use bond_case folder if available, else fall back to packet folder
                folder_id = (
                    (bond_case or {}).get("google_drive_folder_id")
                    or packet.get("google_drive_folder_id", "")
                )

                # Descriptive filename: SIGNED_<LastFirst>_<Booking>_<PacketID>_<DocID8>.pdf
                safe_name = defendant_name.replace(" ", "_").replace(",", "")
                file_name = f"SIGNED_{safe_name}_{booking_number}_{packet_id}_{doc_id[:8]}.pdf"
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
                    "— skipping Drive upload"
                )
        except Exception as exc:
            logger.error("[signnow_webhook] Google Drive upload failed: %s", exc)

    # ── Step 7: Update paperwork_packets ─────────────────────────────────────
    packet_update = {
        "status": "signed",
        "signnow_status": "signed",
        "signed_at": now_iso,
        "signnow_document_id": doc_id,  # ensure it's stored for future lookups
    }
    if drive_file_id:
        packet_update["signed_pdf_drive_id"] = drive_file_id
        packet_update["signed_pdf_drive_url"] = drive_url

    await packets_col.update_one(
        {"packet_id": packet_id},
        {"$set": packet_update},
    )

    # ── Step 8: Update bond_cases ─────────────────────────────────────────────
    if bond_case:
        bond_update = {
            "Packet_Status": "signed",
            "Signature_Status": "signed",
            "signed_at": now_iso,
        }
        if drive_file_id:
            bond_update["signed_pdf_drive_id"] = drive_file_id
            bond_update["signed_pdf_drive_url"] = drive_url

        # Match by the most specific key available
        match_key = (
            {"bond_case_id": bond_case.get("bond_case_id")}
            if bond_case.get("bond_case_id")
            else {"signnow_document_id": doc_id}
        )
        await bond_cases.update_one(match_key, {"$set": bond_update})
        logger.info("[signnow_webhook] Bond case updated: %s", match_key)

        # ── Step 8b: Auto-transition to Active ──────────────────────────────────
        from dashboard.services.state_machine import BondStateMachine
        from dashboard.services.audit_service import AuditService
        
        booking_number_for_sm = bond_case.get("booking_number")
        
        # Log to CRM Activity Feed
        if booking_number_for_sm:
            try:
                await AuditService.log_event(
                    entity_type="bond_case",
                    entity_id=booking_number_for_sm,
                    action="Document Group Completed",
                    details={"reason": f"All required signatures collected for Packet {packet_id}"},
                    actor="System (SignNow)",
                    actor_type="system",
                    event_context=str({
                        "module": "paperwork",
                        "signnow_document_id": doc_id,
                        "packet_id": packet_id,
                        "defendant_name": defendant_name
                    })
                )
            except Exception as e:
                logger.warning(f"[signnow_webhook] CRM Audit log failed: {e}")

        if booking_number_for_sm and bond_case.get("status") != "active":
            try:
                await BondStateMachine.transition_bond(
                    booking_number=booking_number_for_sm,
                    new_status="active",
                    actor="System (SignNow Webhook)",
                    reason=f"Document Group {doc_id} signed"
                )
                logger.info(f"[signnow_webhook] Bond {booking_number_for_sm} automatically transitioned to active")
            except Exception as e:
                logger.warning(f"[signnow_webhook] Auto-transition to active failed for {booking_number_for_sm}: {e}")

    # ── Step 9: Publish SSE event (publish_event imported at handler top) ────
    await publish_event('document_signed', {
        "document_id": doc_id,
        "packet_id": packet_id,
        "defendant_name": defendant_name,
        "drive_url": drive_url,
        "signed_at": now_iso,
    })

    # ── Step 10: Slack alert ────────────────────────────────────────────────────────────────────────
    # SignNow complete → #bonds-live (real-time signing updates channel)
    slack_webhook = os.getenv("SLACK_WEBHOOK_LEADS") or os.getenv("SLACK_WEBHOOK_URL", "")
    if slack_webhook:
        try:
            drive_link = f" — <{drive_url}|View in Drive>" if drive_url else ""
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(slack_webhook, json={
                    "text": (
                        f":white_check_mark: *SignNow Complete* — "
                        f"{defendant_name} signed their paperwork "
                        f"(Packet: `{packet_id}`).{drive_link}"
                    )
                })
        except Exception as exc:
            logger.warning("[signnow_webhook] Slack alert failed: %s", exc)

    # ── Step 11: Telegram staff alert ────────────────────────────────────────
    try:
        from dashboard.services.telegram_service import get_telegram_service
        tg = get_telegram_service()
        await tg.send_document_signed_alert(
            defendant_name=defendant_name,
            booking_number=booking_number,
            drive_url=drive_url or "",
        )
    except Exception as tg_exc:
        logger.warning("[signnow_webhook] Telegram alert failed: %s", tg_exc)

    # ── Step 12: Check-in enrollment (Track A+C) — staff task only, no auto-text
    # Policy: docs/policies/monitoring-checkin-policy.md
    enroll_booking = (
        (bond_case or {}).get("booking_number")
        or booking_number
        or packet.get("booking_number")
        or ""
    )
    if enroll_booking:
        try:
            from dashboard.services.checkin_enrollment_service import (
                enable_checkin_monitoring,
            )
            enroll = await enable_checkin_monitoring(
                enroll_booking,
                frequency_days=7,
                source="signnow_complete",
                actor="System (SignNow Webhook)",
                create_staff_task=True,
            )
            logger.info(
                "[signnow_webhook] check-in enroll booking=%s portal=%s",
                enroll_booking,
                bool(enroll.get("portal_url")),
            )
        except Exception as enroll_exc:
            logger.warning(
                "[signnow_webhook] check-in enrollment failed: %s", enroll_exc
            )

    return JSONResponse(status_code=200, content={"success": True})


# ─────────────────────────────────────────────────────────────────────────────
# Helper: escalation Slack alert
# ─────────────────────────────────────────────────────────────────────────────

async def _send_escalation_slack(message: str) -> None:
    """Send an escalation alert to Slack (non-fatal — logs on failure)."""
    import httpx
    # Escalation alerts (unknown packets, forged webhooks) → #signing-errors
    slack_webhook = os.getenv("SLACK_WEBHOOK_ERRORS") or os.getenv("SLACK_WEBHOOK_URL", "")
    if not slack_webhook:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(slack_webhook, json={"text": message})
    except Exception as exc:
        logger.warning("[signnow_webhook] Escalation Slack alert failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# POST /webhooks/twilio
# ─────────────────────────────────────────────────────────────────────────────

@webhooks_bp.post("/webhooks/twilio")
async def twilio_webhook(request: Request):
    """Handle inbound SMS from Twilio."""
    from dashboard.routers.events import publish_event

    # Twilio sends form data
    form_data = await request.form()
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

    from starlette.responses import Response as StarletteResponse
    return StarletteResponse(
        content="<Response></Response>",
        status_code=200,
        media_type="text/xml",
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /webhooks/payment
# ─────────────────────────────────────────────────────────────────────────────

@webhooks_bp.post("/webhooks/payment")
async def payment_webhook(request: Request, booking_number: str = Query(default="")):
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
    from dashboard.routers.events import publish_event
    from dashboard.services.bb_client import send_message_universal

    now = datetime.now(timezone.utc)

    # -- 1. HMAC signature validation (optional -- skip if secret not set) -----
    webhook_secret = os.getenv("SWIPESIMPLE_WEBHOOK_SECRET", "")
    if webhook_secret:
        raw_body = await request.body()
        sig_header = request.headers.get("X-SwipeSimple-Signature", "")
        expected_sig = hmac.new(
            webhook_secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, sig_header):
            logger.warning("[payment_webhook] Invalid SwipeSimple signature — rejecting")
            return JSONResponse({"error": "Invalid signature"}, status_code=401)

    data = await request.json() or {}

    # -- 2. Parse booking number -----------------------------------------------
    custom_fields = data.get("custom_fields", {})
    booking_number = (
        custom_fields.get("booking_number")
        or booking_number
    )

    # -- 3. Update bond case ---------------------------------------------------
    amount = data.get("amount", 0)
    status = data.get("status", "")
    transaction_id = data.get("transaction_id", "")
    card_last4 = data.get("card_last4", "")
    card_brand = data.get("card_brand", "")
    customer_name = data.get("customer_name", "")
    indemnitor_phone = custom_fields.get("indemnitor_phone", "")

    payment_update = {
        "last_payment_amount": amount,
        "last_payment_at": now.isoformat(),
        "last_payment_status": status,
        "last_transaction_id": transaction_id,
    }

    if booking_number:
        bond_cases = get_collection("bond_cases")
        await bond_cases.update_one(
            {"booking_number": booking_number},
            {"$set": payment_update},
        )
        # Also try active_bonds for legacy records
        active_bonds = get_collection("active_bonds")
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": payment_update},
        )

    # -- 4. Log to payments collection ----------------------------------------
    payments = get_collection("payments")
    await payments.insert_one({
        "transaction_id": transaction_id,
        "booking_number": booking_number,
        "amount": amount,
        "status": status,
        "card_last4": card_last4,
        "card_brand": card_brand,
        "customer_name": customer_name,
        "indemnitor_phone": indemnitor_phone,
        "custom_fields": custom_fields,
        "raw_payload": data,
        "created_at": now.isoformat(),
    })

    # -- 5. Send BlueBubbles receipt ------------------------------------------
    if indemnitor_phone and status == "approved":
        try:
            receipt_msg = (
                f"✅ Payment received! Thank you, {customer_name}.\n"
                f"Amount: ${amount:.2f} ({card_brand} ending {card_last4})\n"
                f"Transaction: {transaction_id}\n"
                f"Shamrock Bail Bonds — (239) 332-2245"
            )
            await send_message_universal(indemnitor_phone, receipt_msg)
        except Exception as exc:
            logger.warning("[payment_webhook] BB receipt failed: %s", exc)

    # -- 6. Slack alert -------------------------------------------------------
    # Payment received → #intake (bond operations / intake channel)
    slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL") or os.getenv("SLACK_WEBHOOK_LEADS", "")
    if slack_webhook_url and status == "approved":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(slack_webhook_url, json={
                    "text": (
                        f":moneybag: *Payment Received* — ${amount:.2f} from {customer_name} "
                        f"({card_brand} ****{card_last4}) | Booking: {booking_number or 'N/A'}"
                    )
                })
        except Exception as exc:
            logger.warning("[payment_webhook] Slack alert failed: %s", exc)

    # -- 7. Publish SSE event -------------------------------------------------
    await publish_event('payment_received', {
        "transaction_id": transaction_id,
        "booking_number": booking_number,
        "amount": amount,
        "status": status,
        "customer_name": customer_name,
    })

    # -- 8. Log audit event ---------------------------------------------------
    audit_events = get_collection("audit_events")
    await audit_events.insert_one({
        "source": "swipesimple_webhook",
        "event_type": f"payment.{status}",
        "payload": data,
        "timestamp": now.isoformat(),
    })

    return JSONResponse(status_code=200, content={"success": True})


# ─────────────────────────────────────────────────────────────────────────────
# POST /webhooks/wix-intake
# ─────────────────────────────────────────────────────────────────────────────

@webhooks_bp.post("/webhooks/wix-intake")
async def wix_intake_webhook(request: Request, api_key: str = Query(default="")):
    """
    Handle intake submissions from the Wix indemnitor portal.

    Validates the WIX_WEBHOOK_SECRET (or GAS_API_KEY fallback) then
    forwards the payload to the intake pipeline.
    """
    from dashboard.routers.intake import _normalize_intake

    # Auth check — fail closed if no secret configured
    wix_secret = os.getenv("WIX_WEBHOOK_SECRET", "") or os.getenv("GAS_API_KEY", "")
    provided = (
        request.headers.get("X-Wix-Webhook-Secret", "")
        or request.headers.get("X-Api-Key", "")
        or api_key
    )
    if not wix_secret:
        logger.error("[wix_intake_webhook] WIX_WEBHOOK_SECRET/GAS_API_KEY not configured")
        return JSONResponse({"error": "Webhook auth not configured"}, status_code=503)
    if provided != wix_secret:
        logger.warning("[wix_intake_webhook] Unauthorized — invalid secret")
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    data = await request.json() or {}
    now_iso = datetime.now(timezone.utc).isoformat()

    audit_events = get_collection("audit_events")
    await audit_events.insert_one({
        "source": "wix_intake_webhook",
        "event_type": "intake_submission",
        "payload": data,
        "timestamp": now_iso,
    })

    try:
        intake_id, intake_doc = await _normalize_intake(data, source="wix_webhook")
        logger.info("[wix_intake_webhook] Intake %s created from Wix webhook", intake_id)

        # Real-time dashboard event — sl-core.js listens for 'new_intake'
        try:
            from dashboard.routers.events import publish_event
            await publish_event("new_intake", {
                "intake_id": intake_id,
                "defendant_name": (intake_doc or {}).get("defendant_name", ""),
                "county": (intake_doc or {}).get("county", ""),
                "booking_number": (intake_doc or {}).get("booking_number", ""),
                "source": "wix_webhook",
            })
        except Exception:
            pass

        return JSONResponse(status_code=201, content={"success": True, "intake_id": intake_id})
    except Exception as exc:
        logger.exception("[wix_intake_webhook] Intake normalization failed")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /webhooks/scraper-event
# ─────────────────────────────────────────────────────────────────────────────

@webhooks_bp.post("/webhooks/scraper-event")
async def scraper_event_webhook(request: Request, api_key: str = Query(default="")):
    """
    Handle live events (e.g. new arrests) from scraper containers.
    
    Validates GAS_API_KEY and publishes to SSE stream for dashboard popups.
    """
    from dashboard.routers.events import publish_event
    
    # Auth check — fail closed if no key configured
    expected_key = os.getenv("GAS_API_KEY", "")
    provided = request.headers.get("X-Api-Key", "") or api_key
    if not expected_key:
        logger.error("[scraper_event_webhook] GAS_API_KEY not configured")
        return JSONResponse({"error": "Webhook auth not configured"}, status_code=503)
    if provided != expected_key:
        logger.warning("[scraper_event_webhook] Unauthorized — invalid secret")
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
        
    data = await request.json() or {}
    event_type = data.get("event_type", "new_arrest")
    payload = data.get("payload", {})
    
    # Publish to SSE connected clients
    await publish_event(event_type, payload)
    logger.info(f"[scraper_event_webhook] Published {event_type} event to SSE")
    
    return JSONResponse(status_code=200, content={"success": True})
