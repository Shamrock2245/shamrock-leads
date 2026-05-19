from fastapi import APIRouter, Request
"""
Wix CMS API Blueprint — Dashboard Integration
===============================================
Exposes Wix CMS and CRM operations via the ShamrockLeads dashboard API.

Endpoints:
    GET    /api/wix/status              — Sync engine status
    POST   /api/wix/sync/intakes        — Sync intakes to Wix CMS
    POST   /api/wix/sync/cases          — Sync bond cases to Wix CMS
    POST   /api/wix/sync/leads-to-crm   — Push hot leads to Wix CRM
    POST   /api/wix/sync/full           — Run all sync operations
    GET    /api/wix/collections         — List Wix CMS collections
    POST   /api/wix/cms/query           — Query a Wix CMS collection
    POST   /api/wix/cms/insert          — Insert item into Wix CMS
    POST   /api/wix/crm/query           — Query Wix CRM contacts
    POST   /api/wix/crm/upsert          — Create/update CRM contact
"""

import logging

from wix.sync import WixSyncEngine
from wix.data import WixDataClient
from wix.contacts import WixContactsClient
from wix.client import WixClient, WixAPIError

logger = logging.getLogger("dashboard.api.wix_cms")

wix_cms_bp = APIRouter(prefix="/api", tags=["wix_cms"])
def _get_sync_engine() -> WixSyncEngine:
    """Get a WixSyncEngine with the current app's DB."""
    return WixSyncEngine(db=current_app.db)


def _get_client() -> WixClient:
    """Get a shared WixClient instance."""
    return WixClient()


# ── Sync Status ─────────────────────────────────────────────────────────────────

@wix_cms_bp.get("/wix/status")
async def wix_status():
    """Get Wix sync engine status and statistics."""
    try:
        engine = _get_sync_engine()
        status = await engine.get_sync_status()
        return status
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)


# ── Sync Operations ─────────────────────────────────────────────────────────────

@wix_cms_bp.post("/wix/sync/intakes")
async def sync_intakes(request: Request):
    """Sync MongoDB intakes → Wix CMS IntakeQueue."""
    try:
        data = await request.json() or {}
        limit = data.get("limit", 50)

        engine = _get_sync_engine()
        result = await engine.sync_intakes(limit=limit)
        return result
    except Exception as e:
        logger.error(f"Intake sync failed: {e}")
        return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)


@wix_cms_bp.post("/wix/sync/cases")
async def sync_cases(request: Request):
    """Sync MongoDB bond_cases → Wix CMS Cases."""
    try:
        data = await request.json() or {}
        limit = data.get("limit", 50)

        engine = _get_sync_engine()
        result = await engine.sync_cases(limit=limit)
        return result
    except Exception as e:
        logger.error(f"Case sync failed: {e}")
        return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)


@wix_cms_bp.post("/wix/sync/leads-to-crm")
async def sync_leads_to_crm(request: Request):
    """Push hot leads from MongoDB → Wix CRM Contacts."""
    try:
        data = await request.json() or {}
        min_score = data.get("min_score", 70)
        limit = data.get("limit", 25)

        engine = _get_sync_engine()
        result = await engine.sync_hot_leads_to_crm(
            min_score=min_score,
            limit=limit,
        )
        return result
    except Exception as e:
        logger.error(f"Lead-to-CRM sync failed: {e}")
        return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)


@wix_cms_bp.post("/wix/sync/full")
async def sync_full():
    """Run all sync operations (intakes + cases + leads→CRM)."""
    try:
        engine = _get_sync_engine()
        result = await engine.run_full_sync()
        return result
    except Exception as e:
        logger.error(f"Full sync failed: {e}")
        return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)


# ── CMS Direct Operations ───────────────────────────────────────────────────────

@wix_cms_bp.get("/wix/collections")
async def list_collections():
    """List all Wix CMS collections on the site."""
    try:
        engine = _get_sync_engine()
        collections = await engine.discover_collections()
        return {
            "status": "ok",
            "collections": collections,
            "count": len(collections),
        }
    except Exception as e:
        logger.error(f"Collection listing failed: {e}")
        return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)


@wix_cms_bp.post("/wix/cms/query")
async def cms_query(request: Request):
    """
    Query a Wix CMS collection directly.

    Body:
        {
            "collection": "IntakeQueue",
            "filter": {"status": {"$eq": "new"}},
            "limit": 20,
            "offset": 0
        }
    """
    try:
        data = await request.json()
        if not data or "collection" not in data:
            return JSONResponse({"error": "collection is required"}, status_code=400)

        import asyncio
        cms = WixDataClient()
        result = await asyncio.to_thread(
            cms.query,
            collection=data["collection"],
            filter=data.get("filter"),
            sort=data.get("sort"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
        )
        return {"status": "ok", **result}
    except WixAPIError as e:
        return {"error": e.message, "status_code": e.status_code}, e.status_code
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@wix_cms_bp.post("/wix/cms/insert")
async def cms_insert(request: Request):
    """
    Insert an item into a Wix CMS collection.

    Body:
        {
            "collection": "IntakeQueue",
            "data": {"defendantName": "John Doe", "bondAmount": 5000}
        }
    """
    try:
        data = await request.json()
        if not data or "collection" not in data or "data" not in data:
            return JSONResponse({"error": "collection and data are required"}, status_code=400)

        import asyncio
        cms = WixDataClient()
        result = await asyncio.to_thread(
            cms.insert,
            collection=data["collection"],
            data=data["data"],
        )
        return {"status": "ok", "item": result}
    except WixAPIError as e:
        return {"error": e.message, "status_code": e.status_code}, e.status_code
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@wix_cms_bp.post("/wix/cms/update")
async def cms_update(request: Request):
    """
    Patch (partial update) an item in a Wix CMS collection.

    Body:
        {
            "collection": "Cases",
            "item_id": "abc-123",
            "data": {"status": "discharged"}
        }
    """
    try:
        data = await request.json()
        if not data or not all(k in data for k in ["collection", "item_id", "data"]):
            return JSONResponse({"error": "collection, item_id, and data are required"}, status_code=400)

        import asyncio
        cms = WixDataClient()
        result = await asyncio.to_thread(
            cms.patch,
            collection=data["collection"],
            item_id=data["item_id"],
            data=data["data"],
        )
        return {"status": "ok", "item": result}
    except WixAPIError as e:
        return {"error": e.message, "status_code": e.status_code}, e.status_code
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── CRM Contact Operations ──────────────────────────────────────────────────────

@wix_cms_bp.post("/wix/crm/query")
async def crm_query(request: Request):
    """
    Query Wix CRM contacts.

    Body:
        {
            "search": "John Doe",
            "label_keys": ["hot-lead"],
            "limit": 20
        }
    """
    try:
        data = await request.json() or {}

        import asyncio
        crm = WixContactsClient()
        result = await asyncio.to_thread(
            crm.query,
            filter=data.get("filter"),
            label_keys=data.get("label_keys"),
            search=data.get("search"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
        )
        return {"status": "ok", **result}
    except WixAPIError as e:
        return {"error": e.message, "status_code": e.status_code}, e.status_code
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@wix_cms_bp.post("/wix/crm/upsert")
async def crm_upsert(request: Request):
    """
    Create or update a CRM contact (dedup by email/phone).

    Body:
        {
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@email.com",
            "phone": "+12395551234",
            "labels": ["indemnitor", "hot-lead"]
        }
    """
    try:
        data = await request.json()
        if not data or not data.get("first_name"):
            return JSONResponse({"error": "first_name is required"}, status_code=400)

        import asyncio
        crm = WixContactsClient()
        contact = await asyncio.to_thread(
            crm.upsert_contact,
            email=data.get("email"),
            phone=data.get("phone"),
            first_name=data["first_name"],
            last_name=data.get("last_name", ""),
            labels=data.get("labels"),
        )
        return {"status": "ok", "contact": contact}
    except WixAPIError as e:
        return {"error": e.message, "status_code": e.status_code}, e.status_code
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@wix_cms_bp.get("/wix/crm/labels")
async def crm_list_labels():
    """List all CRM labels on the site."""
    try:
        import asyncio
        crm = WixContactsClient()
        labels = await asyncio.to_thread(crm.list_labels)
        return {"status": "ok", "labels": labels}
    except WixAPIError as e:
        return {"error": e.message}, e.status_code
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
