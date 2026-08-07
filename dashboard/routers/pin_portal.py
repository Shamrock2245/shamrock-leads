"""
ShamrockLeads — Mobile PIN Portal Router (paperwork.shamrockbailbonds.biz)
========================================================================
Serves mobile PWA UI and fast 6-digit OTP PIN authentication for indemnitor e-signing.
Bypasses traditional passwords; clients log in using a 6-digit PIN sent via SMS / Telegram.
"""
import os
import random
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from dashboard.deps import get_collection

logger = logging.getLogger(__name__)

pin_portal_router = APIRouter(prefix="/api/portal", tags=["pin_portal"])
portal_page_router = APIRouter(tags=["pin_portal_pages"])

# 6-digit PIN OTP store in MongoDB `portal_pins`
# pin -> {phone, intake_id, booking_number, expires_at}


class SendPinRequest(BaseModel):
    phone: str
    booking_number: Optional[str] = None
    intake_id: Optional[str] = None


class VerifyPinRequest(BaseModel):
    phone: str
    pin: str


@pin_portal_router.post("/send-pin")
async def send_portal_pin(req: SendPinRequest):
    """
    Generate & dispatch a 6-digit OTP PIN to client's phone via iMessage / SMS.
    """
    clean_phone = "".join(ch for ch in req.phone if ch.isdigit())[-10:]
    if not clean_phone or len(clean_phone) < 10:
        return JSONResponse({"success": False, "error": "Invalid 10-digit phone number"}, status_code=400)

    # Master dev PIN for testing
    otp_pin = f"{random.randint(100000, 999999)}"
    if req.phone == "2395550199":
        otp_pin = "224545"

    pins_col = get_collection("portal_pins")
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=15)).isoformat()

    pin_doc = {
        "phone": clean_phone,
        "pin": otp_pin,
        "booking_number": req.booking_number or "",
        "intake_id": req.intake_id or "",
        "created_at": now.isoformat(),
        "expires_at": expires_at,
        "verified": False,
    }

    await pins_col.update_one(
        {"phone": clean_phone},
        {"$set": pin_doc},
        upsert=True,
    )

    logger.info("[PIN Portal] Generated PIN %s for phone %s", otp_pin, clean_phone)

    # Send OTP PIN strictly via BlueBubbles (send_message_universal) — NO Twilio fallback
    try:
        from dashboard.services.bb_client import send_message_universal
        msg = f"Your Shamrock Bail Bonds e-sign verification PIN is: {otp_pin}. Valid for 15 minutes."
        send_res = await send_message_universal(clean_phone, msg)
        logger.info("[PIN Portal] send_message_universal result: %s", send_res)
        
        sent_ok = send_res.get("sent", False) or send_res.get("queued", False)
        if not sent_ok:
            return JSONResponse({
                "success": False,
                "error": f"Messaging unavailable: {send_res.get('error', 'send failed')}",
                "channel": send_res.get("channel", "failed")
            }, status_code=503)

        return {
            "success": True,
            "phone": clean_phone,
            "channel": send_res.get("channel", "imessage"),
            "queued": send_res.get("queued", False),
            "expires_in_minutes": 15,
            "debug_pin": otp_pin if os.getenv("ENV") != "production" else None
        }
    except Exception as exc:
        logger.error("[PIN Portal] Send PIN exception: %s", exc)
        return JSONResponse({"success": False, "error": f"Send error: {exc}"}, status_code=500)


@pin_portal_router.post("/verify-pin")
async def verify_portal_pin(req: VerifyPinRequest):
    """
    Verify 6-digit OTP PIN and return session token + packet deep-link signing URL.
    """
    clean_phone = "".join(ch for ch in req.phone if ch.isdigit())[-10:]
    input_pin = req.pin.strip()

    # Look up packet signing link helper
    async def _resolve_signing_link(phone_str: str, booking: str = "", intake: str = "") -> str:
        packets = get_collection("paperwork_packets")
        query = {"$or": []}
        if booking:
            query["$or"].append({"booking_number": booking})
        if intake:
            query["$or"].append({"intake_id": intake})
        query["$or"].extend([
            {"indemnitor_phone": {"$regex": phone_str}},
            {"signer_email": {"$regex": phone_str}},
        ])
        doc = await packets.find_one(query, sort=[("created_at", -1)])
        if doc:
            return doc.get("signing_link") or (doc.get("docuseal_submitters") or [{}])[0].get("sign_url") or ""
        return ""

    # Master admin bypass
    if input_pin == "224545":
        link = await _resolve_signing_link(clean_phone)
        return {
            "success": True,
            "verified": True,
            "phone": clean_phone,
            "session_token": f"PORTAL-ADMIN-{clean_phone}",
            "role": "indemnitor",
            "signing_link": link,
        }

    pins_col = get_collection("portal_pins")
    pin_doc = await pins_col.find_one({"phone": clean_phone, "pin": input_pin})

    if not pin_doc:
        return JSONResponse({"success": False, "error": "Invalid PIN or phone number"}, status_code=401)

    # Check expiration
    expires_at_str = pin_doc.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if datetime.now(timezone.utc) > expires_at:
                return JSONResponse({"success": False, "error": "PIN has expired"}, status_code=401)
        except ValueError:
            pass

    await pins_col.update_one({"_id": pin_doc["_id"]}, {"$set": {"verified": True}})
    link = await _resolve_signing_link(
        clean_phone,
        booking=pin_doc.get("booking_number", ""),
        intake=pin_doc.get("intake_id", "")
    )

    return {
        "success": True,
        "verified": True,
        "phone": clean_phone,
        "booking_number": pin_doc.get("booking_number"),
        "intake_id": pin_doc.get("intake_id"),
        "session_token": f"PORTAL-{pin_doc['_id']}",
        "signing_link": link,
    }


@portal_page_router.get("/", response_class=HTMLResponse)
@portal_page_router.get("/done", response_class=HTMLResponse)
@portal_page_router.get("/paperwork", response_class=HTMLResponse)
@pin_portal_router.get("/portal-ui", response_class=HTMLResponse)
@pin_portal_router.get("/done", response_class=HTMLResponse)
async def get_portal_ui(request: Request):
    """
    Render lightweight mobile PWA UI for paperwork.shamrockbailbonds.biz
    and /done completion page.
    """
    if request.url.path.endswith("/done"):
        html_done = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Shamrock Bail Bonds — Document Packet Complete</title>
    <style>
        :root { --bg: #0b0f19; --card: #151c2c; --accent: #22c55e; --text: #f8fafc; --muted: #94a3b8; }
        body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); padding: 20px; text-align: center; }
        .card { background: var(--card); border-radius: 16px; padding: 32px 24px; max-width: 400px; margin: 40px auto; border: 1px solid rgba(34,197,94,0.3); box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
        .icon { font-size: 48px; margin-bottom: 12px; }
        h1 { font-size: 22px; margin-bottom: 8px; color: var(--accent); }
        p { font-size: 14px; color: var(--muted); line-height: 1.6; }
        .btn { display: inline-block; width: 100%; padding: 14px; background: var(--accent); color: #000; font-weight: 700; border-radius: 8px; text-decoration: none; margin-top: 20px; box-sizing: border-box; }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">✅</div>
        <h1>Paperwork Successfully Signed!</h1>
        <p>Thank you. Your document packet has been securely signed and submitted. Our bond agents have been alerted and are processing your release.</p>
        <p>A copy of your signed paperwork has been filed to Drive and sent to your email.</p>
        <a href="tel:2393322245" class="btn">📞 Call Office: (239) 332-2245</a>
    </div>
</body>
</html>"""
        return HTMLResponse(content=html_done)
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Shamrock Bail Bonds — Mobile E-Sign Portal</title>
    <style>
        :root { --bg: #0b0f19; --card: #151c2c; --accent: #22c55e; --text: #f8fafc; --muted: #94a3b8; }
        body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); padding: 20px; text-align: center; }
        .card { background: var(--card); border-radius: 16px; padding: 24px; max-width: 400px; margin: 40px auto; border: 1px solid rgba(255,255,255,0.08); box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
        h1 { font-size: 20px; margin-bottom: 8px; color: var(--accent); }
        p { font-size: 14px; color: var(--muted); line-height: 1.5; }
        input { width: 100%; padding: 14px; margin: 12px 0; border-radius: 8px; border: 1px solid rgba(255,255,255,0.15); background: rgba(0,0,0,0.3); color: var(--text); font-size: 18px; text-align: center; box-sizing: border-box; }
        button { width: 100%; padding: 14px; background: var(--accent); color: #000; font-weight: 700; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; margin-top: 10px; }
        .status { margin-top: 14px; font-size: 13px; }
    </style>
</head>
<body>
    <div class="card">
        <h1>☘️ Shamrock Bail Bonds</h1>
        <p>Enter your phone number to receive your 6-digit access PIN for mobile e-signing.</p>
        
        <div id="step-phone">
            <input type="tel" id="phoneInput" placeholder="(239) 555-0199">
            <button onclick="sendPin()">Send Access PIN</button>
        </div>

        <div id="step-pin" style="display:none">
            <input type="number" id="pinInput" placeholder="6-Digit PIN" maxlength="6">
            <button onclick="verifyPin()">Verify & Enter Portal</button>
        </div>

        <div id="status" class="status"></div>
    </div>

    <script>
        async function sendPin() {
            const phone = document.getElementById('phoneInput').value;
            document.getElementById('status').textContent = 'Sending PIN...';
            const r = await fetch('/api/portal/send-pin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({phone})
            });
            const d = await r.json();
            if (d.success) {
                document.getElementById('step-phone').style.display = 'none';
                document.getElementById('step-pin').style.display = 'block';
                document.getElementById('status').textContent = 'PIN sent via SMS!';
            } else {
                document.getElementById('status').textContent = 'Error: ' + (d.error || 'Failed');
            }
        }

        async function verifyPin() {
            const phone = document.getElementById('phoneInput').value;
            const pin = document.getElementById('pinInput').value;
            document.getElementById('status').textContent = 'Verifying...';
            const r = await fetch('/api/portal/verify-pin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({phone, pin})
            });
            const d = await r.json();
            if (d.success) {
                document.getElementById('status').textContent = '✅ Verified! Loading e-sign packet...';
                window.location.href = d.signing_link || 'https://sign.shamrockbailbonds.biz';
            } else {
                document.getElementById('status').textContent = '❌ ' + (d.error || 'Invalid PIN');
            }
        }
    </script>
</body>
</html>"""
    return HTMLResponse(content=html)
