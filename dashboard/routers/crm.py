"""
ShamrockLeads Super CRM — Hub API
=================================
Unified health, pipeline overview, and global search for the intelligence CRM.

Endpoints:
  GET /api/crm/health     — module + Mongo readiness
  GET /api/crm/overview   — pipeline counts for Command Center / widgets
  GET /api/crm/search     — omnibar search (arrests, bonds, people, intake, tasks)
  GET /api/crm/pipeline   — ordered funnel stages for Super CRM
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from typing import Optional
from pydantic import BaseModel, Field

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

crm_bp = APIRouter(prefix="/api/crm", tags=["crm"])

# Core CRM collections expected in ShamrockBailDB
CRM_COLLECTIONS = (
    "arrests",
    "defendants",
    "indemnitors",
    "matches",
    "active_bonds",
    "prospective_bonds",
    "intake_queue",
    "payments",
    "payment_plans",
    "tasks",
    "paperwork_packets",
    "poa_inventory",
    "audit_events",
    "notifications",
    "scraper_status",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@crm_bp.get("/health")
async def crm_health():
    """
    Super CRM readiness probe.
    Returns per-collection existence/count sample and integration flags.
    """
    import os

    modules: dict = {}
    ok = True

    for name in CRM_COLLECTIONS:
        try:
            col = get_collection(name)
            # estimated_document_count is cheap; falls back to 0
            try:
                n = await col.estimated_document_count()
            except Exception:
                n = await col.count_documents({}, limit=1)
                n = 1 if n else 0
            modules[name] = {"ok": True, "approx_count": n}
        except Exception as exc:
            modules[name] = {"ok": False, "error": str(exc)[:120]}
            ok = False

    integrations = {
        "mongodb": ok,
        "gas_configured": bool(os.getenv("GAS_WEB_APP_URL") and os.getenv("GAS_API_KEY")),
        "wix_webhook_auth": bool(
            os.getenv("WIX_WEBHOOK_SECRET") or os.getenv("GAS_API_KEY")
        ),
        "signnow": bool(
            os.getenv("SIGNNOW_API_TOKEN") or os.getenv("SIGNNOW_BASIC_AUTH")
        ),
        "twilio": bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN")),
        "slack": bool(
            os.getenv("SLACK_WEBHOOK_LEADS") or os.getenv("SLACK_WEBHOOK_ARRESTS")
        ),
        "bluebubbles": bool(
            os.getenv("BLUEBUBBLES_URL_0178")
            or os.getenv("BLUEBUBBLES_URL")
        ),
        "dashboard_pin": bool(os.getenv("DASHBOARD_PIN")),
        "secret_key": bool(os.getenv("SECRET_KEY")),
    }

    if not integrations["dashboard_pin"] or not integrations["secret_key"]:
        ok = False

    return {
        "status": "ok" if ok else "degraded",
        "product": "ShamrockLeads Super CRM",
        "timestamp": _utc_now().isoformat(),
        "collections": modules,
        "integrations": integrations,
    }


@crm_bp.get("/overview")
async def crm_overview():
    """Pipeline snapshot for Super CRM widgets."""
    try:
        arrests = get_collection("arrests")
        bonds = get_collection("active_bonds")
        intake = get_collection("intake_queue")
        prosp = get_collection("prospective_bonds")
        tasks = get_collection("tasks")
        payments = get_collection("payments")
        scrapers = get_collection("scraper_status")

        since = (_utc_now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

        hot = await arrests.count_documents(
            {"lead_status": {"$regex": "^hot$", "$options": "i"}}
        )
        warm = await arrests.count_documents(
            {"lead_status": {"$regex": "^warm$", "$options": "i"}}
        )
        recent = await arrests.count_documents(
            {"scraped_at": {"$gte": since}}
        )
        active_bond_n = await bonds.count_documents(
            {"status": {"$nin": ["Exonerated", "Forfeited", "Surrendered", "Closed"]}}
        )
        open_intake = await intake.count_documents(
            {"status": {"$nin": ["completed", "closed", "bonded", "rejected"]}}
        )
        open_prosp = await prosp.count_documents(
            {"stage": {"$nin": ["won", "lost", "closed"]}}
        )
        pending_tasks = await tasks.count_documents({"status": "pending"})
        overdue_tasks = await tasks.count_documents({"status": "overdue"})
        pay_n = await payments.count_documents({})

        scraper_ok = 0
        scraper_err = 0
        async for doc in scrapers.find({}, {"status": 1}):
            st = (doc.get("status") or "").lower()
            if st in ("ok", "success", "healthy", ""):
                scraper_ok += 1
            else:
                scraper_err += 1

        return {
            "success": True,
            "pipeline": {
                "hot_leads": hot,
                "warm_leads": warm,
                "arrests_7d": recent,
                "active_bonds": active_bond_n,
                "open_intake": open_intake,
                "prospective": open_prosp,
                "tasks_pending": pending_tasks,
                "tasks_overdue": overdue_tasks,
                "payments_logged": pay_n,
                "scrapers_ok": scraper_ok,
                "scrapers_error": scraper_err,
            },
            "funnel": [
                {"stage": "Arrest / Lead", "count": hot + warm, "tab": "tabLeads"},
                {"stage": "Outreach", "count": open_prosp, "tab": "tabProspective"},
                {"stage": "Intake", "count": open_intake, "tab": "tabIntake"},
                {"stage": "Active Bond", "count": active_bond_n, "tab": "tabActiveBonds"},
                {"stage": "Tasks", "count": pending_tasks + overdue_tasks, "tab": "tabCommand"},
            ],
            "timestamp": _utc_now().isoformat(),
        }
    except Exception as exc:
        logger.exception("crm_overview failed")
        return JSONResponse(
            {"success": False, "error": str(exc)[:200]}, status_code=500
        )


@crm_bp.get("/pipeline")
async def crm_pipeline():
    """Alias of overview funnel for CRM integrations."""
    overview = await crm_overview()
    if isinstance(overview, JSONResponse):
        return overview
    return {
        "success": True,
        "stages": overview.get("funnel", []),
        "pipeline": overview.get("pipeline", {}),
    }


@crm_bp.get("/search")
async def crm_search(q: str = Query(default="", min_length=0)):
    """
    Unified Super CRM search for omnibar and mobile.
    Covers: active_bonds, arrests, prospective, indemnitors, intake, tasks.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return {"success": True, "results": [], "total": 0}

    try:
        results: list[dict] = []
        escaped = re.escape(q)
        regex = {"$regex": escaped, "$options": "i"}

        # Active bonds
        bonds_col = get_collection("active_bonds")
        async for doc in bonds_col.find(
            {
                "$or": [
                    {"defendant_name": regex},
                    {"booking_number": regex},
                    {"poa_number": regex},
                    {"case_number": regex},
                    {"indemnitor_name": regex},
                    {"indemnitor.name": regex},
                    {"indemnitor.phone": regex},
                ]
            },
            {
                "_id": 0,
                "defendant_name": 1,
                "booking_number": 1,
                "county": 1,
                "bond_amount": 1,
                "poa_number": 1,
                "case_number": 1,
                "status": 1,
                "indemnitor_name": 1,
            },
        ).limit(8):
            doc["source"] = "active_bonds"
            doc["type"] = "bond"
            results.append(doc)

        # Arrests / leads
        arrests_col = get_collection("arrests")
        async for doc in arrests_col.find(
            {
                "$or": [
                    {"full_name": regex},
                    {"booking_number": regex},
                    {"case_number": regex},
                    {"charges": regex},
                ]
            },
            {
                "_id": 0,
                "full_name": 1,
                "booking_number": 1,
                "county": 1,
                "bond_amount": 1,
                "total_bond_amount": 1,
                "lead_score": 1,
                "lead_status": 1,
                "custody_status": 1,
            },
        ).limit(8):
            doc["source"] = "arrests"
            doc["type"] = "lead"
            doc["defendant_name"] = doc.pop("full_name", "")
            if not doc.get("bond_amount"):
                doc["bond_amount"] = doc.get("total_bond_amount") or 0
            results.append(doc)

        # Prospective / outreach
        prosp_col = get_collection("prospective_bonds")
        async for doc in prosp_col.find(
            {
                "$or": [
                    {"defendant_name": regex},
                    {"booking_number": regex},
                ]
            },
            {
                "_id": 0,
                "defendant_name": 1,
                "booking_number": 1,
                "county": 1,
                "bond_amount": 1,
                "stage": 1,
            },
        ).limit(5):
            doc["source"] = "prospective_bonds"
            doc["type"] = "prospective"
            results.append(doc)

        # Indemnitors
        ind_col = get_collection("indemnitors")
        async for doc in ind_col.find(
            {
                "$or": [
                    {"name": regex},
                    {"full_name": regex},
                    {"email": regex},
                    {"phone": regex},
                ]
            },
            {"_id": 0, "name": 1, "full_name": 1, "email": 1, "phone": 1, "relationship": 1},
        ).limit(5):
            results.append(
                {
                    "source": "indemnitors",
                    "type": "indemnitor",
                    "defendant_name": doc.get("name") or doc.get("full_name") or "",
                    "booking_number": doc.get("phone") or doc.get("email") or "",
                    "bond_amount": "",
                    "stage": doc.get("relationship") or "indemnitor",
                }
            )

        # Intake queue
        intake_col = get_collection("intake_queue")
        async for doc in intake_col.find(
            {
                "$or": [
                    {"defendant_name": regex},
                    {"indemnitor_name": regex},
                    {"phone": regex},
                    {"email": regex},
                    {"county": regex},
                ]
            },
            {
                "_id": 1,
                "defendant_name": 1,
                "indemnitor_name": 1,
                "county": 1,
                "status": 1,
                "phone": 1,
            },
        ).limit(5):
            results.append(
                {
                    "source": "intake_queue",
                    "type": "intake",
                    "defendant_name": doc.get("defendant_name")
                    or doc.get("indemnitor_name")
                    or "Intake",
                    "booking_number": str(doc.get("_id", "")),
                    "county": doc.get("county"),
                    "stage": doc.get("status") or "intake",
                    "bond_amount": "",
                }
            )

        # Open tasks
        tasks_col = get_collection("tasks")
        async for doc in tasks_col.find(
            {
                "status": {"$in": ["pending", "overdue"]},
                "$or": [
                    {"title": regex},
                    {"booking_number": regex},
                    {"description": regex},
                ],
            },
            {
                "_id": 1,
                "title": 1,
                "booking_number": 1,
                "status": 1,
                "task_type": 1,
                "due_date": 1,
            },
        ).limit(5):
            results.append(
                {
                    "source": "tasks",
                    "type": "task",
                    "defendant_name": doc.get("title") or "Task",
                    "booking_number": doc.get("booking_number") or str(doc.get("_id")),
                    "stage": doc.get("status"),
                    "bond_amount": doc.get("task_type") or "",
                }
            )

        return {"success": True, "results": results, "total": len(results), "q": q}

    except Exception as exc:
        logger.exception("crm_search failed")
        return JSONResponse(
            {"success": False, "error": str(exc)[:200], "results": []},
            status_code=500,
        )


# ── Bond Desk Card Dismissal (Permanent Soft-Delete) ──────────────────────────

class CardDismissRequest(BaseModel):
    card_type: str = Field(..., description="'intake' or 'prospective' or 'lead'")
    card_id: str = Field(..., description="ID or booking number of the card")
    permanent: bool = Field(True, description="True for permanent desk soft-delete")
    actor: Optional[str] = Field("user", description="User or agent performing dismissal")


def _card_id_match_filter(id_fields: list[str], card_id: str) -> dict:
    """Build a $or filter that matches string IDs and ObjectId when valid."""
    clauses = [{field: card_id} for field in id_fields]
    try:
        from bson import ObjectId
        if ObjectId.is_valid(card_id):
            clauses.append({"_id": ObjectId(card_id)})
    except Exception:
        pass
    return {"$or": clauses}


@crm_bp.post("/cards/dismiss")
async def dismiss_card(req: CardDismissRequest):
    """
    Permanently remove (soft-delete) a card from the user's Bond Desk / Lead board.
    Underlying lead/defendant record remains stored in MongoDB for audit compliance.
    """
    now = datetime.now(timezone.utc)
    card_type = req.card_type.lower().strip()
    card_id = req.card_id.strip()
    if not card_id:
        return JSONResponse({"success": False, "error": "card_id required"}, status_code=400)

    dismiss_fields = {
        "desk_dismissed": True,
        "desk_dismissed_at": now.isoformat(),
        "desk_dismissed_by": req.actor or "user",
    }

    if card_type in ("intake", "intake_queue"):
        col = get_collection("intake_queue")
        res = await col.update_one(
            _card_id_match_filter(["intake_id"], card_id),
            {"$set": dismiss_fields},
        )
    elif card_type in ("prospective", "prospective_bonds", "lead"):
        col = get_collection("prospective_bonds")
        res = await col.update_one(
            _card_id_match_filter(["booking_number"], card_id),
            {"$set": dismiss_fields},
        )
    else:
        return JSONResponse({"success": False, "error": f"Invalid card_type '{card_type}'"}, status_code=400)

    if res.matched_count == 0:
        return JSONResponse({"success": False, "error": f"Card '{card_id}' not found in {card_type}"}, status_code=404)

    logger.info(f"[crm] Card permanently dismissed: type={card_type} id={card_id} by={req.actor}")
    return {"success": True, "card_id": card_id, "card_type": card_type, "desk_dismissed": True}


@crm_bp.post("/cards/restore")
async def restore_card(req: CardDismissRequest):
    """
    Restore a permanently dismissed card back to the Bond Desk.
    """
    card_type = req.card_type.lower().strip()
    card_id = req.card_id.strip()
    if not card_id:
        return JSONResponse({"success": False, "error": "card_id required"}, status_code=400)

    restore_fields = {
        "desk_dismissed": False,
        "desk_dismissed_at": None,
        "desk_dismissed_by": None,
    }

    if card_type in ("intake", "intake_queue"):
        col = get_collection("intake_queue")
        res = await col.update_one(
            _card_id_match_filter(["intake_id"], card_id),
            {"$set": restore_fields},
        )
    elif card_type in ("prospective", "prospective_bonds", "lead"):
        col = get_collection("prospective_bonds")
        res = await col.update_one(
            _card_id_match_filter(["booking_number"], card_id),
            {"$set": restore_fields},
        )
    else:
        return JSONResponse({"success": False, "error": f"Invalid card_type '{card_type}'"}, status_code=400)

    if res.matched_count == 0:
        return JSONResponse({"success": False, "error": f"Card '{card_id}' not found in {card_type}"}, status_code=404)

    return {"success": True, "card_id": card_id, "card_type": card_type, "desk_dismissed": False}

