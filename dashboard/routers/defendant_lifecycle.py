
"""
ShamrockLeads — Defendant Lifecycle API
Endpoints for notes, contact log, DNB/DNC flags, preferred comms,
bond finalization (two-step), and outreach notes.

All data is stored in a separate `defendant_notes` collection keyed by
booking_number so the core `arrests` collection stays clean.

Lifecycle-to-Pipeline Bridge:
  When a defendant's status changes to 'contacted' or higher, or when
  a contact is logged, the system auto-syncs to the prospective_bonds
  collection so the lead appears on the Outreach Kanban board.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from dashboard.extensions import get_collection
import logging

logger = logging.getLogger(__name__)

lifecycle_bp = APIRouter(prefix="/api", tags=["defendant_lifecycle"])
# Statuses that should auto-promote to the outreach pipeline
_PIPELINE_STATUSES = {"contacted", "negotiating", "paperwork", "ready"}
_STATUS_TO_STAGE = {
    "contacted": "contacted",
    "negotiating": "negotiating",
    "paperwork": "paperwork",
    "ready": "ready",
    "bonded": "ready",
}

# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_notes_doc(booking_number: str):
    col = get_collection("defendant_notes")
    doc = await col.find_one({"booking_number": booking_number}, {"_id": 0})
    return doc or {}


async def _sync_to_pipeline(booking_number: str, trigger: str = "auto",
                            contact_entry: dict = None, stage_override: str = None):
    """Sync defendant_notes activity to prospective_bonds (outreach pipeline).

    Creates a new prospective_bonds entry if none exists and the defendant
    has been contacted. If one already exists, syncs contact logs and stage.
    Returns the prospective bond doc if synced, None otherwise.
    """
    try:
        pb_col = get_collection("prospective_bonds")
        arrests_col = get_collection("arrests")
        notes_col = get_collection("defendant_notes")

        notes = await notes_col.find_one({"booking_number": booking_number}, {"_id": 0}) or {}
        status = notes.get("shamrock_status", "new")

        # Determine the pipeline stage from the lifecycle status
        stage = stage_override or _STATUS_TO_STAGE.get(status, "contacted")

        existing_pb = await pb_col.find_one({"booking_number": booking_number})
        now = datetime.now(timezone.utc)

        if existing_pb:
            # Already tracked — sync contact log and stage
            updates = {"$set": {"updated_at": now}}

            # Sync stage if lifecycle status advanced
            if stage and stage != existing_pb.get("stage"):
                old_stage = existing_pb.get("stage", "")
                updates["$set"]["stage"] = stage
                updates.setdefault("$push", {})
                updates["$push"]["timeline"] = {
                    "timestamp": now.isoformat(),
                    "event": "stage_change",
                    "detail": f"Auto-synced: {old_stage} → {stage} (from Defendant Notes)",
                    "agent": notes.get("agent", "Lifecycle Sync"),
                }

            # Sync contact entry to communication_log
            if contact_entry:
                comm_entry = {
                    "timestamp": contact_entry.get("ts", now.isoformat()),
                    "type": contact_entry.get("method", "note"),
                    "channel": contact_entry.get("method", "call"),
                    "direction": contact_entry.get("direction", "outbound"),
                    "text": contact_entry.get("summary", ""),
                    "message": contact_entry.get("summary", ""),
                    "agent": contact_entry.get("agent", "Dashboard"),
                    "source": "defendant_notes",
                }
                updates.setdefault("$push", {})
                if "timeline" in updates.get("$push", {}):
                    # Use $each for timeline + communication_log
                    timeline_entry = updates["$push"]["timeline"]
                    updates["$push"] = {
                        "timeline": {"$each": [timeline_entry, {
                            "timestamp": now.isoformat(),
                            "event": contact_entry.get("method", "note"),
                            "detail": contact_entry.get("summary", ""),
                            "agent": contact_entry.get("agent", "Dashboard"),
                        }]},
                        "communication_log": comm_entry,
                    }
                else:
                    updates["$push"]["communication_log"] = comm_entry
                    updates["$push"]["timeline"] = {
                        "timestamp": now.isoformat(),
                        "event": contact_entry.get("method", "note"),
                        "detail": contact_entry.get("summary", ""),
                        "agent": contact_entry.get("agent", "Dashboard"),
                    }

            await pb_col.update_one({"booking_number": booking_number}, updates)
            logger.info(f"Pipeline synced for {booking_number} (trigger={trigger})")
            return existing_pb

        else:
            # Not yet tracked — auto-create if status warrants it
            if status not in _PIPELINE_STATUSES and not stage_override:
                return None

            arrest_doc = await arrests_col.find_one(
                {"booking_number": booking_number}, {"_id": 0}
            ) or {}

            # Build communication_log from existing contact_log
            comm_log = []
            for cl in notes.get("contact_log", []):
                comm_log.append({
                    "timestamp": cl.get("ts", now.isoformat()),
                    "type": cl.get("method", "note"),
                    "channel": cl.get("method", "call"),
                    "direction": cl.get("direction", "outbound"),
                    "text": cl.get("summary", ""),
                    "message": cl.get("summary", ""),
                    "agent": cl.get("agent", "Dashboard"),
                    "source": "defendant_notes",
                })
            # Add the new contact entry too
            if contact_entry:
                comm_log.append({
                    "timestamp": contact_entry.get("ts", now.isoformat()),
                    "type": contact_entry.get("method", "note"),
                    "channel": contact_entry.get("method", "call"),
                    "direction": contact_entry.get("direction", "outbound"),
                    "text": contact_entry.get("summary", ""),
                    "message": contact_entry.get("summary", ""),
                    "agent": contact_entry.get("agent", "Dashboard"),
                    "source": "defendant_notes",
                })

            doc = {
                "booking_number": booking_number,
                "defendant_name": arrest_doc.get("full_name", "Unknown"),
                "county": arrest_doc.get("county", ""),
                "bond_amount": float(arrest_doc.get("bond_amount", 0) or 0),
                "charges": arrest_doc.get("charges", ""),
                "lead_score": int(arrest_doc.get("lead_score", 0) or 0),
                "lead_status": arrest_doc.get("lead_status", ""),
                "detail_url": arrest_doc.get("detail_url", ""),
                "stage": stage,
                "status": "active",
                "indemnitor": {
                    "name": "", "phone": "", "email": "", "relationship": "",
                },
                "communication_log": comm_log,
                "timeline": [{
                    "timestamp": now.isoformat(),
                    "event": "created",
                    "detail": f"Auto-promoted from Defendant Notes ({trigger})",
                    "agent": notes.get("agent", "Lifecycle Sync"),
                }],
                "outcome": None,
                "outcome_note": "",
                "closed_at": None,
                "defendant_snapshot": arrest_doc,
                "created_at": now,
                "updated_at": now,
                "created_by": notes.get("agent", "Lifecycle Sync"),
                "source": "defendant_notes",
            }

            await pb_col.insert_one(doc)
            logger.info(f"Pipeline entry created for {booking_number} (trigger={trigger})")
            return doc

    except Exception as exc:
        logger.warning(f"_sync_to_pipeline error for {booking_number}: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/defendant-notes/<booking_number>
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.get("/defendant-notes/{booking_number}")
async def get_defendant_notes(booking_number: str):
    doc = await _get_notes_doc(booking_number)
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# PATCH  /api/defendant-notes/<booking_number>
# Body fields (all optional):
#   shamrock_status   — "new" | "contacted" | "negotiating" | "paperwork" | "ready" | "bonded" | "closed"
#   shamrock_notes    — free-text notes
#   follow_up_date    — ISO date string
#   next_action       — free-text
#   pref_comm         — "call" | "text" | "email" | "whatsapp"
#   dnb               — bool  (Do Not Bond)
#   dnc               — bool  (Do Not Call)
#   dnb_reason        — string
#   dnc_reason        — string
#   agent             — agent name who made the update
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.patch("/defendant-notes/{booking_number}")
async def patch_defendant_notes(request: Request, booking_number: str):
    col = get_collection("defendant_notes")
    body = await request.json() or {}

    allowed = {
        "shamrock_status", "shamrock_notes", "follow_up_date", "next_action",
        "pref_comm", "dnb", "dnc", "dnb_reason", "dnc_reason", "agent",
    }
    update = {k: v for k, v in body.items() if k in allowed}
    update["updated_at"] = _now()
    update["booking_number"] = booking_number

    await col.update_one(
        {"booking_number": booking_number},
        {"$set": update},
        upsert=True,
    )
    doc = await _get_notes_doc(booking_number)
    return {"success": True, "notes": doc}


# ─────────────────────────────────────────────────────────────────────────────
# POST  /api/defendant-contact-log/<booking_number>
# Body:
#   method    — "call" | "text" | "email" | "in_person"
#   direction — "outbound" | "inbound"
#   summary   — what was said / outcome
#   agent     — agent name
#   contact   — who was contacted (defendant | cosigner name)
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.post("/defendant-contact-log/{booking_number}")
async def log_contact(request: Request, booking_number: str):
    col = get_collection("defendant_notes")
    body = await request.json() or {}

    entry = {
        "method":    body.get("method", "call"),
        "direction": body.get("direction", "outbound"),
        "summary":   body.get("summary", ""),
        "agent":     body.get("agent", ""),
        "contact":   body.get("contact", "defendant"),
        "ts":        _now(),
    }

    # Push to contact_log array; also set contacted_at if first contact
    update_ops = {"$push": {"contact_log": entry}, "$set": {"updated_at": _now()}}

    # Auto-bump shamrock_status to "contacted" if it was "new" or unset
    existing = await _get_notes_doc(booking_number)
    if not existing.get("shamrock_status") or existing.get("shamrock_status") == "new":
        update_ops["$set"]["shamrock_status"] = "contacted"
        update_ops["$set"]["first_contacted_at"] = _now()

    await col.update_one(
        {"booking_number": booking_number},
        update_ops,
        upsert=True,
    )

    # ── Auto-sync to outreach pipeline ──
    pipeline_result = await _sync_to_pipeline(
        booking_number, trigger="contact_logged", contact_entry=entry
    )
    synced = pipeline_result is not None

    doc = await _get_notes_doc(booking_number)
    return {"success": True, "notes": doc, "pipeline_synced": synced}


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/defendant-notes/bulk
# Query: booking_numbers=BN1,BN2,BN3,...
# Returns a map { booking_number: notes_doc }
# Used by the frontend to batch-load notes for all visible cards.
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.get("/defendant-notes/bulk")
async def bulk_get_notes(booking_numbers: str = Query(default="")):
    raw = booking_numbers
    booking_numbers = [b.strip() for b in raw.split(",") if b.strip()]
    if not booking_numbers:
        return {}

    col = get_collection("defendant_notes")
    result = {}
    async for doc in col.find(
        {"booking_number": {"$in": booking_numbers}}, {"_id": 0}
    ):
        result[doc["booking_number"]] = doc
    return result


# ─────────────────────────────────────────────────────────────────────────────
# POST  /api/finalize-bond/step1/<booking_number>
# Step 1: Review — returns a summary for the agent to confirm
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.post("/finalize-bond/step1/{booking_number}")
async def finalize_bond_step1(request: Request, booking_number: str):
    arrests = get_collection("arrests")
    notes_col = get_collection("defendant_notes")

    arrest = await arrests.find_one({"booking_number": booking_number}, {"_id": 0})
    if not arrest:
        return JSONResponse({"success": False, "error": "Defendant not found"}, status_code=404)

    notes = await _get_notes_doc(booking_number)
    body = await request.json() or {}

    # Build review checklist
    checklist = {
        "defendant_name":    arrest.get("full_name", "—"),
        "booking_number":    booking_number,
        "county":            arrest.get("county", "—"),
        "bond_amount":       arrest.get("bond_amount", 0),
        "charges":           arrest.get("charges", "—"),
        "insurance_company": body.get("insurance_company", notes.get("insurance_company", "—")),
        "premium":           round((arrest.get("bond_amount", 0) or 0) * 0.10, 2),
        "poa_number":        body.get("poa_number", notes.get("poa_number", "")),
        "indemnitor_name":   body.get("indemnitor_name", notes.get("indemnitor_name", "")),
        "indemnitor_phone":  body.get("indemnitor_phone", notes.get("indemnitor_phone", "")),
        "court_date":        arrest.get("court_date", "—"),
        "case_number":       arrest.get("case_number", "—"),
        "agent":             body.get("agent", notes.get("agent", "")),
        "notes":             notes.get("shamrock_notes", ""),
        "review_token":      f"REVIEW-{booking_number}-{int(datetime.now().timestamp())}",
    }

    # Save partial finalization data
    await notes_col.update_one(
        {"booking_number": booking_number},
        {"$set": {
            "finalization_step1": checklist,
            "finalization_step1_at": _now(),
            "updated_at": _now(),
        }},
        upsert=True,
    )

    return {"success": True, "review": checklist}


# ─────────────────────────────────────────────────────────────────────────────
# POST  /api/finalize-bond/step2/<booking_number>
# Step 2: Confirm — marks the bond as finalized
# Body:
#   review_token   — must match the token from step1
#   confirmed_by   — agent name
#   poa_number     — final POA number
#   notes          — any final notes
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.post("/finalize-bond/step2/{booking_number}")
async def finalize_bond_step2(request: Request, booking_number: str):
    arrests = get_collection("arrests")
    notes_col = get_collection("defendant_notes")
    active_bonds = get_collection("active_bonds")

    body = await request.json() or {}
    notes = await _get_notes_doc(booking_number)

    step1 = notes.get("finalization_step1", {})
    if not step1:
        return JSONResponse({"success": False, "error": "Step 1 review not found. Please complete Step 1 first."}, status_code=400)

    # Token validation (soft check — tokens are informational, not cryptographic)
    provided_token = body.get("review_token", "")
    expected_token = step1.get("review_token", "")
    if provided_token and expected_token and provided_token != expected_token:
        return JSONResponse({"success": False, "error": "Review token mismatch. Please restart the finalization process."}, status_code=400)

    arrest = await arrests.find_one({"booking_number": booking_number}, {"_id": 0})
    if not arrest:
        return JSONResponse({"success": False, "error": "Defendant not found"}, status_code=404)

    finalized_at = _now()
    confirmed_by = body.get("confirmed_by", step1.get("agent", ""))
    poa_number = body.get("poa_number", step1.get("poa_number", ""))
    final_notes = body.get("notes", step1.get("notes", ""))

    # Build active bond record
    active_bond = {
        "booking_number":    booking_number,
        "defendant_name":    arrest.get("full_name", ""),
        "county":            arrest.get("county", ""),
        "bond_amount":       arrest.get("bond_amount", 0),
        "premium":           round((arrest.get("bond_amount", 0) or 0) * 0.10, 2),
        "insurance_company": step1.get("insurance_company", ""),
        "poa_number":        poa_number,
        "indemnitor_name":   step1.get("indemnitor_name", ""),
        "indemnitor_phone":  step1.get("indemnitor_phone", ""),
        "court_date":        arrest.get("court_date", ""),
        "case_number":       arrest.get("case_number", ""),
        "charges":           arrest.get("charges", ""),
        "agent":             confirmed_by,
        "notes":             final_notes,
        "finalized_at":      finalized_at,
        "bond_status":       "active",
        "dob":               arrest.get("dob", ""),
        "address":           arrest.get("address", ""),
        "city":              arrest.get("city", ""),
        "state":             arrest.get("state", ""),
        "zip":               arrest.get("zip", ""),
    }

    # Upsert into active_bonds
    await active_bonds.update_one(
        {"booking_number": booking_number},
        {"$set": active_bond},
        upsert=True,
    )

    # Update defendant notes
    await notes_col.update_one(
        {"booking_number": booking_number},
        {"$set": {
            "shamrock_status":   "bonded",
            "bond_finalized":    True,
            "bond_finalized_at": finalized_at,
            "confirmed_by":      confirmed_by,
            "poa_number":        poa_number,
            "updated_at":        finalized_at,
        }},
        upsert=True,
    )

    return {
        "success": True,
        "message": f"Bond finalized for {arrest.get('full_name', booking_number)}",
        "finalized_at": finalized_at,
        "active_bond": active_bond,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/dnb-list   — returns all defendants marked DNB or DNC
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.get("/dnb-list")
async def get_dnb_list():
    col = get_collection("defendant_notes")
    results = []
    async for doc in col.find(
        {"$or": [{"dnb": True}, {"dnc": True}]}, {"_id": 0}
    ).sort("updated_at", -1):
        results.append(doc)
    return {"count": len(results), "records": results}


# ─────────────────────────────────────────────────────────────────────────────
# POST  /api/defendant-notes/<booking_number>/promote-to-pipeline
# Explicitly move a defendant to the Outreach pipeline
# Body (all optional):
#   stage   — pipeline stage to start at (default: current shamrock_status or "contacted")
#   note    — optional note to attach to the timeline
#   agent   — agent name
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.post("/defendant-notes/{booking_number}/promote-to-pipeline")
async def promote_to_pipeline(request: Request, booking_number: str):
    """Explicitly promote a defendant to the outreach (prospective bonds) pipeline."""
    try:
        body = await request.json() or {}
        stage = body.get("stage", "").strip()
        note = body.get("note", "").strip()
        agent = body.get("agent", "Dashboard")

        # Ensure the defendant_notes record exists
        notes_col = get_collection("defendant_notes")
        notes = await _get_notes_doc(booking_number)

        # If no status set, default to "contacted"
        if not notes.get("shamrock_status") or notes.get("shamrock_status") == "new":
            await notes_col.update_one(
                {"booking_number": booking_number},
                {"$set": {"shamrock_status": "contacted", "updated_at": _now(), "agent": agent}},
                upsert=True,
            )

        # Check if already tracked
        pb_col = get_collection("prospective_bonds")
        existing = await pb_col.find_one({"booking_number": booking_number})
        if existing and existing.get("status") == "active":
            return JSONResponse(status_code=409, content={
                "success": False,
                "error": "Already in outreach pipeline",
                "stage": existing.get("stage", "contacted"),
            })

        # Determine stage
        if not stage:
            status = notes.get("shamrock_status", "contacted")
            stage = _STATUS_TO_STAGE.get(status, "contacted")

        # Create via sync helper
        result = await _sync_to_pipeline(
            booking_number,
            trigger="manual_promote",
            stage_override=stage,
        )

        if result:
            # Add the optional note
            if note:
                now = datetime.now(timezone.utc)
                await pb_col.update_one(
                    {"booking_number": booking_number},
                    {"$push": {
                        "timeline": {
                            "timestamp": now.isoformat(),
                            "event": "note",
                            "detail": note,
                            "agent": agent,
                        },
                        "communication_log": {
                            "timestamp": now.isoformat(),
                            "type": "note",
                            "text": note,
                            "agent": agent,
                            "source": "promote_action",
                        },
                    }},
                )

            return {
                "success": True,
                "message": f"Promoted to outreach pipeline (stage: {stage})",
                "stage": stage,
            }
        else:
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": "Could not promote — check defendant exists in arrests collection",
            })

    except Exception as exc:
        logger.exception(f"promote_to_pipeline error for {booking_number}")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/defendant-notes/<booking_number>/pipeline-status
# Quick check: is this defendant tracked in the outreach pipeline?
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.get("/defendant-notes/{booking_number}/pipeline-status")
async def pipeline_status(booking_number: str):
    """Check if a defendant is in the outreach pipeline."""
    pb_col = get_collection("prospective_bonds")
    existing = await pb_col.find_one({"booking_number": booking_number})
    if existing:
        return {
            "tracked": True,
            "stage": existing.get("stage", "contacted"),
            "status": existing.get("status", "active"),
        }
    return {"tracked": False}