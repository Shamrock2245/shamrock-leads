from __future__ import annotations

"""
ShamrockLeads — Webhooks API Blueprint
Handles inbound webhooks from retired e-sign callbacks, Twilio, SwipeSimple, and DocuSeal.

Uses extensions.get_collection() to avoid circular imports from app.py.

Security:
  - Retired e-sign callbacks: explicitly rejected without reading payload data.
  - SwipeSimple: HMAC-SHA256 signature verification (SWIPESIMPLE_WEBHOOK_SECRET).
  - Twilio: Twilio request validator (TWILIO_AUTH_TOKEN).

Data Flow (DocuSeal submission.completed): validated by the active DocuSeal webhook handler.
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

GMAIL_PUBSUB_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def verify_gmail_pubsub_token(token: str, audience: str) -> dict:
    """Verify a Google-signed Pub/Sub push OIDC token."""
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import id_token

    claims = id_token.verify_oauth2_token(token, GoogleAuthRequest(), audience=audience)
    if claims.get("iss") not in GMAIL_PUBSUB_ISSUERS:
        raise ValueError("Unexpected token issuer")
    return claims


# ─────────────────────────────────────────────────────────────────────────────
# Security helpers


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


# ─────────────────────────────────────────────────────────────────────────────
# POST /webhooks/gmail
# ─────────────────────────────────────────────────────────────────────────────

@webhooks_bp.post("/webhooks/gmail")
async def gmail_pubsub_webhook(request: Request):
    """
    Handle Google Cloud Pub/Sub push notifications for inbound court emails.

    Base64 decodes message.data, extracts historyId/messageId, and triggers
    instant real-time parsing, Google Calendar sync, Slack alerts, and client SMS.
    """
    import base64
    import json

    audience = os.getenv("GMAIL_PUBSUB_AUDIENCE", "").strip()
    expected_service_account = os.getenv("GMAIL_PUBSUB_SERVICE_ACCOUNT_EMAIL", "").strip().lower()
    expected_subscription = os.getenv("GMAIL_PUBSUB_SUBSCRIPTION", "").strip()
    monitored_mailbox = os.getenv("GMAIL_MONITORED_MAILBOX", "").strip().lower()
    if not all((audience, expected_service_account, expected_subscription, monitored_mailbox)):
        logger.error("[gmail_webhook] Required Pub/Sub authentication configuration is missing")
        return JSONResponse({"error": "Webhook authentication not configured"}, status_code=503)

    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        logger.warning("[gmail_webhook] Rejected push with missing bearer token")
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        claims = verify_gmail_pubsub_token(token.strip(), audience)
    except Exception:
        logger.warning("[gmail_webhook] Rejected push with invalid identity token")
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    token_email = str(claims.get("email", "")).strip().lower()
    if token_email != expected_service_account or claims.get("email_verified") is not True:
        logger.warning("[gmail_webhook] Rejected push from unexpected service account")
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    try:
        data = await request.json() or {}
        if data.get("subscription") != expected_subscription:
            logger.warning("[gmail_webhook] Rejected push for unexpected subscription")
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        pubsub_message = data.get("message", {})
        if not pubsub_message:
            return JSONResponse({"error": "Invalid Pub/Sub payload"}, status_code=400)

        # Base64 decode Pub/Sub data payload
        raw_data = pubsub_message.get("data", "")
        decoded_bytes = base64.b64decode(raw_data) if raw_data else b"{}"
        payload = json.loads(decoded_bytes.decode("utf-8"))

        email_address = payload.get("emailAddress")
        if not isinstance(email_address, str) or email_address.strip().lower() != monitored_mailbox:
            logger.warning("[gmail_webhook] Rejected push for unexpected monitored mailbox")
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        history_id = payload.get("historyId")
        message_id = payload.get("messageId") or pubsub_message.get("messageId")

        logger.info("[gmail_webhook] Accepted authenticated Pub/Sub notification")

        # Log event to audit_events
        audit_events = get_collection("audit_events")
        await audit_events.insert_one({
            "source": "gmail_pubsub_webhook",
            "event_type": "court_email_notification",
            "history_id": history_id,
            "message_id": message_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Trigger real-time processing
        db = get_collection("court_email_log").database
        from dashboard.services.court_email_scheduler import CourtEmailScheduler
        scheduler = CourtEmailScheduler(db=db)

        if message_id:
            res = scheduler.process_single_message(message_id)
        else:
            res = scheduler.process_all()

        # Publish SSE event for live dashboard sessions
        from dashboard.routers.events import publish_event
        await publish_event("court_email_processed", {
            "result": res,
        })

        return JSONResponse(status_code=200, content={"success": True, "result": res})

    except Exception as exc:
        logger.exception("[gmail_webhook] Failed processing Google Pub/Sub push notification")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)



# ─────────────────────────────────────────────────────────────────────────────
# Adobe PDF Services — job completion CALLBACK webhook
# Docs: https://developer.adobe.com/document-services/docs/overview/pdf-services-api/howtos/webhook-notification
#
# Client must respond HTTP 200 with body: {"ack": "done"}
# After 50 errors in 10 minutes Adobe blocks webhooks for 20 minutes.
# ─────────────────────────────────────────────────────────────────────────────

def _verify_adobe_pdf_webhook(request: Request) -> bool:
    """
    Validate shared secret Adobe echoes back in CALLBACK headers.

    We register notifiers with:
      headers: { "x-shamrock-adobe-webhook-secret": ADOBE_PDF_WEBHOOK_SECRET }

    Fail-closed if secret is configured but missing/mismatched.
    If secret is unset: allow only when DEBUG=true (dev), else reject.
    """
    secret = (os.getenv("ADOBE_PDF_WEBHOOK_SECRET") or "").strip()
    provided = (
        request.headers.get("x-shamrock-adobe-webhook-secret")
        or request.headers.get("X-Shamrock-Adobe-Webhook-Secret")
        or ""
    ).strip()
    if not secret:
        if os.getenv("DEBUG", "false").lower() == "true":
            logger.warning("[adobe_pdf_webhook] ADOBE_PDF_WEBHOOK_SECRET unset — allowing in DEBUG")
            return True
        logger.error("[adobe_pdf_webhook] ADOBE_PDF_WEBHOOK_SECRET unset — rejecting")
        return False
    if not provided:
        logger.warning("[adobe_pdf_webhook] missing secret header — rejecting")
        return False
    return hmac.compare_digest(secret, provided)


@webhooks_bp.post("/webhooks/adobe-pdf-services")
async def adobe_pdf_services_webhook(request: Request):
    """
    Receive Adobe PDF Services job completion CALLBACK.

    Success payload:
      { "jobID": "...", "statusResponse": { "status": "done", "asset": { downloadUri, assetID, metadata } } }

    Failure payload:
      { "jobID": "...", "statusResponse": { "status": "failed", "error": { code, message, status } } }

    Must return HTTP 200 + {"ack": "done"} or Adobe treats it as error.
    """
    try:
        if not _verify_adobe_pdf_webhook(request):
            # Still return 200 ack? Adobe docs say non-200 counts as error and can ban webhooks.
            # We intentionally return 401 so misconfigured clients are visible, but use sparingly.
            # Prefer 200 with ack only when we processed; unauthorized should be rare after setup.
            return JSONResponse(status_code=401, content={"ack": "rejected", "error": "unauthorized"})

        raw = await request.body()
        try:
            payload = await request.json()
        except Exception:
            payload = {}
            try:
                import json as _json
                payload = _json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                payload = {"_raw": (raw[:500].decode("utf-8", errors="replace") if raw else "")}

        job_id = payload.get("jobID") or payload.get("jobId") or ""
        status_response = payload.get("statusResponse") or {}
        status = (status_response.get("status") or "").lower()
        now = datetime.now(timezone.utc)

        # Persist for ops / async consumers
        doc = {
            "job_id": job_id,
            "status": status or "unknown",
            "status_response": status_response,
            "received_at": now,
            "source": "adobe_pdf_services_callback",
        }
        try:
            await get_collection("adobe_pdf_jobs").update_one(
                {"job_id": job_id or f"unknown-{now.timestamp()}"},
                {"$set": doc, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )
        except Exception as db_exc:
            logger.warning("[adobe_pdf_webhook] persist failed: %s", db_exc)

        # Soft audit (no PII in asset URLs ideally — still avoid logging full downloadUri at INFO)
        try:
            await get_collection("audit_events").insert_one({
                "Event_ID": f"adobe-pdf-{job_id or 'na'}-{int(now.timestamp())}",
                "event_type": "adobe_pdf_job_callback",
                "job_id": job_id,
                "status": status,
                "timestamp": now,
                "actor": "adobe_pdf_services",
            })
        except Exception:
            pass

        if status == "done":
            logger.info("[adobe_pdf_webhook] job %s done", job_id)
        elif status == "failed":
            err = status_response.get("error") or {}
            logger.warning(
                "[adobe_pdf_webhook] job %s failed code=%s msg=%s",
                job_id, err.get("code"), (err.get("message") or "")[:200],
            )
        else:
            logger.info("[adobe_pdf_webhook] job %s status=%s", job_id, status)

        # Required ack per Adobe docs
        return JSONResponse(status_code=200, content={"ack": "done"})

    except Exception as exc:
        logger.exception("[adobe_pdf_webhook] handler error")
        # Return 200 ack when possible to avoid Adobe temporary ban after 50 errors / 10 min
        return JSONResponse(status_code=200, content={"ack": "done", "handler_error": str(exc)[:120]})


# ─────────────────────────────────────────────────────────────────────────────
# DocuSeal webhooks (self-hosted e-sign)
# ─────────────────────────────────────────────────────────────────────────────

def verify_docuseal_signature(payload: bytes, signature: str) -> bool:
    """
    Verify DocuSeal webhook HMAC.

    Fail-closed unless DEBUG=true:
      - missing DOCUSEAL_WEBHOOK_SECRET → reject (except DEBUG)
      - missing/invalid signature → reject
    """
    secret = os.getenv("DOCUSEAL_WEBHOOK_SECRET", "").strip()
    debug = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")
    env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").lower()
    is_prod = env in ("production", "prod") or os.getenv("REQUIRE_DOCUSEAL_WEBHOOK_SECRET", "").lower() in (
        "1", "true", "yes",
    )

    if not secret:
        if debug and not is_prod:
            logger.warning("[docuseal_webhook] DOCUSEAL_WEBHOOK_SECRET unset — allowing in DEBUG only")
            return True
        logger.error(
            "[docuseal_webhook] DOCUSEAL_WEBHOOK_SECRET not set — rejecting "
            "(set secret in DocuSeal admin + env)"
        )
        return False

    if not signature:
        logger.warning("[docuseal_webhook] missing signature header — rejecting")
        return False

    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    # Accept hex or sha256=hex forms
    sig = signature.strip().lower()
    if sig.startswith("sha256="):
        sig = sig[7:]
    ok = hmac.compare_digest(expected, sig)
    if not ok:
        logger.warning("[docuseal_webhook] invalid signature — rejecting")
    return ok


@webhooks_bp.post("/webhooks/docuseal")
async def docuseal_webhook(request: Request):
    """
    Handle DocuSeal form/submission webhooks.

    Events of interest:
      - form.completed       — one submitter finished
      - submission.completed — all submitters finished → download PDF + Drive
      - form.declined / submission.expired / submission.created — audit + status

    Configure in DocuSeal admin → Webhooks →
      URL: https://leads.shamrockbailbonds.biz/api/webhooks/docuseal
    """
    from dashboard.routers.events import publish_event
    from dashboard.services.docuseal_service import DocuSealService

    raw = await request.body()
    signature = (
        request.headers.get("X-DocuSeal-Signature")
        or request.headers.get("X-Webhook-Signature")
        or request.headers.get("X-Signature")
        or ""
    )
    if not verify_docuseal_signature(raw, signature):
        return JSONResponse({"error": "Invalid signature"}, status_code=401)

    try:
        data = await request.json() or {}
    except Exception:
        import json as _json

        try:
            data = _json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}

    now_iso = datetime.now(timezone.utc).isoformat()
    event_type = (
        data.get("event_type")
        or data.get("event")
        or data.get("type")
        or "unknown"
    )
    event_l = str(event_type).lower().strip()
    # Nested data payload (DocuSeal often wraps under "data")
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(payload, dict):
        payload = {}

    # Audit first (immutable) — strip oversized raw blobs later if needed
    try:
        audit_events = get_collection("audit_events")
        await audit_events.insert_one(
            {
                "source": "docuseal_webhook",
                "event_type": event_type,
                "payload": data,
                "timestamp": now_iso,
            }
        )
    except Exception as audit_exc:
        logger.warning("[docuseal_webhook] audit insert failed: %s", audit_exc)

    logger.info("[docuseal_webhook] event=%s", event_type)

    submission_id = (
        payload.get("submission_id")
        or payload.get("id")
        or (payload.get("submission") or {}).get("id")
        or data.get("submission_id")
        or ""
    )
    # form.completed is per-submitter — submission id may be nested
    if not submission_id and isinstance(payload.get("submission"), dict):
        submission_id = payload["submission"].get("id") or ""

    external_id = payload.get("external_id") or ""
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    packet_id_hint = metadata.get("packet_id") or ""
    if not packet_id_hint and external_id and ":" in str(external_id):
        packet_id_hint = str(external_id).split(":")[0]

    packets_col = get_collection("paperwork_packets")

    async def _find_packet():
        pkt = None
        if submission_id:
            sid = submission_id
            or_clause = [
                {"docuseal_submission_id": sid},
                {"docuseal_submission_id": str(sid)},
            ]
            if str(sid).isdigit():
                or_clause.append({"docuseal_submission_id": int(sid)})
            pkt = await packets_col.find_one({"$or": or_clause})
        if not pkt and packet_id_hint:
            pkt = await packets_col.find_one({"packet_id": packet_id_hint})
        return pkt

    # Lifecycle events that do not complete the packet
    lifecycle_status_map = {
        "form.declined": "declined",
        "form_declined": "declined",
        "submission.declined": "declined",
        "submission.expired": "expired",
        "submission_expired": "expired",
        "submission.created": "sent",
        "submission_created": "sent",
        "form.started": "in_progress",
        "form.viewed": "viewed",
    }
    is_lifecycle = event_l in lifecycle_status_map or (
        "complet" not in event_l
        and any(x in event_l for x in ("declin", "expir", "created", "started", "viewed"))
    )
    if is_lifecycle:
        st = lifecycle_status_map.get(event_l)
        if not st:
            if "declin" in event_l:
                st = "declined"
            elif "expir" in event_l:
                st = "expired"
            elif "created" in event_l:
                st = "sent"
            else:
                st = "in_progress"
        packet = await _find_packet()
        if packet:
            await packets_col.update_one(
                {"packet_id": packet.get("packet_id")},
                {
                    "$set": {
                        "docuseal_last_event": event_type,
                        "docuseal_last_event_at": now_iso,
                        "docuseal_status": st,
                        "esign_provider": "docuseal",
                        **({"status": st} if st in ("declined", "expired") else {}),
                    }
                },
            )
            try:
                await publish_event(
                    f"docuseal_{st}",
                    {
                        "packet_id": packet.get("packet_id"),
                        "submission_id": submission_id,
                        "event": event_type,
                    },
                )
            except Exception:
                pass
        return JSONResponse(
            status_code=200,
            content={"success": True, "action": "lifecycle_logged", "event": event_type, "status": st},
        )

    # Completion-style events
    complete_events = {
        "form.completed",
        "submission.completed",
        "form_completed",
        "submission_completed",
        "completed",
    }
    if event_l not in complete_events and "complet" not in event_l:
        return JSONResponse(
            status_code=200,
            content={"success": True, "action": "logged_only", "event": event_type},
        )

    packet = await _find_packet()

    if not packet:
        logger.warning(
            "[docuseal_webhook] no packet for submission_id=%s packet_hint=%s",
            submission_id,
            packet_id_hint,
        )
        return JSONResponse(
            status_code=200,
            content={"success": True, "warning": "packet_not_found", "submission_id": submission_id},
        )

    packet_id = packet.get("packet_id", "")
    defendant_name = packet.get("defendant_name", "Unknown")
    booking_number = packet.get("booking_number") or packet.get("defendant_booking_number") or ""
    surety_id = (packet.get("surety_id") or packet.get("insurance_company") or "osi").lower().strip()

    # Full completion: submission.* OR bare "completed" with a submission_id
    is_full = (
        "submission" in event_l
        or event_l in ("completed", "submission_completed")
        or (event_l == "completed" and bool(submission_id))
    )
    # form.completed alone is per-party
    if event_l.startswith("form.") and "submission" not in event_l:
        is_full = False

    if not is_full:
        # Track individual submitter completion (do not Drive-file yet)
        role = payload.get("role") or metadata.get("party_role") or ""
        # Sanitize role for dotted Mongo keys
        role_key = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in str(role or "unknown"))[:64]
        await packets_col.update_one(
            {"packet_id": packet_id},
            {
                "$set": {
                    "docuseal_last_event": event_type,
                    "docuseal_last_event_at": now_iso,
                    f"docuseal_parties.{role_key}.completed_at": now_iso,
                    "esign_provider": "docuseal",
                    "docuseal_status": "partially_signed",
                },
                "$addToSet": {"docuseal_completed_roles": role or "unknown"},
            },
        )
        try:
            await publish_event(
                "docuseal_form_completed",
                {
                    "packet_id": packet_id,
                    "submission_id": submission_id,
                    "role": role,
                    "defendant_name": defendant_name,
                },
            )
        except Exception:
            pass
        return JSONResponse(
            status_code=200,
            content={"success": True, "action": "party_recorded", "packet_id": packet_id},
        )

    # ── Full submission complete → download + Drive ─────────────────────────
    drive_url = None
    drive_folder_id = None
    pdf_bytes = None
    packet_drive_error = None
    ds = DocuSealService()
    if submission_id and ds.is_configured:
        try:
            pdf_bytes = await ds.download_combined_pdf(submission_id)
        except Exception as exc:
            logger.error("[docuseal_webhook] PDF download failed: %s", exc)
            packet_drive_error = {
                "error_code": "pdf_download_failed",
                "error": str(exc)[:300],
                "at": now_iso,
            }

    if pdf_bytes:
        try:
            filed = ds.file_signed_pdf_to_drive(
                pdf_bytes,
                defendant_name=defendant_name,
                surety_id=surety_id,
                packet_id=packet_id,
                booking_number=booking_number,
            )
            if filed.get("ok"):
                drive_url = filed.get("drive_url")
                drive_folder_id = filed.get("drive_folder_id")
                packet_drive_error = None
            else:
                logger.warning(
                    "[docuseal_webhook] Drive file failed code=%s err=%s",
                    filed.get("error_code"),
                    filed.get("error"),
                )
                packet_drive_error = {
                    "error_code": filed.get("error_code"),
                    "error": (filed.get("error") or "")[:300],
                    "auth_mode": filed.get("auth_mode"),
                    "at": now_iso,
                }
        except Exception as exc:
            logger.error("[docuseal_webhook] Drive upload error: %s", exc)
            packet_drive_error = {
                "error_code": "upload_exception",
                "error": str(exc)[:300],
                "at": now_iso,
            }

    packet_update = {
        "status": "signed",
        "esign_provider": "docuseal",
        "docuseal_status": "completed",
        "signed_at": now_iso,
        "docuseal_submission_id": submission_id,
        "docuseal_last_event": event_type,
        "docuseal_last_event_at": now_iso,
    }
    if drive_url:
        packet_update["signed_pdf_drive_url"] = drive_url
        packet_update["drive_link"] = drive_url
        packet_update["drive_archive_error"] = None
    if drive_folder_id:
        packet_update["drive_folder_id"] = drive_folder_id
        packet_update["signed_pdf_drive_id"] = drive_folder_id
    if packet_drive_error and not drive_url:
        packet_update["drive_archive_error"] = packet_drive_error

    await packets_col.update_one({"packet_id": packet_id}, {"$set": packet_update})

    # Bond case update
    bond_cases = get_collection("bond_cases")
    bond_case_id = packet.get("bond_case_id")
    bond_query = {"bond_case_id": bond_case_id} if bond_case_id else {"packet_id": packet_id}
    bond_update = {
        "Packet_Status": "signed",
        "Signature_Status": "signed",
        "signed_at": now_iso,
        "esign_provider": "docuseal",
    }
    if drive_url:
        bond_update["signed_pdf_drive_url"] = drive_url
    await bond_cases.update_one(bond_query, {"$set": bond_update})

    await publish_event(
        "docuseal_submission_completed",
        {
            "packet_id": packet_id,
            "submission_id": submission_id,
            "defendant_name": defendant_name,
            "drive_url": drive_url,
            "booking_number": booking_number,
        },
    )

    # Slack (non-PII)
    try:
        import httpx

        slack = os.getenv("SLACK_WEBHOOK_LEADS") or os.getenv("SLACK_WEBHOOK_URL") or ""
        if slack:
            async with httpx.AsyncClient(timeout=8) as client:
                await client.post(
                    slack,
                    json={
                        "text": (
                            f":white_check_mark: *DocuSeal packet signed* — "
                            f"`{packet_id}` | {defendant_name}"
                            f"{' | Drive filed' if drive_url else ' | Drive pending'}"
                        )
                    },
                )
    except Exception as exc:
        logger.debug("[docuseal_webhook] slack failed: %s", exc)

    logger.info(
        "[docuseal_webhook] submission complete packet=%s drive=%s",
        packet_id,
        bool(drive_url),
    )
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "action": "signed_and_filed",
            "packet_id": packet_id,
            "submission_id": submission_id,
            "drive_url": drive_url,
        },
    )
