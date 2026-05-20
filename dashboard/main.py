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

# ── CORS ── restrict to known origins in production ──
_ALLOWED_ORIGINS = [
    "https://leads.shamrockbailbonds.biz",
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
# ── Phase 1 Routers (hand-ported) ──
from dashboard.routers.arrests import router as arrests_router
from dashboard.routers.stats import router as stats_router
from dashboard.routers.leads import router as leads_router
from dashboard.routers.defendants import router as defendants_router
from dashboard.routers.indemnitors import router as indemnitors_router

app.include_router(arrests_router)
app.include_router(stats_router)
app.include_router(leads_router)
app.include_router(defendants_router)
app.include_router(indemnitors_router)

# ── Phase 2 Routers (auto-migrated from Quart blueprints) ──
from dashboard.routers.accounting import accounting_bp as accounting_router
from dashboard.routers.agent_analytics import agent_analytics_bp as agent_analytics_router
from dashboard.routers.agent_brain_api import agent_brain_api_bp as agent_brain_api_router
from dashboard.routers.analytics import analytics_bp as analytics_router
from dashboard.routers.automation_control import automation_control_bp as automation_control_router
from dashboard.routers.bb_contact_sync import bb_contacts_bp as bb_contact_sync_router
from dashboard.routers.bb_document_delivery import bb_docs_bp as bb_document_delivery_router
from dashboard.routers.bb_health_monitor import bb_health_bp as bb_health_monitor_router
from dashboard.routers.bb_prospecting import bb_prospecting_bp as bb_prospecting_router
from dashboard.routers.bb_scheduled_messages import bb_schedule_bp as bb_scheduled_messages_router
from dashboard.routers.bb_webhook_receiver import bb_webhook_bp as bb_webhook_receiver_router
from dashboard.routers.bond_lifecycle import bond_lifecycle_bp as bond_lifecycle_router
from dashboard.routers.bonds import bonds_bp as bonds_router
from dashboard.routers.calendar import calendar_bp as calendar_router
from dashboard.routers.client_portal import portal_bp as client_portal_router
from dashboard.routers.contacts import contacts_bp as contacts_router
from dashboard.routers.court_dockets import court_intel_bp as court_dockets_router
from dashboard.routers.court_reminders import court_reminders_bp as court_reminders_router
from dashboard.routers.data_retention import retention_bp as data_retention_router
from dashboard.routers.defendant_lifecycle import lifecycle_bp as defendant_lifecycle_router
from dashboard.routers.discharge_monitor import discharge_monitor_bp as discharge_monitor_router
from dashboard.routers.docket_monitor_api import docket_monitor_bp as docket_monitor_api_router
from dashboard.routers.events import events_bp as events_router
from dashboard.routers.fldfs_compliance import fldfs_bp as fldfs_compliance_router
from dashboard.routers.fta import fta_bp as fta_router
from dashboard.routers.geo import geo_bp as geo_router
from dashboard.routers.geo_intelligence import geo_intel_bp as geo_intelligence_router
from dashboard.routers.imessage_automation import imessage_auto_bp as imessage_automation_router
from dashboard.routers.intelligence import intelligence_bp as intelligence_router
from dashboard.routers.intake import intake_bp as intake_router
from dashboard.routers.lead_intelligence import lead_intel_bp as lead_intelligence_router
from dashboard.routers.legacy import legacy_bp as legacy_router
from dashboard.routers.legal_nlp import legal_nlp_bp as legal_nlp_router
from dashboard.routers.lifecycle_timeline import lifecycle_timeline_bp as lifecycle_timeline_router
from dashboard.routers.match_manager import match_manager_bp as match_manager_router
from dashboard.routers.matching import matching_bp as matching_router
from dashboard.routers.ml_intelligence import ml_bp as ml_intelligence_router
from dashboard.routers.notifications import notifications_bp as notifications_router
from dashboard.routers.ops_summary import ops_summary_bp as ops_summary_router
from dashboard.routers.outreach import outreach_bp as outreach_router
from dashboard.routers.paperwork import paperwork_bp as paperwork_router
from dashboard.routers.payment_plans import payment_plans_bp as payment_plans_router
from dashboard.routers.payments import payments_bp as payments_router
from dashboard.routers.poa import poa_bp as poa_router
from dashboard.routers.prospective_bonds import prospective_bonds_bp as prospective_bonds_router
from dashboard.routers.rearrest_detector import rearrest_bp as rearrest_detector_router
from dashboard.routers.rearrest_notifier import rearrest_bp as rearrest_notifier_router
from dashboard.routers.reports import reports_bp as reports_router
from dashboard.routers.scraper_control import scraper_control_bp as scraper_control_router
from dashboard.routers.source_performance import source_performance_bp as source_performance_router
from dashboard.routers.tracking import tracking_bp as tracking_router
from dashboard.routers.webhooks import webhooks_bp as webhooks_router
from dashboard.routers.wix_cms import wix_cms_bp as wix_cms_router

for _r in [
    accounting_router, agent_analytics_router, agent_brain_api_router, analytics_router, automation_control_router,
    bb_contact_sync_router, bb_document_delivery_router,
    bb_health_monitor_router, bb_prospecting_router, bb_scheduled_messages_router,
    bb_webhook_receiver_router, bond_lifecycle_router, bonds_router, calendar_router, client_portal_router,
    contacts_router, court_dockets_router, court_reminders_router,
    data_retention_router, defendant_lifecycle_router, discharge_monitor_router,
    docket_monitor_api_router, events_router, fldfs_compliance_router, fta_router,
    geo_router, geo_intelligence_router, imessage_automation_router,
    intelligence_router, intake_router, lead_intelligence_router, legacy_router,
    legal_nlp_router, lifecycle_timeline_router, match_manager_router,
    matching_router, ml_intelligence_router, notifications_router,
    ops_summary_router, outreach_router, paperwork_router, payment_plans_router,
    payments_router, poa_router, prospective_bonds_router,
    rearrest_detector_router, rearrest_notifier_router, reports_router,
    scraper_control_router, source_performance_router, tracking_router,
    webhooks_router, wix_cms_router,
]:
    app.include_router(_r)



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
