from datetime import datetime
from bson import ObjectId
from dashboard.extensions import get_db

class AuditService:
    """Service to create immutable audit events."""
    
    @staticmethod
    async def log_event(entity_type: str, entity_id: str, action: str, details: dict, actor: str):
        db = get_db()
        await db.audit_events.insert_one({
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "details": details,
            "actor": actor,
            "timestamp": datetime.utcnow()
        })
