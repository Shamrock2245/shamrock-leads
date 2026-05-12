"""
ShamrockLeads Dashboard — FastAPI Application Factory

Production entry point for the async dashboard API (Phase 0 migration).
Runs in PARALLEL with Quart during migration — no production disruption.

Usage:
    uvicorn dashboard.main:app --host 0.0.0.0 --port 5050 --workers 1 --access-log

Architecture:
    - deps.py         → DI providers (get_db, get_collection, get_settings)
    - cron.py         → Background task extraction (16+ loops)
    - auth/           → PIN auth middleware (itsdangerous signed cookies)
    - routers/        → FastAPI APIRouter instances (migrated from api/ blueprints)
"""
from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from dashboard.deps import get_db, get_collection, get_settings
from dashboard.extensions import init_bluebubbles
from dashboard.auth.pin_middleware import PinAuthMiddleware, mount_login_routes

logger = logging.getLogger(__name__)

# ── Dashboard directory — for serving static assets ──
DASHBOARD_DIR = os.path.dirname(__file__)


# ═══════════════════════════════════════════════════════════════════════════════
# Lifespan: startup / shutdown
# ═══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup → background crons → yield → shutdown cleanup."""
    logger.info("☘️  FastAPI lifespan: startup")

    # ── Initialize singletons ──
    init_bluebubbles()

    # ── Seed POA inventory (first boot only) ──
    from dashboard.extensions import _seed_poa_inventory_async
    await _seed_poa_inventory_async()

    # ── Start background cron loops ──
    from dashboard.cron import start_all_crons
    tasks = await start_all_crons()

    db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
    logger.info(
        "☘️  FastAPI ready — Motor connected to %s — %d cron tasks launched",
        db_name, len(tasks),
    )

    yield  # ── Application runs ──

    # ── Shutdown: cancel all cron tasks ──
    for t in tasks:
        t.cancel()
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
)

# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── PIN Authentication ──
app.add_middleware(PinAuthMiddleware)
mount_login_routes(app)


# ═══════════════════════════════════════════════════════════════════════════════
# No-Cache Middleware for JS/CSS (replaces Quart serve_static() headers)
# ═══════════════════════════════════════════════════════════════════════════════

@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """Ensure JS/CSS files are never cached — deploys take effect immediately."""
    response: Response = await call_next(request)
    path = request.url.path
    if path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# Register Routers
# ═══════════════════════════════════════════════════════════════════════════════
# ── Phase 1 Routers (ported from Quart blueprints) ──
from dashboard.routers.arrests import router as arrests_router
from dashboard.routers.stats import router as stats_router
from dashboard.routers.leads import router as leads_router
from dashboard.routers.defendants import router as defendants_router

app.include_router(arrests_router)
app.include_router(stats_router)
app.include_router(leads_router)
app.include_router(defendants_router)


# ── Health Check ──
@app.get("/health", tags=["infra"])
async def health():
    """Health check — verifies MongoDB connectivity."""
    try:
        arrests = get_collection("arrests")
        total = await arrests.estimated_document_count()
        return {"status": "ok", "engine": "fastapi", "total_arrests": total}
    except Exception:
        return JSONResponse({"status": "degraded", "engine": "fastapi"}, status_code=503)


# ═══════════════════════════════════════════════════════════════════════════════
# Static File Serving (SPA fallback)
# ═══════════════════════════════════════════════════════════════════════════════
# Quart used a catch-all route. FastAPI uses StaticFiles mount with a custom
# SPA fallback. Mounted LAST so API routes take priority.

@app.get("/", include_in_schema=False)
async def index():
    """Serve dashboard index.html."""
    return FileResponse(os.path.join(DASHBOARD_DIR, "index.html"))


# Mount static files for JS/CSS/images/etc.
# html=True enables SPA fallback (returns index.html for unmatched paths)
app.mount(
    "/",
    StaticFiles(directory=DASHBOARD_DIR, html=True),
    name="static",
)
