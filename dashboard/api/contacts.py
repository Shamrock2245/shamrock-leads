<<<<<<< HEAD
"""Contacts API Blueprint (Phase 2) — contact discovery"""
=======
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
from quart import Blueprint, jsonify, request
from datetime import datetime, timezone
from dashboard.extensions import get_collection
from dashboard.services.contact_discovery import ContactDiscoveryService

contacts_bp = Blueprint('contacts', __name__)
discovery_service = ContactDiscoveryService()

<<<<<<< HEAD

@contacts_bp.route('/contacts/discover', methods=['POST'])
async def discover_contacts():
    """Trigger OSINT discovery for a defendant."""
=======
@contacts_bp.route('/contacts/discover', methods=['POST'])
async def discover_contacts():
    """Trigger discovery for a defendant."""
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
    try:
        data = await request.get_json()
        booking_number = data.get("booking_number")
        full_name = data.get("full_name")
        county = data.get("county")
        age = data.get("age")
        address = data.get("address")
<<<<<<< HEAD

        if not booking_number or not full_name:
            return jsonify({"error": "Missing required fields (booking_number, full_name)"}), 400

=======
        
        if not booking_number or not full_name:
            return jsonify({"error": "Missing required fields (booking_number, full_name)"}), 400
            
        # Run discovery service
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
        result = await discovery_service.discover_contacts(
            booking_number=booking_number,
            full_name=full_name,
            county=county,
            age=age,
<<<<<<< HEAD
            address=address,
        )
=======
            address=address
        )
        
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

<<<<<<< HEAD

=======
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
@contacts_bp.route('/contacts/<booking_number>', methods=['GET'])
async def get_contacts(booking_number):
    """Retrieve discovered contacts for a case."""
    try:
        contacts_col = get_collection("contacts")
<<<<<<< HEAD
        doc = await contacts_col.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )
        if not doc:
            return jsonify({"error": "No contacts found for this booking number"}), 404
=======
        doc = await contacts_col.find_one({"booking_number": booking_number}, {"_id": 0})
        
        if not doc:
            return jsonify({"error": "No contacts found for this booking number"}), 404
            
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
        return jsonify(doc)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
