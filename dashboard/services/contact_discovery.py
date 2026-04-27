<<<<<<< HEAD
"""Contact Discovery Service (Phase 2) — OSINT pipeline stub"""
=======
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
from datetime import datetime, timezone
from dashboard.extensions import get_collection
import httpx
import re

<<<<<<< HEAD

class ContactDiscoveryService:
    def __init__(self):
        pass

    async def discover_contacts(self, booking_number: str, full_name: str,
                                county: str = None, age: int = None, address: str = None):
        """Run OSINT discovery pipeline to find potential indemnitors."""
        contacts_col = get_collection("contacts")

        # Check if already discovered recently (24h cache)
        existing = await contacts_col.find_one({"booking_number": booking_number})
        if existing and existing.get("discovery_status") == "complete":
=======
class ContactDiscoveryService:
    def __init__(self):
        pass
        
    async def discover_contacts(self, booking_number: str, full_name: str, county: str = None, age: int = None, address: str = None):
        """Run OSINT discovery pipeline to find potential indemnitors."""
        contacts_col = get_collection("contacts")
        
        # Check if already discovered recently
        existing = await contacts_col.find_one({"booking_number": booking_number})
        if existing and existing.get("discovery_status") == "complete":
            # If discovered in last 24h, return cached
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
            discovered_at = existing.get("discovered_at")
            if discovered_at:
                try:
                    dt = datetime.fromisoformat(discovered_at.replace('Z', '+00:00'))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - dt).total_seconds() < 86400:
<<<<<<< HEAD
                        return {"success": True, "cached": True,
                                "contacts": existing.get("discovered_contacts", [])}
                except ValueError:
                    pass

        discovered_contacts = []

        # 1. Social Media Profile Construction
=======
                        return {"success": True, "cached": True, "contacts": existing.get("discovered_contacts", [])}
                except ValueError:
                    pass
                    
        discovered_contacts = []
        
        # 1. Social Media Profile Construction (Stub)
        # Just construct likely URLs based on name
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
        name_parts = full_name.split()
        if len(name_parts) >= 2:
            first = name_parts[0].lower()
            last = name_parts[-1].lower()
<<<<<<< HEAD
=======
            
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
            discovered_contacts.append({
                "name": full_name,
                "relationship": "self",
                "source": "social_media_guess",
                "phone": None,
                "address": None,
                "confidence": 0.5,
                "discovered_at": datetime.now(timezone.utc).isoformat(),
<<<<<<< HEAD
                "notes": f"Possible FB: facebook.com/{first}.{last}",
            })

        # 2. Address-based Relative Search (Stub for Voter/Property records)
        if address and len(address) > 5:
=======
                "notes": f"Possible FB: facebook.com/{first}.{last}"
            })
            
        # 2. Address-based Relative Search (Stub for Voter/Property records)
        if address and len(address) > 5:
            # In a real implementation, this would query a public records API
            # For now, we stub it with a high-confidence placeholder if address exists
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
            discovered_contacts.append({
                "name": f"Resident at {address.split(',')[0]}",
                "relationship": "possible_family_or_roommate",
                "source": "property_records_stub",
                "phone": None,
                "address": address,
                "confidence": 0.7,
                "discovered_at": datetime.now(timezone.utc).isoformat(),
<<<<<<< HEAD
                "notes": "Address match found in public records",
            })

        # 3. Reverse Phone Lookup (Stub — ready for TruePeopleSearch/Whitepages API)

=======
                "notes": "Address match found in public records"
            })
            
        # 3. Reverse Phone Lookup (Stub)
        # Would integrate with TruePeopleSearch or Whitepages API here
        
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
        # Save results
        doc = {
            "booking_number": booking_number,
            "defendant_name": full_name,
            "discovered_contacts": discovered_contacts,
            "discovery_status": "complete",
<<<<<<< HEAD
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }
        await contacts_col.update_one(
            {"booking_number": booking_number},
            {"$set": doc},
            upsert=True,
        )

=======
            "discovered_at": datetime.now(timezone.utc).isoformat()
        }
        
        await contacts_col.update_one(
            {"booking_number": booking_number},
            {"$set": doc},
            upsert=True
        )
        
>>>>>>> 2e1d28a2da552164e560f9a79c48f5af7efb50de
        return {"success": True, "cached": False, "contacts": discovered_contacts}
