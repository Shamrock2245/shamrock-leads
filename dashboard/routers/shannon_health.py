"""Public Shannon/voice path health (no secrets)."""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter

logger = logging.getLogger(__name__)

shannon_health_bp = APIRouter(prefix="/api", tags=["shannon_health"])

VOICE_URL = "https://shamrock-telegram.netlify.app/api/twilio-voice"
VOICE_FALLBACK_URL = "https://shamrock-telegram.netlify.app/api/twilio-voice-fallback"
MEMORY_STATUS_PATH = "/api/agent-brain/memory/status"
DESK_E164 = "+12399550301"
OFFICE_E164 = "+12393322245"
SHANNON_E164 = "+17272952245"


async def collect_shannon_path_checks() -> dict:
    """BlueBubbles, Mem0, unsigned Netlify voice 403, fallback Dial 332, GAS health."""
    checks = {}
    ok = True

    try:
        from dashboard.extensions import BB_SERVERS, init_bluebubbles
        if not BB_SERVERS:
            init_bluebubbles()
        from dashboard.routers.legacy import imessage_status
        bb = await imessage_status()
        checks["bluebubbles"] = {
            "ok": bool(bb.get("connected")),
            "path_in_use": (bb.get("servers") or [{}])[0].get("path_in_use"),
        }
        ok = ok and checks["bluebubbles"]["ok"]
    except Exception as exc:
        checks["bluebubbles"] = {"ok": False, "error": type(exc).__name__}
        ok = False

    try:
        from dashboard.services.mem0_service import status_snapshot
        snap = status_snapshot()
        checks["mem0"] = {
            "ok": bool(snap.get("configured")),
            "enabled": bool(snap.get("enabled")),
            "status_path": MEMORY_STATUS_PATH,
        }
        ok = ok and checks["mem0"]["ok"]
    except Exception as exc:
        checks["mem0"] = {"ok": False, "error": type(exc).__name__}
        ok = False

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(VOICE_URL)
        checks["netlify_voice"] = {"ok": r.status_code == 403, "status": r.status_code}
        ok = ok and checks["netlify_voice"]["ok"]
    except Exception as exc:
        checks["netlify_voice"] = {"ok": False, "error": type(exc).__name__}
        ok = False

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(VOICE_FALLBACK_URL)
        body = r.text or ""
        fallback_ok = (
            r.status_code == 200
            and DESK_E164 in body
            and OFFICE_E164 in body
            and SHANNON_E164 in body
        )
        checks["netlify_voice_fallback"] = {
            "ok": fallback_ok,
            "status": r.status_code,
        }
        ok = ok and fallback_ok
    except Exception as exc:
        checks["netlify_voice_fallback"] = {"ok": False, "error": type(exc).__name__}
        ok = False

    gas = (os.getenv("GAS_WEB_APP_URL") or "").rstrip("/")
    if gas:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                r = await client.get(gas, params={"action": "health"})
            checks["gas_health"] = {"ok": r.status_code < 500, "status": r.status_code}
            ok = ok and checks["gas_health"]["ok"]
        except Exception as exc:
            checks["gas_health"] = {"ok": False, "error": type(exc).__name__}
            ok = False
    else:
        checks["gas_health"] = {"ok": False, "error": "GAS_WEB_APP_URL unset"}
        ok = False

    return {"success": ok, "checks": checks}


@shannon_health_bp.get("/ops/shannon-health")
async def shannon_health():
    return await collect_shannon_path_checks()
