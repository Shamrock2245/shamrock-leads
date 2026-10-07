"""
BailSafe Recovery — staff share a forfeiture file; recovery works the card.

Recovery sessions are pinned to the route allowlist in
``dashboard.auth.recovery_scope``. This router still checks the role so a
missing middleware gate cannot widen access.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from dashboard.auth.pin_middleware import COOKIE_NAME, get_session_from_request
from dashboard.auth.recovery_scope import (
    recovery_id_from_session,
    session_is_recovery,
    session_is_staff,
)
from dashboard.services.recovery_case_service import (
    RecoveryError,
    add_case_document,
    add_case_note,
    drop_denied_keys,
    get_case_for_actor,
    list_cases_for_recovery,
    list_cases_for_staff,
    list_forfeiture_candidates,
    list_recovery_agents,
    provision_recovery_agent,
    resolve_document,
    revoke_share,
    set_disposition,
    share_forfeiture_case,
)

logger = logging.getLogger("shamrock.recovery")

recovery_bp = APIRouter(tags=["recovery"])

_PORTAL = os.path.join(os.path.dirname(os.path.dirname(__file__)), "recovery_portal.html")


def _json(payload: dict, status: int = 200) -> JSONResponse:
    response = JSONResponse(drop_denied_keys(payload), status_code=status)
    response.headers["Cache-Control"] = "no-store"
    return response


def _actor(request: Request) -> tuple[str, str, str, str | None]:
    sess = get_session_from_request(request) or {}
    email = str(sess.get("email") or "")
    if session_is_recovery(request):
        label = str(sess.get("agent_name") or sess.get("recovery_id") or "Recovery")
        return email, label, "recovery", recovery_id_from_session(request)
    return email, "Staff", "staff", None


def _require_user(request: Request):
    if not get_session_from_request(request):
        return _json({"success": False, "error": "Authentication required", "code": "auth_required"}, 401)
    return None


def _require_staff(request: Request):
    denied = _require_user(request)
    if denied:
        return denied
    if not session_is_staff(request):
        return _json({"success": False, "error": "Staff access required", "code": "recovery_staff_required"}, 403)
    return None


def _require_worker(request: Request):
    """Staff or recovery. Sub-agents are refused."""
    denied = _require_user(request)
    if denied:
        return denied
    if session_is_staff(request) or session_is_recovery(request):
        return None
    return _json({"success": False, "error": "Recovery access required", "code": "recovery_role_required"}, 403)


async def _read_json(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _handle(exc: Exception) -> JSONResponse:
    if isinstance(exc, RecoveryError):
        return _json({"success": False, "error": exc.message, "code": exc.code}, exc.status)
    logger.exception("recovery request failed")
    return _json({"success": False, "error": "Recovery request failed", "code": "recovery_error"}, 500)


@recovery_bp.api_route("/recovery", methods=["GET", "HEAD"], include_in_schema=False)
async def recovery_portal(request: Request):
    sess = get_session_from_request(request)
    headers = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}
    if not sess:
        return RedirectResponse("/login?next=%2Frecovery", status_code=302)
    if not (session_is_staff(request) or session_is_recovery(request)):
        return HTMLResponse(
            "Recovery is limited to staff and recovery agents.",
            status_code=403,
            headers=headers,
        )
    if request.method == "HEAD":
        return HTMLResponse("", headers=headers)
    return FileResponse(_PORTAL, media_type="text/html", headers=headers)


@recovery_bp.post("/api/recovery/logout")
async def recovery_logout():
    response = _json({"success": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@recovery_bp.get("/api/recovery/agents")
async def api_list_agents(request: Request):
    denied = _require_staff(request)
    if denied:
        return denied
    try:
        agents = await list_recovery_agents()
        return _json({"success": True, "agents": agents})
    except Exception as exc:
        return _handle(exc)


@recovery_bp.post("/api/recovery/agents")
async def api_provision_agent(request: Request):
    denied = _require_staff(request)
    if denied:
        return denied
    data = await _read_json(request)
    actor, _label, _role, _rid = _actor(request)
    try:
        result = await provision_recovery_agent(
            str(data.get("recovery_id") or ""),
            str(data.get("display_name") or ""),
            actor=actor,
        )
        return _json(result)
    except Exception as exc:
        return _handle(exc)


@recovery_bp.get("/api/recovery/forfeiture-candidates")
async def api_forfeiture_candidates(request: Request):
    denied = _require_staff(request)
    if denied:
        return denied
    try:
        rows = await list_forfeiture_candidates()
        return _json({"success": True, "count": len(rows), "forfeitures": rows})
    except Exception as exc:
        return _handle(exc)


@recovery_bp.post("/api/recovery/shares")
async def api_share_case(request: Request):
    denied = _require_staff(request)
    if denied:
        return denied
    data = await _read_json(request)
    actor, _label, _role, _rid = _actor(request)
    try:
        result = await share_forfeiture_case(
            booking_number=str(data.get("booking_number") or ""),
            bond_case_id=str(data.get("bond_case_id") or ""),
            staff_notes=str(data.get("staff_notes") or ""),
            expires_in_days=data.get("expires_in_days"),
            recovery_agent_id=str(data.get("recovery_agent_id") or ""),
            actor=actor,
        )
        return _json(result)
    except Exception as exc:
        return _handle(exc)


@recovery_bp.post("/api/recovery/shares/{share_id}/revoke")
async def api_revoke_share(request: Request, share_id: str):
    denied = _require_staff(request)
    if denied:
        return denied
    actor, _label, _role, _rid = _actor(request)
    try:
        result = await revoke_share(share_id, actor=actor)
        return _json(result)
    except Exception as exc:
        return _handle(exc)


@recovery_bp.get("/api/recovery/shares")
async def api_list_shares(request: Request):
    denied = _require_staff(request)
    if denied:
        return denied
    include_closed = request.query_params.get("include") == "closed"
    try:
        cases = await list_cases_for_staff(include_closed=include_closed)
        return _json({"success": True, "count": len(cases), "cases": cases})
    except Exception as exc:
        return _handle(exc)


@recovery_bp.get("/api/recovery/cases")
async def api_list_cases(request: Request):
    denied = _require_worker(request)
    if denied:
        return denied
    try:
        if session_is_recovery(request):
            cases = await list_cases_for_recovery(recovery_id_from_session(request))
        else:
            include_closed = request.query_params.get("include") == "closed"
            cases = await list_cases_for_staff(include_closed=include_closed)
        return _json({"success": True, "count": len(cases), "cases": cases})
    except Exception as exc:
        return _handle(exc)


@recovery_bp.get("/api/recovery/cases/{share_id}")
async def api_get_case(request: Request, share_id: str):
    denied = _require_worker(request)
    if denied:
        return denied
    try:
        if session_is_recovery(request):
            case = await get_case_for_actor(
                share_id,
                recovery_id=recovery_id_from_session(request),
                allow_closed=False,
            )
        else:
            case = await get_case_for_actor(share_id, recovery_id=None, allow_closed=True)
        return _json({"success": True, "case": case})
    except Exception as exc:
        return _handle(exc)


@recovery_bp.post("/api/recovery/cases/{share_id}/notes")
async def api_add_note(request: Request, share_id: str):
    denied = _require_worker(request)
    if denied:
        return denied
    data = await _read_json(request)
    actor, label, role, recovery_id = _actor(request)
    try:
        case = await add_case_note(
            share_id,
            str(data.get("text") or ""),
            actor=actor,
            actor_label=label,
            actor_role=role,
            recovery_id=recovery_id,
        )
        return _json({"success": True, "case": case})
    except Exception as exc:
        return _handle(exc)


@recovery_bp.post("/api/recovery/cases/{share_id}/disposition")
async def api_set_disposition(request: Request, share_id: str):
    denied = _require_worker(request)
    if denied:
        return denied
    data = await _read_json(request)
    actor, label, role, recovery_id = _actor(request)
    try:
        case = await set_disposition(
            share_id,
            str(data.get("disposition") or ""),
            actor=actor,
            actor_label=label,
            actor_role=role,
            recovery_id=recovery_id,
        )
        return _json({"success": True, "case": case})
    except Exception as exc:
        return _handle(exc)


@recovery_bp.post("/api/recovery/cases/{share_id}/documents")
async def api_upload_document(request: Request, share_id: str):
    denied = _require_worker(request)
    if denied:
        return denied
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return _json({"success": False, "error": "A file is required.", "code": "document_required"}, 400)
    data = await upload.read()
    actor, label, role, recovery_id = _actor(request)
    try:
        case = await add_case_document(
            share_id,
            filename=str(getattr(upload, "filename", "") or "document"),
            data=data,
            actor=actor,
            actor_label=label,
            actor_role=role,
            recovery_id=recovery_id,
        )
        return _json({"success": True, "case": case})
    except Exception as exc:
        return _handle(exc)


@recovery_bp.get("/api/recovery/cases/{share_id}/documents/{doc_id}")
async def api_download_document(request: Request, share_id: str, doc_id: str):
    denied = _require_worker(request)
    if denied:
        return denied
    recovery_id = recovery_id_from_session(request) if session_is_recovery(request) else None
    try:
        path, meta = await resolve_document(share_id, doc_id, recovery_id=recovery_id)
    except Exception as exc:
        return _handle(exc)
    return FileResponse(
        path,
        media_type=meta.get("content_type") or "application/octet-stream",
        filename=meta.get("filename") or path.name,
        headers={"Cache-Control": "no-store"},
    )
