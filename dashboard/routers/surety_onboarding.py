"""
Staff Add-surety API (Super CRM).

God-admin, admin, and staff only. Recovery and sub-agent sessions are refused.
Uploads, mapping, preview, and publish stay inside this process. Preview renders
a local PDF with fake sample data. Nothing is emailed, texted, or sent to DocuSeal.
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Any, Dict

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from dashboard.auth.pin_middleware import (
    DASHBOARD_PIN,
    get_session_from_request,
    is_machine_auth_valid,
    session_is_admin,
    session_is_god_admin,
)
from dashboard.services.surety_canonical import canonical_catalog, required_rule_ids, validate_publish
from dashboard.services.surety_packet_fill import render_preview
from dashboard.services.surety_template_store import (
    SuretyTemplateError,
    active_published,
    add_form,
    create_draft,
    get_version,
    list_onboarding,
    publish_draft,
    published_versions,
    update_draft,
)

logger = logging.getLogger(__name__)

surety_onboarding_bp = APIRouter(prefix="/api/crm/sureties", tags=["surety_onboarding"])


def _check_staff_auth(request: Request) -> tuple[bool, str]:
    """Same staff gate as the chain-ensure tool. Fail closed."""
    sess = get_session_from_request(request)
    if sess and sess.get("auth"):
        role = str(sess.get("role") or "").strip().lower()
        if role != "sub_agent" and role != "recovery" and (
            session_is_god_admin(request)
            or session_is_admin(request)
            or role in ("staff", "admin", "god_admin")
        ):
            actor = sess.get("email") or sess.get("agent_name") or "staff_session"
            return True, str(actor)

    if is_machine_auth_valid(request):
        actor = request.headers.get("X-Staff-Email", "").strip() or "machine_key"
        return True, actor

    admin_token = (
        request.headers.get("X-Admin-Token")
        or request.headers.get("X-PIN")
        or request.headers.get("X-Staff-PIN")
        or ""
    ).strip()
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer_val = auth_header[7:].strip()
        if bearer_val:
            admin_token = bearer_val

    active_pin = (os.getenv("DASHBOARD_PIN") or DASHBOARD_PIN or "").strip()
    if active_pin and admin_token and secrets.compare_digest(admin_token, active_pin):
        actor = request.headers.get("X-Staff-Email", "").strip() or "god_admin"
        return True, actor
    return False, ""


def _denied() -> JSONResponse:
    return JSONResponse(
        {"success": False, "error": "staff_auth_required"},
        status_code=401,
    )


def _error(exc: SuretyTemplateError, status: int = 400) -> JSONResponse:
    code = getattr(exc, "code", "surety_template_error")
    if code == "required_unmapped":
        http = 422
    elif code == "durable_storage_unavailable":
        http = 503
    else:
        http = status
    return JSONResponse(
        {"success": False, "error": code, "message": str(exc)},
        status_code=http,
    )


@surety_onboarding_bp.get("/onboarding/catalog")
async def onboarding_catalog(request: Request):
    ok, _actor = _check_staff_auth(request)
    if not ok:
        return _denied()
    return {
        "success": True,
        "fields": canonical_catalog(),
        "required": required_rule_ids(),
    }


@surety_onboarding_bp.get("/onboarding")
async def onboarding_list(request: Request):
    ok, _actor = _check_staff_auth(request)
    if not ok:
        return _denied()
    return {"success": True, "sureties": list_onboarding()}


@surety_onboarding_bp.get("/onboarding/{surety_id}/versions")
async def onboarding_versions(surety_id: str, request: Request):
    ok, _actor = _check_staff_auth(request)
    if not ok:
        return _denied()
    rows = []
    for doc in published_versions(surety_id):
        rows.append({
            "version_id": doc.get("version_id"),
            "version": doc.get("version"),
            "status": doc.get("status"),
            "immutable": doc.get("immutable"),
            "value_profile": doc.get("value_profile"),
            "docuseal_template_id": doc.get("docuseal_template_id") or "",
            "published_at": doc.get("published_at"),
            "published_by": doc.get("published_by"),
            "migration_seed": bool(doc.get("migration_seed")),
        })
    active = active_published(surety_id)
    return {
        "success": True,
        "surety_id": surety_id,
        "active_version_id": (active or {}).get("version_id"),
        "versions": rows,
    }


@surety_onboarding_bp.get("/onboarding/versions/{version_id}")
async def onboarding_version_detail(version_id: str, request: Request):
    ok, _actor = _check_staff_auth(request)
    if not ok:
        return _denied()
    doc = get_version(version_id)
    if not doc:
        return JSONResponse({"success": False, "error": "not_found"}, status_code=404)
    return {"success": True, "version": doc}


@surety_onboarding_bp.post("/onboarding/drafts")
async def onboarding_create_draft(request: Request):
    ok, actor = _check_staff_auth(request)
    if not ok:
        return _denied()
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"success": False, "error": "invalid_body"}, status_code=400)
    try:
        doc = create_draft(
            surety_id=str(body.get("surety_id") or ""),
            label=str(body.get("label") or ""),
            poa_prefixes=body.get("poa_prefixes") or [],
            repeat_per_charge=bool(body.get("repeat_per_charge", True)),
            docuseal_template_id=str(body.get("docuseal_template_id") or ""),
            drive_folder_label=str(body.get("drive_folder_label") or ""),
            owner_tenant_id=body.get("owner_tenant_id", body.get("owner_tenant")),
            entitled_tenant_ids=body.get("entitled_tenant_ids", body.get("entitlements")),
        )
    except SuretyTemplateError as exc:
        return _error(exc)
    logger.info("[surety-onboarding] draft %s created by %s", doc.get("version_id"), actor)
    return {"success": True, "version": doc}


@surety_onboarding_bp.post("/onboarding/drafts/{version_id}/forms")
async def onboarding_upload_form(
    version_id: str,
    request: Request,
    file: UploadFile = File(...),
):
    ok, _actor = _check_staff_auth(request)
    if not ok:
        return _denied()
    blob = await file.read()
    try:
        doc = add_form(version_id, file.filename or "form.pdf", blob)
    except SuretyTemplateError as exc:
        return _error(exc)
    return {"success": True, "version": doc}


@surety_onboarding_bp.put("/onboarding/drafts/{version_id}")
async def onboarding_update_draft(version_id: str, request: Request):
    ok, _actor = _check_staff_auth(request)
    if not ok:
        return _denied()
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"success": False, "error": "invalid_body"}, status_code=400)
    try:
        doc = update_draft(version_id, body)
    except SuretyTemplateError as exc:
        return _error(exc)
    return {"success": True, "version": doc, "publish_check": doc.get("publish_check")}


@surety_onboarding_bp.post("/onboarding/drafts/{version_id}/preview")
async def onboarding_preview(version_id: str, request: Request):
    """Local sample PDF. Does not call DocuSeal or contact anyone."""
    ok, _actor = _check_staff_auth(request)
    if not ok:
        return _denied()
    doc = get_version(version_id, include_storage=True)
    if not doc:
        return JSONResponse({"success": False, "error": "not_found"}, status_code=404)
    try:
        pdf = render_preview(doc)
    except SuretyTemplateError as exc:
        return _error(exc)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="surety-sample-preview.pdf"'},
    )


@surety_onboarding_bp.post("/onboarding/drafts/{version_id}/publish")
async def onboarding_publish(version_id: str, request: Request):
    ok, actor = _check_staff_auth(request)
    if not ok:
        return _denied()
    current = get_version(version_id)
    if not current:
        return JSONResponse({"success": False, "error": "not_found"}, status_code=404)
    if current.get("status") != "draft" or current.get("immutable"):
        return JSONResponse(
            {"success": False, "error": "immutable_version"},
            status_code=409,
        )
    check = validate_publish(current)
    if not check["ok"]:
        return JSONResponse(
            {
                "success": False,
                "error": "required_unmapped",
                "missing": check["missing"],
                "required": check["required"],
                "warnings": check["warnings"],
            },
            status_code=422,
        )
    try:
        doc = publish_draft(version_id, actor)
    except SuretyTemplateError as exc:
        return _error(exc)
    logger.info(
        "[surety-onboarding] published surety=%s version=%s by=%s",
        doc.get("surety_id"),
        doc.get("version"),
        actor,
    )
    return {"success": True, "version": doc, "warnings": doc.get("publish_warnings") or []}
