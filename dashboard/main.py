"""
ShamrockLeads Dashboard — FastAPI Application Factory

Production entry point for the async dashboard API.
Migration from Quart is COMPLETE as of 2026-05-19.

Usage:
    uvicorn dashboard.main:app --host 0.0.0.0 --port 5050 --workers 1 --access-log

Architecture:
    - deps.py         → DI providers (get_db, get_collection, get_settings)
    - cron.py         → Background task extraction (16+ loops)
    - auth/           → PIN auth middleware (itsdangerous signed cookies)
    - routers/        → FastAPI APIRouter instances (63 routers, all Quart-free)
    - routers/events  → SSE fan-out via sse-starlette EventSourceResponse
"""
from __future__ import annotations

import os
import logging
import traceback
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Load .env file from the project root
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_project_root, ".env"))

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from dashboard.deps import get_db, get_collection, get_settings
from dashboard.extensions import init_bluebubbles
from dashboard.auth.pin_middleware import PinAuthMiddleware, mount_login_routes
from dashboard.logging_redaction import SensitiveDataRedactionFilter

logger = logging.getLogger(__name__)
logger.addFilter(SensitiveDataRedactionFilter())

# ── Dashboard directory — for serving static assets ──
DASHBOARD_DIR = os.path.dirname(__file__)


async def _ensure_core_indexes_async():
    """Ensure high-performance MongoDB indices on startup for M0 tier hygiene."""
    try:
        from dashboard.deps import get_collection
        
        # Paperwork Packets
        packets = get_collection("paperwork_packets")
        await packets.create_index("packet_id", sparse=True)
        await packets.create_index("intake_id")
        await packets.create_index("docuseal_submission_id", sparse=True)
        await packets.create_index("indemnitor_phone")
        await packets.create_index("unassigned_defendant")
        await packets.create_index([("created_at", -1)])

        # Portal PIN OTPs
        pins = get_collection("portal_pins")
        await pins.create_index("phone")
        await pins.create_index([("created_at", -1)])

        # Active Bonds
        bonds = get_collection("active_bonds")
        await bonds.create_index("bond_case_id", sparse=True)
        await bonds.create_index("booking_number", sparse=True)
        await bonds.create_index("defendant_name")

        # Audit Events
        events = get_collection("audit_events")
        await events.create_index("event_id", sparse=True)
        await events.create_index([("timestamp", -1)])
        
        logger.info("⚡ Core MongoDB indexes verified.")
    except Exception as exc:
        logger.warning("Core index creation warning: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Lifespan: startup / shutdown
# ═══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup → background crons → yield → shutdown cleanup."""
    logger.info("☘️  FastAPI lifespan: startup")

    # ── Initialize singletons ──
    init_bluebubbles()

    # ── Seed POA inventory & verify core MongoDB indexes ──
    from dashboard.extensions import _seed_poa_inventory_async
    await _seed_poa_inventory_async()
    await _ensure_core_indexes_async()

    # ── Start background cron loops ──
    from dashboard.cron import start_all_crons
    tasks = await start_all_crons()

    db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
    logger.info(
        "☘️  FastAPI ready — Motor connected to %s — %d cron tasks launched",
        db_name, len(tasks),
    )

    yield  # ── Application runs ──

    # ── Shutdown: cancel all cron tasks + close pooled HTTP clients ──
    for t in tasks:
        t.cancel()
    try:
        from dashboard.services.docuseal_service import close_docuseal_service
        await close_docuseal_service()
    except Exception as exc:
        logger.debug("DocuSeal client close: %s", exc)
    logger.info("☘️  FastAPI lifespan: shutdown — %d cron tasks cancelled", len(tasks))


# ═══════════════════════════════════════════════════════════════════════════════
# App Instance
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="ShamrockLeads Intelligence Dashboard",
    description="Florida Arrest Intelligence & Bond Lifecycle Platform",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    redirect_slashes=True,
)

# ── CORS ── restrict to known origins in production ──
_ALLOWED_ORIGINS = [
    "https://leads.shamrockbailbonds.biz",
    "https://paperwork.shamrockbailbonds.biz",
    "https://sign.shamrockbailbonds.biz",
    "http://178.156.179.237:8088",
    "http://localhost:5050",
    "http://localhost:8088",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── PIN Authentication ──
app.add_middleware(PinAuthMiddleware)
mount_login_routes(app)


# ── Ensure unhandled errors return JSON (not Starlette plain-text "Internal Server Error") ──
# Starlette still routes HTTPException / RequestValidationError to their own handlers (MRO).
@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception):
    """Return a stable, non-sensitive JSON error with an operator lookup ID."""
    path = request.url.path or ""
    request_id = str(uuid.uuid4())
    logger.error(
        "Unhandled error route=%s correlation_id=%s",
        path,
        request_id,
        extra={
            "correlation_id": request_id,
            "route_path": path,
            "exception_type": type(exc).__name__,
            "exception_details": str(exc),
            "exception_traceback": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
        },
    )
    payload = {
        "error": "An unexpected error occurred.",
        "request_id": request_id,
    }
    return JSONResponse(payload, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════════
# No-Cache Middleware for JS/CSS (replaces Quart serve_static() headers)
# ═══════════════════════════════════════════════════════════════════════════════

@app.middleware("http")
async def cache_static_assets(request: Request, call_next):
    """Cache versioned JS/CSS aggressively; revalidate unversioned assets.

    index.html already busts caches with ``?v=N`` query strings. Allowing the
    browser to keep those responses cuts repeat dashboard load time dramatically
    while still letting deploys take effect when the version bump changes.
    """
    response: Response = await call_next(request)
    path = request.url.path
    if path.endswith((".js", ".css")):
        has_version = bool(request.query_params.get("v"))
        if has_version:
            # Versioned URL → safe long cache (immutable content for that URL)
            response.headers["Cache-Control"] = "public, max-age=86400, immutable"
        else:
            # Unversioned → short revalidate so deploys still land quickly
            response.headers["Cache-Control"] = "public, max-age=120, must-revalidate"
        # Starlette MutableHeaders has no .pop(); delete safely if present
        for header_name in ("pragma", "expires"):
            if header_name in response.headers:
                del response.headers[header_name]
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# Register Routers
# ═══════════════════════════════════════════════════════════════════════════════
from dashboard.routers import init_routers

init_routers(app)




# ── Health Check ──
@app.get("/health", tags=["infra"])
async def health():
    """Health check — verifies MongoDB connectivity + full router registration."""
    from dashboard.routers import FAILED_ROUTER_MODULES
    try:
        arrests = get_collection("arrests")
        total = await arrests.estimated_document_count()
        body = {"status": "ok", "engine": "fastapi", "total_arrests": total}
        if FAILED_ROUTER_MODULES:
            # A failed router module silently removes its endpoint group —
            # degrade health so ops sees the gap instead of a green check.
            body["status"] = "degraded"
            body["failed_router_modules"] = FAILED_ROUTER_MODULES
            return JSONResponse(body, status_code=503)
        return body
    except Exception:
        return JSONResponse({"status": "degraded", "engine": "fastapi"}, status_code=503)


@app.get("/health/live", tags=["infra"])
async def health_live():
    """Liveness check — verifies the FastAPI process is serving requests."""
    return {"status": "ok", "engine": "fastapi"}


# ═══════════════════════════════════════════════════════════════════════════════
# Static File Serving (SPA fallback)
# ═══════════════════════════════════════════════════════════════════════════════
# Quart used a catch-all route. FastAPI uses StaticFiles mount with a custom
# SPA fallback. Mounted LAST so API routes take priority.

@app.get("/", include_in_schema=False)
async def index(request: Request):
    """Serve staff CRM dashboard, or indemnitor portal on paperwork host.

    Two PIN access points must stay separate:
      - leads.shamrockbailbonds.biz / :8088 → staff Auto-CRM (index.html)
      - paperwork.shamrockbailbonds.biz → indemnitor OTP portal
    """
    host = (request.headers.get("host") or request.url.hostname or "").lower().split(":")[0]
    if "paperwork.shamrockbailbonds.biz" in host or host.startswith("paperwork.") or host == "paperwork.localhost":
        from dashboard.routers.pin_portal import get_portal_ui
        return await get_portal_ui(request)
    return FileResponse(os.path.join(DASHBOARD_DIR, "index.html"))


@app.get("/track/{session_id}", include_in_schema=False)
async def trape_lure_redirect(request: Request, session_id: str):
    """
    Handle Trape tracking links natively.
    Captures IP, User-Agent, and redirects to the lure_url.
    Also updates the OSINT Trape session if it exists.
    """
    from dashboard.deps import get_collection
    from dashboard.services.osint_service import get_osint_service
    from fastapi.responses import RedirectResponse
    
    try:
        svc = get_osint_service()
        trape_col = get_collection("osint_trape_sessions")
        session = await trape_col.find_one({"session_id": session_id})
        
        if not session:
            # Fallback redirect if session missing
            return RedirectResponse("https://shamrockbailbonds.biz")
            
        lure_url = session.get("lure_url") or "https://shamrockbailbonds.biz"
        
        # Capture basic IP and UA
        ip_address = request.headers.get("X-Forwarded-For", request.client.host if request.client else "")
        if ip_address and "," in ip_address:
            ip_address = ip_address.split(",")[0].strip()
            
        user_agent = request.headers.get("User-Agent", "")
        
        await svc.update_trape_session(
            session_id=session_id,
            data={
                "ip_address": ip_address,
                "device_info": user_agent,
            },
            actor="system"
        )
        
        return RedirectResponse(lure_url)
    except Exception as e:
        logger.error("Error handling /track/%s: %s", session_id, e)
        return RedirectResponse("https://shamrockbailbonds.biz")



@app.get("/uploads/{entity_key}/{filename}", include_in_schema=False)
async def serve_identity_upload(entity_key: str, filename: str):
    """Serve identity media (DL/ID/selfie) from dashboard/uploads/.

    Registered before the catch-all StaticFiles mount so files are reachable
    at ``/uploads/<booking|entity>/<file>`` (matches frontend img srcs).
    """
    from dashboard.services.identity_media_service import resolve_upload_path

    path = resolve_upload_path(entity_key, filename)
    if path is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(str(path))


# Mount static files for JS/CSS/images/etc.
# html=True enables SPA fallback (returns index.html for unmatched paths)
app.mount(
    "/",
    StaticFiles(directory=DASHBOARD_DIR, html=True),
    name="static",
)
