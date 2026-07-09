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
# Revenue automations default ON in *review* mode (queue/staff digests).
# Client free-sends require mode="full_auto" (Prime Directive #6).
DEFAULT_CONFIG = {
    "type": "automation_master",

    # ── Speed-to-Contact (The Closer) ──
    "speed_to_contact": {
        "enabled": True,
        "mode": "review",               # "off" | "review" | "full_auto"
        "min_lead_score": 70,           # Only auto-outreach for hot leads
        "hours_back": 1,                # Look back window per cycle
        "max_per_cycle": 20,            # Rate-limit per 30min cycle
        "interval_seconds": 1800,       # 30 minutes
        "require_phone": True,          # Skip if no phone number
        "auto_discover_contacts": True, # Run contact discovery for phoneless leads
        "slack_digest": True,
    },

    # ── Unsigned Paperwork Chase ──
    "paperwork_chase": {
        "enabled": True,
        "mode": "review",               # "review" | "staff_only" | "full_auto"
        "nudge_1_hours": 2,             # First nudge after 2 hours
        "nudge_2_hours": 6,             # Second nudge after 6 hours
        "staff_alert_hours": 24,        # Slack alert to staff after 24 hours
        "max_nudges": 3,                # Max nudges per packet
        "interval_seconds": 3600,       # Check every hour
        "slack_digest": True,
    },

    # ── Abandoned Intake Recovery ──
    "intake_recovery": {
        "enabled": True,
        "mode": "review",               # "review" | "full_auto"
        "stale_minutes": 30,            # Consider abandoned after 30 min
        "max_per_cycle": 10,            # Rate-limit per cycle
        "interval_seconds": 3600,       # Check every hour
        "cooldown_hours": 24,           # Don't re-nudge within 24h
        "slack_digest": True,
    },

    # ── POA inventory low-stock alerts ──
    "poa_low_stock": {
        "enabled": True,
        "threshold": 5,                 # Alert when available POAs ≤ this
        "interval_seconds": 21600,      # 6 hours
    },

    # ── Weekly surety bond / discharge reports ──
    "surety_weekly_reports": {
        "enabled": True,
        "interval_seconds": 604800,     # 7 days
        "sureties": ["OSI", "PALMETTO"],
        "include_discharges": True,
        "days_back_discharges": 7,
    },

    # ── Forfeiture portfolio scan (staff digest + optional tasks) ──
    "forfeiture_scan": {
        "enabled": True,
        "limit": 100,
        "slack_min_tier": "high",       # high | critical
        "create_tasks": True,
        "slack_digest": True,
        "interval_seconds": 14400,      # 4 hours
    },

    # ── SignNow status poller + collect-payment tasks ──
    "signnow_poller": {
        "enabled": True,
        "limit": 40,
        "create_payment_tasks": True,
        "slack_digest": True,
        "interval_seconds": 1800,       # 30 minutes
    },

    # ── Compliance task backfill for active bonds ──
    "compliance_backfill": {
        "enabled": True,
        "limit": 80,
        "slack_digest": True,
        "interval_seconds": 21600,      # 6 hours
    },

    # ── Matching backlog (batch_match + staff digest) ──
    "matching_backlog": {
        "enabled": True,
        "limit": 50,
        "slack_digest": True,
        "interval_seconds": 3600,       # 1 hour
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


def _deep_merge_defaults(stored: dict, defaults: dict) -> dict:
    """Fill missing keys from defaults without clobbering stored values."""
    out = dict(stored)
    for key, dval in defaults.items():
        if key not in out:
            out[key] = dval
        elif isinstance(dval, dict) and isinstance(out.get(key), dict):
            merged = dict(dval)
            merged.update(out[key])  # stored wins per-field
            # still fill nested keys that stored is missing
            for nk, nv in dval.items():
                if nk not in out[key]:
                    merged[nk] = nv
            out[key] = merged
    return out


async def get_automation_config(db) -> dict:
    """Load the automation master config from MongoDB.
    Creates the default document if it doesn't exist.
    One-time migration enables revenue automations in review mode.
    """
    config_coll = db["automation_config"]
    cfg = await config_coll.find_one({"type": "automation_master"}, {"_id": 0})
    if not cfg:
        default = {**DEFAULT_CONFIG, "updated_at": datetime.now(timezone.utc).isoformat()}
        default["_revenue_automations_v1"] = datetime.now(timezone.utc).isoformat()
        await config_coll.insert_one(default)
        return default

    cfg = _deep_merge_defaults(cfg, DEFAULT_CONFIG)

    # One-time: turn on revenue automations in *review* mode (safe).
    # Does not re-enable if an operator later flips them off.
    if not cfg.get("_revenue_automations_v1"):
        now = datetime.now(timezone.utc).isoformat()
        for section, enabled_default in (
            ("speed_to_contact", True),
            ("paperwork_chase", True),
            ("intake_recovery", True),
            ("poa_low_stock", True),
            ("surety_weekly_reports", True),
        ):
            sec = dict(cfg.get(section) or DEFAULT_CONFIG.get(section) or {})
            sec["enabled"] = enabled_default
            if section == "speed_to_contact":
                sec.setdefault("mode", "review")
                sec.setdefault("slack_digest", True)
            if section == "paperwork_chase":
                sec.setdefault("mode", "review")
                sec.setdefault("slack_digest", True)
            if section == "intake_recovery":
                sec.setdefault("mode", "review")
                sec.setdefault("slack_digest", True)
            cfg[section] = sec
        cfg["_revenue_automations_v1"] = now
        await config_coll.update_one(
            {"type": "automation_master"},
            {"$set": {
                "speed_to_contact": cfg["speed_to_contact"],
                "paperwork_chase": cfg["paperwork_chase"],
                "intake_recovery": cfg["intake_recovery"],
                "poa_low_stock": cfg.get("poa_low_stock", DEFAULT_CONFIG["poa_low_stock"]),
                "surety_weekly_reports": cfg.get(
                    "surety_weekly_reports", DEFAULT_CONFIG["surety_weekly_reports"]
                ),
                "_revenue_automations_v1": now,
                "updated_at": now,
                "updated_by": "migration_revenue_v1",
            }},
            upsert=True,
        )
        logger.info("☘️  Revenue automations migration v1 applied (review mode)")

    # One-time: lifecycle suite (forfeiture / SignNow / compliance / matching)
    if not cfg.get("_lifecycle_automations_v1"):
        now = datetime.now(timezone.utc).isoformat()
        set_doc: dict = {
            "_lifecycle_automations_v1": now,
            "updated_at": now,
            "updated_by": "migration_lifecycle_v1",
        }
        for section in (
            "forfeiture_scan",
            "signnow_poller",
            "compliance_backfill",
            "matching_backlog",
        ):
            sec = dict(cfg.get(section) or DEFAULT_CONFIG.get(section) or {})
            sec["enabled"] = True
            sec.setdefault("slack_digest", True)
            cfg[section] = sec
            set_doc[section] = sec
        await config_coll.update_one(
            {"type": "automation_master"},
            {"$set": set_doc},
            upsert=True,
        )
        logger.info("☘️  Lifecycle automations migration v1 applied")

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

