"""
ShamrockLeads — BlueBubbles Bond Document Delivery
===================================================
Send bond documents, receipts, and court paperwork directly via iMessage
using BlueBubbles attachment sending capabilities.

Use Cases
---------
  1. Send signed bond agreement PDF to indemnitor after signing
  2. Send court date reminder with attached court notice PDF
  3. Send payment receipt after a payment is processed
  4. Send "walk-out" instructions when defendant is about to be released
  5. Send indemnitor agreement / power of attorney for e-signature

Architecture
------------
  Documents are uploaded to a temporary public URL (S3 or VPS static),
  then BlueBubbles downloads and sends them as iMessage attachments.
  This avoids the need to stream files through the VPS.

Endpoints
---------
  POST   /api/bb-docs/send-pdf          — Send a PDF file via iMessage
  POST   /api/bb-docs/send-receipt      — Send a payment receipt
  POST   /api/bb-docs/send-court-notice — Send a court notice/subpoena
  POST   /api/bb-docs/send-signing-link — Send a SignNow signing link
"""
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from quart import Blueprint, jsonify, request

from dashboard.api.bb_private_api import BlueBubblesClient
from dashboard.extensions import BB_SERVERS, get_collection, format_phone

logger = logging.getLogger(__name__)

bb_docs_bp = Blueprint("bb_document_delivery", __name__)

# VPS base URL for serving temporary document files
_VPS_STATIC_URL = os.getenv("BB_WEBHOOK_PUBLIC_URL", "").rstrip("/")
_SIGNNOW_BASE = "https://app.signnow.com/webapp/document/"


async def _send_document_via_bb(
    phone: str,
    document_url: str,
    filename: str,
    caption: Optional[str] = None,
) -> dict:
    """Send a document attachment via BlueBubbles iMessage.

    If a caption is provided, sends the caption text first, then the attachment.
    """
    phone = format_phone(phone)
    chat_guid = f"any;-;{phone}"

    bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
    if not bb_server:
        return {"success": False, "error": "No BlueBubbles server configured"}

    bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"])

    results = {}

    # Send caption first if provided
    if caption:
        caption_result = await bb_client.send_human_like(chat_guid, caption, typing_delay=1.5)
        results["caption"] = caption_result

    # Send the attachment
    attachment_result = await bb_client.send_attachment_url(chat_guid, document_url, filename)
    results["attachment"] = attachment_result

    success = attachment_result.get("success", False)

    # Log to MongoDB
    docs_coll = get_collection("document_deliveries")
    await docs_coll.insert_one({
        "phone": phone,
        "document_url": document_url,
        "filename": filename,
        "caption": caption,
        "channel": "imessage",
        "status": "sent" if success else "failed",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "results": {k: v.get("success") for k, v in results.items()},
    })

    return {
        "success": success,
        "phone": phone,
        "filename": filename,
        "results": results,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@bb_docs_bp.route("/bb-docs/send-pdf", methods=["POST"])
async def api_send_pdf():
    """Send any PDF document via iMessage.

    Body:
        {
            "phone": "+12395550178",
            "document_url": "https://...",
            "filename": "Bond_Agreement_2024.pdf",
            "caption": "Here is your signed bond agreement! 📄"  (optional)
        }
    """
    try:
        data = await request.get_json(silent=True) or {}
        phone = data.get("phone", "")
        document_url = data.get("document_url", "")
        filename = data.get("filename", "document.pdf")
        caption = data.get("caption", "")

        if not phone or not document_url:
            return jsonify({"success": False, "error": "phone and document_url required"}), 400

        result = await _send_document_via_bb(phone, document_url, filename, caption or None)
        return jsonify(result)

    except Exception as e:
        logger.error("Send PDF error: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@bb_docs_bp.route("/bb-docs/send-signing-link", methods=["POST"])
async def api_send_signing_link():
    """Send a SignNow document signing link via iMessage.

    Body:
        {
            "phone": "+12395550178",
            "indemnitor_name": "Jane Smith",
            "defendant_name": "JOHN SMITH",
            "signing_url": "https://app.signnow.com/webapp/document/...",
            "document_type": "Indemnitor Agreement"  (optional)
        }
    """
    try:
        data = await request.get_json(silent=True) or {}
        phone = format_phone(data.get("phone", ""))
        indemnitor_name = data.get("indemnitor_name", "")
        defendant_name = data.get("defendant_name", "")
        signing_url = data.get("signing_url", "")
        doc_type = data.get("document_type", "Bond Documents")

        if not phone or not signing_url or not defendant_name:
            return jsonify({"success": False, "error": "phone, signing_url, defendant_name required"}), 400

        first_name = indemnitor_name.split()[0] if indemnitor_name else "there"
        message = (
            f"Hi {first_name}! Your {doc_type} for {defendant_name} are ready to sign 📝\n\n"
            f"Tap the link below to review and sign — it takes about 2 minutes:\n"
            f"{signing_url}\n\n"
            f"Reply if you have any questions! — Shamrock Bail Bonds 🍀"
        )

        bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
        if not bb_server:
            return jsonify({"success": False, "error": "No BlueBubbles server configured"}), 503

        bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"])
        chat_guid = f"any;-;{phone}"

        result = await bb_client.send_human_like(chat_guid, message, typing_delay=2.5)

        # Log
        docs_coll = get_collection("document_deliveries")
        await docs_coll.insert_one({
            "phone": phone,
            "indemnitor_name": indemnitor_name,
            "defendant_name": defendant_name,
            "signing_url": signing_url,
            "doc_type": doc_type,
            "channel": "imessage",
            "status": "sent" if result.get("success") else "failed",
            "sent_at": datetime.now(timezone.utc).isoformat(),
        })

        return jsonify({"success": result.get("success", False), "result": result})

    except Exception as e:
        logger.error("Send signing link error: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@bb_docs_bp.route("/bb-docs/send-receipt", methods=["POST"])
async def api_send_receipt():
    """Send a payment receipt via iMessage.

    Body:
        {
            "phone": "+12395550178",
            "indemnitor_name": "Jane Smith",
            "defendant_name": "JOHN SMITH",
            "amount": 500.00,
            "payment_date": "2024-03-15",
            "receipt_url": "https://..."  (optional — attach PDF receipt)
        }
    """
    try:
        data = await request.get_json(silent=True) or {}
        phone = format_phone(data.get("phone", ""))
        indemnitor_name = data.get("indemnitor_name", "")
        defendant_name = data.get("defendant_name", "")
        amount = float(data.get("amount", 0))
        payment_date = data.get("payment_date", datetime.now().strftime("%B %d, %Y"))
        receipt_url = data.get("receipt_url", "")

        if not phone or not defendant_name or not amount:
            return jsonify({"success": False, "error": "phone, defendant_name, amount required"}), 400

        first_name = indemnitor_name.split()[0] if indemnitor_name else "there"
        message = (
            f"Hi {first_name}! ✅ We received your payment of ${amount:,.2f} "
            f"on {payment_date} for {defendant_name}'s bond. "
            f"Thank you! Reply anytime if you need anything. — Shamrock Bail Bonds 🍀"
        )

        bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
        if not bb_server:
            return jsonify({"success": False, "error": "No BlueBubbles server configured"}), 503

        bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"])
        chat_guid = f"any;-;{phone}"

        result = await bb_client.send_human_like(chat_guid, message, typing_delay=2.0)

        # Optionally attach PDF receipt
        if receipt_url and result.get("success"):
            await bb_client.send_attachment_url(chat_guid, receipt_url, "Payment_Receipt.pdf")

        return jsonify({"success": result.get("success", False), "result": result})

    except Exception as e:
        logger.error("Send receipt error: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
