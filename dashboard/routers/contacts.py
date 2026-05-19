"""Contacts API Blueprint (Phase 2) — contact discovery"""
from dashboard.extensions import get_collection
from dashboard.services.contact_discovery import ContactDiscoveryService

contacts_bp = APIRouter(prefix="/api", tags=["contacts"])
@contacts_bp.route("/contacts/discover", methods=["POST"])
async def discover_contacts():
    """Run OSINT contact discovery for a defendant."""
    try:
        data = await request.get_json()
        booking_number = data.get("booking_number")
        full_name = data.get("full_name")
        county = data.get("county")
        address = data.get("address")

        if not booking_number or not full_name:
            return jsonify({"error": "Missing required fields (booking_number, full_name)"}), 400

        service = ContactDiscoveryService(get_db())
        result = await service.discover(
            booking_number=booking_number,
            full_name=full_name,
            county=county,
            address=address,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@contacts_bp.route("/contacts/<booking_number>", methods=["GET"])
async def get_contacts(booking_number):
    """Get discovered contacts for a defendant."""
    try:
        contacts_col = get_collection("contacts")
        doc = await contacts_col.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )
        if not doc:
            return jsonify({"contacts": [], "status": "not_found"})
        return jsonify(doc)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
