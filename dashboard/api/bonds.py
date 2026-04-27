"""
ShamrockLeads — Bonds API Blueprint
Endpoints: /api/write-bond, /api/active-bonds (CRUD), /api/appearance-bond-pdf
"""

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
    Accept defendant data + insurance company selection,
    format a GAS-compatible SignNow payload, and forward it.
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
            "fullName": defendant.get("full_name", ""),
            "firstName": defendant.get("first_name", ""),
            "lastName": defendant.get("last_name", ""),
            "middleName": defendant.get("middle_name", ""),
            "dob": defendant.get("dob", ""),
            "address": defendant.get("address", ""),
            "city": defendant.get("city", ""),
            "state": defendant.get("state", "FL"),
            "zip": defendant.get("zip", ""),
            "sex": defendant.get("sex", ""),
            "race": defendant.get("race", ""),
            "height": defendant.get("height", ""),
            "weight": defendant.get("weight", ""),
        },
        "booking": {
            "bookingNumber": booking.get("booking_number", ""),
            "county": booking.get("county", ""),
            "facility": booking.get("facility", ""),
            "agency": booking.get("agency", ""),
            "arrestDate": booking.get("arrest_date", ""),
            "bookingDate": booking.get("booking_date", ""),
        },
        "bond": {
            "totalAmount": bond.get("amount", 0),
            "premium": bond.get("premium", 0),
            "type": bond.get("type", ""),
            "paid": bond.get("paid", "NO"),
        },
        "charges": charges,
        "court": {
            "date": court.get("date", ""),
            "time": court.get("time", ""),
            "type": court.get("type", ""),
            "location": court.get("location", ""),
            "caseNumber": court.get("case_number", ""),
        },
    }

    # Log the formatted payload
    print(f"\n{'═' * 60}")
    print(f"📋 WRITE BOND — {defendant.get('full_name', 'Unknown')}")
    print(f"   Insurance: {insurer.upper()}")
    print(f"   Bond: ${bond.get('amount', 0):,.2f}")
    print(f"   Premium: ${bond.get('premium', 0):,.2f}")
    print(f"   County: {booking.get('county', 'Unknown')}")
    print(f"   Booking #: {booking.get('booking_number', 'N/A')}")
    print(f"{'═' * 60}")
    print(f"   GAS Payload: {json_lib.dumps(gas_payload, indent=2)[:500]}...")
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
        status_filter = request.args.get("status", "").strip()
        query = {}
        if status_filter:
            query["status"] = status_filter
        else:
            query["status"] = {"$in": ["active", "monitoring", "alert"]}

        cursor = active_bonds.find(query, {"_id": 0}).sort("bond_date", -1).limit(200)
        bonds = []
        now = datetime.now(timezone.utc)

        async for b in cursor:
            for k, v in b.items():
                if isinstance(v, datetime):
                    b[k] = v.isoformat()
            # Compute live risk score
            b["risk_score"] = compute_risk_score(b)
            # Check if overdue
            next_due_str = b.get("next_check_in_due", "")
            if next_due_str:
                try:
                    from dateutil import parser as dateparser
                    next_due = dateparser.parse(next_due_str)
                    if next_due and next_due.tzinfo is None:
                        next_due = next_due.replace(tzinfo=timezone.utc)
                    b["check_in_overdue"] = next_due < now if next_due else False
                    b["hours_overdue"] = round((now - next_due).total_seconds() / 3600, 1) if b["check_in_overdue"] else 0
                except Exception:
                    b["check_in_overdue"] = False
                    b["hours_overdue"] = 0
            else:
                b["check_in_overdue"] = False
                b["hours_overdue"] = 0
            bonds.append(b)

        total = len(bonds)
        alerts = sum(1 for b in bonds if b.get("check_in_overdue") or b.get("risk_score", 0) >= 75)
        high_risk = sum(1 for b in bonds if b.get("risk_score", 0) >= 75)

        return jsonify({
            "bonds": bonds,
            "total": total,
            "alerts": alerts,
            "high_risk": high_risk,
            "updated_at": now.isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bonds_bp.route("/active-bonds", methods=["POST"])
async def api_active_bonds_create():
    """Register a new active bond after Write Bond is clicked."""
    active_bonds = get_collection("active_bonds")
    try:
        data = await request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No payload"}), 400

        booking_number = data.get("booking_number", "")
        if not booking_number:
            return jsonify({"success": False, "error": "booking_number required"}), 400

        now = datetime.now(timezone.utc)
        check_in_hours = int(data.get("check_in_interval_hours", 24))

        doc = {
            "booking_number": booking_number,
            "defendant_name": data.get("defendant_name", ""),
            "county": data.get("county", ""),
            "bond_amount": float(data.get("bond_amount", 0) or 0),
            "premium": float(data.get("premium", 0) or 0),
            "surety": data.get("surety", "osi").upper(),
            "charges": data.get("charges", []),
            "charges_raw": data.get("charges_raw", ""),
            "bond_date": data.get("bond_date", now.isoformat()),
            "status": "active",
            "risk_score": 50,
            "check_in_required": True,
            "check_in_interval_hours": check_in_hours,
            "last_check_in": None,
            "next_check_in_due": (now + timedelta(hours=check_in_hours)).isoformat(),
            "missed_check_ins": 0,
            "out_of_area_count": 0,
            "geolocation_enabled": True,
            "location_history": [],
            "alerts": [],
            "defendant_info": data.get("defendant_info", {}),
            "created_at": now,
            "updated_at": now,
        }
        doc["risk_score"] = compute_risk_score(doc)

        result = await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": doc},
            upsert=True,
        )

        return jsonify({
            "success": True,
            "message": f"Active bond registered for {doc['defendant_name']}",
            "booking_number": booking_number,
            "risk_score": doc["risk_score"],
            "upserted": result.upserted_id is not None,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bonds_bp.route("/active-bonds/<booking_number>/check-in", methods=["POST"])
async def api_active_bond_check_in(booking_number):
    """Record a geolocation check-in for an active bond."""
    active_bonds = get_collection("active_bonds")
    try:
        data = (await request.get_json()) or {}
        now = datetime.now(timezone.utc)

        bond = await active_bonds.find_one({"booking_number": booking_number})
        if not bond:
            return jsonify({"success": False, "error": "Bond not found"}), 404

        lat = data.get("lat")
        lng = data.get("lng")
        accuracy = data.get("accuracy", 0)
        source = data.get("source", "manual")

        location_entry = {
            "timestamp": now.isoformat(),
            "lat": lat,
            "lng": lng,
            "accuracy": accuracy,
            "source": source,
            "county": data.get("county", ""),
            "address": data.get("address", ""),
        }

        check_in_hours = bond.get("check_in_interval_hours", 24)
        next_due = (now + timedelta(hours=check_in_hours)).isoformat()

        home_county = bond.get("county", "").lower()
        checkin_county = data.get("county", "").lower()
        out_of_area = bool(checkin_county and home_county and checkin_county != home_county)

        update = {
            "$set": {
                "last_check_in": now.isoformat(),
                "next_check_in_due": next_due,
                "updated_at": now,
            },
            "$push": {
                "location_history": {
                    "$each": [location_entry],
                    "$slice": -100,
                }
            },
        }
        if out_of_area:
            update["$inc"] = {"out_of_area_count": 1}
            alert = {
                "type": "out_of_area",
                "message": f"Check-in from {checkin_county.title()} (home: {home_county.title()})",
                "timestamp": now.isoformat(),
                "location": location_entry,
            }
            update["$push"]["alerts"] = alert

        await active_bonds.update_one({"booking_number": booking_number}, update)

        updated = await active_bonds.find_one({"booking_number": booking_number})
        new_risk = compute_risk_score(updated) if updated else 50
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {"risk_score": new_risk}},
        )

        return jsonify({
            "success": True,
            "message": "Check-in recorded",
            "booking_number": booking_number,
            "next_check_in_due": next_due,
            "risk_score": new_risk,
            "out_of_area": out_of_area,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bonds_bp.route("/active-bonds/<booking_number>/alert", methods=["POST"])
async def api_active_bond_alert(booking_number):
    """Manually add an alert to an active bond."""
    active_bonds = get_collection("active_bonds")
    try:
        data = (await request.get_json()) or {}
        now = datetime.now(timezone.utc)
        alert = {
            "type": data.get("type", "manual"),
            "message": data.get("message", "Manual alert"),
            "severity": data.get("severity", "medium"),
            "timestamp": now.isoformat(),
        }
        result = await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$push": {"alerts": alert}, "$set": {"status": "alert", "updated_at": now}},
        )
        if result.matched_count == 0:
            return jsonify({"success": False, "error": "Bond not found"}), 404
        return jsonify({"success": True, "alert": alert})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bonds_bp.route("/active-bonds/<booking_number>/status", methods=["PATCH"])
async def api_active_bond_status(booking_number):
    """Update bond status (active | monitoring | alert | exonerated | forfeited)."""
    active_bonds = get_collection("active_bonds")
    try:
        data = (await request.get_json()) or {}
        new_status = data.get("status", "active")
        valid_statuses = ["active", "monitoring", "alert", "exonerated", "forfeited", "surrendered"]
        if new_status not in valid_statuses:
            return jsonify({"success": False, "error": f"Invalid status. Use: {valid_statuses}"}), 400
        now = datetime.now(timezone.utc)
        result = await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {"status": new_status, "updated_at": now}},
        )
        if result.matched_count == 0:
            return jsonify({"success": False, "error": "Bond not found"}), 404
        return jsonify({"success": True, "status": new_status})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bonds_bp.route("/active-bonds/missed-checkins", methods=["POST"])
async def api_active_bonds_process_missed():
    """Cron-style endpoint: scan for overdue check-ins and increment missed count."""
    active_bonds = get_collection("active_bonds")
    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        cursor = active_bonds.find({
            "status": {"$in": ["active", "monitoring"]},
            "check_in_required": True,
            "next_check_in_due": {"$lt": now_iso},
        })
        updated = 0
        async for bond in cursor:
            booking_number = bond.get("booking_number", "")
            missed = bond.get("missed_check_ins", 0) + 1
            check_in_hours = bond.get("check_in_interval_hours", 24)
            next_due = (now + timedelta(hours=check_in_hours)).isoformat()
            alert = {
                "type": "missed_check_in",
                "message": f"Missed check-in #{missed} for {bond.get('defendant_name', 'Unknown')}",
                "severity": "high" if missed >= 2 else "medium",
                "timestamp": now.isoformat(),
            }
            new_status = "alert" if missed >= 2 else bond.get("status", "active")
            new_risk = compute_risk_score({**bond, "missed_check_ins": missed})
            await active_bonds.update_one(
                {"booking_number": booking_number},
                {
                    "$set": {
                        "missed_check_ins": missed,
                        "next_check_in_due": next_due,
                        "status": new_status,
                        "risk_score": new_risk,
                        "updated_at": now,
                    },
                    "$push": {"alerts": alert},
                },
            )
            updated += 1

        return jsonify({"success": True, "processed": updated, "timestamp": now_iso})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# APPEARANCE BOND PDF
# ═══════════════════════════════════════════════════════════════════════════════

@bonds_bp.route("/appearance-bond-pdf", methods=["GET", "POST"])
async def api_appearance_bond_pdf():
    """Generate a pre-populated Appearance Bond PDF using official templates."""
    try:
        try:
            from bond_pdf_service import generate_appearance_bond, generate_safe_filename
        except ImportError:
            from dashboard.bond_pdf_service import generate_appearance_bond, generate_safe_filename

        if request.method == "POST":
            d = (await request.get_json(force=True)) or {}

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
            content_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except FileNotFoundError as e:
        return jsonify({"error": f"Template not found: {str(e)}. Ensure templates are in templates/ directory."}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500
