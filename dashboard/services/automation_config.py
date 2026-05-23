"""
ShamrockLeads — Automation Config Service
==========================================
Central configuration for all background revenue automations.

All automations default to DISABLED (Prime Directive #6: Human-in-the-Loop).
Enable via dashboard toggle or API call.

Config stored in MongoDB `automation_config` collection as a single document.

Automations controlled:
  1. Speed-to-Contact   — Auto-start outreach for hot leads
  2. Paperwork Chase    — Auto-nudge unsigned SignNow packets
  3. Intake Recovery    — Auto-recover abandoned intakes
  4. Auto-Reply AI      — AI responds to inbound iMessages
  5. FindMy Geofence    — Alert on Lee County boundary breach
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Default configuration ───────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "type": "automation_master",

    # ── Speed-to-Contact (The Closer) ──
    "speed_to_contact": {
        "enabled": False,
        "mode": "review",               # "off" | "review" | "full_auto"
        "min_lead_score": 70,           # Only auto-outreach for hot leads
        "hours_back": 1,                # Look back window per cycle
        "max_per_cycle": 20,            # Rate-limit per 30min cycle
        "interval_seconds": 1800,       # 30 minutes
        "require_phone": True,          # Skip if no phone number
        "auto_discover_contacts": True, # Run contact discovery for phoneless leads
    },

    # ── Unsigned Paperwork Chase ──
    "paperwork_chase": {
        "enabled": False,
        "nudge_1_hours": 2,             # First nudge after 2 hours
        "nudge_2_hours": 6,             # Second nudge after 6 hours
        "staff_alert_hours": 24,        # Slack alert to staff after 24 hours
        "max_nudges": 3,                # Max nudges per packet
        "interval_seconds": 3600,       # Check every hour
    },

    # ── Abandoned Intake Recovery ──
    "intake_recovery": {
        "enabled": False,
        "stale_minutes": 30,            # Consider abandoned after 30 min
        "max_per_cycle": 10,            # Rate-limit per cycle
        "interval_seconds": 3600,       # Check every hour
        "cooldown_hours": 24,           # Don't re-nudge within 24h
    },

    # ── Auto-Reply AI (BlueBubbles inbound message handler) ──
    "auto_reply": {
        "enabled": False,
        "cooldown_minutes": 5,           # Per-sender cooldown between auto-replies
        "confidence_threshold": 0.8,     # Minimum AI confidence to auto-reply
        "keywords": ["bail", "bond", "help", "arrested"],  # Trigger keywords
        "after_hours_only": False,       # Only auto-reply outside business hours
    },

    # ── FindMy Geofence (Lee County boundary monitoring) ──
    "findmy_geofence": {
        "enabled": False,
        "geofence_miles": 25,            # Radius in miles from Lee County center
        "center_lat": 26.5629,           # Lee County center latitude
        "center_lng": -81.8723,          # Lee County center longitude
        "poll_interval_minutes": 15,     # How often to poll FindMy
        "alert_channel": "slack",        # "slack" | "telegram" | "both"
    },

    # ─── Intelligence Pipeline (default: ON) ─────────────────────────────────
    "docket_monitor": {
        "enabled": True,
        "interval_seconds": 14400,       # 4 hours
    },
    "court_intel": {
        "enabled": True,
        "interval_seconds": 21600,       # 6 hours
    },
    "nlp_enrichment": {
        "enabled": True,
        "interval_seconds": 7200,        # 2 hours
    },

    # ─── Monitoring & Compliance (default: ON) ───────────────────────────────
    "court_reminders": {
        "enabled": True,
        "interval_seconds": 3600,        # 1 hour
    },
    "rearrest_detection": {
        "enabled": True,
        "interval_seconds": 7200,        # 2 hours
    },
    "delinquency_scanner": {
        "enabled": True,
        "interval_seconds": 14400,       # 4 hours
    },
    "court_email": {
        "enabled": True,
        "interval_seconds": 900,         # 15 minutes
    },
    "bb_health": {
        "enabled": True,
        "interval_seconds": 600,         # 10 minutes
    },
    "data_retention": {
        "enabled": True,
        "interval_seconds": 604800,      # 7 days
    },

    # ─── Geo Intelligence (default: OFF — opt-in) ───────────────────────────
    "geo_intelligence": {
        "enabled": False,
        "interval_seconds": 300,         # 5 minutes
    },

    # ─── Content (default: ON) ──────────────────────────────────────────────
    "blog_publisher": {
        "enabled": True,
        "interval_seconds": 21600,       # 6 hours
    },
    "wix_sync": {
        "enabled": True,
        "interval_seconds": 14400,       # 4 hours — sync MongoDB → Wix CMS
    },

    # ── Metadata ──
    "updated_at": None,
    "updated_by": None,
}


async def get_automation_config(db) -> dict:
    """Load the automation master config from MongoDB.
    Creates the default document if it doesn't exist.
    """
    config_coll = db["automation_config"]
    cfg = await config_coll.find_one({"type": "automation_master"}, {"_id": 0})
    if not cfg:
        default = DEFAULT_CONFIG.copy()
        default["updated_at"] = datetime.now(timezone.utc).isoformat()
        await config_coll.insert_one(default)
        return default
    return cfg


async def update_automation_config(db, updates: dict, actor: str = "dashboard") -> dict:
    """Update automation config fields.

    Args:
        db: Motor database instance
        updates: Partial update dict (nested keys like "speed_to_contact.enabled")
        actor: Who made the change (for audit trail)

    Returns:
        Updated config dict
    """
    config_coll = db["automation_config"]
    updates.pop("type", None)
    updates.pop("_id", None)
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    updates["updated_by"] = actor

    await config_coll.update_one(
        {"type": "automation_master"},
        {"$set": updates},
        upsert=True,
    )

    # Log the change for audit
    logger.info("☘️  Automation config updated by %s: %s", actor, list(updates.keys()))

    return await get_automation_config(db)


async def is_enabled(db, automation_key: str) -> bool:
    """Quick check if a specific automation is enabled.

    Args:
        automation_key: One of "speed_to_contact", "paperwork_chase", "intake_recovery"

    Returns:
        True if the automation is enabled, False otherwise
    """
    cfg = await get_automation_config(db)
    section = cfg.get(automation_key, {})
    return section.get("enabled", False)


async def should_run(db, key: str, default: bool = True) -> bool:
    """Check if a background service should run this cycle.

    Unlike is_enabled(), this defaults to True so system services
    keep running even if the config document doesn't exist yet.

    Args:
        db: Motor database instance
        key: Service key (e.g. "docket_monitor", "court_reminders")
        default: Value to return if config can't be read

    Returns:
        True if the service should execute, False to skip.
    """
    try:
        cfg = await get_automation_config(db)
        section = cfg.get(key, {})
        return section.get("enabled", default)
    except Exception:
        return default

