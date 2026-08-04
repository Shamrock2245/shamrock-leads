"""
Superadmin Data Hygiene — hard-delete test junk & repair mismatches.

Endpoints (admin session required):
  GET    /api/admin/hygiene/search          — search across arrests/defendants
  GET    /api/admin/hygiene/test-records    — find Jon/John/Jane Doe + TEST junk
  GET    /api/admin/hygiene/related         — full related graph for one booking
  PATCH  /api/admin/hygiene/arrest          — fix fields on an arrest lead
  PATCH  /api/admin/hygiene/defendant       — fix fields on a defendant
  POST   /api/admin/hygiene/delete          — hard-delete one identity graph
  POST   /api/admin/hygiene/purge-test      — bulk purge known test name patterns
  POST   /api/admin/hygiene/unlink-match    — remove a wrong match without deleting people

Destructive ops always write an audit_events row.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from dashboard.auth.pin_middleware import get_session_from_request, session_is_admin
from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/hygiene", tags=["admin_hygiene"])

# Name patterns that are almost always demo/test pollution
DEFAULT_TEST_NAME_REGEX = (
    r"(?i)^\s*(joh?n|jon|jane)\s*[\.,]?\s*doe\s*$"
    r"|^\s*doe\s*[\.,]?\s*(joh?n|jon|jane)\s*$"
    r"|\btest\s*(user|defendant|person|lead|inmate)\b"
    r"|^\s*test\s*$"
    r"|^\s*asdf+\s*$"
    r"|^\s*foo\s*bar\s*$"
    r"|^\s*sample\s*(person|defendant)?\s*$"
)

# Collections that may hold identity fragments for a booking/defendant
_RELATED_COLLECTIONS = (
    ("arrests", ("booking_number", "defendant_id")),
    ("defendants", ("booking_numbers", "defendant_id")),  # special
    ("leads", ("booking_number", "defendant_id")),
    ("matches", ("booking_number", "defendant_id")),
    ("intake_queue", ("booking_number", "matched_booking_number", "defendant_id")),
    ("prospective_bonds", ("booking_number", "defendant_id")),
    ("active_bonds", ("booking_number", "defendant_id")),
    ("defendant_notes", ("booking_number", "defendant_id")),
    ("court_reminders", ("booking_number", "defendant_id")),
    ("paperwork_packets", ("booking_number", "defendant_id")),
    ("payments", ("booking_number", "defendant_id")),
)


def _require_admin(request: Request) -> dict:
    if not session_is_admin(request):
        raise HTTPException(status_code=403, detail="Superadmin only.")
    sess = get_session_from_request(request) or {}
    return sess


async def _audit(actor: str, action: str, detail: dict) -> None:
    try:
        col = get_collection("audit_events")
        await col.insert_one({
            "Event_ID": f"hyg-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            "timestamp": datetime.now(timezone.utc),
            "actor": actor or "superadmin",
            "agent": "AdminHygiene",
            "action": action,
            "detail": detail,
        })
    except Exception as e:
        logger.warning("audit write failed: %s", e)


def _name_clause(pattern: str) -> dict:
    return {
        "$or": [
            {"full_name": {"$regex": pattern}},
            {"first_name": {"$regex": pattern}},
            {"last_name": {"$regex": pattern}},
            {"defendant_name": {"$regex": pattern}},
            {"name": {"$regex": pattern}},
        ]
    }


# ── Search ───────────────────────────────────────────────────────────────────

@router.get("/search")
async def hygiene_search(
    request: Request,
    q: str = Query("", min_length=0),
    collection: str = Query("arrests"),
    limit: int = Query(50, ge=1, le=200),
):
    """Search a collection by name / booking for repair UI."""
    _require_admin(request)
    q = (q or "").strip()
    if not q:
        return {"results": [], "total": 0}

    allowed = {
        "arrests", "defendants", "leads", "matches", "intake_queue",
        "prospective_bonds", "active_bonds",
    }
    if collection not in allowed:
        raise HTTPException(400, f"collection must be one of {sorted(allowed)}")

    col = get_collection(collection)
    clauses = [
        {"booking_number": {"$regex": re.escape(q), "$options": "i"}},
        {"full_name": {"$regex": re.escape(q), "$options": "i"}},
        {"first_name": {"$regex": re.escape(q), "$options": "i"}},
        {"last_name": {"$regex": re.escape(q), "$options": "i"}},
        {"defendant_name": {"$regex": re.escape(q), "$options": "i"}},
        {"name": {"$regex": re.escape(q), "$options": "i"}},
        {"defendant_id": q},
        {"case_number": {"$regex": re.escape(q), "$options": "i"}},
    ]
    # ObjectId match if valid
    if ObjectId.is_valid(q) and len(q) == 24:
        clauses.append({"_id": ObjectId(q)})

    query = {"$or": clauses}
    total = await col.count_documents(query)
    results = []
    async for doc in col.find(query).sort([("updated_at", -1), ("scraped_at", -1)]).limit(limit):
        results.append(_serialize(doc))
    return {"results": results, "total": total, "collection": collection, "q": q}


@router.get("/test-records")
async def list_test_records(
    request: Request,
    pattern: str = Query("", description="Optional custom regex; default = Jon/John/Jane Doe + TEST"),
    limit: int = Query(100, ge=1, le=500),
):
    """Find pollution rows matching demo names across arrests + defendants."""
    _require_admin(request)
    pat = (pattern or "").strip() or DEFAULT_TEST_NAME_REGEX
    try:
        re.compile(pat)
    except re.error as e:
        raise HTTPException(400, f"Invalid regex: {e}") from e

    clause = _name_clause(pat)
    out: dict[str, list] = {}
    counts: dict[str, int] = {}
    for cname in ("arrests", "defendants", "leads", "prospective_bonds", "intake_queue"):
        col = get_collection(cname)
        counts[cname] = await col.count_documents(clause)
        rows = []
        async for doc in col.find(clause).limit(limit):
            rows.append(_serialize(doc))
        out[cname] = rows

    return {
        "pattern": pat,
        "counts": counts,
        "total_hits": sum(counts.values()),
        "records": out,
    }


@router.get("/related")
async def related_graph(
    request: Request,
    booking_number: str = "",
    defendant_id: str = "",
    state: str = "",
    county: str = "",
):
    """Return every related document for one booking / defendant (repair view)."""
    _require_admin(request)
    if not booking_number and not defendant_id:
        raise HTTPException(400, "booking_number or defendant_id required")

    graph: dict[str, list] = {}
    for cname, _ in _RELATED_COLLECTIONS:
        col = get_collection(cname)
        q: dict[str, Any] = {"$or": []}
        if booking_number:
            q["$or"].extend([
                {"booking_number": booking_number},
                {"matched_booking_number": booking_number},
                {"booking_numbers": booking_number},
            ])
        if defendant_id:
            q["$or"].extend([
                {"defendant_id": defendant_id},
                {"Defendant_ID": defendant_id},
            ])
        if state:
            q["state"] = state.upper()
        if county:
            q["county"] = {"$regex": f"^{re.escape(county)}$", "$options": "i"}
        if not q["$or"]:
            continue
        rows = []
        async for doc in col.find(q).limit(50):
            rows.append(_serialize(doc))
        if rows:
            graph[cname] = rows

    return {
        "booking_number": booking_number,
        "defendant_id": defendant_id,
        "graph": graph,
        "collections_hit": list(graph.keys()),
    }


# ── Patch / fix ──────────────────────────────────────────────────────────────

class ArrestPatch(BaseModel):
    booking_number: str
    county: Optional[str] = None
    state: Optional[str] = None
    # New values
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    charges: Optional[str] = None
    bond_amount: Optional[float] = None
    status: Optional[str] = None
    lead_status: Optional[str] = None
    lead_score: Optional[int] = None
    dob: Optional[str] = None
    facility: Optional[str] = None
    new_booking_number: Optional[str] = None  # re-key (careful)
    new_county: Optional[str] = None
    new_state: Optional[str] = None
    reason: str = Field(default="", description="Why this fix was made")


class DefendantPatch(BaseModel):
    defendant_id: str
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[str] = None
    active: Optional[bool] = None
    reason: str = ""


@router.patch("/arrest")
async def patch_arrest(request: Request, body: ArrestPatch):
    """Fix mismatched fields on a single arrest lead (superadmin)."""
    sess = _require_admin(request)
    arrests = get_collection("arrests")
    filt: dict[str, Any] = {"booking_number": body.booking_number}
    if body.county:
        filt["county"] = {"$regex": f"^{re.escape(body.county)}$", "$options": "i"}
    if body.state:
        filt["state"] = body.state.upper()

    existing = await arrests.find_one(filt)
    if not existing:
        raise HTTPException(404, "Arrest not found")

    sets: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
    field_map = {
        "full_name": body.full_name,
        "first_name": body.first_name,
        "last_name": body.last_name,
        "charges": body.charges,
        "status": body.status,
        "lead_status": body.lead_status,
        "lead_score": body.lead_score,
        "dob": body.dob,
        "facility": body.facility,
    }
    for k, v in field_map.items():
        if v is not None:
            sets[k] = v
    if body.bond_amount is not None:
        sets["bond_amount"] = float(body.bond_amount)
        sets["bond_amount_raw"] = str(body.bond_amount)
    if body.new_booking_number:
        sets["booking_number"] = body.new_booking_number.strip()
    if body.new_county:
        sets["county"] = body.new_county.strip()
    if body.new_state:
        sets["state"] = body.new_state.strip().upper()

    await arrests.update_one({"_id": existing["_id"]}, {"$set": sets})
    await _audit(
        sess.get("email") or "superadmin",
        "hygiene_patch_arrest",
        {
            "booking_number": body.booking_number,
            "before": {
                "full_name": existing.get("full_name"),
                "county": existing.get("county"),
                "state": existing.get("state"),
                "bond_amount": existing.get("bond_amount"),
            },
            "after": sets,
            "reason": body.reason,
        },
    )
    updated = await arrests.find_one({"_id": existing["_id"]})
    return {"ok": True, "arrest": _serialize(updated)}


@router.patch("/defendant")
async def patch_defendant(request: Request, body: DefendantPatch):
    sess = _require_admin(request)
    col = get_collection("defendants")
    filt = {
        "$or": [
            {"defendant_id": body.defendant_id},
            {"Defendant_ID": body.defendant_id},
        ]
    }
    if ObjectId.is_valid(body.defendant_id):
        filt["$or"].append({"_id": ObjectId(body.defendant_id)})

    existing = await col.find_one(filt)
    if not existing:
        raise HTTPException(404, "Defendant not found")

    sets: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
    for k in ("full_name", "first_name", "last_name", "dob", "active"):
        v = getattr(body, k)
        if v is not None:
            sets[k] = v

    await col.update_one({"_id": existing["_id"]}, {"$set": sets})
    await _audit(
        sess.get("email") or "superadmin",
        "hygiene_patch_defendant",
        {"defendant_id": body.defendant_id, "after": sets, "reason": body.reason},
    )
    updated = await col.find_one({"_id": existing["_id"]})
    return {"ok": True, "defendant": _serialize(updated)}


# ── Delete ───────────────────────────────────────────────────────────────────

class DeleteRequest(BaseModel):
    booking_number: Optional[str] = None
    defendant_id: Optional[str] = None
    county: Optional[str] = None
    state: Optional[str] = None
    full_name: Optional[str] = None
    # Safety: must type DELETE or the full_name to confirm
    confirm: str = Field(..., description="Type DELETE or the exact full_name")
    dry_run: bool = False
    # If true, refuse when an active bond exists
    allow_active_bond: bool = False


@router.post("/delete")
async def hard_delete(request: Request, body: DeleteRequest):
    """Completely remove a person/lead graph from Mongo (superadmin)."""
    sess = _require_admin(request)
    if not body.booking_number and not body.defendant_id and not body.full_name:
        raise HTTPException(400, "Provide booking_number, defendant_id, or full_name")

    confirm_ok = (body.confirm or "").strip().upper() == "DELETE"
    if body.full_name and (body.confirm or "").strip().lower() == body.full_name.strip().lower():
        confirm_ok = True
    if not confirm_ok:
        raise HTTPException(
            400,
            "confirm must be the word DELETE (or exact full_name match)",
        )

    # Guard active bonds
    if body.booking_number and not body.allow_active_bond:
        ab = get_collection("active_bonds")
        live = await ab.find_one({
            "booking_number": body.booking_number,
            "status": {"$in": ["active", "pending", "monitoring", "alert", "reinstated"]},
        })
        if live:
            raise HTTPException(
                409,
                "Active bond exists for this booking. Set allow_active_bond=true to force.",
            )

    plan = await _collect_delete_targets(body)
    if body.dry_run:
        return {"ok": True, "dry_run": True, "would_delete": plan}

    deleted = await _execute_delete(plan)
    await _audit(
        sess.get("email") or "superadmin",
        "hygiene_hard_delete",
        {
            "booking_number": body.booking_number,
            "defendant_id": body.defendant_id,
            "full_name": body.full_name,
            "deleted": deleted,
        },
    )
    return {"ok": True, "dry_run": False, "deleted": deleted}


class PurgeTestRequest(BaseModel):
    pattern: str = ""
    confirm: str = Field(..., description="Must be PURGE_TEST")
    dry_run: bool = True
    limit: int = Field(500, ge=1, le=2000)


@router.post("/purge-test")
async def purge_test_records(request: Request, body: PurgeTestRequest):
    """Bulk-delete Jon/John/Jane Doe and other test-name pollution."""
    sess = _require_admin(request)
    if (body.confirm or "").strip() != "PURGE_TEST":
        raise HTTPException(400, "confirm must be exactly PURGE_TEST")

    pat = (body.pattern or "").strip() or DEFAULT_TEST_NAME_REGEX
    try:
        re.compile(pat)
    except re.error as e:
        raise HTTPException(400, f"Invalid regex: {e}") from e

    clause = _name_clause(pat)
    plan: dict[str, list] = {}
    for cname in ("arrests", "defendants", "leads", "prospective_bonds", "intake_queue", "matches"):
        col = get_collection(cname)
        ids = []
        async for doc in col.find(clause, {"_id": 1}).limit(body.limit):
            ids.append(doc["_id"])
        plan[cname] = ids

    total = sum(len(v) for v in plan.values())
    if body.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "pattern": pat,
            "would_delete_counts": {k: len(v) for k, v in plan.items()},
            "total": total,
        }

    deleted: dict[str, int] = {}
    for cname, ids in plan.items():
        if not ids:
            deleted[cname] = 0
            continue
        r = await get_collection(cname).delete_many({"_id": {"$in": ids}})
        deleted[cname] = r.deleted_count

    await _audit(
        sess.get("email") or "superadmin",
        "hygiene_purge_test",
        {"pattern": pat, "deleted": deleted},
    )
    return {
        "ok": True,
        "dry_run": False,
        "pattern": pat,
        "deleted": deleted,
        "total": sum(deleted.values()),
    }


class UnlinkMatchRequest(BaseModel):
    match_id: Optional[str] = None
    booking_number: Optional[str] = None
    indemnitor_id: Optional[str] = None
    reason: str = ""


@router.post("/unlink-match")
async def unlink_match(request: Request, body: UnlinkMatchRequest):
    """Remove a wrong match link without deleting people."""
    sess = _require_admin(request)
    matches = get_collection("matches")
    q: dict[str, Any] = {}
    if body.match_id:
        q["$or"] = [{"Match_ID": body.match_id}, {"match_id": body.match_id}]
        if ObjectId.is_valid(body.match_id):
            q["$or"].append({"_id": ObjectId(body.match_id)})
    elif body.booking_number:
        q["booking_number"] = body.booking_number
        if body.indemnitor_id:
            q["indemnitor_id"] = body.indemnitor_id
    else:
        raise HTTPException(400, "match_id or booking_number required")

    found = []
    async for doc in matches.find(q):
        found.append(doc)
    if not found:
        raise HTTPException(404, "No match found")

    ids = [d["_id"] for d in found]
    r = await matches.delete_many({"_id": {"$in": ids}})

    # Clear intake match pointers
    if body.booking_number:
        await get_collection("intake_queue").update_many(
            {"$or": [
                {"booking_number": body.booking_number},
                {"matched_booking_number": body.booking_number},
            ]},
            {"$unset": {"matched_booking_number": "", "match_id": ""},
             "$set": {"status": "pending", "updated_at": datetime.now(timezone.utc)}},
        )

    await _audit(
        sess.get("email") or "superadmin",
        "hygiene_unlink_match",
        {"query": q, "deleted": r.deleted_count, "reason": body.reason},
    )
    return {"ok": True, "unlinked": r.deleted_count}


# ── Internals ────────────────────────────────────────────────────────────────

async def _collect_delete_targets(body: DeleteRequest) -> dict[str, list]:
    plan: dict[str, list] = {}
    for cname, fields in _RELATED_COLLECTIONS:
        col = get_collection(cname)
        or_clauses = []
        if body.booking_number:
            bn = body.booking_number
            or_clauses.extend([
                {"booking_number": bn},
                {"matched_booking_number": bn},
                {"booking_numbers": bn},
            ])
        if body.defendant_id:
            did = body.defendant_id
            or_clauses.extend([
                {"defendant_id": did},
                {"Defendant_ID": did},
            ])
            if ObjectId.is_valid(did):
                or_clauses.append({"_id": ObjectId(did)})
        if body.full_name and cname in ("arrests", "defendants", "leads", "prospective_bonds", "intake_queue"):
            # Exact-ish name match (case insensitive)
            name = body.full_name.strip()
            or_clauses.extend([
                {"full_name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}},
                {"defendant_name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}},
            ])
        if not or_clauses:
            continue
        q: dict[str, Any] = {"$or": or_clauses}
        if body.county and cname in ("arrests", "leads", "prospective_bonds"):
            q["county"] = {"$regex": f"^{re.escape(body.county)}$", "$options": "i"}
        if body.state and cname in ("arrests", "leads"):
            q["state"] = body.state.upper()

        ids = []
        async for doc in col.find(q, {"_id": 1}).limit(500):
            ids.append(doc["_id"])
        if ids:
            plan[cname] = ids
    return plan


async def _execute_delete(plan: dict[str, list]) -> dict[str, int]:
    deleted: dict[str, int] = {}
    for cname, ids in plan.items():
        if not ids:
            deleted[cname] = 0
            continue
        r = await get_collection(cname).delete_many({"_id": {"$in": ids}})
        deleted[cname] = r.deleted_count
    return deleted


def _serialize(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out
