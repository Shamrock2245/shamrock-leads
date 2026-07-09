from __future__ import annotations

"""
ShamrockLeads — Match Manager API Blueprint
Manual bond-to-defendant-to-indemnitor association and POA matching.

Endpoints:
  POST   /api/bonds/match          — Associate POA with defendant/case/indemnitor
  GET    /api/bonds/unmatched       — List bonds missing POA or indemnitor
  PATCH  /api/bonds/<booking>/assign-poa — Assign POA to existing bond
  PATCH  /api/bonds/<booking>/assign-indemnitor — Assign indemnitor to existing bond
  GET    /api/match-manager/search  — Search defendants, bonds, indemnitors
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from datetime import datetime, timezone
from dashboard.extensions import get_collection
import logging
import uuid

logger = logging.getLogger(__name__)

match_manager_bp = APIRouter(prefix="/api", tags=["match_manager"])
# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/bonds/match — Full manual match: POA + defendant + indemnitor
# ─────────────────────────────────────────────────────────────────────────────
@match_manager_bp.post("/bonds/match")
async def api_bonds_match(request: Request):
    """
    Create or update a bond with full manual matching.
    Associates a POA number with a defendant, case number, and indemnitor.
    
    Body:
        {
            "booking_number": "2025-001234",
            "defendant_name": "John Doe",
            "county": "Lee",
            "poa_number": "PSC2 2644680",
            "case_number": "25-CF-001234",
            "surety": "osi",
            "bond_amount": 5000,
            "indemnitor_name": "Jane Doe",
            "indemnitor_phone": "2395550000",
            "indemnitor_email": "jane@example.com",
            "indemnitor_relationship": "Wife",
            "agent": "Brendan"
        }
    """
    data = await request.json() or {}
    
    booking_number = (data.get("booking_number") or "").strip()
    poa_number = (data.get("poa_number") or "").strip()
    defendant_name = (data.get("defendant_name") or "").strip()
    
    if not booking_number:
        return JSONResponse({"success": False, "error": "booking_number is required"}, status_code=400)
    if not poa_number:
        return JSONResponse({"success": False, "error": "poa_number is required"}, status_code=400)
    
    try:
        now = datetime.now(timezone.utc)
        agent = data.get("agent", "Dashboard")
        surety = (data.get("surety") or "osi").lower().strip()
        
        bonds_col = get_collection("active_bonds")
        poa_col = get_collection("poa_inventory")
        audit_col = get_collection("audit_events")
        
        # Check if bond already exists
        existing = await bonds_col.find_one({"booking_number": booking_number})
        
        indemnitor_data = {}
        if data.get("indemnitor_name"):
            indemnitor_data = {
                "name": data.get("indemnitor_name", ""),
                "phone": data.get("indemnitor_phone", ""),
                "email": data.get("indemnitor_email", ""),
                "relationship": data.get("indemnitor_relationship", ""),
            }
        
        if existing:
            # Update existing bond with match data
            update_fields = {"updated_at": now}
            if poa_number:
                update_fields["poa_number"] = poa_number
            if data.get("case_number"):
                update_fields["case_number"] = data["case_number"]
            if surety:
                update_fields["insurance_company"] = surety.upper()
            if indemnitor_data:
                update_fields["indemnitor"] = indemnitor_data
                update_fields["indemnitor_name"] = indemnitor_data.get("name", "")
                update_fields["indemnitor_phone"] = indemnitor_data.get("phone", "")
            
            await bonds_col.update_one(
                {"booking_number": booking_number},
                {
                    "$set": update_fields,
                    "$push": {"status_history": {
                        "status": "matched",
                        "timestamp": now.isoformat(),
                        "agent": agent,
                        "note": f"Manual match: POA {poa_number}" + 
                                (f", Indemnitor: {indemnitor_data.get('name', '')}" if indemnitor_data else ""),
                    }},
                },
            )
            
            # Mark POA as assigned
            if poa_number:
                await poa_col.update_one(
                    {"poa_number": poa_number},
                    {"$set": {
                        "status": "assigned",
                        "assigned_to": booking_number,
                        "assigned_defendant": defendant_name or existing.get("defendant_name", ""),
                        "assigned_at": now.isoformat(),
                    }},
                    upsert=True,
                )
            
            # Audit event
            await audit_col.insert_one({
                "event_id": str(uuid.uuid4()),
                "event_type": "bond_matched",
                "booking_number": booking_number,
                "actor": agent,
                "timestamp": now.isoformat(),
                "details": {
                    "poa_number": poa_number,
                    "case_number": data.get("case_number", ""),
                    "surety": surety,
                    "indemnitor": indemnitor_data,
                },
            })
            
            return {
                "success": True,
                "action": "updated",
                "booking_number": booking_number,
                "poa_number": poa_number,
            }
        
        else:
            # Create new bond record with match data
            bond_doc = {
                "booking_number": booking_number,
                "defendant_name": defendant_name,
                "county": (data.get("county") or "").strip(),
                "bond_amount": float(data.get("bond_amount") or 0),
                "premium": float(data.get("premium") or 0),
                "insurance_company": surety.upper(),
                "poa_number": poa_number,
                "case_number": (data.get("case_number") or "").strip(),
                "court_date": (data.get("court_date") or "").strip(),
                "court_location": (data.get("court_location") or "").strip(),
                "charges": (data.get("charges") or "").strip(),
                "facility": (data.get("facility") or "").strip(),
                "indemnitor": indemnitor_data,
                "indemnitor_name": indemnitor_data.get("name", ""),
                "indemnitor_phone": indemnitor_data.get("phone", ""),
                "agent_name": agent,
                "status": "active",
                "risk_score": 0,
                "created_at": now,
                "updated_at": now,
                "status_history": [{
                    "status": "active",
                    "timestamp": now.isoformat(),
                    "agent": agent,
                    "note": f"Manual match entry: POA {poa_number}",
                }],
                "bond_date": (data.get("bond_date") or now.strftime("%Y-%m-%d")),
            }
            
            await bonds_col.insert_one(bond_doc)
            
            # Mark POA as assigned
            if poa_number:
                await poa_col.update_one(
                    {"poa_number": poa_number},
                    {"$set": {
                        "status": "assigned",
                        "assigned_to": booking_number,
                        "assigned_defendant": defendant_name,
                        "assigned_at": now.isoformat(),
                    }},
                    upsert=True,
                )
            
            # Audit
            await audit_col.insert_one({
                "event_id": str(uuid.uuid4()),
                "event_type": "bond_created_manual_match",
                "booking_number": booking_number,
                "actor": agent,
                "timestamp": now.isoformat(),
                "details": {
                    "poa_number": poa_number,
                    "defendant_name": defendant_name,
                    "surety": surety,
                },
            })
            
            return {
                "success": True,
                "action": "created",
                "booking_number": booking_number,
                "poa_number": poa_number,
            }
    
    except Exception as exc:
        logger.exception("api_bonds_match error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/bonds/unmatched — List bonds needing POA or indemnitor
# ─────────────────────────────────────────────────────────────────────────────
@match_manager_bp.get("/bonds/unmatched")
async def api_bonds_unmatched():
    """List active bonds missing POA, case number, or indemnitor."""
    try:
        bonds_col = get_collection("active_bonds")
        
        query = {
            "status": {"$in": ["active", "monitoring"]},
            "$or": [
                {"poa_number": {"$in": ["", None]}},
                {"case_number": {"$in": ["", None]}},
                {"indemnitor_name": {"$in": ["", None]}},
                {"indemnitor.name": {"$in": ["", None]}},
            ],
        }
        
        unmatched = []
        async for doc in bonds_col.find(query, {"_id": 0}).sort("created_at", -1).limit(100):
            missing = []
            if not doc.get("poa_number"):
                missing.append("POA")
            if not doc.get("case_number"):
                missing.append("Case #")
            ind_name = (doc.get("indemnitor") or {}).get("name") or doc.get("indemnitor_name") or ""
            if not ind_name:
                missing.append("Indemnitor")
            
            doc["missing_fields"] = missing
            # Serialize datetime
            for k in ["created_at", "updated_at"]:
                if hasattr(doc.get(k), "isoformat"):
                    doc[k] = doc[k].isoformat()
            unmatched.append(doc)
        
        return {"success": True, "unmatched": unmatched, "total": len(unmatched)}
    
    except Exception as exc:
        logger.exception("api_bonds_unmatched error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  PATCH /api/bonds/<booking>/assign-poa — Quick POA assignment
# ─────────────────────────────────────────────────────────────────────────────
@match_manager_bp.patch("/bonds/{booking_number}/assign-poa")
async def api_assign_poa(request: Request, booking_number: str):
    """Quick-assign a POA number to an existing bond."""
    data = await request.json() or {}
    poa_number = (data.get("poa_number") or "").strip()
    surety = (data.get("surety") or "").strip().lower()
    agent = data.get("agent", "Dashboard")
    
    if not poa_number:
        return JSONResponse({"success": False, "error": "poa_number required"}, status_code=400)
    
    try:
        bonds_col = get_collection("active_bonds")
        poa_col = get_collection("poa_inventory")
        now = datetime.now(timezone.utc)
        
        update_fields = {"poa_number": poa_number, "updated_at": now}
        if surety:
            update_fields["insurance_company"] = surety.upper()
        
        result = await bonds_col.update_one(
            {"booking_number": booking_number},
            {
                "$set": update_fields,
                "$push": {"status_history": {
                    "status": "poa_assigned",
                    "timestamp": now.isoformat(),
                    "agent": agent,
                    "note": f"POA {poa_number} assigned",
                }},
            },
        )
        
        if result.matched_count == 0:
            return JSONResponse({"success": False, "error": "Bond not found"}, status_code=404)
        
        # Mark POA as assigned
        bond = await bonds_col.find_one({"booking_number": booking_number})
        await poa_col.update_one(
            {"poa_number": poa_number},
            {"$set": {
                "status": "assigned",
                "assigned_to": booking_number,
                "assigned_defendant": bond.get("defendant_name", "") if bond else "",
                "assigned_at": now.isoformat(),
            }},
            upsert=True,
        )
        
        return {"success": True, "poa_number": poa_number}
    
    except Exception as exc:
        logger.exception("api_assign_poa error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  PATCH /api/bonds/<booking>/assign-indemnitor — Quick indemnitor assignment
# ─────────────────────────────────────────────────────────────────────────────
@match_manager_bp.patch("/bonds/{booking_number}/assign-indemnitor")
async def api_assign_indemnitor(request: Request, booking_number: str):
    """Quick-assign indemnitor info to an existing bond."""
    data = await request.json() or {}
    name = (data.get("name") or "").strip()
    agent = data.get("agent", "Dashboard")
    
    if not name:
        return JSONResponse({"success": False, "error": "name required"}, status_code=400)
    
    try:
        bonds_col = get_collection("active_bonds")
        now = datetime.now(timezone.utc)
        
        indemnitor = {
            "name": name,
            "phone": (data.get("phone") or "").strip(),
            "email": (data.get("email") or "").strip(),
            "relationship": (data.get("relationship") or "").strip(),
        }
        
        result = await bonds_col.update_one(
            {"booking_number": booking_number},
            {
                "$set": {
                    "indemnitor": indemnitor,
                    "indemnitor_name": name,
                    "indemnitor_phone": indemnitor["phone"],
                    "updated_at": now,
                },
                "$push": {"status_history": {
                    "status": "indemnitor_assigned",
                    "timestamp": now.isoformat(),
                    "agent": agent,
                    "note": f"Indemnitor assigned: {name}",
                }},
            },
        )
        
        if result.matched_count == 0:
            return JSONResponse({"success": False, "error": "Bond not found"}, status_code=404)
        
        return {"success": True, "indemnitor": indemnitor}
    
    except Exception as exc:
        logger.exception("api_assign_indemnitor error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/match-manager/search — Unified search across entities
# ─────────────────────────────────────────────────────────────────────────────
@match_manager_bp.get("/match-manager/search")
async def api_match_search(q: str = Query(default="")):
    """Search defendants, bonds, and arrest records for matching."""
    q = q.strip()
    if not q or len(q) < 2:
        return JSONResponse(status_code=200, content={"results": []})
    
    try:
        results = []
        regex = {"$regex": q, "$options": "i"}
        
        # Search active_bonds
        bonds_col = get_collection("active_bonds")
        async for doc in bonds_col.find(
            {"$or": [
                {"defendant_name": regex},
                {"booking_number": regex},
                {"poa_number": regex},
                {"case_number": regex},
            ]},
            {"_id": 0, "defendant_name": 1, "booking_number": 1, "county": 1,
             "bond_amount": 1, "poa_number": 1, "case_number": 1, "status": 1,
             "indemnitor_name": 1, "indemnitor": 1},
        ).limit(10):
            doc["source"] = "active_bonds"
            results.append(doc)
        
        # Search arrests
        arrests_col = get_collection("arrests")
        async for doc in arrests_col.find(
            {"$or": [
                {"full_name": regex},
                {"booking_number": regex},
            ]},
            {"_id": 0, "full_name": 1, "booking_number": 1, "county": 1,
             "bond_amount": 1, "charges": 1, "lead_score": 1, "lead_status": 1},
        ).limit(10):
            doc["source"] = "arrests"
            doc["defendant_name"] = doc.pop("full_name", "")
            results.append(doc)
        
        # Search prospective_bonds
        prosp_col = get_collection("prospective_bonds")
        async for doc in prosp_col.find(
            {"$or": [
                {"defendant_name": regex},
                {"booking_number": regex},
            ]},
            {"_id": 0, "defendant_name": 1, "booking_number": 1, "county": 1,
             "bond_amount": 1, "stage": 1, "indemnitor": 1},
        ).limit(10):
            doc["source"] = "prospective_bonds"
            results.append(doc)

        # Search indemnitors (Super CRM parity)
        try:
            ind_col = get_collection("indemnitors")
            async for doc in ind_col.find(
                {"$or": [
                    {"name": regex},
                    {"full_name": regex},
                    {"phone": regex},
                    {"email": regex},
                ]},
                {"_id": 0, "name": 1, "full_name": 1, "phone": 1, "email": 1},
            ).limit(5):
                results.append({
                    "source": "indemnitors",
                    "defendant_name": doc.get("name") or doc.get("full_name") or "",
                    "booking_number": doc.get("phone") or doc.get("email") or "",
                    "bond_amount": "",
                    "stage": "indemnitor",
                })
        except Exception:
            pass
        
        return {"success": True, "results": results, "total": len(results)}
    
    except Exception as exc:
        logger.exception("api_match_search error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
