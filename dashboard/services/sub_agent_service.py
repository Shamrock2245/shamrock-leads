"""
ShamrockLeads — Sub-Agent Whitelist Service
============================================
Manages the MongoDB `sub_agents` collection for God-Admin-only whitelist control.

Only God-Admin (Brendan / admin@shamrockbailbonds.biz) can add, remove, or list
whitelisted sub-agents. Sub-agents must be whitelisted before they can log in.

Collection schema (sub_agents):
  - agent_name:      str  (full legal name)
  - license_number:  str  (FL bail bond license, e.g. P123456)
  - phone:           str  (agent's phone number for alerts)
  - is_active:       bool (True = can log in, False = revoked)
  - whitelisted_at:  str  (ISO timestamp)
  - whitelisted_by:  str  (email of God-Admin who approved)
  - revoked_at:      str | None
  - notes:           str  (optional admin notes)
"""

import logging
from datetime import datetime, timezone

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def is_whitelisted(license_number: str) -> bool:
    """Check if a sub-agent license is whitelisted and active."""
    if not license_number:
        return False
    sub_agents = get_collection("sub_agents")
    doc = await sub_agents.find_one({
        "license_number": {"$regex": f"^{license_number.strip()}$", "$options": "i"},
        "is_active": True,
    })
    return doc is not None


async def get_agent_by_license(license_number: str) -> dict | None:
    """Get a whitelisted sub-agent by license number."""
    if not license_number:
        return None
    sub_agents = get_collection("sub_agents")
    doc = await sub_agents.find_one({
        "license_number": {"$regex": f"^{license_number.strip()}$", "$options": "i"},
    })
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


async def list_sub_agents() -> list[dict]:
    """List all sub-agents (active and revoked)."""
    sub_agents = get_collection("sub_agents")
    cursor = sub_agents.find({}, {"_id": 0}).sort("whitelisted_at", -1)
    return await cursor.to_list(200)


async def add_sub_agent(
    agent_name: str,
    license_number: str,
    phone: str = "",
    whitelisted_by: str = "admin@shamrockbailbonds.biz",
    notes: str = "",
) -> dict:
    """
    Add a sub-agent to the whitelist.
    Returns the created document or error dict.
    """
    sub_agents = get_collection("sub_agents")
    license_number = license_number.strip().upper()
    agent_name = agent_name.strip()

    if not agent_name or not license_number:
        return {"success": False, "error": "Agent name and license number are required"}

    # Check if already exists
    existing = await sub_agents.find_one({
        "license_number": {"$regex": f"^{license_number}$", "$options": "i"},
    })
    if existing:
        if existing.get("is_active"):
            return {"success": False, "error": f"Agent {license_number} is already whitelisted"}
        # Re-activate
        await sub_agents.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "is_active": True,
                "agent_name": agent_name,
                "phone": phone,
                "whitelisted_at": _utc_now().isoformat(),
                "whitelisted_by": whitelisted_by,
                "revoked_at": None,
                "notes": notes,
            }}
        )
        logger.info("[sub-agent] Re-activated %s (%s) by %s", agent_name, license_number, whitelisted_by)
        return {"success": True, "action": "reactivated", "license_number": license_number, "agent_name": agent_name}

    doc = {
        "agent_name": agent_name,
        "license_number": license_number,
        "phone": phone,
        "is_active": True,
        "whitelisted_at": _utc_now().isoformat(),
        "whitelisted_by": whitelisted_by,
        "revoked_at": None,
        "notes": notes,
    }
    await sub_agents.insert_one(doc)
    logger.info("[sub-agent] Whitelisted %s (%s) by %s", agent_name, license_number, whitelisted_by)
    return {"success": True, "action": "created", "license_number": license_number, "agent_name": agent_name}


async def remove_sub_agent(license_number: str, revoked_by: str = "admin@shamrockbailbonds.biz") -> dict:
    """Revoke a sub-agent's access (soft delete — sets is_active=False)."""
    sub_agents = get_collection("sub_agents")
    license_number = license_number.strip().upper()

    result = await sub_agents.update_one(
        {"license_number": {"$regex": f"^{license_number}$", "$options": "i"}},
        {"$set": {"is_active": False, "revoked_at": _utc_now().isoformat(), "revoked_by": revoked_by}},
    )
    if result.modified_count == 0:
        return {"success": False, "error": f"Agent {license_number} not found"}
    logger.info("[sub-agent] Revoked %s by %s", license_number, revoked_by)
    return {"success": True, "license_number": license_number, "action": "revoked"}
