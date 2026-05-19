# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""
ShamrockLeads — Notification Center API
Centralized alert aggregation from all subsystems.

Endpoints:
  GET  /notifications             — Get recent notifications (paginated)
  POST /notifications             — Create a notification
  POST /notifications/<id>/read   — Mark as read
  POST /notifications/read-all    — Mark all as read
  GET  /notifications/unread-count — Quick badge count
  DELETE /notifications/<id>      — Dismiss a notification
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from datetime import datetime, timezone, timedelta
import uuid

from dashboard.extensions import get_collection

notifications_bp = APIRouter(prefix="/api", tags=["notifications"])
# Notification categories with priority levels
NOTIFICATION_TYPES = {
    "hot_lead": {"icon": "🔥", "priority": "high", "color": "#ef4444"},
    "payment_due": {"icon": "💳", "priority": "high", "color": "#f59e0b"},
    "payment_received": {"icon": "💰", "priority": "medium", "color": "#10b981"},
    "court_reminder": {"icon": "⚖️", "priority": "critical", "color": "#8b5cf6"},
    "delinquent": {"icon": "🚨", "priority": "critical", "color": "#dc2626"},
    "signing_complete": {"icon": "✍️", "priority": "medium", "color": "#3b82f6"},
    "signing_expired": {"icon": "⏰", "priority": "high", "color": "#f97316"},
    "intake_received": {"icon": "📥", "priority": "medium", "color": "#06b6d4"},
    "match_found": {"icon": "🤝", "priority": "high", "color": "#8b5cf6"},
    "scraper_error": {"icon": "🛠️", "priority": "low", "color": "#6b7280"},
    "rearrest": {"icon": "🔄", "priority": "high", "color": "#ef4444"},
    "system": {"icon": "☘️", "priority": "low", "color": "#22c55e"},
    "discharge": {"icon": "✅", "priority": "medium", "color": "#10b981"},
    "forfeiture": {"icon": "⚠️", "priority": "critical", "color": "#dc2626"},
    "bb_message": {"icon": "💬", "priority": "medium", "color": "#3b82f6"},
}

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


async def create_notification(
    notification_type: str,
    title: str,
    message: str,
    entity_id: str = "",
    entity_type: str = "",
    metadata: dict = None,
):
    """Programmatically create a notification from any subsystem."""
    notif_col = get_collection("notifications")
    type_config = NOTIFICATION_TYPES.get(notification_type, NOTIFICATION_TYPES["system"])

    doc = {
        "notification_id": str(uuid.uuid4())[:12],
        "type": notification_type,
        "icon": type_config["icon"],
        "priority": type_config["priority"],
        "color": type_config["color"],
        "title": title,
        "message": message,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "metadata": metadata or {},
        "read": False,
        "dismissed": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    await notif_col.insert_one(doc)
    doc["_id"] = str(doc.get("_id", ""))
    return doc


@notifications_bp.get("/notifications")
async def get_notifications():
    """Get recent notifications, sorted by priority then time."""
    _qp = dict(request.query_params)
    notif_col = get_collection("notifications")
    limit = min(100, int(_qp.get('limit', 50)))
    unread_only = _qp.get('unread', '').lower() == 'true'
    ntype = _qp.get('type', '').strip()

    query = {"dismissed": {"$ne": True}}
    if unread_only:
        query["read"] = False
    if ntype:
        query["type"] = ntype

    cursor = notif_col.find(query).sort("created_at", -1).limit(limit)
    results = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        doc["priority_order"] = PRIORITY_ORDER.get(doc.get("priority", "low"), 3)
        results.append(doc)

    # Sort by priority first, then by time
    results.sort(key=lambda x: (x["priority_order"], x.get("created_at", "")))

    return {"notifications": results, "total": len(results)}


@notifications_bp.post("/notifications")
async def post_notification():
    """Manually create a notification."""
    data = await request.json()
    if not data or 'title' not in data:
        return {"error": "Missing title"}, 400

    doc = await create_notification(
        notification_type=data.get("type", "system"),
        title=data["title"],
        message=data.get("message", ""),
        entity_id=data.get("entity_id", ""),
        entity_type=data.get("entity_type", ""),
        metadata=data.get("metadata"),
    )
    return {"success": True, "notification": doc}, 201


@notifications_bp.post("/notifications/<notification_id>/read")
async def mark_read(notification_id):
    """Mark a single notification as read."""
    notif_col = get_collection("notifications")
    result = await notif_col.update_one(
        {"notification_id": notification_id},
        {"$set": {"read": True, "read_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"success": result.modified_count > 0}


@notifications_bp.post("/notifications/read-all")
async def mark_all_read():
    """Mark all unread notifications as read."""
    notif_col = get_collection("notifications")
    result = await notif_col.update_many(
        {"read": False},
        {"$set": {"read": True, "read_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"success": True, "marked": result.modified_count}


@notifications_bp.get("/notifications/unread-count")
async def unread_count():
    """Quick unread count for badge display."""
    notif_col = get_collection("notifications")
    count = await notif_col.count_documents({"read": False, "dismissed": {"$ne": True}})
    return {"unread": count}


@notifications_bp.delete("/notifications/<notification_id>")
async def dismiss_notification(notification_id):
    """Dismiss (soft-delete) a notification."""
    notif_col = get_collection("notifications")
    result = await notif_col.update_one(
        {"notification_id": notification_id},
        {"$set": {"dismissed": True, "dismissed_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"success": result.modified_count > 0}
