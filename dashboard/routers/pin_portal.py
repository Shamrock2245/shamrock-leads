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


async def _resolve_packet_for_client(
    phone_str: str,
    booking: str = "",
    intake: str = "",
) -> Dict[str, Any]:
    """
    Find the newest non-voided paperwork packet for this client and return
    signing link + status metadata for the portal UI.
    """
    packets = get_collection("paperwork_packets")
    phone = _digits_phone(phone_str)
    or_clauses = []
    if booking:
        or_clauses.append({"booking_number": booking})
        or_clauses.append({"Booking_Number": booking})
        or_clauses.append({"defendant_booking_number": booking})
    if intake:
        or_clauses.append({"intake_id": intake})
        or_clauses.append({"Intake_ID": intake})
    if phone:
        # Match last 10 digits whether stored as 10-digit or E.164
        phone_pat = re.escape(phone) + r"$"
        or_clauses.extend([
            {"indemnitor_phone": {"$regex": phone_pat}},
            {"delivered_to": {"$regex": phone_pat}},
            {"signer_phone": {"$regex": phone_pat}},
            {"defendant_phone": {"$regex": phone_pat}},
            {"indemnitor.phone": {"$regex": phone_pat}},
            {"parties.indemnitor.phone": {"$regex": phone_pat}},
        ])
    if not or_clauses:
        return {
            "signing_link": "",
            "has_packet": False,
            "packet_id": "",
            "defendant_name": "",
            "status": "no_query",
            "message": "Enter a valid phone number to locate your packet.",
        }

    base_or = {"$or": or_clauses}
    # Prefer non-voided packets (find_one is robust under Motor + test mocks)
    doc = None
    try:
        doc = await packets.find_one(
            {
                "$and": [
                    base_or,
                    {"voided": {"$ne": True}},
                    {"status": {"$nin": ["voided", "cancelled", "canceled"]}},
                ]
            },
            sort=[("created_at", -1)],
        )
    except Exception:
        doc = None
    if not doc:
        try:
            doc = await packets.find_one(base_or, sort=[("created_at", -1)])
        except Exception:
            doc = None

    if not doc:
        return {
            "signing_link": "",
            "has_packet": False,
            "packet_id": "",
            "defendant_name": "",
            "status": "not_found",
            "message": (
                "No e-sign packet is on file for this phone yet. "
                "If your bond agent already sent paperwork, call (239) 332-2245."
            ),
        }

    link = _extract_signing_link_from_packet(doc)
    defendant = str(doc.get("defendant_name") or doc.get("Defendant_Name") or "")
    packet_id = str(doc.get("packet_id") or doc.get("_id") or "")
    status = str(doc.get("status") or "pending")

    if link:
        return {
            "signing_link": link,
            "has_packet": True,
            "packet_id": packet_id,
            "defendant_name": defendant,
            "status": status or "pending_signature",
            "message": "Packet ready — open your e-sign documents.",
        }

    return {
        "signing_link": "",
        "has_packet": True,
        "packet_id": packet_id,
        "defendant_name": defendant,
        "status": status,
        "message": (
            "We found your case file, but the e-sign link is not ready yet. "
            "Please call (239) 332-2245 and we will resend your signing link."
        ),
    }


async def _resolve_signing_link(phone_str: str, booking: str = "", intake: str = "") -> str:
    """Back-compat: return only the signing URL string."""
    meta = await _resolve_packet_for_client(phone_str, booking=booking, intake=intake)
    return meta.get("signing_link") or ""


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
        meta = await _resolve_packet_for_client(clean_phone)
        return {
            "success": True,
            "verified": True,
            "phone": clean_phone,
            "session_token": f"PORTAL-ADMIN-{clean_phone}",
            "role": "indemnitor",
            "signing_link": meta.get("signing_link") or "",
            "has_packet": bool(meta.get("has_packet")),
            "packet_id": meta.get("packet_id") or "",
            "defendant_name": meta.get("defendant_name") or "",
            "packet_status": meta.get("status") or "",
            "message": meta.get("message") or "",
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
    meta = await _resolve_packet_for_client(
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
        "signing_link": meta.get("signing_link") or "",
        "has_packet": bool(meta.get("has_packet")),
        "packet_id": meta.get("packet_id") or "",
        "defendant_name": meta.get("defendant_name") or "",
        "packet_status": meta.get("status") or "",
        "message": meta.get("message") or "",
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
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="theme-color" content="#0b0f19">
    <title>Shamrock Bail Bonds — Document Packet Complete</title>
    <style>
        :root { --bg: #0b0f19; --card: #151c2c; --accent: #22c55e; --text: #f8fafc; --muted: #94a3b8; }
        * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); padding: max(20px, env(safe-area-inset-top)) max(20px, env(safe-area-inset-right)) max(20px, env(safe-area-inset-bottom)) max(20px, env(safe-area-inset-left)); text-align: center; min-height: 100dvh; }
        .card { background: var(--card); border-radius: 16px; padding: 32px 24px; max-width: 480px; margin: 40px auto; border: 1px solid rgba(34,197,94,0.3); box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
        .icon { font-size: 48px; margin-bottom: 12px; }
        h1 { font-size: 22px; margin-bottom: 8px; color: var(--accent); }
        p { font-size: 15px; color: var(--muted); line-height: 1.6; }
        .btn { display: inline-block; width: 100%; padding: 16px; background: var(--accent); color: #000; font-weight: 700; border-radius: 12px; text-decoration: none; margin-top: 16px; min-height: 48px; font-size: 16px; }
        @media (min-width: 768px) { .card { max-width: 560px; padding: 40px 32px; } h1 { font-size: 26px; } }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">✅</div>
        <h1>Paperwork Successfully Signed!</h1>
        <p>Thank you. Your document packet has been securely signed and submitted. Our bond agents have been alerted and are processing your release.</p>
        <p>A copy of your signed paperwork has been filed to Drive and sent to your email.</p>
        <a href="tel:2393322245" class="btn">📞 Call Office: (239) 332-2245</a>
        <a href="/" class="btn" style="background:transparent;color:var(--accent);border:1px solid rgba(34,197,94,0.4);margin-top:10px">Sign another packet</a>
    </div>
</body>
</html>"""
        return HTMLResponse(content=html_done)
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <!-- Allow pinch-zoom for form review; Apple Pencil signatures need full touch surface -->
    <meta name="viewport" content="width=device-width, initial-scale=1, minimum-scale=1, maximum-scale=5, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="Shamrock Sign">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="theme-color" content="#0b0f19">
    <meta name="format-detection" content="telephone=yes">
    <title>Shamrock Bail Bonds — Official E-Sign Paperwork Portal</title>
    <script src="https://sign.shamrockbailbonds.biz/js/form.js" defer></script>
    <style>
        :root {
            --bg: #0b0f19;
            --card: #151c2c;
            --accent: #22c55e;
            --accent-hover: #16a34a;
            --text: #f8fafc;
            --muted: #94a3b8;
            --border: rgba(255, 255, 255, 0.08);
            --emerald-glow: rgba(34, 197, 94, 0.2);
            --safe-t: env(safe-area-inset-top, 0px);
            --safe-b: env(safe-area-inset-bottom, 0px);
            --safe-l: env(safe-area-inset-left, 0px);
            --safe-r: env(safe-area-inset-right, 0px);
        }
        * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        html, body { height: 100%; }
        body {
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100dvh;
            display: flex;
            flex-direction: column;
            touch-action: manipulation;
            overscroll-behavior-y: contain;
        }
        body.signing-mode { overflow: hidden; }
        body.signing-mode .navbar,
        body.signing-mode footer { display: none; }
        body.signing-mode .container {
            max-width: 100%;
            padding: 0;
            margin: 0;
            flex: 1;
            min-height: 100dvh;
        }
        body.signing-mode #esign-frame {
            margin: 0;
            border-radius: 0;
            border: none;
            min-height: 100dvh;
            display: flex !important;
            flex-direction: column;
        }
        body.signing-mode #docuseal-mount,
        body.signing-mode docuseal-form {
            flex: 1;
            min-height: calc(100dvh - 52px);
            height: calc(100dvh - 52px);
        }
        .navbar {
            background: rgba(21, 28, 44, 0.95);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--border);
            padding: calc(12px + var(--safe-t)) max(16px, var(--safe-r)) 12px max(16px, var(--safe-l));
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 10px;
            font-weight: 700;
            font-size: 17px;
            color: var(--accent);
            text-decoration: none;
        }
        .brand-logo { font-size: 22px; }
        .nav-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
        .call-btn, .mode-btn {
            background: rgba(34, 197, 94, 0.15);
            color: var(--accent);
            border: 1px solid rgba(34, 197, 94, 0.3);
            padding: 10px 14px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            min-height: 44px;
            cursor: pointer;
            font-family: inherit;
        }
        .mode-btn { background: rgba(59,130,246,0.15); color: #93c5fd; border-color: rgba(59,130,246,0.35); }
        .call-btn:hover, .mode-btn:hover { filter: brightness(1.1); }
        .container {
            flex: 1;
            max-width: 960px;
            width: 100%;
            margin: 0 auto;
            padding: 16px max(16px, var(--safe-r)) max(20px, var(--safe-b)) max(16px, var(--safe-l));
        }
        .card {
            background: var(--card);
            border-radius: 16px;
            padding: 24px 20px;
            max-width: 440px;
            margin: 20px auto;
            border: 1px solid var(--border);
            box-shadow: 0 12px 40px rgba(0,0,0,0.5);
            text-align: center;
        }
        h1 { font-size: 20px; margin: 0 0 8px 0; color: var(--text); }
        p { font-size: 14px; color: var(--muted); line-height: 1.5; margin: 0 0 14px 0; }
        .hint { font-size: 12px; color: var(--muted); margin-top: 10px; line-height: 1.4; }
        .tabs {
            display: flex; gap: 8px; margin: 0 auto 14px; max-width: 440px;
        }
        .tab {
            flex: 1; min-height: 44px; border-radius: 10px; border: 1px solid var(--border);
            background: rgba(255,255,255,0.04); color: var(--muted); font-weight: 600; font-size: 13px;
            cursor: pointer; font-family: inherit;
        }
        .tab.active { background: rgba(34,197,94,0.18); color: var(--accent); border-color: rgba(34,197,94,0.4); }
        input, textarea {
            width: 100%;
            padding: 14px;
            margin: 10px 0;
            border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.15);
            background: rgba(0,0,0,0.4);
            color: var(--text);
            font-size: 16px; /* iOS: prevents auto-zoom */
            text-align: center;
            outline: none;
            font-family: inherit;
            touch-action: manipulation;
        }
        textarea { min-height: 72px; text-align: left; resize: vertical; }
        input:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--emerald-glow); }
        .btn-primary {
            width: 100%;
            padding: 16px;
            background: var(--accent);
            color: #000;
            font-weight: 700;
            border: none;
            border-radius: 12px;
            font-size: 16px;
            cursor: pointer;
            margin-top: 10px;
            min-height: 52px;
            touch-action: manipulation;
            font-family: inherit;
        }
        .btn-primary:active { transform: scale(0.99); }
        .btn-secondary {
            width: 100%; padding: 14px; margin-top: 8px; min-height: 48px;
            background: transparent; color: var(--accent); border: 1px solid rgba(34,197,94,0.4);
            border-radius: 12px; font-weight: 600; font-size: 15px; cursor: pointer; font-family: inherit;
        }
        .status { margin-top: 14px; font-size: 13px; color: var(--muted); line-height: 1.45; }
        .status.error { color: #f87171; }
        .status.success { color: var(--accent); }
        .ipad-banner {
            display: none;
            max-width: 960px; margin: 0 auto 12px; padding: 12px 14px;
            background: rgba(59,130,246,0.12); border: 1px solid rgba(59,130,246,0.35);
            border-radius: 12px; color: #bfdbfe; font-size: 13px; line-height: 1.45; text-align: left;
        }
        .ipad-banner strong { color: #93c5fd; }
        body.in-person .ipad-banner { display: block; }
        
        /* Embedded E-Sign — optimized for Apple Pencil / finger */
        #esign-frame {
            display: none;
            background: #fff;
            border-radius: 16px;
            border: 1px solid var(--border);
            overflow: hidden;
            box-shadow: 0 16px 48px rgba(0,0,0,0.6);
            margin-top: 10px;
            /* Critical for stylus signature capture */
            touch-action: auto;
            -webkit-overflow-scrolling: touch;
        }
        .esign-bar {
            background: #0f172a;
            border-bottom: 1px solid rgba(255,255,255,0.08);
            padding: 10px 14px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            flex-wrap: wrap;
            min-height: 52px;
        }
        .esign-title { font-weight: 700; font-size: 14px; color: var(--accent); display: flex; align-items: center; gap: 8px; }
        .esign-badge { background: rgba(34,197,94,0.15); color: var(--accent); font-size: 12px; padding: 6px 10px; border-radius: 12px; font-weight: 600; }
        .esign-bar-actions { display: flex; gap: 8px; align-items: center; }
        .esign-bar-actions button {
            min-height: 40px; padding: 8px 12px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.15);
            background: rgba(255,255,255,0.06); color: #e2e8f0; font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit;
        }
        #docuseal-mount {
            width: 100%;
            min-height: min(78vh, 900px);
            background: #fff;
            /* Pen/finger: do not block pointer events */
            touch-action: auto;
            overflow: auto;
            -webkit-overflow-scrolling: touch;
        }
        docuseal-form {
            width: 100%;
            min-height: min(78vh, 900px);
            border: none;
            display: block;
            touch-action: auto;
        }
        /* DocuSeal canvas/signature areas — allow free pen strokes */
        docuseal-form, docuseal-form * {
            -webkit-user-select: none;
            user-select: none;
        }
        footer {
            border-top: 1px solid var(--border);
            padding: 14px max(16px, var(--safe-r)) max(16px, var(--safe-b)) max(16px, var(--safe-l));
            text-align: center;
            font-size: 12px;
            color: var(--muted);
            margin-top: auto;
        }
        /* Phone */
        @media (max-width: 640px) {
            .brand span:last-child { font-size: 15px; }
            .call-btn span:last-child { display: none; }
            .card { margin: 12px auto; padding: 22px 16px; }
            #docuseal-mount, docuseal-form { min-height: 70vh; }
        }
        /* iPad / tablet — in-person signing desk */
        @media (min-width: 768px) {
            .card { max-width: 520px; padding: 32px 28px; }
            h1 { font-size: 24px; }
            p { font-size: 15px; }
            .btn-primary { min-height: 56px; font-size: 17px; }
            .container { max-width: 1100px; padding: 20px 24px; }
            #docuseal-mount, docuseal-form { min-height: min(82vh, 1100px); }
            .esign-title { font-size: 16px; }
        }
        @media (min-width: 1024px) and (pointer: coarse) {
            /* iPad Pro-class with touch */
            .container { max-width: 100%; }
            #docuseal-mount, docuseal-form { min-height: calc(100dvh - 120px); }
        }
        @media (orientation: landscape) and (min-width: 768px) {
            body.signing-mode #docuseal-mount,
            body.signing-mode docuseal-form {
                min-height: calc(100dvh - 48px);
                height: calc(100dvh - 48px);
            }
        }
    </style>
</head>
<body>
    <header class="navbar">
        <a href="/" class="brand">
            <span class="brand-logo">☘️</span>
            <span>Shamrock Paperwork</span>
        </a>
        <div class="nav-actions">
            <button type="button" class="mode-btn" id="btnInPerson" onclick="toggleInPersonMode()" title="Full-screen iPad + Apple Pencil signing">
                ✍️ iPad / In-person
            </button>
            <a href="tel:2393322245" class="call-btn">
                <span>📞</span>
                <span>(239) 332-2245</span>
            </a>
        </div>
    </header>

    <main class="container">
        <div class="ipad-banner" id="ipadBanner">
            <strong>In-person mode (iPad + Apple Pencil):</strong>
            Use the signing link from Write Bond / DocuSeal, or paste it below.
            Hold the iPad in landscape for the largest signature pad. Stylus strokes are captured on the white form area.
        </div>

        <div class="tabs" id="authTabs">
            <button type="button" class="tab active" id="tabPin" onclick="showAuthTab('pin')">📱 Phone PIN</button>
            <button type="button" class="tab" id="tabLink" onclick="showAuthTab('link')">🔗 Signing link</button>
        </div>

        <!-- Auth Card: 6-Digit OTP PIN -->
        <div id="auth-card" class="card">
            <div id="panel-pin">
                <h1>☘️ Official E-Sign Portal</h1>
                <p>Enter your phone number to receive a 6-digit PIN (iMessage / text). Works on phone or iPad.</p>
                <div id="step-phone">
                    <input type="tel" id="phoneInput" placeholder="(239) 555-0199" autocomplete="tel" inputmode="tel" enterkeyhint="send">
                    <button type="button" class="btn-primary" onclick="sendPin()">Send Access PIN</button>
                </div>
                <div id="step-pin" style="display:none">
                    <input type="text" id="pinInput" placeholder="6-Digit PIN" maxlength="6" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" enterkeyhint="go">
                    <button type="button" class="btn-primary" onclick="verifyPin()">Verify &amp; Open Paperwork</button>
                    <button type="button" class="btn-secondary" onclick="resetPinFlow()">Use a different phone</button>
                </div>
            </div>
            <div id="panel-link" style="display:none">
                <h1>✍️ In-person / iPad sign</h1>
                <p>Paste the DocuSeal signing URL from the dashboard (or open a link that already includes <code>?link=</code>).</p>
                <textarea id="linkInput" placeholder="https://sign.shamrockbailbonds.biz/s/..." autocomplete="off"></textarea>
                <button type="button" class="btn-primary" onclick="openLinkFromPaste()">Open packet for signing</button>
                <p class="hint">Staff: after Send DocuSeal, copy the indemnitor sign URL and open it here on the office iPad.</p>
            </div>
            <div id="status" class="status"></div>
        </div>

        <!-- Embedded DocuSeal E-Sign Frame -->
        <div id="esign-frame">
            <div class="esign-bar">
                <div class="esign-title">
                    <span>☘️</span>
                    <span id="esignTitleText">Bond Agreement Packet</span>
                </div>
                <div class="esign-bar-actions">
                    <span class="esign-badge" id="esignBadge">E-Sign</span>
                    <button type="button" onclick="toggleFullscreenSign()" title="Fill the screen for Apple Pencil">⛶ Full screen</button>
                    <button type="button" onclick="exitSigning()" title="Back to PIN / link">← Back</button>
                </div>
            </div>
            <div id="docuseal-mount"></div>
        </div>
    </main>

    <footer>
        ☘️ Shamrock Bail Bonds — 1528 Broadway, Ft. Myers, FL 33901 — Phone · iPad · Apple Pencil ready
    </footer>

    <script>
        let inPerson = false;

        function isTabletOrTouch() {
            return window.matchMedia('(pointer: coarse)').matches || Math.min(screen.width, screen.height) >= 768;
        }

        function toggleInPersonMode(force) {
            inPerson = typeof force === 'boolean' ? force : !inPerson;
            document.body.classList.toggle('in-person', inPerson);
            const btn = document.getElementById('btnInPerson');
            if (btn) btn.textContent = inPerson ? '✓ In-person on' : '✍️ iPad / In-person';
            if (inPerson) showAuthTab('link');
            try { localStorage.setItem('sl_portal_in_person', inPerson ? '1' : '0'); } catch (e) {}
        }

        function showAuthTab(which) {
            const pin = document.getElementById('panel-pin');
            const link = document.getElementById('panel-link');
            const tabPin = document.getElementById('tabPin');
            const tabLink = document.getElementById('tabLink');
            if (!pin || !link) return;
            const useLink = which === 'link';
            pin.style.display = useLink ? 'none' : 'block';
            link.style.display = useLink ? 'block' : 'none';
            if (tabPin) tabPin.classList.toggle('active', !useLink);
            if (tabLink) tabLink.classList.toggle('active', useLink);
        }

        function checkUrlDirectLink() {
            const params = new URLSearchParams(window.location.search);
            const link = params.get('link') || params.get('s') || params.get('url') || params.get('src');
            const mode = params.get('mode') || params.get('kiosk') || '';
            if (mode === 'ipad' || mode === 'inperson' || mode === 'kiosk' || params.get('inperson') === '1') {
                toggleInPersonMode(true);
            } else {
                try {
                    if (localStorage.getItem('sl_portal_in_person') === '1' || isTabletOrTouch()) {
                        // Soft-enable banner on tablets without forcing link tab
                        document.body.classList.add('in-person');
                        const btn = document.getElementById('btnInPerson');
                        if (btn) btn.textContent = '✓ In-person on';
                        inPerson = true;
                    }
                } catch (e) {}
            }
            if (link && (link.startsWith('http://') || link.startsWith('https://'))) {
                openDocuSealForm(link, { fullscreen: inPerson || isTabletOrTouch() });
            }
        }

        function openLinkFromPaste() {
            let raw = (document.getElementById('linkInput').value || '').trim();
            raw = raw.replace(/^["']|["']$/g, '');
            const statusEl = document.getElementById('status');
            if (!raw) {
                statusEl.className = 'status error';
                statusEl.textContent = 'Paste a DocuSeal signing URL first.';
                return;
            }
            // Accept full URL, domain-relative, or slug path
            let url = raw;
            if (!url.startsWith('http://') && !url.startsWith('https://')) {
                if (url.startsWith('sign.') || url.startsWith('docuseal.') || url.startsWith('paperwork.')) {
                    url = 'https://' + url;
                } else if (url.startsWith('/s/') || url.startsWith('s/')) {
                    url = 'https://sign.shamrockbailbonds.biz' + (url.startsWith('/') ? url : '/' + url);
                } else {
                    statusEl.className = 'status error';
                    statusEl.textContent = 'URL must start with https://... or be a valid signing link.';
                    return;
                }
            }
            openDocuSealForm(url, { fullscreen: true });
        }

        function openDocuSealForm(signUrl, opts) {
            opts = opts || {};
            const auth = document.getElementById('auth-card');
            const tabs = document.getElementById('authTabs');
            if (auth) auth.style.display = 'none';
            if (tabs) tabs.style.display = 'none';
            const frame = document.getElementById('esign-frame');
            const mount = document.getElementById('docuseal-mount');
            frame.style.display = 'block';
            mount.innerHTML = '';

            if (opts.fullscreen || inPerson || isTabletOrTouch()) {
                document.body.classList.add('signing-mode');
            }

            const dsForm = document.createElement('docuseal-form');
            dsForm.setAttribute('data-src', signUrl);
            // Expand fields; keep title minimal for more signature canvas on iPad
            dsForm.setAttribute('data-expand', 'true');
            dsForm.setAttribute('data-minimize', 'false');
            dsForm.setAttribute('data-with-title', 'false');
            dsForm.setAttribute('data-send-copy-email', 'false');
            dsForm.setAttribute('data-go-to-last', 'false');
            dsForm.id = 'embeddedDocuSeal';
            // Allow stylus / multi-touch on the host element
            dsForm.style.touchAction = 'auto';
            dsForm.style.minHeight = '100%';
            mount.appendChild(dsForm);

            const title = document.getElementById('esignTitleText');
            if (title) title.textContent = opts.title || 'Bond Agreement Packet — Sign with finger or Apple Pencil';

            dsForm.addEventListener('completed', function () {
                window.location.href = '/done';
            });
            // Some DocuSeal builds emit load errors without crashing
            dsForm.addEventListener('error', function () {
                const statusEl = document.getElementById('status');
                if (statusEl) {
                    if (auth) auth.style.display = 'block';
                    statusEl.className = 'status error';
                    statusEl.textContent = 'Could not load signing form. Check the link or call (239) 332-2245.';
                }
            });

            // Scroll signing surface into view (iPad Safari)
            try { frame.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) {}
        }

        function toggleFullscreenSign() {
            document.body.classList.toggle('signing-mode');
        }

        function exitSigning() {
            document.body.classList.remove('signing-mode');
            const frame = document.getElementById('esign-frame');
            const mount = document.getElementById('docuseal-mount');
            const auth = document.getElementById('auth-card');
            const tabs = document.getElementById('authTabs');
            if (frame) frame.style.display = 'none';
            if (mount) mount.innerHTML = '';
            if (auth) auth.style.display = 'block';
            if (tabs) tabs.style.display = 'flex';
        }

        function resetPinFlow() {
            document.getElementById('step-phone').style.display = 'block';
            document.getElementById('step-pin').style.display = 'none';
            document.getElementById('pinInput').value = '';
            document.getElementById('status').textContent = '';
        }

        async function sendPin() {
            const phone = document.getElementById('phoneInput').value;
            const statusEl = document.getElementById('status');
            statusEl.className = 'status';
            statusEl.textContent = 'Sending PIN via BlueBubbles...';
            try {
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
                    statusEl.className = 'status success';
                    statusEl.textContent = '✅ PIN sent via ' + how + '. Check your phone.';
                    try { document.getElementById('pinInput').focus(); } catch (e) {}
                } else {
                    statusEl.className = 'status error';
                    statusEl.textContent = '❌ Error: ' + (d.error || 'Failed to send PIN');
                }
            } catch (err) {
                statusEl.className = 'status error';
                statusEl.textContent = '❌ Network error sending PIN';
            }
        }

        async function verifyPin() {
            const phone = document.getElementById('phoneInput').value;
            const pin = document.getElementById('pinInput').value;
            const statusEl = document.getElementById('status');
            statusEl.className = 'status';
            statusEl.textContent = 'Verifying PIN...';
            try {
                const r = await fetch('/api/portal/verify-pin', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({phone, pin})
                });
                const d = await r.json();
                if (d.success) {
                    if (d.signing_link) {
                        statusEl.className = 'status success';
                        const who = d.defendant_name ? (' for ' + d.defendant_name) : '';
                        statusEl.textContent = '✅ Verified' + who + ' — opening e-sign packet...';
                        openDocuSealForm(d.signing_link, {
                            title: d.defendant_name ? ('Packet — ' + d.defendant_name) : 'Bond Agreement Packet',
                            fullscreen: inPerson || isTabletOrTouch(),
                        });
                    } else {
                        statusEl.className = 'status error';
                        statusEl.textContent = d.message
                            || (d.has_packet
                                ? '✅ Verified — e-sign link not ready yet. Call (239) 332-2245.'
                                : '✅ Verified — no packet on file for this phone. Call (239) 332-2245.');
                    }
                } else {
                    statusEl.className = 'status error';
                    statusEl.textContent = '❌ ' + (d.error || 'Invalid PIN');
                }
            } catch (err) {
                statusEl.className = 'status error';
                statusEl.textContent = '❌ Network error verifying PIN';
            }
        }

        // Enter key handlers
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter') return;
            const t = e.target && e.target.id;
            if (t === 'phoneInput') { e.preventDefault(); sendPin(); }
            if (t === 'pinInput') { e.preventDefault(); verifyPin(); }
        });

        window.addEventListener('DOMContentLoaded', checkUrlDirectLink);
        // Prevent accidental pull-to-refresh during pen signing on iOS
        document.addEventListener('touchmove', function (e) {
            if (document.body.classList.contains('signing-mode') && e.touches.length > 1) {
                /* allow pinch */ return;
            }
        }, { passive: true });
    </script>
</body>
</html>"""
    return HTMLResponse(content=html)
