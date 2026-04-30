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


# ═══════════════════════════════════════════════════════════════════════════════
# WRITE BOND — Export to SignNow / GAS
# ═══════════════════════════════════════════════════════════════════════════════

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
    print(f"\n{'═' * 60}")
    print(f"📋 WRITE BOND — {defendant.get('full_name', 'Unknown')}")
    print(f"   Insurance: {insurer.upper()}")
    print(f"   Bond: ${bond.get('amount', 0):,.2f}")
    print(f"   Premium: ${bond.get('premium', 0):,.2f}")
    print(f"   County: {booking.get('county', 'Unknown')}")
    print(f"   Booking #: {booking.get('booking_number', 'N/A')}")
    print(f"   Indemnitors: {len(indemnitors_payload)}")
    print(f"{'═' * 60}")
    print(f"   GAS Payload: {json_lib.dumps(gas_payload, indent=2)[:600]}...")
    print(f"{'═' * 60}\n")

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
    """List all active bonds with risk scores and check-in status."""
    active_bonds = get_collection("active_bonds")
    try:
        cursor = active_bonds.find({}, {"_id": 0}).sort("created_at", -1).limit(100)
        bonds = await cursor.to_list(length=100)
        for b in bonds:
            if hasattr(b.get("created_at"), "isoformat"):
                b["created_at"] = b["created_at"].isoformat()
            if hasattr(b.get("last_checkin"), "isoformat"):
                b["last_checkin"] = b["last_checkin"].isoformat()
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
    """Update bond status (active, exonerated, forfeited, surrendered)."""
    active_bonds = get_collection("active_bonds")
    data = await request.get_json(force=True) or {}
    new_status = data.get("status", "")
    valid_statuses = {"active", "exonerated", "forfeited", "surrendered", "reinstated"}
    if new_status not in valid_statuses:
        return jsonify({"success": False, "error": f"Invalid status. Must be one of: {valid_statuses}"}), 400
    try:
        result = await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {"status": new_status, "updated_at": datetime.now(timezone.utc)}},
        )
        if result.matched_count == 0:
            return jsonify({"success": False, "error": "Bond not found"}), 404
        return jsonify({"success": True, "booking_number": booking_number, "status": new_status})
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
    Generate or retrieve the appearance bond PDF for a given booking number.
    GET  ?booking_number=XXX  — return existing PDF URL
    POST {booking_number, ...} — trigger PDF generation
    """
    if request.method == "GET":
        booking_number = request.args.get("booking_number", "")
        if not booking_number:
            return jsonify({"success": False, "error": "booking_number required"}), 400
        # TODO: look up generated PDF in Google Drive / S3
        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "pdf_url": None,
            "note": "PDF generation via GAS/SignNow — check Drive for completed documents.",
        })
    else:
        data = await request.get_json(force=True) or {}
        booking_number = data.get("booking_number", "")
        if not booking_number:
            return jsonify({"success": False, "error": "booking_number required"}), 400
        # Forward to GAS for PDF generation
        gas_url = os.getenv("GAS_WEB_APP_URL", "")
        if gas_url:
            try:
                import httpx
                payload = {"action": "generateAppearanceBond", **data}
                async with httpx.AsyncClient() as client:
                    resp = await client.post(gas_url, json=payload, timeout=30)
                    if resp.status_code < 400:
                        return jsonify({"success": True, "gas_response": resp.json()})
                    return jsonify({"success": False, "error": resp.text[:200]}), 502
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 502
        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "note": "GAS_WEB_APP_URL not configured — set it in .env to enable PDF generation.",
        })


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/active-bonds/<booking_number>/release
# Mark defendant as released and trigger Phase 2 signing flow via BlueBubbles
# ─────────────────────────────────────────────────────────────────────────────
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
            "agent_name":        "Brendan",
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
        dashboard_url = os.getenv("DASHBOARD_PUBLIC_URL", "https://leads.shamrockbailbonds.biz")
        geo_url = f"{dashboard_url}/g/{booking_number}"
        walkout_result = await send_message_universal(indemnitor_phone, walkout_msg, geo_url=geo_url)
        results["walkout_msg"] = {
            "success": walkout_result.get("success"),
            "channel": walkout_result.get("channel"),
            "phone": indemnitor_phone,
        }
        logger.info("[release] Walk-out msg to %s: %s", indemnitor_phone, walkout_result.get("success"))

    # 3. Phase 2 SignNow packet + send link via BlueBubbles
    if data.get("send_signing_link", True):
        poa_number = data.get("poa_number", bond.get("poa_number", ""))
        agent_name = data.get("agent_name", os.getenv("DEFAULT_AGENT_NAME", "Brendan"))
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
