"""Public co-signer link and the staff action that creates it."""

from __future__ import annotations

import json as _json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard.services.cosigner_link_service import (
    CosignerLinkError,
    cosigner_state,
    create_cosigner_link,
    render_cosigner_page,
    submit_cosigner_scan,
)

cosigner_bp = APIRouter(tags=["cosigner"])


def _actor(request: Request):
    from dashboard.routers.paperwork import _attestation_actor

    allowed = _attestation_actor(request)
    if not allowed:
        from dashboard.auth.pin_middleware import get_session_from_request

        sess = get_session_from_request(request)
        if not sess or not sess.get("auth"):
            return None, 401
        return None, 403
    return allowed[1], 200


@cosigner_bp.post("/api/paperwork/cosigner-link")
async def issue_cosigner_link(request: Request):
    actor, status = _actor(request)
    if status != 200:
        code = "auth_required" if status == 401 else "staff_required"
        return JSONResponse({"error": code}, status_code=status)
    try:
        body = _json.loads(await request.body() or b"{}")
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_body"}, status_code=400)
    packet_id = str(body.get("packet_id") or "").strip()
    if not packet_id:
        return JSONResponse({"error": "packet_id_required"}, status_code=400)
    try:
        created = await create_cosigner_link(
            actor=actor,
            packet_id=packet_id,
            lang=str(body.get("lang") or "en"),
        )
    except CosignerLinkError as exc:
        return JSONResponse({"error": exc.code}, status_code=exc.status)
    return created


@cosigner_bp.get("/api/cosigner/{token}")
async def cosigner_status(token: str, lang: str = ""):
    try:
        state = await cosigner_state(token, lang=lang or None)
    except CosignerLinkError as exc:
        return JSONResponse({"error": exc.code}, status_code=exc.status)
    return state


async def _scan_body_from_upload(form) -> dict | None:
    """OCR the uploaded ID. The typed name is not a scan."""
    upload = form.get("id_image")
    if upload is None or not hasattr(upload, "read"):
        return None
    raw = await upload.read()
    if not raw:
        return None
    from dashboard.services.id_scanner_service import IDScannerService

    scanned = await IDScannerService.scan_id_image(
        raw, filename=str(getattr(upload, "filename", "") or "")
    )
    extracted = scanned.get("extracted") if isinstance(scanned, dict) else {}
    if not isinstance(extracted, dict):
        extracted = {}
    scanned = scanned if isinstance(scanned, dict) else {}
    # Full ID numbers from OCR stay out of the stored scan. Issuer fields
    # stay separate from the address so a country-only extract cannot pass
    # as a US state.
    return {
        "full_name": extracted.get("full_name") or "",
        "dl_state": extracted.get("dl_state") or "",
        "issuing_state": extracted.get("issuing_state") or "",
        "issuing_jurisdiction": extracted.get("issuing_jurisdiction") or "",
        "id_state": extracted.get("id_state") or "",
        "state": extracted.get("state") or "",
        "issuing_country": extracted.get("issuing_country") or extracted.get("country") or "",
        "success": bool(scanned.get("success") and extracted.get("full_name")),
        "passed": scanned.get("passed"),
        "status": scanned.get("status") or "",
        "lang": form.get("lang") or "",
    }


@cosigner_bp.post("/api/cosigner/{token}/scan")
async def cosigner_scan(token: str, request: Request):
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            body = _json.loads(await request.body() or b"{}")
        except Exception:
            body = None
    else:
        form = await request.form()
        body = await _scan_body_from_upload(form)
        if body is None:
            return JSONResponse({"error": "scan_image_required"}, status_code=400)
    try:
        state = await submit_cosigner_scan(token, body if isinstance(body, dict) else {})
    except CosignerLinkError as exc:
        if "application/json" in content_type:
            return JSONResponse({"error": exc.code}, status_code=exc.status)
        return HTMLResponse(exc.code, status_code=exc.status)
    if "application/json" in content_type:
        return state
    return HTMLResponse(render_cosigner_page(state, token))


@cosigner_bp.get("/cosigner/{token}")
async def cosigner_page(token: str, lang: str = ""):
    try:
        state = await cosigner_state(token, lang=lang or None)
    except CosignerLinkError as exc:
        return HTMLResponse("Link not found", status_code=exc.status)
    return HTMLResponse(render_cosigner_page(state, token), headers={"Cache-Control": "no-store"})
