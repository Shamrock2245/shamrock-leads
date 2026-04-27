"""
ShamrockLeads — Payments API Blueprint
Records payment events and retrieves payment history.

Uses extensions.get_collection() to avoid circular imports from app.py.
"""

from quart import Blueprint, jsonify, request
from datetime import datetime, timezone

from dashboard.extensions import get_collection

payments_bp = Blueprint('payments', __name__)


@payments_bp.route('/payments/log', methods=['POST'])
async def log_payment():
    """Record a payment to MongoDB 'payments' collection."""
    from dashboard.api.events import publish_event

    data = await request.get_json()
    if not data or 'booking_number' not in data or 'amount' not in data:
        return jsonify({"error": "Missing required fields"}), 400

    payments = get_collection("payments")

    payment_doc = {
        "booking_number": data['booking_number'],
        "amount": float(data['amount']),
        "method": data.get('method', 'Unknown'),
        "status": data.get('status', 'Completed'),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "transaction_id": data.get('transaction_id', ''),
        "notes": data.get('notes', '')
    }

    # Insert into MongoDB
    result = await payments.insert_one(payment_doc)
    payment_doc['_id'] = str(result.inserted_id)

    # Publish SSE event
    await publish_event('payment_received', payment_doc)

    return jsonify({"success": True, "payment_id": payment_doc['_id']}), 201


@payments_bp.route('/payments/<booking_number>', methods=['GET'])
async def get_payments(booking_number):
    """Get payment history for a case."""
    payments = get_collection("payments")

    cursor = payments.find({"booking_number": booking_number}).sort("timestamp", -1)
    payment_list = []
    async for doc in cursor:
        doc['_id'] = str(doc['_id'])
        payment_list.append(doc)

    return jsonify({"payments": payment_list}), 200
