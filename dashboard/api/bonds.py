"""
ShamrockLeads — Bonds API Blueprint
Endpoints: /api/write-bond, /api/active-bonds (CRUD), /api/appearance-bond-pdf
"""
from __future__ import annotations

import json as json_lib
import os
from datetime import datetime, timezone, timedelta

from quart import Blueprint, jsonify, request, Response
from dashboard.extensions import get_collection
from dashboard.services.risk_engine import compute_risk_score

bonds_bp = Blueprint("bonds", __name__)

import asyncio
import logging
import os
import traceback
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# RECORD BOND — Retrospective manual bond entry
# Creates active_bonds + payments + assigns POA + audit log
# ═══════════════════════════════════════════════════════════════════════════════

@bonds_bp.route("/bonds/record", methods=["POST"])
async def api_record_bond():
    """
    Record a bond retrospectively. This is for bonds that were written
    manually (in-person, phone, etc.) and need to be logged into the system
    so they feed into analytics, liability, and revenue tracking.

    Creates:
      1. active_bonds document (liability tracking)
      2. payments document (revenue tracking)
      3. poa_inventory update (marks POA as assigned)
      4. audit_events document (audit trail)

    Body:
        {
            "defendant_name":    "John Doe",
            "booking_number":    "2025-001234",
            "county":            "Lee",
            "bond_amount":       5000,
            "premium":           500,
            "surety":            "osi",
            "poa_number":        "12345",
            "case_number":       "25-CF-001234",
            "court_date":        "2025-06-15",
            "court_time":        "8:30 AM",
            "court_location":    "Lee County Justice Center",
            "bond_date":         "2025-04-30",
            "charges":           "Battery (Domestic Violence)",
            "facility":          "Lee County Jail",
            "indemnitor_name":   "Jane Doe",
            "indemnitor_phone":  "2395550000",
            "indemnitor_email":  "jane@example.com",
            "indemnitor_relationship": "Wife",
            "payment_method":    "cash",
            "agent_name":        "Brendan O'Neal",
            "notes":             "Walk-in client"
        }
    """
    data = await request.get_json(force=True) or {}

    # ── Validate required fields ────────────────────────────────────────────
    defendant_name = (data.get("defendant_name") or "").strip()
    booking_number = (data.get("booking_number") or "").strip()
    poa_number = (data.get("poa_number") or "").strip()
    surety = (data.get("surety") or "osi").lower().strip()

    errors = []
    if not defendant_name:
        errors.append("defendant_name is required")
    if not booking_number:
        errors.append("booking_number is required")
    if not poa_number:
        errors.append("poa_number is required")
    if surety not in ("osi", "palmetto"):
        errors.append("surety must be 'osi' or 'palmetto'")
    if errors:
        return jsonify({"success": False, "errors": errors}), 400

    try:
        bond_amount = float(data.get("bond_amount") or 0)
    except (ValueError, TypeError):
        bond_amount = 0.0
    try:
        premium = float(data.get("premium") or 0)
    except (ValueError, TypeError):
        premium = 0.0

    county = (data.get("county") or "").strip()
    case_number = (data.get("case_number") or "").strip()
    court_date = (data.get("court_date") or "").strip()
    court_time = (data.get("court_time") or "").strip()
    court_location = (data.get("court_location") or "").strip()
    bond_date_str = (data.get("bond_date") or "").strip()
    charges = (data.get("charges") or "").strip()
    facility = (data.get("facility") or "").strip()
    indemnitor_name = (data.get("indemnitor_name") or "").strip()
    indemnitor_phone = (data.get("indemnitor_phone") or "").strip()
    indemnitor_email = (data.get("indemnitor_email") or "").strip()
    indemnitor_relationship = (data.get("indemnitor_relationship") or "").strip()
    payment_method = (data.get("payment_method") or "cash").strip()
    agent_name = (data.get("agent_name") or "Brendan O'Neal").strip()
    notes = (data.get("notes") or "").strip()

    now = datetime.now(timezone.utc)

    # Parse bond_date or default to now
    bond_date = now
    if bond_date_str:
        try:
            bond_date = datetime.strptime(bond_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass  # Fall back to now

    # ── 0. Snapshot FTA risk intelligence from arrest record ─────────────────
    fta_risk_score = None
    fta_risk_level = ""
    fta_risk_confidence = None
    try:
        arrests = get_collection("arrests")
        arrest_doc = await arrests.find_one(
            {"booking_number": booking_number},
            {"fta_risk_score": 1, "fta_risk_level": 1, "fta_risk_confidence": 1},
        )
        if arrest_doc:
            fta_risk_score = arrest_doc.get("fta_risk_score")
            fta_risk_level = arrest_doc.get("fta_risk_level", "")
            fta_risk_confidence = arrest_doc.get("fta_risk_confidence")
    except Exception as exc:
        logger.warning("[record-bond] FTA lookup error: %s", exc)

    # ── 1. Create / upsert active_bonds document ────────────────────────────
    active_bonds = get_collection("active_bonds")
    bond_doc = {
        "booking_number": booking_number,
        "defendant_name": defendant_name,
        "county": county,
        "facility": facility,
        "bond_amount": bond_amount,
        "premium": premium,
        "insurance_company": surety.upper(),
        "poa_number": poa_number,
        "case_number": case_number,
        "charges": charges,
        "court_date": court_date,
        "court_time": court_time,
        "court_location": court_location,
        "bond_date": bond_date.isoformat(),
        "status": "active",
        "source": "retrospective_manual",
        "manual_entry": booking_number.startswith("MANUAL-"),
        "agent_name": agent_name,
        "indemnitor_name": indemnitor_name,
        "indemnitor_phone": indemnitor_phone,
        "indemnitor_email": indemnitor_email,
        "indemnitor_relationship": indemnitor_relationship,
        "payment_method": payment_method,
        "notes": notes,
        "check_in_required": False,
        "fta_risk_score": fta_risk_score,
        "fta_risk_level": fta_risk_level,
        "fta_risk_confidence": fta_risk_confidence,
        "created_at": bond_date,
        "updated_at": now,
    }

    await active_bonds.update_one(
        {"booking_number": booking_number},
        {"$set": bond_doc},
        upsert=True,
    )
    logger.info("[record-bond] Active bond created: %s — %s (%s)", booking_number, defendant_name, surety.upper())

    # ── 2. Create payments document (revenue tracking) ──────────────────────
    payment_doc = None
    if premium > 0:
        payments = get_collection("payments")
        payment_doc = {
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "county": county,
            "amount": premium,
            "bond_amount": bond_amount,
            "surety": surety.upper(),
            "poa_number": poa_number,
            "method": payment_method,
            "status": "completed",
            "source": "retrospective_manual",
            "agent_name": agent_name,
            "indemnitor_name": indemnitor_name,
            "indemnitor_phone": indemnitor_phone,
            "timestamp": bond_date,
            "created_at": now,
        }
        await payments.update_one(
            {"booking_number": booking_number, "source": "retrospective_manual"},
            {"$set": payment_doc},
            upsert=True,
        )
        logger.info("[record-bond] Payment recorded: $%.2f for %s", premium, booking_number)

    # ── 3. Assign POA in inventory ──────────────────────────────────────────
    poa_result = {"assigned": False}
    poa_inventory = get_collection("poa_inventory")
    poa_doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety})
    if poa_doc:
        if poa_doc.get("status") == "available":
            await poa_inventory.update_one(
                {"poa_number": poa_number, "surety_id": surety},
                {"$set": {
                    "status": "assigned",
                    "bond_case_id": booking_number,
                    "used_at": now.isoformat(),
                    "defendant_name": defendant_name,
                }},
            )
            poa_result = {"assigned": True, "poa_number": poa_number, "was": "available"}
            logger.info("[record-bond] POA %s assigned to %s", poa_number, booking_number)
        elif poa_doc.get("status") == "assigned":
            # Already assigned — update the case link
            await poa_inventory.update_one(
                {"poa_number": poa_number, "surety_id": surety},
                {"$set": {"bond_case_id": booking_number, "defendant_name": defendant_name}},
            )
            poa_result = {"assigned": True, "poa_number": poa_number, "was": "already_assigned"}
        else:
            poa_result = {"assigned": False, "poa_number": poa_number, "reason": f"POA is {poa_doc.get('status')}"}
    else:
        # POA not in inventory — create it as assigned (user manually entered a number)
        await poa_inventory.insert_one({
            "surety_id": surety,
            "poa_prefix": "",
            "poa_number": poa_number,
            "poa_full": poa_number,
            "max_bond_value": 0,
            "status": "assigned",
            "bond_case_id": booking_number,
            "defendant_name": defendant_name,
            "used_at": now.isoformat(),
            "book_number": "manual_entry",
            "assigned_to_agent": agent_name,
            "received_at": now.isoformat(),
        })
        poa_result = {"assigned": True, "poa_number": poa_number, "was": "created_and_assigned"}
        logger.info("[record-bond] POA %s created and assigned (not in inventory)", poa_number)

    # ── 4. Audit event ──────────────────────────────────────────────────────
    try:
        audit_col = get_collection("audit_events")
        await audit_col.insert_one({
            "event_type": "bond_recorded_retroactive",
            "entity_id": booking_number,
            "entity_type": "bond_case",
            "defendant_name": defendant_name,
            "county": county,
            "bond_amount": bond_amount,
            "premium": premium,
            "surety": surety.upper(),
            "poa_number": poa_number,
            "case_number": case_number,
            "agent_name": agent_name,
            "source": "retrospective_manual",
            "timestamp": now,
        })
    except Exception as exc:
        logger.warning("[record-bond] Audit log error: %s", exc)

    # ── 5. Update arrest record with bond status (if exists) ────────────────
    try:
        arrests = get_collection("arrests")
        await arrests.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "bond_written": True,
                "bond_written_at": now.isoformat(),
                "bond_poa_number": poa_number,
                "bond_surety": surety.upper(),
                "bond_premium": premium,
            }},
        )
    except Exception as exc:
        logger.warning("[record-bond] Arrest update error: %s", exc)

    logger.info(
        "☘️ BOND RECORDED — %s | Booking: %s | County: %s | Bond: $%.2f | Premium: $%.2f | Surety: %s | POA: %s | Indemnitor: %s (%s)",
        defendant_name, booking_number, county, bond_amount, premium, surety.upper(), poa_number, indemnitor_name, indemnitor_phone
    )

    return jsonify({
        "success": True,
        "message": f"Bond recorded for {defendant_name}",
        "booking_number": booking_number,
        "bond_amount": bond_amount,
        "premium": premium,
        "surety": surety.upper(),
        "poa": poa_result,
        "payment_recorded": premium > 0,
    })

@bonds_bp.route("/write-bond", methods=["POST"])
async def api_write_bond():
    """
    Accept defendant + indemnitor data + insurance company selection,
    format a GAS-compatible SignNow payload, and forward it.

    Accepts indemnitors as a list under the key "indemnitors" (up to 5),
    or a single indemnitor under the legacy key "indemnitor".
    All fields mirror Dashboard.html addIndemnitor() schema exactly.
    """
    data = await request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No payload received"}), 400

    insurer = data.get("insurance_company", "osi")
    defendant = data.get("defendant", {})
    booking = data.get("booking", {})
    bond = data.get("bond", {})
    charges = data.get("charges", "")
    court = data.get("court", {})

    # ── Indemnitor(s) — accept list or single object ──────────────────────────
    raw_indemnitors = data.get("indemnitors") or []
    if not raw_indemnitors and data.get("indemnitor"):
        raw_indemnitors = [data["indemnitor"]]

    def _build_indemnitor(ind: dict) -> dict:
        """Normalize a single indemnitor dict to the GAS/Dashboard.html schema."""
        g = lambda *keys: next((str(ind.get(k, "")).strip() for k in keys if ind.get(k)), "")
        return {
            # Personal
            "firstName":        g("firstName", "IndFirstName", "indemnitorFirstName", "first_name"),
            "middleName":       g("middleName", "IndMiddleName", "indemnitorMiddleName"),
            "lastName":         g("lastName", "IndLastName", "indemnitorLastName", "last_name"),
            "relationship":     g("relationship", "IndRelation", "indemnitorRelation", "Relationship"),
            "dob":              g("dob", "IndDOB", "indemnitorDOB"),
            "ssn":              g("ssn", "IndSSN", "indemnitorSSN"),
            "dl":               g("dl", "IndDL", "indemnitorDL", "dlNumber"),
            "dlState":          g("dlState", "IndDLState", "indemnitorDLState") or "FL",
            # Contact
            "phone":            g("phone", "IndPhone", "indemnitorPhone"),
            "email":            g("email", "IndEmail", "indemnitorEmail"),
            # Address
            "address":          g("address", "IndAddress", "indemnitorStreetAddress", "indemnitorAddress"),
            "city":             g("city", "IndCity", "indemnitorCity"),
            "state":            g("state", "IndState", "indemnitorState") or "FL",
            "zip":              g("zip", "IndZip", "indemnitorZipCode", "indemnitorZip"),
            # Employment
            "employer":         g("employer", "IndEmployer", "indemnitorEmployerName"),
            "employerPhone":    g("employerPhone", "IndEmployerPhone", "indemnitorEmployerPhone"),
            "employerCity":     g("employerCity", "IndEmployerCity", "indemnitorEmployerCity"),
            "employerState":    g("employerState", "IndEmployerState", "indemnitorEmployerState"),
            "supervisor":       g("supervisor", "IndJobTitle", "indemnitorSupervisorName", "jobTitle"),
            "supervisorPhone":  g("supervisorPhone", "IndSupervisorPhone", "indemnitorSupervisorPhone"),
            # References
            "ref1Name":         g("ref1Name", "Ref1Name", "reference1Name"),
            "ref1Relation":     g("ref1Relation", "Ref1Relation", "reference1Relation"),
            "ref1Phone":        g("ref1Phone", "Ref1Phone", "reference1Phone"),
            "ref1Address":      g("ref1Address", "Ref1Address", "reference1Address"),
            "ref2Name":         g("ref2Name", "Ref2Name", "reference2Name"),
            "ref2Relation":     g("ref2Relation", "Ref2Relation", "reference2Relation"),
            "ref2Phone":        g("ref2Phone", "Ref2Phone", "reference2Phone"),
            "ref2Address":      g("ref2Address", "Ref2Address", "reference2Address"),
        }

    indemnitors_payload = [_build_indemnitor(ind) for ind in raw_indemnitors]

    # Validate required fields
    if not defendant.get("full_name"):
        return jsonify({"success": False, "error": "Defendant name required"}), 400
    if not booking.get("booking_number"):
        return jsonify({"success": False, "error": "Booking number required"}), 400

    # ── Format GAS-compatible payload ──
    gas_payload = {
        "action": "sendPaperwork",
        "source": "shamrock-leads-dashboard",
        "insuranceCompany": insurer.upper(),
        "defendant": {
            "fullName":   defendant.get("full_name", ""),
            "firstName":  defendant.get("first_name", ""),
            "lastName":   defendant.get("last_name", ""),
            "middleName": defendant.get("middle_name", ""),
            "dob":        defendant.get("dob", ""),
            "address":    defendant.get("address", ""),
            "city":       defendant.get("city", ""),
            "state":      defendant.get("state", "FL"),
            "zip":        defendant.get("zip", ""),
            "sex":        defendant.get("sex", ""),
            "race":       defendant.get("race", ""),
            "height":     defendant.get("height", ""),
            "weight":     defendant.get("weight", ""),
        },
        "booking": {
            "bookingNumber": booking.get("booking_number", ""),
            "county":        booking.get("county", ""),
            "facility":      booking.get("facility", ""),
            "agency":        booking.get("agency", ""),
            "arrestDate":    booking.get("arrest_date", ""),
            "bookingDate":   booking.get("booking_date", ""),
        },
        "bond": {
            "totalAmount": bond.get("amount", 0),
            "premium":     bond.get("premium", 0),
            "type":        bond.get("type", ""),
            "paid":        bond.get("paid", "NO"),
        },
        "charges": charges,
        "court": {
            "date":       court.get("date", ""),
            "time":       court.get("time", ""),
            "type":       court.get("type", ""),
            "location":   court.get("location", ""),
            "caseNumber": court.get("case_number", ""),
        },
        # ── Indemnitors (full schema, mirrors Dashboard.html addIndemnitor) ──
        "indemnitors": indemnitors_payload,
        # Legacy single-indemnitor key for GAS backward compat
        "indemnitor": indemnitors_payload[0] if indemnitors_payload else {},
        # Intake source tracking
        "intake_id":  data.get("intake_id", ""),
        "intake_source": data.get("intake_source", "shamrock-leads-dashboard"),
    }

    # Log the formatted payload
    logger.info(
        "📋 WRITE BOND — %s | Insurance: %s | Bond: $%.2f | Premium: $%.2f | County: %s | Booking: %s | Indemnitors: %d",
        defendant.get("full_name", "Unknown"), insurer.upper(), bond.get("amount", 0), bond.get("premium", 0), booking.get("county", "Unknown"), booking.get("booking_number", "N/A"), len(indemnitors_payload)
    )

    # ── Forward to GAS (when configured) ──
    gas_url = os.getenv("GAS_WEB_APP_URL", "")
    if gas_url:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(gas_url, json=gas_payload, timeout=30)
                if resp.status_code < 400:
                    content_type = resp.headers.get("content-type", "")
                    gas_resp = resp.json() if "application/json" in content_type else resp.text[:200]
                    return jsonify({
                        "success": True,
                        "message": f"Packet sent to GAS for {defendant.get('full_name')}",
                        "insurance_company": insurer.upper(),
                        "indemnitor_count": len(indemnitors_payload),
                        "gas_response": gas_resp,
                    })
                else:
                    return jsonify({
                        "success": False,
                        "error": f"GAS returned {resp.status_code}: {resp.text[:200]}",
                    }), 502
        except Exception as e:
            return jsonify({
                "success": False,
                "error": f"GAS connection failed: {str(e)}",
            }), 502

    # No GAS URL configured — return success with payload for review
    return jsonify({
        "success": True,
        "message": f"Bond packet prepared for {defendant.get('full_name', 'Unknown')} via {insurer.upper()}",
        "insurance_company": insurer.upper(),
        "indemnitor_count": len(indemnitors_payload),
        "payload": gas_payload,
        "note": "GAS_WEB_APP_URL not configured — payload logged to console. Set GAS_WEB_APP_URL in .env to enable forwarding.",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVE BONDS — GEOLOCATION & RISK MITIGATION
# ═══════════════════════════════════════════════════════════════════════════════

@bonds_bp.route("/active-bonds", methods=["GET"])
async def api_active_bonds_list():
    """List all active bonds with risk scores, check-in status, and FTA intelligence."""
    active_bonds = get_collection("active_bonds")
    try:
        cursor = active_bonds.find({}, {"_id": 0}).sort("created_at", -1).limit(100)
        bonds = await cursor.to_list(length=100)

        # ── Bulk-enrich FTA intelligence from arrests for bonds missing it ─────
        needs_fta = [b["booking_number"] for b in bonds
                     if b.get("fta_risk_score") is None and b.get("booking_number")]
        fta_map = {}
        if needs_fta:
            try:
                arrests = get_collection("arrests")
                fta_cursor = arrests.find(
                    {"booking_number": {"$in": needs_fta}, "fta_risk_score": {"$exists": True}},
                    {"_id": 0, "booking_number": 1, "fta_risk_score": 1, "fta_risk_level": 1, "fta_risk_confidence": 1},
                )
                async for adoc in fta_cursor:
                    fta_map[adoc["booking_number"]] = adoc
            except Exception as fta_err:
                logger.warning("[active-bonds] FTA enrichment error: %s", fta_err)

        for b in bonds:
            if hasattr(b.get("created_at"), "isoformat"):
                b["created_at"] = b["created_at"].isoformat()
            if hasattr(b.get("last_checkin"), "isoformat"):
                b["last_checkin"] = b["last_checkin"].isoformat()
            # Merge FTA data from arrest lookup if bond record doesn't have it
            if b.get("fta_risk_score") is None and b.get("booking_number") in fta_map:
                adoc = fta_map[b["booking_number"]]
                b["fta_risk_score"] = adoc.get("fta_risk_score")
                b["fta_risk_level"] = adoc.get("fta_risk_level", "")
                b["fta_risk_confidence"] = adoc.get("fta_risk_confidence")

        return jsonify({"success": True, "bonds": bonds, "count": len(bonds)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "bonds": []}), 500


@bonds_bp.route("/active-bonds", methods=["POST"])
async def api_active_bonds_create():
    """Create a new active bond record."""
    data = await request.get_json(force=True) or {}
    active_bonds = get_collection("active_bonds")
    booking_number = data.get("booking_number", "")
    if not booking_number:
        return jsonify({"success": False, "error": "booking_number required"}), 400
    now = datetime.now(timezone.utc)
    doc = {
        "booking_number": booking_number,
        "defendant_name": data.get("defendant_name", ""),
        "county": data.get("county", ""),
        "facility": data.get("facility", ""),
        "bond_amount": data.get("bond_amount", 0),
        "premium": data.get("premium", 0),
        "insurance_company": data.get("insurance_company", "osi").upper(),
        "poa_number": data.get("poa_number", ""),
        "case_number": data.get("case_number", ""),
        "status": "active",
        "risk_score": data.get("risk_score", 0),
        "check_in_required": data.get("check_in_required", False),
        "check_in_frequency_days": data.get("check_in_frequency_days", 30),
        "last_checkin": None,
        "next_checkin_due": None,
        "indemnitor_name": data.get("indemnitor_name", ""),
        "indemnitor_phone": data.get("indemnitor_phone", ""),
        "indemnitor_email": data.get("indemnitor_email", ""),
        "fta_risk_score": data.get("fta_risk_score"),
        "fta_risk_level": data.get("fta_risk_level", ""),
        "fta_risk_confidence": data.get("fta_risk_confidence"),
        "created_at": now,
        "updated_at": now,
    }
    try:
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": doc},
            upsert=True,
        )
        return jsonify({"success": True, "booking_number": booking_number})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bonds_bp.route("/active-bonds/<booking_number>/check-in", methods=["POST"])
async def api_active_bond_check_in(booking_number):
    """Record a defendant check-in."""
    active_bonds = get_collection("active_bonds")
    data = await request.get_json(force=True) or {}
    now = datetime.now(timezone.utc)
    try:
        bond = await active_bonds.find_one({"booking_number": booking_number})
        if not bond:
            return jsonify({"success": False, "error": "Bond not found"}), 404
        freq_days = bond.get("check_in_frequency_days", 30)
        next_due = now + timedelta(days=freq_days)
        checkin_doc = {
            "booking_number": booking_number,
            "checkin_at": now,
            "method": data.get("method", "manual"),
            "location": data.get("location", ""),
            "notes": data.get("notes", ""),
            "gps_lat": data.get("gps_lat"),
            "gps_lon": data.get("gps_lon"),
        }
        checkins = get_collection("bond_checkins")
        await checkins.insert_one(checkin_doc)
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {"last_checkin": now, "next_checkin_due": next_due, "updated_at": now}},
        )
        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "checked_in_at": now.isoformat(),
            "next_due": next_due.isoformat(),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bonds_bp.route("/active-bonds/<booking_number>/alert", methods=["POST"])
async def api_active_bond_alert(booking_number):
    """Create a risk alert for an active bond."""
    active_bonds = get_collection("active_bonds")
    data = await request.get_json(force=True) or {}
    now = datetime.now(timezone.utc)
    alert = {
        "booking_number": booking_number,
        "alert_type": data.get("alert_type", "manual"),
        "severity": data.get("severity", "medium"),
        "message": data.get("message", ""),
        "created_at": now,
    }
    try:
        alerts = get_collection("bond_alerts")
        await alerts.insert_one(alert)
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {"last_alert": now, "updated_at": now}, "$inc": {"alert_count": 1}},
        )
        return jsonify({"success": True, "booking_number": booking_number, "alert_type": alert["alert_type"]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bonds_bp.route("/active-bonds/<booking_number>/status", methods=["PATCH"])
async def api_active_bond_status(booking_number):
    """Update bond status with full audit trail, status_history tracking, and POA lifecycle.

    Valid statuses: active | monitoring | alert | exonerated | forfeited | surrendered | reinstated

    Side-effects:
      - Appends to bond's ``status_history`` array
      - Creates an ``audit_events`` document for every transition
      - On transition TO ``exonerated``: releases assigned POA back to ``available``
      - On transition FROM ``exonerated``: clears exonerated_at timestamp
    """
    active_bonds = get_collection("active_bonds")
    data = await request.get_json(force=True) or {}
    new_status = data.get("status", "")
    agent = data.get("agent", "Dashboard")
    note = data.get("note", "")
    valid_statuses = {"active", "monitoring", "alert", "exonerated", "forfeited", "surrendered", "reinstated"}
    if new_status not in valid_statuses:
        return jsonify({"success": False, "error": f"Invalid status. Must be one of: {sorted(valid_statuses)}"}), 400
    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Fetch current bond for old status + POA info
        bond = await active_bonds.find_one({"booking_number": booking_number})
        if not bond:
            return jsonify({"success": False, "error": "Bond not found"}), 404

        old_status = bond.get("status", "active")
        if old_status == new_status:
            return jsonify({"success": True, "status": new_status, "note": "No change"})

        # Build status history entry
        history_entry = {
            "from_status": old_status,
            "to_status": new_status,
            "timestamp": now_iso,
            "agent": agent,
            "note": note,
        }

        # Build update payload
        set_fields: dict[str, object] = {"status": new_status, "updated_at": now}
        if new_status == "exonerated":
            set_fields["exonerated_at"] = now_iso
        elif old_status == "exonerated":
            set_fields["exonerated_at"] = None

        await active_bonds.update_one(
            {"booking_number": booking_number},
            {
                "$set": set_fields,
                "$push": {"status_history": history_entry},
            },
        )

        # Auto-release POA when bond is exonerated
        poa_released = False
        poa_number = bond.get("poa_number")
        if new_status == "exonerated" and poa_number:
            try:
                surety_raw = str(bond.get("insurance_company") or bond.get("surety") or "osi").lower()
                surety_id = "palmetto" if ("palm" in surety_raw or "psc" in surety_raw) else "osi"
                poa_col = get_collection("poa_inventory")
                await poa_col.update_one(
                    {"poa_number": str(poa_number), "surety_id": surety_id},
                    {"$set": {"status": "available", "bond_case_id": None, "released_at": now_iso}},
                )
                poa_released = True
            except Exception:
                logger.warning("POA release failed for %s", poa_number, exc_info=True)

        # Write audit event
        try:
            audit_col = get_collection("audit_events")
            await audit_col.insert_one({
                "event_type": "status_change",
                "booking_number": booking_number,
                "defendant_name": bond.get("defendant_name", ""),
                "from_status": old_status,
                "to_status": new_status,
                "agent": agent,
                "note": note,
                "poa_released": poa_released,
                "poa_number": poa_number,
                "timestamp": now_iso,
            })
        except Exception:
            logger.warning("Audit event write failed for %s", booking_number, exc_info=True)

        return jsonify({
            "success": True,
            "status": new_status,
            "from_status": old_status,
            "poa_released": poa_released,
            "poa_number": poa_number if poa_released else None,
            "history_entry": history_entry,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bonds_bp.route("/active-bonds/<booking_number>/status-history", methods=["GET"])
async def api_active_bond_status_history(booking_number):
    """Return the full status_history array for a bond, newest first."""
    active_bonds = get_collection("active_bonds")
    try:
        bond = await active_bonds.find_one(
            {"booking_number": booking_number},
            {"_id": 0, "status_history": 1, "status": 1, "defendant_name": 1},
        )
        if not bond:
            return jsonify({"success": False, "error": "Bond not found"}), 404
        history = list(reversed(bond.get("status_history", [])))
        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "defendant_name": bond.get("defendant_name", ""),
            "current_status": bond.get("status", "active"),
            "history": history,
            "total": len(history),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bonds_bp.route("/active-bonds/missed-checkins", methods=["POST"])
async def api_active_bonds_process_missed():
    """Scan for missed check-ins and create alerts."""
    active_bonds = get_collection("active_bonds")
    now = datetime.now(timezone.utc)
    try:
        cursor = active_bonds.find({
            "status": "active",
            "check_in_required": True,
            "next_checkin_due": {"$lt": now},
        }, {"_id": 0})
        overdue = await cursor.to_list(length=500)
        alerts = get_collection("bond_alerts")
        alert_docs = []
        for bond in overdue:
            alert_docs.append({
                "booking_number": bond["booking_number"],
                "alert_type": "missed_checkin",
                "severity": "high",
                "message": f"Missed check-in — due {bond.get('next_checkin_due')}",
                "created_at": now,
            })
        if alert_docs:
            await alerts.insert_many(alert_docs)
        return jsonify({
            "success": True,
            "overdue_count": len(overdue),
            "alerts_created": len(alert_docs),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# APPEARANCE BOND PDF
# ═══════════════════════════════════════════════════════════════════════════════

@bonds_bp.route("/appearance-bond-pdf", methods=["GET", "POST"])
async def api_appearance_bond_pdf():
    """
    Generate a pre-populated Appearance Bond PDF using the official
    OSI or Palmetto surety-approved templates.

    Accepts GET query params or POST JSON body:
        name, booking, county, bond, charge, surety, date, dob, address,
        court_date, court_time, case_number, poa_number, court_type,
        first_name, last_name, indemnitor_name
    """
    try:
        from dashboard.bond_pdf_service import generate_appearance_bond, generate_safe_filename

        # Accept both GET query params and POST JSON body
        if request.method == "POST":
            try:
                d = await request.get_json(force=True) or {}
            except Exception:
                d = {}
            def _p(key, default=""):
                return d.get(key, request.args.get(key, default))
        else:
            def _p(key, default=""):
                return request.args.get(key, default)

        data = {
            "name": _p("name") or _p("defendant_name", ""),
            "first_name": _p("first_name", ""),
            "last_name": _p("last_name", ""),
            "booking_number": _p("booking") or _p("booking_number", ""),
            "county": _p("county", ""),
            "bond_amount": _p("bond") or _p("bond_amount", "0"),
            "charge": _p("charge", ""),
            "surety": _p("surety", "osi"),
            "bond_date": _p("date") or _p("bond_date") or datetime.now().strftime("%m/%d/%Y"),
            "dob": _p("dob") or _p("date_of_birth", ""),
            "address": _p("address", ""),
            "court_date": _p("court_date", ""),
            "court_time": _p("court_time", ""),
            "case_number": _p("case_number", ""),
            "poa_number": _p("poa_number", ""),
            "court_type": _p("court_type", ""),
            "indemnitor_name": _p("indemnitor_name", ""),
        }

        pdf_bytes = generate_appearance_bond(data)
        filename = generate_safe_filename(data)

        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except FileNotFoundError as e:
        return jsonify({"error": f"Template not found: {str(e)}. Ensure templates are in templates/ directory."}), 404
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/active-bonds/<booking_number>/release
# Mark defendant as released and trigger Phase 2 signing flow via BlueBubbles
# ─────────────────────────────────────────────────────────────────────────────

@bonds_bp.route("/active-bonds/<booking_number>/edit", methods=["PATCH"])
async def api_active_bond_edit(booking_number: str):
    """
    Full-field edit of an active bond record.
    Accepts any subset of editable fields and updates only those provided.
    """
    active_bonds = get_collection("active_bonds")
    data = await request.get_json(force=True) or {}
    EDITABLE = [
        "defendant_name", "county", "facility", "bond_amount", "premium",
        "insurance_company", "poa_number", "case_number", "check_in_required",
        "check_in_frequency_days", "indemnitor_name", "indemnitor_phone",
        "indemnitor_email", "agent_name", "notes", "court_date",
        "court_location", "charges",
    ]
    updates = {k: data[k] for k in EDITABLE if k in data}
    if not updates:
        return jsonify({"success": False, "error": "No editable fields provided"}), 400
    updates["updated_at"] = datetime.now(timezone.utc)
    try:
        result = await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": updates},
        )
        if result.matched_count == 0:
            return jsonify({"success": False, "error": "Bond not found"}), 404
        try:
            audit = get_collection("audit_events")
            await audit.insert_one({
                "entity_id": booking_number,
                "event_type": "bond_edited",
                "fields_changed": list(updates.keys()),
                "agent": data.get("agent", "Dashboard"),
                "timestamp": datetime.now(timezone.utc),
            })
        except Exception as _audit_err:
            logger.warning("[bonds] audit write failed for %s: %s", booking_number, _audit_err)
        return jsonify({"success": True, "booking_number": booking_number, "updated": list(updates.keys())})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bonds_bp.route("/active-bonds/<booking_number>/release", methods=["POST"])
async def api_active_bond_release(booking_number: str):
    """
    Mark a defendant as released from custody and trigger the post-release
    Phase 2 signing flow via BlueBubbles.

    Steps:
      1. Update bond case status to 'released' with released_at timestamp
      2. Send walk-out notification to indemnitor via BlueBubbles
      3. Generate Phase 2 SignNow signing link and send to indemnitor
      4. Log audit event

    Body (all optional — falls back to stored bond case values):
        {
            "released_at":       "2025-01-15T14:30:00Z",
            "release_facility":  "Lee County Jail",
            "next_court_date":   "2025-02-10",
            "court_location":    "Lee County Justice Center",
            "poa_number":        "OSI-12345",
            "agent_name":        "Brendan O'Neal",
            "agent_license":     "P123456",
            "surety_id":         "osi",
            "send_signing_link": true,
            "send_walkout_msg":  true
        }
    """
    active_bonds = get_collection("active_bonds")
    data = await request.get_json(force=True) or {}

    bond = await active_bonds.find_one({"booking_number": booking_number})
    if not bond:
        return jsonify({"success": False, "error": f"Bond case {booking_number} not found"}), 404

    now = datetime.now(timezone.utc)
    released_at = data.get("released_at", now.isoformat())
    release_facility = data.get("release_facility", bond.get("facility", ""))
    next_court_date = data.get("next_court_date", bond.get("next_court_date", "TBD"))
    court_location = data.get("court_location", bond.get("court_location", "Lee County Justice Center"))

    # 1. Update bond case status
    await active_bonds.update_one(
        {"booking_number": booking_number},
        {"$set": {
            "status": "released",
            "released_at": released_at,
            "release_facility": release_facility,
            "next_court_date": next_court_date,
            "court_location": court_location,
            "updated_at": now,
        }},
    )
    logger.info("[release] Bond %s marked released at %s", booking_number, released_at)

    defendant_name = bond.get("defendant_name", "your defendant")
    indemnitor_name = bond.get("indemnitor_name", "")
    indemnitor_phone = bond.get("indemnitor_phone", "")
    county = bond.get("county", "Lee")
    results = {
        "booking_number": booking_number,
        "released_at": released_at,
        "walkout_msg": None,
        "phase2_signing": None,
    }

    from dashboard.services.bb_client import send_message_universal

    # 2. Walk-out notification to indemnitor
    if data.get("send_walkout_msg", True) and indemnitor_phone:
        first_name = indemnitor_name.split()[0] if indemnitor_name else "there"
        walkout_msg = (
            f"Hi {first_name}! Great news — {defendant_name} has been released from "
            f"{county} County Jail \U0001f389\n\n"
            f"Remember: they MUST appear for ALL court dates. "
            f"Next date: {next_court_date} at {court_location}.\n\n"
            f"We'll send the remaining paperwork shortly. "
            f"— Shamrock Bail Bonds \U0001f340 (239) 332-2245"
        )
        walkout_result = await send_message_universal(indemnitor_phone, walkout_msg)
        results["walkout_msg"] = {
            "success": walkout_result.get("success"),
            "channel": walkout_result.get("channel"),
            "phone": indemnitor_phone,
        }
        logger.info("[release] Walk-out msg to %s: %s", indemnitor_phone, walkout_result.get("success"))

    # 3. Phase 2 SignNow packet + send link via BlueBubbles
    if data.get("send_signing_link", True):
        poa_number = data.get("poa_number", bond.get("poa_number", ""))
        agent_name = data.get("agent_name", os.getenv("DEFAULT_AGENT_NAME", "Brendan O'Neal"))
        agent_license = data.get("agent_license", os.getenv("DEFAULT_AGENT_LICENSE", ""))
        surety_id = data.get("surety_id", bond.get("insurance_company", "osi").lower())

        intake_doc = {
            "intake_id": booking_number,
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "defendant_first_name": bond.get("defendant_first_name", (defendant_name.split()[0] if defendant_name else "")),
            "defendant_last_name": bond.get("defendant_last_name", (defendant_name.split()[-1] if defendant_name else "")),
            "defendant_dob": bond.get("defendant_dob", ""),
            "defendant_address": bond.get("defendant_address", ""),
            "county": county,
            "facility": release_facility,
            "bond_amount": bond.get("bond_amount", 0),
            "premium": bond.get("premium", 0),
            "poa_number": poa_number,
            "agent_name": agent_name,
            "agent_license": agent_license,
            "surety_id": surety_id,
            "indemnitor_name": indemnitor_name,
            "indemnitor_phone": indemnitor_phone,
            "indemnitor_email": bond.get("indemnitor_email", ""),
            "next_court_date": next_court_date,
            "court_location": court_location,
            "phase": 2,
        }

        try:
            from dashboard.services.signnow_packet_service import SignNowPacketService
            svc = SignNowPacketService()
            packet_result = await svc.create_packet(
                intake_doc=intake_doc,
                packet_id=f"{booking_number}-phase2-release",
            )
            signing_link = packet_result.get("signing_link", "")
            results["phase2_signing"] = {
                "success": bool(signing_link),
                "signing_link": signing_link,
                "invite_id": packet_result.get("invite_id"),
                "group_id": packet_result.get("group_id"),
            }

            if signing_link and indemnitor_phone:
                first_name = indemnitor_name.split()[0] if indemnitor_name else "there"
                sign_msg = (
                    f"Hi {first_name}! Now that {defendant_name} has been released, "
                    f"please complete the remaining bond documents \U0001f4dd\n\n"
                    f"Tap to review and sign (~2 min):\n{signing_link}\n\n"
                    f"Questions? Call/text: (239) 332-2245 — Shamrock Bail Bonds \U0001f340"
                )
                sign_result = await send_message_universal(indemnitor_phone, sign_msg)
                results["phase2_signing"]["bb_sent"] = sign_result.get("success")
                results["phase2_signing"]["bb_channel"] = sign_result.get("channel")
                logger.info("[release] Phase 2 link sent to %s: %s", indemnitor_phone, sign_result.get("success"))

            await active_bonds.update_one(
                {"booking_number": booking_number},
                {"$set": {
                    "phase2_packet_sent": True,
                    "phase2_signing_link": signing_link,
                    "phase2_invite_id": packet_result.get("invite_id"),
                    "phase2_sent_at": now,
                    "updated_at": now,
                }},
            )
        except Exception as exc:
            logger.error("[release] Phase 2 SignNow error for %s: %s", booking_number, exc)
            results["phase2_signing"] = {"success": False, "error": str(exc)}

    # 4. Audit log
    try:
        audit_col = get_collection("audit_events")
        await audit_col.insert_one({
            "event_type": "defendant_released",
            "entity_id": booking_number,
            "entity_type": "bond_case",
            "defendant_name": defendant_name,
            "released_at": released_at,
            "walkout_sent": results["walkout_msg"],
            "phase2_sent": results["phase2_signing"],
            "timestamp": now,
        })
    except Exception as exc:
        logger.warning("[release] Audit log error: %s", exc)

    return jsonify({"success": True, **results})


@bonds_bp.route("/active-bonds/bulk-exonerate", methods=["POST"])
async def api_bulk_exonerate():
    """
    Batch exonerate multiple bonds in one request.

    Body:
        {
            "booking_numbers": ["BK001", "BK002", ...],
            "source": "manual_bulk",
            "note": "Batch discharge from court email",
            "notify_indemnitors": false
        }

    Returns per-bond results with idempotency:
    - already_exonerated bonds are reported but not double-processed
    - POA is only released if status == "assigned" (safety guard)
    - Reminders are cancelled for each bond
    - Audit event written per bond
    - SSE bond_exonerated fired per bond
    """
    # ── Auth guard: X-Admin-Token must match DASHBOARD_PIN ──────────────────
    _pin = os.getenv("DASHBOARD_PIN", "")
    if _pin:
        token = request.headers.get("X-Admin-Token", "").strip()
        if token != _pin:
            return jsonify({"success": False, "error": "Unauthorized — X-Admin-Token required"}), 401
    # ────────────────────────────────────────────────────────────────────────
    active_bonds = get_collection("active_bonds")
    poa_inventory = get_collection("poa_inventory")
    court_reminders = get_collection("court_reminders")
    audit_col = get_collection("audit_events")
    now = datetime.now(timezone.utc)

    try:
        data = await request.get_json(force=True) or {}
        booking_numbers = data.get("booking_numbers", [])
        source = data.get("source", "manual_bulk")
        note = data.get("note", "")
        notify = data.get("notify_indemnitors", False)

        if not booking_numbers or not isinstance(booking_numbers, list):
            return jsonify({"success": False, "error": "booking_numbers list required"}), 400
        if len(booking_numbers) > 50:
            return jsonify({"success": False, "error": "Maximum 50 bonds per bulk request"}), 400

        results = []
        exonerated_count = 0
        already_done_count = 0
        not_found_count = 0

        for booking_number in booking_numbers:
            booking_number = str(booking_number).strip()
            if not booking_number:
                continue

            try:
                bond = await active_bonds.find_one({"booking_number": booking_number})
                if not bond:
                    not_found_count += 1
                    results.append({"booking_number": booking_number, "status": "not_found"})
                    continue

                # Idempotency: skip already exonerated
                if bond.get("status") == "exonerated":
                    already_done_count += 1
                    results.append({
                        "booking_number": booking_number,
                        "status": "already_exonerated",
                        "exonerated_at": bond.get("exonerated_at"),
                    })
                    continue

                defendant_name = bond.get("defendant_name", "")

                # 1. Update bond status
                await active_bonds.update_one(
                    {"booking_number": booking_number},
                    {"$set": {
                        "status": "exonerated",
                        "tracking_active": False,
                        "check_in_required": False,
                        "exonerated_at": now.isoformat(),
                        "exoneration_source": source,
                        "exoneration_note": note,
                        "updated_at": now,
                    }}
                )

                # 2. Release POA — only if status == "assigned" (safety guard)
                poa_number = bond.get("poa_number", "")
                surety_id = bond.get("insurance_company", bond.get("surety_id", ""))
                poa_released = False
                if poa_number:
                    poa_doc = await poa_inventory.find_one(
                        {"poa_number": poa_number, "status": "assigned"}
                    )
                    if poa_doc:
                        await poa_inventory.update_one(
                            {"poa_number": poa_number, "status": "assigned"},
                            {"$set": {
                                "status": "exonerated",
                                "exonerated_at": now.isoformat(),
                                "exonerated_booking": booking_number,
                            }}
                        )
                        poa_released = True

                # 3. Cancel pending reminders
                cancel_result = await court_reminders.update_many(
                    {"booking_number": booking_number, "status": {"$in": ["scheduled", "pending"]}},
                    {"$set": {"status": "cancelled_exonerated", "cancelled_at": now.isoformat()}}
                )

                # 4. Audit log
                await audit_col.insert_one({
                    "event_type": "bond_exonerated",
                    "entity_id": booking_number,
                    "entity_type": "bond_case",
                    "defendant_name": defendant_name,
                    "source": source,
                    "note": note,
                    "bulk": True,
                    "poa_released": poa_released,
                    "reminders_cancelled": cancel_result.modified_count,
                    "exonerated_at": now,
                    "timestamp": now,
                })

                # 5. Notify indemnitor via BlueBubbles (optional)
                notify_result = None
                if notify and bond.get("indemnitor_phone"):
                    try:
                        from dashboard.services.bb_client import send_message_universal
                        first_name = (bond.get("indemnitor_name") or "").split()[0] or "there"
                        msg = (
                            f"Hi {first_name}! Great news — {defendant_name}'s bond obligation "
                            f"with Shamrock Bail Bonds has been officially discharged. "
                            f"No further check-ins are required. ☘️ Shamrock Bail Bonds (239) 332-2245"
                        )
                        notify_result = await send_message_universal(bond["indemnitor_phone"], msg)
                    except Exception as notify_err:
                        notify_result = {"success": False, "error": str(notify_err)}

                # 6. Fire SSE event
                try:
                    if hasattr(current_app, 'sse_queue') and current_app.sse_queue:
                        await current_app.sse_queue.put({
                            "event": "bond_exonerated",
                            "data": {
                                "booking_number": booking_number,
                                "defendant_name": defendant_name,
                                "source": source,
                                "exonerated_at": now.isoformat(),
                            },
                        })
                except Exception as _sse_err:
                    logger.debug("[bonds] SSE push failed for %s: %s", booking_number, _sse_err)

                exonerated_count += 1
                results.append({
                    "booking_number": booking_number,
                    "status": "exonerated",
                    "defendant_name": defendant_name,
                    "poa_released": poa_released,
                    "reminders_cancelled": cancel_result.modified_count,
                    "notify_result": notify_result,
                })

            except Exception as bond_err:
                logger.error("[bulk-exonerate] Error for %s: %s", booking_number, bond_err)
                results.append({
                    "booking_number": booking_number,
                    "status": "error",
                    "error": str(bond_err),
                })

        return jsonify({
            "success": True,
            "summary": {
                "requested": len(booking_numbers),
                "exonerated": exonerated_count,
                "already_exonerated": already_done_count,
                "not_found": not_found_count,
                "errors": len([r for r in results if r.get("status") == "error"]),
            },
            "results": results,
            "processed_at": now.isoformat(),
        })

    except Exception as e:
        logger.error("[bulk-exonerate] Fatal error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# PER-DEFENDANT COMPLIANCE SUMMARY
# GET /api/active-bonds/<booking_number>/compliance
# Returns check-in compliance, court appearance, and payment status
# ═══════════════════════════════════════════════════════════════════════════════
@bonds_bp.route("/active-bonds/<booking_number>/compliance", methods=["GET"])
async def api_bond_compliance(booking_number):
    """
    Captira-style per-defendant compliance summary.
    Returns:
      - check_in: last check-in date, streak, overdue status, compliance %
      - court: next court date, days until, missed court dates
      - payment: plan status, balance remaining, days overdue
      - overall_score: 0-100 composite compliance score
    """
    try:
        db_active = get_collection("active_bonds")
        db_checkins = get_collection("bond_checkins")
        db_plans = get_collection("payment_plans")
        db_payments = get_collection("payments")

        bond = await db_active.find_one({"booking_number": booking_number})
        if not bond:
            return jsonify({"success": False, "error": "Bond not found"}), 404

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # ── Check-In Compliance ─────────────────────────────────────────────
        freq_days = bond.get("check_in_frequency_days") or 30
        last_checkin_raw = bond.get("last_checkin") or bond.get("last_check_in")
        next_due_raw = bond.get("next_checkin_due") or bond.get("next_check_in_due")
        missed = bond.get("missed_check_ins", 0)
        ci_required = bond.get("check_in_required", False)

        # Count check-ins in last 90 days
        cutoff_90 = (now - timedelta(days=90)).isoformat()
        ci_count = await db_checkins.count_documents({
            "booking_number": booking_number,
            "checkin_at": {"$gte": cutoff_90},
        })
        # Expected check-ins in 90 days
        expected_ci = max(1, 90 // max(1, freq_days))
        ci_compliance_pct = min(100, round((ci_count / expected_ci) * 100)) if expected_ci > 0 else 100

        # Last check-in details
        last_ci_doc = await db_checkins.find_one(
            {"booking_number": booking_number},
            sort=[("checkin_at", -1)],
        )
        last_ci_str = None
        if last_ci_doc:
            lc = last_ci_doc.get("checkin_at")
            last_ci_str = lc.isoformat() if hasattr(lc, "isoformat") else str(lc)

        # Overdue check
        ci_overdue = False
        hours_overdue = 0
        if ci_required and next_due_raw:
            nd_str = next_due_raw.isoformat() if hasattr(next_due_raw, "isoformat") else str(next_due_raw)
            if nd_str < now_iso:
                ci_overdue = True
                try:
                    nd_dt = datetime.fromisoformat(nd_str.replace("Z", "+00:00"))
                    if nd_dt.tzinfo is None:
                        nd_dt = nd_dt.replace(tzinfo=timezone.utc)
                    hours_overdue = max(0, int((now - nd_dt).total_seconds() / 3600))
                except Exception:
                    hours_overdue = 0

        # ── Court Appearance ────────────────────────────────────────────────
        court_date_raw = bond.get("court_date")
        court_date_str = None
        days_until_court = None
        court_status = "unknown"
        if court_date_raw:
            court_date_str = str(court_date_raw)[:10]
            try:
                cd = datetime.fromisoformat(court_date_str)
                diff = (cd.date() - now.date()).days
                days_until_court = diff
                if diff < 0:
                    court_status = "past"
                elif diff == 0:
                    court_status = "today"
                elif diff <= 3:
                    court_status = "imminent"
                elif diff <= 14:
                    court_status = "upcoming"
                else:
                    court_status = "scheduled"
            except Exception:
                court_status = "scheduled"

        # ── Payment Compliance ──────────────────────────────────────────────
        plan = await db_plans.find_one({"booking_number": booking_number})
        payment_status = "no_plan"
        balance_remaining = 0.0
        payment_days_overdue = 0
        total_paid = 0.0
        plan_amount = 0.0
        if plan:
            plan_status = plan.get("status", "active")
            balance_remaining = plan.get("balance_remaining", 0.0)
            total_paid = plan.get("total_paid", 0.0)
            plan_amount = plan.get("total_amount", 0.0)
            next_due_plan = plan.get("next_due_date", "")
            if plan_status == "paid":
                payment_status = "paid"
            elif next_due_plan and next_due_plan < now_iso:
                payment_status = "overdue"
                try:
                    nd_dt = datetime.fromisoformat(next_due_plan.replace("Z", "+00:00"))
                    if nd_dt.tzinfo is None:
                        nd_dt = nd_dt.replace(tzinfo=timezone.utc)
                    payment_days_overdue = max(0, (now - nd_dt).days)
                except Exception:
                    payment_days_overdue = 0
            else:
                payment_status = "current"
        else:
            # Check if premium was paid (one-time)
            premium_paid = await db_payments.count_documents({
                "booking_number": booking_number,
                "status": "completed",
                "type": {"$in": ["premium", "payment_plan"]},
            })
            if premium_paid > 0:
                payment_status = "paid"
                total_paid = bond.get("premium", 0.0)

        # ── Composite Compliance Score (0-100) ──────────────────────────────
        # Weights: check-in 40%, court 30%, payment 30%
        ci_score = ci_compliance_pct * 0.40
        if not ci_required:
            ci_score = 40  # Full credit if check-in not required

        court_score = 30
        if court_status == "past" and days_until_court is not None and days_until_court < -1:
            court_score = 0  # Missed court date
        elif court_status in ("today", "imminent"):
            court_score = 20  # Needs attention

        pay_score = 30
        if payment_status == "paid":
            pay_score = 30
        elif payment_status == "current":
            pay_score = 25
        elif payment_status == "overdue":
            pay_score = max(0, 25 - payment_days_overdue)
        elif payment_status == "no_plan":
            pay_score = 15  # Unknown

        overall_score = min(100, round(ci_score + court_score + pay_score))

        # ── Compliance Level ────────────────────────────────────────────────
        if overall_score >= 80:
            compliance_level = "compliant"
        elif overall_score >= 50:
            compliance_level = "warning"
        else:
            compliance_level = "critical"

        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "defendant_name": bond.get("defendant_name", ""),
            "overall_score": overall_score,
            "compliance_level": compliance_level,
            "check_in": {
                "required": ci_required,
                "frequency_days": freq_days,
                "last_checkin": last_ci_str,
                "next_due": (next_due_raw.isoformat() if hasattr(next_due_raw, "isoformat") else str(next_due_raw)) if next_due_raw else None,
                "overdue": ci_overdue,
                "hours_overdue": hours_overdue,
                "missed_count": missed,
                "checkins_90d": ci_count,
                "compliance_pct": ci_compliance_pct,
            },
            "court": {
                "court_date": court_date_str,
                "court_location": bond.get("court_location", ""),
                "days_until": days_until_court,
                "status": court_status,
            },
            "payment": {
                "status": payment_status,
                "plan_amount": round(plan_amount, 2),
                "total_paid": round(total_paid, 2),
                "balance_remaining": round(balance_remaining, 2),
                "days_overdue": payment_days_overdue,
            },
            "evaluated_at": now_iso,
        })
    except Exception as exc:
        logger.exception("active-bonds/%s/compliance error: %s", booking_number, exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  Bond Renewal / Re-Write
# ══════════════════════════════════════════════════════════════════════════════

@bonds_bp.route("/active-bonds/<booking_number>/renew", methods=["POST"])
async def api_renew_bond(booking_number: str):
    """
    Re-write / renew an active bond.

    Handles:
      - New court date (continuance)
      - Bond amount reduction / increase
      - Charge amendment
      - New POA assignment
      - Cancels old court reminders, schedules new ones

    Body JSON:
      new_court_date     (str, ISO)   — required
      new_court_location (str)        — optional, defaults to existing
      new_bond_amount    (float)      — optional, defaults to existing
      new_charges        (str)        — optional, defaults to existing
      new_poa_number     (str)        — optional, assign new POA
      renewal_reason     (str)        — required: continuance|reduction|amendment|other
      notes              (str)        — optional agent notes
    """
    try:
        data = await request.get_json() or {}
        new_court_date = data.get("new_court_date")
        renewal_reason = data.get("renewal_reason", "continuance")

        if not new_court_date:
            return jsonify({"success": False, "error": "new_court_date is required"}), 400

        db = get_db()
        col = db["active_bonds"]
        bond = await col.find_one({"booking_number": booking_number})
        if not bond:
            return jsonify({"success": False, "error": "Bond not found"}), 404

        now_iso = datetime.now(timezone.utc).isoformat()

        # Build update fields
        update_fields = {
            "updated_at": now_iso,
            "last_renewed_at": now_iso,
            "renewal_count": bond.get("renewal_count", 0) + 1,
            "renewal_reason": renewal_reason,
            "previous_court_date": bond.get("court_date"),
        }

        if new_court_date:
            update_fields["court_date"] = new_court_date
        if data.get("new_court_location"):
            update_fields["court_location"] = data["new_court_location"]
        if data.get("new_bond_amount") is not None:
            old_amount = bond.get("bond_amount", 0)
            update_fields["bond_amount"] = float(data["new_bond_amount"])
            update_fields["previous_bond_amount"] = old_amount
        if data.get("new_charges"):
            update_fields["charges"] = data["new_charges"]
            update_fields["previous_charges"] = bond.get("charges")
        if data.get("notes"):
            update_fields["renewal_notes"] = data["notes"]

        # Handle POA re-assignment
        new_poa = data.get("new_poa_number")
        old_poa = bond.get("poa_number")
        if new_poa and new_poa != old_poa:
            # Release old POA back to available
            if old_poa:
                await db["poa_inventory"].update_one(
                    {"poa_number": old_poa},
                    {"$set": {"status": "available", "released_at": now_iso,
                              "released_reason": "bond_renewal"}}
                )
            # Mark new POA as used
            await db["poa_inventory"].update_one(
                {"poa_number": new_poa},
                {"$set": {"status": "used", "used_at": now_iso,
                          "booking_number": booking_number}}
            )
            update_fields["poa_number"] = new_poa
            update_fields["previous_poa_number"] = old_poa

        # Append to renewal history
        renewal_record = {
            "renewed_at": now_iso,
            "reason": renewal_reason,
            "old_court_date": bond.get("court_date"),
            "new_court_date": new_court_date,
            "old_bond_amount": bond.get("bond_amount"),
            "new_bond_amount": data.get("new_bond_amount", bond.get("bond_amount")),
            "agent": data.get("agent", "system"),
            "notes": data.get("notes", ""),
        }
        await col.update_one(
            {"booking_number": booking_number},
            {
                "$set": update_fields,
                "$push": {"renewal_history": renewal_record},
            }
        )

        # Cancel old court reminders and schedule new ones via BlueBubbles (iMessage)
        try:
            from dashboard.services.court_reminder_service import CourtReminderService
            svc = CourtReminderService(db)

            # Cancel all pending reminders for this booking
            cancelled = await svc.cancel_reminders(booking_number)
            logger.info("[BondRenewal] Cancelled %d old reminders for %s", cancelled, booking_number)

            defendant_name = bond.get("defendant_name", "")
            # Prefer indemnitor_phone on the bond doc; fall back to phone field
            phone = bond.get("indemnitor_phone") or bond.get("phone", "")
            court_location = update_fields.get("court_location", bond.get("court_location", ""))
            case_number = bond.get("case_number", "")

            # Collect all indemnitor phones from the indemnitors collection
            indemnitor_phones = []
            async for ind in db["indemnitors"].find({"booking_number": booking_number}, {"phone": 1}):
                if ind.get("phone"):
                    indemnitor_phones.append(ind["phone"])

            if phone and defendant_name:
                # schedule_reminders persists to court_reminders collection;
                # CourtReminderService processor delivers via BB iMessage
                sched_result = await svc.schedule_reminders(
                    booking_number=booking_number,
                    defendant_name=defendant_name,
                    phone=phone,
                    court_date_str=new_court_date,
                    court_location=court_location,
                    case_number=case_number,
                    indemnitor_phones=indemnitor_phones,
                )
                reminders_scheduled = sched_result.get("scheduled", 0) if isinstance(sched_result, dict) else 0
            else:
                reminders_scheduled = 0
        except Exception as rem_exc:
            logger.warning("[BondRenewal] BB reminder reschedule failed for %s: %s",
                           booking_number, rem_exc)
            reminders_scheduled = 0

        # Fire SSE event
        try:
            from dashboard.api.events import emit_event
            await emit_event("bond_renewed", {
                "booking_number": booking_number,
                "renewal_reason": renewal_reason,
                "new_court_date": new_court_date,
            })
        except Exception:
            pass

        logger.info("[BondRenewal] %s renewed (%s) — %d new reminders",
                    booking_number, renewal_reason, reminders_scheduled)
        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "renewal_reason": renewal_reason,
            "new_court_date": new_court_date,
            "reminders_scheduled": reminders_scheduled,
            "renewal_count": update_fields["renewal_count"],
        })
    except Exception as exc:
        logger.exception("active-bonds/%s/renew error: %s", booking_number, exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/active-bonds/<booking_number>/renewal-history
# ─────────────────────────────────────────────────────────────────────────────
@bonds_bp.route("/active-bonds/<booking_number>/renewal-history", methods=["GET"])
async def api_bond_renewal_history(booking_number: str):
    """Return the renewal_history array for a bond."""
    try:
        active_bonds = get_collection("active_bonds")
        bond = await active_bonds.find_one(
            {"booking_number": booking_number},
            {"_id": 0, "renewal_history": 1, "renewal_count": 1},
        )
        if not bond:
            return jsonify({"success": False, "error": "Bond not found"}), 404

        history = bond.get("renewal_history", [])
        # Sort newest first
        history = sorted(history, key=lambda r: r.get("renewed_at", ""), reverse=True)

        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "renewal_count": bond.get("renewal_count", len(history)),
            "renewal_history": history,
        })
    except Exception as exc:
        logger.exception("renewal-history error for %s: %s", booking_number, exc)
        return jsonify({"success": False, "error": str(exc)}), 500
