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
    require_cosigner_scan,
    reserve_scan_attempt,
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


def _country_conflicts_with_us_issuer(country: str) -> bool:
    """A residence country that is not the United States. Blank is not a conflict."""
    token = str(country or "").strip().upper().replace(".", "")
    if not token:
        return False
    return token not in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}


def _cosigner_issuer(extracted: dict) -> str:
    """The document issuer. An address state copied into ``dl_state`` is not one.

    The scanner fills ``dl_state`` from the address when no issuer was read.
    A foreign country plus that copied state must not unlock signing.
    """
    issuer = str(extracted.get("dl_state") or "").strip()
    address_state = str(extracted.get("state") or "").strip()
    country = str(extracted.get("issuing_country") or extracted.get("country") or "")
    same_state = (
        issuer
        and address_state
        and issuer.upper()[:2] == address_state.upper()[:2]
    )
    if same_state and _country_conflicts_with_us_issuer(country):
        return ""
    return issuer


async def _read_id_upload(form):
    """The uploaded photo, still unread by OCR. An empty file is not a scan."""
    upload = form.get("id_image")
    if upload is None or not hasattr(upload, "read"):
        return None
    raw = await upload.read()
    if not raw:
        return None
    filename = str(getattr(upload, "filename", "") or "")
    return raw, filename


async def _scan_body_from_upload(raw: bytes, filename: str, lang: str) -> dict:
    """OCR an ID photo that was already accepted for this token."""
    from dashboard.services.id_scanner_service import IDScannerService

    scanned = await IDScannerService.scan_id_image(raw, filename=filename)
    extracted = scanned.get("extracted") if isinstance(scanned, dict) else {}
    if not isinstance(extracted, dict):
        extracted = {}
    scanned = scanned if isinstance(scanned, dict) else {}
    # Full ID numbers from OCR stay out of the stored scan. Issuer fields
    # stay separate from the address so a country-only extract cannot pass
    # as a US state.
    return {
        "full_name": extracted.get("full_name") or "",
        "dl_state": _cosigner_issuer(extracted),
        "issuing_state": extracted.get("issuing_state") or "",
        "issuing_jurisdiction": extracted.get("issuing_jurisdiction") or "",
        "id_state": extracted.get("id_state") or "",
        "state": extracted.get("state") or "",
        "issuing_country": extracted.get("issuing_country") or extracted.get("country") or "",
        "success": bool(scanned.get("success") and extracted.get("full_name")),
        "passed": scanned.get("passed"),
        "status": scanned.get("status") or "",
        "lang": lang,
    }


@cosigner_bp.post("/api/cosigner/{token}/scan")
async def cosigner_scan(token: str, request: Request):
    try:
        await require_cosigner_scan(token)
    except CosignerLinkError as exc:
        return JSONResponse({"error": exc.code}, status_code=exc.status)
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type or "multipart/form-data" not in content_type:
        return JSONResponse({"error": "scan_image_required"}, status_code=400)
    form = await request.form()
    uploaded = await _read_id_upload(form)
    if uploaded is None:
        return JSONResponse({"error": "scan_image_required"}, status_code=400)
    raw, filename = uploaded
    try:
        await reserve_scan_attempt(token)
    except CosignerLinkError as exc:
        return JSONResponse({"error": exc.code}, status_code=exc.status)
    body = await _scan_body_from_upload(raw, filename, str(form.get("lang") or ""))
    try:
        state = await submit_cosigner_scan(token, body)
    except CosignerLinkError as exc:
        return HTMLResponse(exc.code, status_code=exc.status)
    return HTMLResponse(render_cosigner_page(state, token))


@cosigner_bp.get("/cosigner/{token}")
async def cosigner_page(token: str, lang: str = ""):
    try:
        state = await cosigner_state(token, lang=lang or None)
    except CosignerLinkError as exc:
        return HTMLResponse("Link not found", status_code=exc.status)
    return HTMLResponse(render_cosigner_page(state, token), headers={"Cache-Control": "no-store"})
