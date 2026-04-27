from quart import Blueprint, jsonify, request
from datetime import datetime, timezone
from dashboard.extensions import get_collection

tracking_bp = Blueprint('tracking', __name__)

@tracking_bp.route('/tracking/map-data', methods=['GET'])
async def get_map_data():
    """Return all active bonds with latest location and risk score for map rendering."""
    try:
        active_bonds = get_collection("active_bonds")
        
        # Get all active bonds
        cursor = active_bonds.find({"status": "active"}, {"_id": 0})
        bonds = await cursor.to_list(length=1000)
        
        defendants = []
        summary = {
            "total_active": len(bonds),
            "overdue": 0,
            "high_risk": 0,
            "out_of_area": 0
        }
        
        now = datetime.now(timezone.utc)
        
        for bond in bonds:
            # Check overdue status
            next_due_str = bond.get("next_check_in_due")
            is_overdue = False
            if next_due_str:
                try:
                    next_due = datetime.fromisoformat(next_due_str.replace('Z', '+00:00'))
                    if next_due.tzinfo is None:
                        next_due = next_due.replace(tzinfo=timezone.utc)
                    is_overdue = next_due < now
                except ValueError:
                    pass
            
            if is_overdue:
                summary["overdue"] += 1
                
            risk_score = bond.get("risk_score", 0)
            if risk_score >= 75:
                summary["high_risk"] += 1
                
            out_of_area_count = bond.get("out_of_area_count", 0)
            if out_of_area_count > 0:
                summary["out_of_area"] += 1
                
            # Get latest location from history if available
            location_history = bond.get("location_history", [])
            latest_location = location_history[-1] if location_history else None
            
            defendants.append({
                "booking_number": bond.get("booking_number"),
                "defendant_name": bond.get("defendant_name"),
                "county": bond.get("county", "Unknown"),
                "bond_amount": bond.get("bond_amount", 0),
                "status": bond.get("status"),
                "risk_score": risk_score,
                "last_check_in": bond.get("last_check_in"),
                "next_check_in_due": next_due_str,
                "check_in_overdue": is_overdue,
                "latest_location": latest_location,
                "missed_check_ins": bond.get("missed_check_ins", 0),
                "out_of_area_count": out_of_area_count,
                "alerts_count": len(bond.get("alerts", []))
            })
            
        return jsonify({
            "defendants": defendants,
            "summary": summary
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@tracking_bp.route('/tracking/<booking_number>/history', methods=['GET'])
async def get_tracking_history(booking_number):
    """Full location history + timeline for a specific defendant."""
    try:
        active_bonds = get_collection("active_bonds")
        bond = await active_bonds.find_one({"booking_number": booking_number}, {"_id": 0})
        
        if not bond:
            return jsonify({"error": "Bond not found"}), 404
            
        return jsonify({
            "defendant_name": bond.get("defendant_name"),
            "location_history": bond.get("location_history", []),
            "check_in_history": bond.get("check_in_history", []),
            "alerts": bond.get("alerts", []),
            "court_dates": bond.get("court_dates", [])
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@tracking_bp.route('/tracking/<booking_number>/geofence', methods=['POST'])
async def set_geofence(booking_number):
    """Set a geofence radius (miles) around home address."""
    try:
        data = await request.get_json()
        radius = data.get("radius_miles")
        lat = data.get("home_lat")
        lng = data.get("home_lng")
        
        if radius is None or lat is None or lng is None:
            return jsonify({"error": "Missing required fields"}), 400
            
        active_bonds = get_collection("active_bonds")
        
        geofence = {
            "radius_miles": float(radius),
            "center": {"lat": float(lat), "lng": float(lng)},
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        result = await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {"geofence": geofence}}
        )
        
        if result.matched_count == 0:
            return jsonify({"error": "Bond not found"}), 404
            
        return jsonify({"success": True, "geofence": geofence})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
