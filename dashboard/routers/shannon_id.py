"""Shannon voice ID capture — public token page + machine-auth link mint."""
from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard.extensions import get_collection
from dashboard.routers.automation_control import _require_control_auth
from dashboard.routers.pin_portal import client_fields_from_id_ocr
from dashboard.services.identity_media_service import save_upload_file, merge_id_photos_field
from dashboard.services.id_ocr_service import last_name_token, normalize_person_name, resolve_legal_name
from dashboard.services.id_scanner_service import IDScannerService

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


def _looks_like_call_sid(value: str) -> bool:
    s = str(value or "").strip()
    return bool(
        re.match(r"^(CA|SM|MM|NO|PN)[0-9a-f]{32}$", s, re.I)
        or s.lower().startswith("conv_")
        or s.lower().startswith("tlcal_")
    )


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


def _packet_confirmed_name(doc: dict[str, Any] | None, role_key: str) -> str:
    existing = doc if isinstance(doc, dict) else {}
    confirmed = str(existing.get("shannon_confirmed_name") or "").strip()
    if confirmed:
        return normalize_person_name(confirmed)
    if role_key == "defendant":
        return normalize_person_name(existing.get("defendant_name") or existing.get("caller_name") or "")
    return normalize_person_name(
        existing.get("indemnitor_name") or existing.get("caller_name") or existing.get("FullName") or ""
    )


async def _confirmed_name_for_ocr(packet_id: str, existing: dict[str, Any] | None, role_key: str) -> str:
    """Prefer packet spelling, then the Shannon intake_queue row from create_intake."""
    confirmed = _packet_confirmed_name(existing, role_key)
    if confirmed:
        return confirmed
    if not packet_id:
        return ""
    try:
        doc = await get_collection("intake_queue").find_one(
            {"intake_id": packet_id},
            {"_id": 0, "indemnitor_name": 1, "defendant_name": 1, "indemnitor": 1},
        )
    except Exception:
        return ""
    if not isinstance(doc, dict):
        return ""
    if role_key == "defendant":
        return normalize_person_name(doc.get("defendant_name") or "")
    name = str(doc.get("indemnitor_name") or "").strip()
    ind = doc.get("indemnitor") if isinstance(doc.get("indemnitor"), dict) else {}
    if not name and ind:
        name = " ".join(p for p in (ind.get("firstName"), ind.get("lastName")) if p)
    return normalize_person_name(name)


def _ocr_spoken(fields: dict[str, Any], conflict: dict[str, Any] | None = None) -> str:
    name = str(fields.get("indemnitor_name") or fields.get("defendant_name") or "").strip()
    addr = str(fields.get("indemnitor_address") or fields.get("defendant_address") or "").strip()
    city = str(fields.get("indemnitor_city") or fields.get("defendant_city") or "").strip()
    if isinstance(conflict, dict) and conflict.get("kind") == "confusable_surname":
        ocr_last = str(conflict.get("ocr_last") or last_name_token(conflict.get("ocr") or "") or "").strip()
        said_last = str(
            conflict.get("confirmed_last") or last_name_token(conflict.get("confirmed") or "") or ""
        ).strip()
        if ocr_last and said_last:
            return (
                f"I have the ID. I read the last name as {ocr_last}. You said {said_last}. "
                "Spell the last name on the license for me."
            )
    if isinstance(conflict, dict) and conflict.get("kind") == "name_mismatch":
        ocr_name = str(conflict.get("ocr") or name).strip()
        said = str(conflict.get("confirmed") or "").strip()
        if ocr_name and said:
            return (
                f"I have the ID. I read {ocr_name}. You said {said}. "
                "Confirm the name as it appears on the license."
            )
    parts = [p for p in (name, addr, city) if p]
    if not parts:
        return ""
    return "I read " + ", ".join(parts) + " off the ID."


async def _ocr_upload_into_packet(
    packet_id: str,
    role: str,
    raw: bytes,
    filename: str,
    existing: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Role-scoped DL OCR onto the Shannon packet. Indemnitor ID never overwrites defendant."""
    try:
        scan = await IDScannerService.scan_id_image(raw, filename or "")
    except Exception as exc:
        logger.warning("shannon id ocr failed: %s", type(exc).__name__)
        return dict((existing or {}).get("id_ocr_fields") or {}), None
    extracted = scan.get("extracted") if isinstance(scan, dict) else {}
    if not isinstance(extracted, dict) or not extracted:
        return dict((existing or {}).get("id_ocr_fields") or {}), None
    role_key = "defendant" if str(role or "").lower() == "defendant" else "indemnitor"
    confirmed = await _confirmed_name_for_ocr(packet_id, existing, role_key)
    ocr_full = normalize_person_name(
        extracted.get("full_name")
        or " ".join(p for p in (extracted.get("first_name"), extracted.get("last_name")) if p)
        or ""
    )
    resolution = resolve_legal_name(ocr_full, confirmed)
    fresh = client_fields_from_id_ocr(extracted, role_key, confirmed_name=confirmed)
    prior = dict((existing or {}).get("id_ocr_fields") or {})
    fields = {**prior, **{k: v for k, v in fresh.items() if v}}
    set_fields: dict[str, Any] = {
        "id_ocr": extracted,
        "id_ocr_fields": fields,
        "id_ocr_role": role_key,
        "id_ocr_at": _now().isoformat(),
        "id_ocr_raw_name": resolution.get("ocr_name") or ocr_full,
        "id_ocr_name_source": resolution.get("source") or "ocr",
        "id_ocr_name_conflict": resolution.get("conflict"),
    }
    for key, val in fields.items():
        if not val:
            continue
        if role_key == "indemnitor" and str(key).lower().startswith("defendant"):
            continue
        if role_key == "defendant" and str(key).lower().startswith("indemnitor"):
            continue
        set_fields[key] = val
    pkts = get_collection("paperwork_packets")
    await pkts.update_one({"packet_id": packet_id}, {"$set": set_fields})
    conflict = resolution.get("conflict") if isinstance(resolution.get("conflict"), dict) else None
    return fields, conflict


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
    if not packet_id or _looks_like_call_sid(packet_id):
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
    caller_name = str(body.get("caller_name") or body.get("indemnitor_name") or "").strip()
    if caller_name:
        set_fields["shannon_confirmed_name"] = caller_name
        set_fields["caller_name"] = caller_name
        if role != "defendant":
            set_fields["indemnitor_name"] = caller_name
        elif not defendant_name:
            set_fields["defendant_name"] = caller_name
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
    ocr_fields: dict[str, Any] = {}
    name_conflict: dict[str, Any] | None = None
    if doc_type in ("govt_id_front", "govt_id_back"):
        role = str(doc.get("shannon_id_role") or "indemnitor")
        ocr_fields, name_conflict = await _ocr_upload_into_packet(
            packet_id, role, raw, file.filename or "", doc,
        )
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
    return {
        "success": True,
        "packet_id": packet_id,
        "slot": doc_type,
        "slots": slots,
        "ocr": {k: v for k, v in ocr_fields.items() if v} if ocr_fields else {},
        "spoken": _ocr_spoken(ocr_fields, name_conflict),
        "name_conflict": name_conflict,
    }


@shannon_id_api.post("/paperwork/shannon/id-status")
async def shannon_id_status(request: Request):
    """Machine-auth poll: whether Shannon ID photos landed and what OCR read."""
    denied = _require_control_auth(request, allow_machine=True)
    if denied:
        return denied
    try:
        body = await request.json() or {}
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"}, status_code=400)
    packet_id = str(body.get("packet_id") or body.get("case_reference") or "").strip()
    if not packet_id:
        return JSONResponse({"success": False, "error": "missing_packet_id"}, status_code=400)
    pkts = get_collection("paperwork_packets")
    doc = await pkts.find_one({"packet_id": packet_id}, {"_id": 0})
    if not doc:
        return {"success": True, "received": False, "slots": {"govt_id_front": False, "govt_id_back": False}, "ocr": {}, "spoken": "I do not have an ID photo yet."}
    photos = doc.get("id_photos") or {}
    slots = {k: bool(photos.get(k)) for k in ("govt_id_front", "govt_id_back")}
    ocr = {k: v for k, v in (doc.get("id_ocr_fields") or {}).items() if v}
    received = bool(slots.get("govt_id_front") or slots.get("govt_id_back"))
    conflict = doc.get("id_ocr_name_conflict") if isinstance(doc.get("id_ocr_name_conflict"), dict) else None
    spoken = _ocr_spoken(ocr, conflict) if received else "I do not have an ID photo yet."
    if received and not spoken:
        spoken = "I have the ID photo, but I could not read the print. Ask them to confirm their name as it appears on the license."
    return {
        "success": True,
        "received": received,
        "packet_id": packet_id,
        "slots": slots,
        "ocr": ocr,
        "spoken": spoken,
        "name_conflict": conflict,
        "name_source": doc.get("id_ocr_name_source") or "",
    }


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
<input id="front" type="file" accept="image/*" capture="environment" onchange="send('front')">
<button type="button" onclick="document.getElementById('front').click()">Photograph front</button>
</div>
<div class="card">
<label>Back {('<span class=ok>received</span>' if photos.get('govt_id_back') else '')}</label>
<input id="back" type="file" accept="image/*" capture="environment" onchange="send('back')">
<button type="button" onclick="document.getElementById('back').click()">Photograph back</button>
</div>
<p id="msg"></p>
<p class="foot">Packet {packet_id}. Questions: (239) 332-2245.</p>
</div>
<script>
async function send(slot){{
  const input = document.getElementById(slot);
  const msg = document.getElementById('msg');
  if(!input.files.length){{ return; }}
  const fd = new FormData();
  fd.append('slot', slot);
  fd.append('file', input.files[0]);
  msg.textContent='Reading ID…';
  const r = await fetch('/api/paperwork/shannon/id/{token}', {{ method:'POST', body: fd }});
  const d = await r.json();
  if(!d.success){{ msg.textContent = d.error || 'Upload failed'; return; }}
  const name = (d.ocr && (d.ocr.indemnitor_name || d.ocr.defendant_name)) || '';
  msg.textContent = name ? ('Saved '+slot+'. We read '+name+'.') : ('Saved '+slot+'.');
}}
</script></body></html>"""
    return HTMLResponse(html)
