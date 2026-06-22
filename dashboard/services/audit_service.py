from datetime import datetime, timezone
from bson import ObjectId
from dashboard.extensions import get_db

class AuditService:
    """Service to create immutable audit events."""
    
    @staticmethod
    async def log_event(
        entity_type: str, 
        entity_id: str, 
        action: str, 
        details: dict, 
        actor: str,
        actor_type: str = "system",
        event_context: str = ""
    ):
        db = get_db()
        await db.audit_events.insert_one({
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "details": details,
            "actor": actor,
            "actor_type": actor_type,
            "event_context": event_context,
            "timestamp": datetime.now(timezone.utc)
        })
