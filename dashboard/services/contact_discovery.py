"""Contact Discovery Service (Phase 2) — OSINT pipeline stub"""

from datetime import datetime, timezone
from typing import Dict, List, Optional


class ContactDiscoveryService:
    """Discovers potential indemnitor contacts for a defendant via OSINT."""

    def __init__(self, db):
        self.db = db

    async def discover(self, booking_number: str, full_name: str, county: str = None,
                       address: str = None):
        """Run discovery pipeline for a defendant.
        
        Currently a stub that constructs social media profiles and
        address-based relative searches. Ready for TruePeopleSearch/
        Whitepages API key integration.
        """
        contacts_col = self.db["contacts"]

        # Check for recent cached results (< 24h)
        existing = await contacts_col.find_one({"booking_number": booking_number})
        if existing:
            discovered_at = existing.get("discovered_at")
            if discovered_at:
                try:
                    dt = datetime.fromisoformat(discovered_at.replace('Z', '+00:00'))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - dt).total_seconds() < 86400:
                        return {"success": True, "cached": True,
                                "contacts": existing.get("discovered_contacts", [])}
                except ValueError:
                    pass

        discovered_contacts = []

        # 1. Social Media Profile Construction
        name_parts = full_name.split()
        if len(name_parts) >= 2:
            first = name_parts[0].lower()
            last = name_parts[-1].lower()
            discovered_contacts.append({
                "name": full_name,
                "relationship": "self",
                "source": "social_media_guess",
                "phone": None,
                "address": None,
                "confidence": 0.5,
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "notes": f"Possible FB: facebook.com/{first}.{last}",
            })

        # 2. Address-based Relative Search (Stub for Voter/Property records)
        if address and len(address) > 5:
            discovered_contacts.append({
                "name": f"Resident at {address.split(',')[0]}",
                "relationship": "possible_family_or_roommate",
                "source": "property_records_stub",
                "phone": None,
                "address": address,
                "confidence": 0.7,
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "notes": "Address match found in public records",
            })

        # 3. Reverse Phone Lookup (Stub — ready for TruePeopleSearch/Whitepages API)

        # Save results
        doc = {
            "booking_number": booking_number,
            "defendant_name": full_name,
            "discovered_contacts": discovered_contacts,
            "discovery_status": "complete",
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }
        await contacts_col.update_one(
            {"booking_number": booking_number},
            {"$set": doc},
            upsert=True,
        )

        return {"success": True, "cached": False, "contacts": discovered_contacts}

    async def _search_voter_records(self, full_name: str, county: str) -> List[Dict]:
        """Stub for Florida voter registration search."""
        # In production, this would query a public records API
        return []
        
    async def _search_property_records(self, full_name: str, county: str) -> List[Dict]:
        """Stub for county property appraiser search."""
        # In production, this would query the county property appraiser API
        return []
        
    async def _reverse_phone_lookup(self, phone: str) -> Dict:
        """Stub for reverse phone lookup (e.g., TruePeopleSearch/Whitepages)."""
        # In production, this would query a reverse phone API
        return {}
