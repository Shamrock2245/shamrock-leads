"""Tracking API Blueprint (Phase 2) — map data, history, geofence"""
from __future__ import annotations
from quart import Blueprint, jsonify, request
from datetime import datetime, timezone
from dashboard.extensions import get_collection
from dashboard.services.risk_engine import compute_risk_score

tracking_bp = Blueprint('tracking', __name__)


@tracking_bp.route('/tracking/map-data')
async def tracking_map_data():
    """Return all active bonds with latest location for map rendering."""
    active_bonds = get_collection("active_bonds")
    try:
        cursor = active_bonds.find(
            {"status": {"$in": ["active", "monitoring", "alert"]}},
            {"_id": 0}
        ).sort("bond_date", -1).limit(500)

        defendants = []
        total_active = 0
        overdue = 0
        high_risk = 0
        out_of_area = 0
        now = datetime.now(timezone.utc)

        async for bond in cursor:
            total_active += 1
            for k, v in bond.items():
                if isinstance(v, datetime):
                    bond[k] = v.isoformat()

            # Latest location
            loc_history = bond.get("location_history", [])
            latest_loc = loc_history[-1] if loc_history else None

            # Check-in overdue?
            next_due_str = bond.get("next_check_in_due", "")
            is_overdue = False
            if next_due_str:
                try:
                    from dateutil import parser as dateparser
                    next_due = dateparser.parse(next_due_str)
                    if next_due and next_due.tzinfo is None:
                        next_due = next_due.replace(tzinfo=timezone.utc)
                    is_overdue = next_due < now if next_due else False
                except Exception:
                    pass

            risk = bond.get("risk_score", compute_risk_score(bond))
            if is_overdue:
                overdue += 1
            if risk >= 75:
                high_risk += 1
            if bond.get("out_of_area_count", 0) > 0:
                out_of_area += 1

            defendants.append({
                "booking_number": bond.get("booking_number"),
                "defendant_name": bond.get("defendant_name"),
                "county": bond.get("county"),
                "bond_amount": bond.get("bond_amount"),
                "status": bond.get("status"),
                "risk_score": risk,
                "last_check_in": bond.get("last_check_in"),
                "next_check_in_due": bond.get("next_check_in_due"),
                "check_in_overdue": is_overdue,
                "latest_location": latest_loc,
                "missed_check_ins": bond.get("missed_check_ins", 0),
                "out_of_area_count": bond.get("out_of_area_count", 0),
                "alerts_count": len(bond.get("alerts", [])),
            })

        return jsonify({
            "defendants": defendants,
            "summary": {
                "total_active": total_active,
                "overdue": overdue,
                "high_risk": high_risk,
                "out_of_area": out_of_area,
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@tracking_bp.route('/tracking/<booking_number>/history')
async def tracking_history(booking_number):
    """Full location history + timeline for a specific defendant."""
    active_bonds = get_collection("active_bonds")
    try:
        bond = await active_bonds.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )
        if not bond:
            return jsonify({"error": "Bond not found"}), 404

        for k, v in bond.items():
            if isinstance(v, datetime):
                bond[k] = v.isoformat()

        court_reminders = get_collection("court_reminders")
        court_dates = []
        async for rem in court_reminders.find(
            {"booking_number": booking_number}, {"_id": 0}
        ).sort("send_at", 1):
            court_dates.append(rem)

        return jsonify({
            "defendant_name": bond.get("defendant_name"),
            "location_history": bond.get("location_history", []),
            "check_in_history": bond.get("location_history", []),
            "alerts": bond.get("alerts", []),
            "court_dates": court_dates,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@tracking_bp.route('/tracking/<booking_number>/geofence', methods=['POST'])
async def tracking_set_geofence(booking_number):
    """Set a geofence radius (miles) around home address."""
    active_bonds = get_collection("active_bonds")
    try:
        data = await request.get_json()
        radius_miles = float(data.get("radius_miles", 50))
        center_lat = data.get("center_lat")
        center_lng = data.get("center_lng")

        result = await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "geofence": {
                    "radius_miles": radius_miles,
                    "center_lat": center_lat,
                    "center_lng": center_lng,
                    "set_at": datetime.now(timezone.utc).isoformat(),
                },
                "updated_at": datetime.now(timezone.utc),
            }}
        )
        if result.matched_count == 0:
            return jsonify({"error": "Bond not found"}), 404

        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "radius_miles": radius_miles,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
