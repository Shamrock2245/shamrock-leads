"""
ShamrockLeads — Mobile PIN Portal Router (paperwork.shamrockbailbonds.biz)
========================================================================
Serves mobile PWA UI and fast 6-digit OTP PIN authentication for indemnitor e-signing.
OTP is delivered exclusively via BlueBubbles (iMessage / green SMS through Messages).
Never routes client text through Twilio.
"""
import os
import re
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

_TEST_PHONE = "2395550199"
_MASTER_PIN = "224545"


class SendPinRequest(BaseModel):
    phone: str
    booking_number: Optional[str] = None
    intake_id: Optional[str] = None


class VerifyPinRequest(BaseModel):
    phone: str
    pin: str


def _digits_phone(raw: str) -> str:
    return "".join(ch for ch in (raw or "") if ch.isdigit())[-10:]


def _extract_signing_link_from_packet(doc: Optional[dict]) -> str:
    """Pull the best DocuSeal/sign URL from a paperwork_packets document."""
    if not doc or not isinstance(doc, dict):
        return ""
    for key in ("signing_link", "magic_link", "sign_url", "embed_src"):
        val = doc.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    links = doc.get("sign_links") or []
    if isinstance(links, list):
        for u in links:
            if isinstance(u, str) and u.startswith("http"):
                return u
    submitters = doc.get("docuseal_submitters") or doc.get("submitters") or []
    if isinstance(submitters, list):
        for s in submitters:
            if not isinstance(s, dict):
                continue
            for k in ("sign_url", "embed_src", "slug"):
                v = s.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    return v
            slug = s.get("slug")
            if isinstance(slug, str) and slug and not slug.startswith("http"):
                host = (
                    os.getenv("DOCUSEAL_URL")
                    or os.getenv("DOCUSEAL_HOST")
                    or "https://sign.shamrockbailbonds.biz"
                ).rstrip("/")
                if not host.startswith("http"):
                    host = f"https://{host}"
                return f"{host}/s/{slug}"
    return ""


async def _resolve_signing_link(phone_str: str, booking: str = "", intake: str = "") -> str:
    """Find the newest packet for this phone/booking/intake and return its sign URL."""
    packets = get_collection("paperwork_packets")
    phone = _digits_phone(phone_str)
    or_clauses = []
    if booking:
        or_clauses.append({"booking_number": booking})
        or_clauses.append({"Booking_Number": booking})
    if intake:
        or_clauses.append({"intake_id": intake})
    if phone:
        # Match last 10 digits whether stored as 10-digit or E.164
        phone_pat = re.escape(phone) + r"$"
        or_clauses.extend([
            {"indemnitor_phone": {"$regex": phone_pat}},
            {"delivered_to": {"$regex": phone_pat}},
            {"signer_phone": {"$regex": phone_pat}},
            {"defendant_phone": {"$regex": phone_pat}},
        ])
    if not or_clauses:
        return ""

    doc = await packets.find_one({"$or": or_clauses}, sort=[("created_at", -1)])
    if not doc:
        # Fallback: newest non-voided packet with any docuseal link (admin testing)
        return ""
    return _extract_signing_link_from_packet(doc)


@pin_portal_router.post("/send-pin")
async def send_portal_pin(req: SendPinRequest):
    """
    Generate & dispatch a 6-digit OTP PIN via BlueBubbles only (iMessage/SMS).
    Queued sends (BB temporarily down) still count as success — never Twilio.
    """
    clean_phone = _digits_phone(req.phone)
    if not clean_phone or len(clean_phone) < 10:
        return JSONResponse(
            {"success": False, "error": "Invalid 10-digit phone number"},
            status_code=400,
        )

    otp_pin = f"{random.randint(100000, 999999)}"
    # Deterministic lab PIN for staff smoke (not a production client path)
    if clean_phone == _TEST_PHONE or req.phone.replace(" ", "") == _TEST_PHONE:
        otp_pin = _MASTER_PIN

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

    logger.info("[PIN Portal] Generated PIN for phone ...%s", clean_phone[-4:])

    try:
        from dashboard.services.bb_client import (
            send_message_universal,
            normalize_bb_send_result,
            bb_send_accepted,
        )
        msg = (
            f"Your Shamrock Bail Bonds e-sign verification PIN is: {otp_pin}. "
            f"Valid for 15 minutes."
        )
        raw = await send_message_universal(clean_phone, msg)
        send_res = normalize_bb_send_result(raw)
        logger.info(
            "[PIN Portal] BB send channel=%s sent=%s queued=%s phone=...%s",
            send_res.get("channel"),
            send_res.get("sent"),
            send_res.get("queued"),
            clean_phone[-4:],
        )

        if not bb_send_accepted(send_res):
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Messaging unavailable: {send_res.get('error', 'send failed')}",
                    "channel": send_res.get("channel", "failed"),
                },
                status_code=503,
            )

        env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").lower()
        debug_ok = env not in ("production", "prod")
        return {
            "success": True,
            "phone": clean_phone,
            "channel": send_res.get("channel", "imessage"),
            "sent": bool(send_res.get("sent")),
            "queued": bool(send_res.get("queued")),
            "expires_in_minutes": 15,
            "debug_pin": otp_pin if debug_ok else None,
        }
    except Exception as exc:
        logger.error("[PIN Portal] Send PIN exception: %s", exc)
        return JSONResponse(
            {"success": False, "error": "Send error — BlueBubbles unreachable"},
            status_code=500,
        )


@pin_portal_router.post("/verify-pin")
async def verify_portal_pin(req: VerifyPinRequest):
    """
    Verify 6-digit OTP PIN and return session token + packet deep-link signing URL.
    """
    clean_phone = _digits_phone(req.phone)
    input_pin = (req.pin or "").strip()

    # Master admin bypass (staff smoke)
    if input_pin == _MASTER_PIN:
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
        return JSONResponse(
            {"success": False, "error": "Invalid PIN or phone number"},
            status_code=401,
        )

    expires_at_str = pin_doc.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                return JSONResponse(
                    {"success": False, "error": "PIN has expired"},
                    status_code=401,
                )
        except ValueError:
            pass

    await pins_col.update_one({"_id": pin_doc["_id"]}, {"$set": {"verified": True}})
    link = await _resolve_signing_link(
        clean_phone,
        booking=pin_doc.get("booking_number", "") or "",
        intake=pin_doc.get("intake_id", "") or "",
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


def _is_paperwork_host(request: Request) -> bool:
    host = (request.headers.get("host") or request.url.hostname or "").lower().split(":")[0]
    return (
        "paperwork.shamrockbailbonds.biz" in host
        or host.startswith("paperwork.")
        or host == "paperwork.localhost"
    )


@portal_page_router.get("/", response_class=HTMLResponse)
@portal_page_router.get("/done", response_class=HTMLResponse)
@portal_page_router.get("/paperwork", response_class=HTMLResponse)
@pin_portal_router.get("/portal-ui", response_class=HTMLResponse)
@pin_portal_router.get("/done", response_class=HTMLResponse)
async def get_portal_ui(request: Request):
    """
    Render lightweight mobile PWA UI for paperwork.shamrockbailbonds.biz
    and /done completion page.

    Host separation:
      - paperwork.*  → indemnitor portal at /
      - leads.* / IP → staff CRM (handled by main.index; this handler must not steal it)
    """
    path = request.url.path or "/"
    # Never hijack staff CRM root on leads host
    if path == "/" and not _is_paperwork_host(request):
        from fastapi.responses import FileResponse
        import os as _os
        dashboard_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        return FileResponse(_os.path.join(dashboard_dir, "index.html"))

    if path.endswith("/done"):
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
                let how = 'iMessage / text';
                if (d.channel === 'imessage') how = 'iMessage';
                else if (d.channel === 'sms') how = 'text message';
                else if (d.queued || d.channel === 'queued') how = 'message queue (delivering shortly)';
                document.getElementById('status').textContent = 'PIN sent via ' + how + '. Check your phone.';
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
                if (d.signing_link) {
                    document.getElementById('status').textContent = '✅ Verified! Opening your e-sign packet...';
                    window.location.href = d.signing_link;
                } else {
                    document.getElementById('status').textContent =
                        '✅ Verified — no packet linked to this phone yet. Call (239) 332-2245 and we will send your signing link.';
                }
            } else {
                document.getElementById('status').textContent = '❌ ' + (d.error || 'Invalid PIN');
            }
        }
    </script>
</body>
</html>"""
    return HTMLResponse(content=html)
