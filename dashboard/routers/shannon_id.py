"""Shannon voice ID capture — public token page + machine-auth link mint."""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard.extensions import get_collection
from dashboard.routers.automation_control import _require_control_auth
from dashboard.services.identity_media_service import save_upload_file, merge_id_photos_field

logger = logging.getLogger(__name__)

shannon_id_api = APIRouter(prefix="/api", tags=["shannon_id"])
shannon_id_pages = APIRouter(tags=["shannon_id_pages"])

_TOKEN_TTL_HOURS = 48
_MAX_BYTES = 8 * 1024 * 1024
_SLOT_ALIASES = {
    "front": "govt_id_front",
    "govt_id_front": "govt_id_front",
    "back": "govt_id_back",
    "govt_id_back": "govt_id_back",
    "selfie": "selfie",
}


def _public_base() -> str:
    return (os.getenv("PAPERWORK_PUBLIC_URL") or "https://paperwork.shamrockbailbonds.biz").rstrip("/")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slot(raw: str) -> str:
    key = str(raw or "front").strip().lower()
    return _SLOT_ALIASES.get(key, "govt_id_front")


async def _packet_by_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    pkts = get_collection("paperwork_packets")
    doc = await pkts.find_one({"shannon_id_token": token}, {"_id": 0})
    if not doc:
        return None
    exp = str(doc.get("shannon_id_token_exp") or "")
    if exp:
        try:
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            if exp_dt < _now():
                return None
        except ValueError:
            pass
    return doc


@shannon_id_api.post("/paperwork/shannon/id-link")
async def shannon_id_link(request: Request):
    """Mint a public ID-upload URL for a Shannon case. Machine auth."""
    denied = _require_control_auth(request, allow_machine=True)
    if denied:
        return denied
    try:
        body = await request.json() or {}
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"}, status_code=400)

    packet_id = str(body.get("packet_id") or body.get("case_reference") or "").strip()
    if not packet_id:
        packet_id = f"SH-{secrets.token_hex(5).upper()}"
    role = str(body.get("caller_role") or body.get("role") or "indemnitor").strip().lower()
    if role in ("cosigner", "primary"):
        role = "indemnitor"
    token = secrets.token_urlsafe(18)
    now = _now()
    exp = now + timedelta(hours=_TOKEN_TTL_HOURS)
    set_fields = {
        "packet_id": packet_id,
        "source": "shannon_voice",
        "shannon_id_token": token,
        "shannon_id_token_exp": exp.isoformat(),
        "shannon_id_role": role,
        "pending_staff_match": True,
        "updated_at": now.isoformat(),
    }
    defendant_name = str(body.get("defendant_name") or "").strip()
    if defendant_name:
        set_fields["defendant_name"] = defendant_name
    pkts = get_collection("paperwork_packets")
    await pkts.update_one(
        {"packet_id": packet_id},
        {
            "$set": set_fields,
            "$setOnInsert": {"created_at": now.isoformat(), "kyc_uploads": [], "id_photos": {}},
        },
        upsert=True,
    )
    upload_url = f"{_public_base()}/paperwork/shannon/id/{token}"
    return {
        "success": True,
        "packet_id": packet_id,
        "upload_url": upload_url,
        "expires_at": exp.isoformat(),
        "role": role,
    }


@shannon_id_api.post("/paperwork/shannon/id/{token}")
async def shannon_id_upload(
    token: str,
    slot: str = Form("front"),
    file: UploadFile = File(...),
):
    """Public token upload of a DL/ID photo onto the Shannon packet."""
    doc = await _packet_by_token(token)
    if not doc:
        return JSONResponse({"success": False, "error": "invalid_or_expired"}, status_code=404)
    raw = await file.read()
    if not raw:
        return JSONResponse({"success": False, "error": "empty_file"}, status_code=400)
    if len(raw) > _MAX_BYTES:
        return JSONResponse({"success": False, "error": "file_too_large"}, status_code=413)
    doc_type = _slot(slot)
    packet_id = str(doc.get("packet_id") or "unknown")
    try:
        meta = save_upload_file(packet_id, doc_type, file.filename or f"{doc_type}.jpg", raw)
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    pkts = get_collection("paperwork_packets")
    id_photos = merge_id_photos_field(doc.get("id_photos") or {}, meta)
    await pkts.update_one(
        {"packet_id": packet_id},
        {
            "$set": {
                "id_photos": id_photos,
                "shannon_id_received_at": _now().isoformat(),
                "updated_at": _now().isoformat(),
            },
            "$push": {"kyc_uploads": meta},
        },
    )
    slots = {k: bool(id_photos.get(k)) for k in ("govt_id_front", "govt_id_back")}
    try:
        import httpx
        hook = (os.getenv("SLACK_WEBHOOK_LEADS") or os.getenv("SLACK_WEBHOOK_URL") or "").strip()
        if hook:
            httpx.post(
                hook,
                json={"text": "Shannon ID photo received for packet " + packet_id + " (" + doc_type + "). Staff: attach to the case."},
                timeout=4.0,
            )
    except Exception:
        pass
    return {"success": True, "packet_id": packet_id, "slot": doc_type, "slots": slots}


@shannon_id_pages.get("/paperwork/shannon/id/{token}")
async def shannon_id_page(token: str):
    doc = await _packet_by_token(token)
    if not doc:
        return HTMLResponse(
            "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<body style='font-family:system-ui;background:#0a0f1a;color:#e8eee8;padding:32px'>"
            "<h1>Link expired</h1><p>Ask Shannon to text a new ID link, or call (239) 332-2245.</p></body>",
            status_code=404,
        )
    packet_id = doc.get("packet_id") or ""
    photos = doc.get("id_photos") or {}
    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shamrock — ID photos</title>
<style>
body{{margin:0;font-family:system-ui,sans-serif;background:#0a0f1a;color:#e8eee8}}
.wrap{{max-width:420px;margin:0 auto;padding:24px}}
h1{{color:#7CFF6B;font-size:1.3rem}}
.card{{background:#132018;border:1px solid #1f3d2a;border-radius:16px;padding:16px;margin:12px 0}}
label{{display:block;font-weight:700;margin-bottom:8px}}
input[type=file]{{width:100%}}
.ok{{color:#7CFF6B}}
button{{width:100%;margin-top:10px;padding:12px;border:0;border-radius:10px;background:#1B7A4E;color:#fff;font-weight:800}}
.foot{{opacity:.7;font-size:.85rem;margin-top:24px}}
</style></head>
<body><div class="wrap">
<h1>☘️ Shamrock Bail Bonds</h1>
<p>Photograph the <strong>front</strong> and <strong>back</strong> of your driver license or state ID.</p>
<div class="card">
<label>Front {('<span class=ok>received</span>' if photos.get('govt_id_front') else '')}</label>
<input id="front" type="file" accept="image/*" capture="environment">
<button type="button" onclick="send('front')">Upload front</button>
</div>
<div class="card">
<label>Back {('<span class=ok>received</span>' if photos.get('govt_id_back') else '')}</label>
<input id="back" type="file" accept="image/*" capture="environment">
<button type="button" onclick="send('back')">Upload back</button>
</div>
<p id="msg"></p>
<p class="foot">Packet {packet_id}. Questions: (239) 332-2245.</p>
</div>
<script>
async function send(slot){{
  const input = document.getElementById(slot);
  const msg = document.getElementById('msg');
  if(!input.files.length){{ msg.textContent='Choose a photo first.'; return; }}
  const fd = new FormData();
  fd.append('slot', slot);
  fd.append('file', input.files[0]);
  msg.textContent='Uploading…';
  const r = await fetch('/api/paperwork/shannon/id/{token}', {{ method:'POST', body: fd }});
  const d = await r.json();
  msg.textContent = d.success ? ('Saved '+slot+'. Thank you.') : (d.error || 'Upload failed');
}}
</script></body></html>"""
    return HTMLResponse(html)
