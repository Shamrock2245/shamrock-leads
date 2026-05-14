"""
Contact Discovery Service — ShamrockLeads OSINT Pipeline

Discovers potential indemnitor contacts for a defendant via:
  1. Social media profile construction (FB/IG name guessing)
  2. Florida Voter Registration search (FL Division of Elections public endpoint)
  3. County Property Appraiser search (Lee, Collier, Charlotte, Hendry)
  4. Reverse phone lookup (NumVerify — set NUMVERIFY_API_KEY in .env)
  5. Address-based relative inference

FL voter records are public under F.S. 97.0585.
Property records are public under F.S. 119.011.

Production upgrades:
  - Whitepages Pro API ($0.10/lookup) for reverse phone with name data
  - DataTree / ATTOM for property records
  - Melissa Data for address verification
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, List
from urllib.parse import urlencode

import aiohttp

log = logging.getLogger("shamrock.contact_discovery")

# FL County Property Appraiser public search endpoints
COUNTY_APPRAISER_URLS = {
    "lee":       "https://www.leepa.org/Search/SearchAddress.aspx",
    "collier":   "https://www.collierappraiser.com/search/commonsearch.aspx",
    "charlotte": "https://www.ccappraiser.com/search/commonsearch.aspx",
    "hendry":    "https://www.hendrypa.com/search/commonsearch.aspx",
    "desoto":    "https://www.desotopafl.com/search/commonsearch.aspx",
    "manatee":   "https://www.manateepao.gov/search/commonsearch.aspx",
    "sarasota":  "https://www.sc-pa.com/search/commonsearch.aspx",
}

FL_VOTER_SEARCH_URL = "https://registration.elections.myflorida.com/CheckVoterStatus"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ShamrockLeads/1.0; +https://shamrockbailbonds.biz)",
    "Accept": "text/html,application/xhtml+xml,application/json",
}
_TIMEOUT = aiohttp.ClientTimeout(total=10)


def _parse_name(full_name: str):
    """Parse Last, First Middle or First Last into (first, last) tuple."""
    if not full_name:
        return None
    name = full_name.strip()
    if "," in name:
        parts = name.split(",", 1)
        last = parts[0].strip()
        first = parts[1].strip().split()[0] if parts[1].strip() else ""
    else:
        parts = name.split()
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
        else:
            return None
    return (first, last) if first and last else None


def _parse_voter_html(html: str, full_name: str) -> List[Dict]:
    """Parse FL voter registration HTML for address data."""
    results = []
    try:
        pat = re.compile(
            r"(\d+\s+[A-Z0-9\s,\.]+(?:ST|AVE|DR|RD|BLVD|LN|CT|WAY|CIR|PL|TER|HWY|PKWY)[A-Z\s,\.]*\d{5})",
            re.IGNORECASE,
        )
        for addr in pat.findall(html)[:3]:
            results.append({
                "name": full_name,
                "relationship": "voter_record_match",
                "source": "fl_voter_registration",
                "phone": None,
                "address": addr.strip(),
                "confidence": 0.8,
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "notes": "FL voter registration address match (F.S. 97.0585)",
            })
    except Exception as e:
        log.debug("[ContactDiscovery] Voter HTML parse error: %s", e)
    return results


def _parse_property_html(html: str, full_name: str, county: str) -> List[Dict]:
    """Parse county property appraiser HTML for address data."""
    results = []
    try:
        pat = re.compile(
            r"(\d+\s+[A-Z0-9\s,\.]+(?:ST|AVE|DR|RD|BLVD|LN|CT|WAY|CIR|PL|TER|HWY|PKWY)[A-Z\s,\.]*)",
            re.IGNORECASE,
        )
        for addr in pat.findall(html)[:2]:
            results.append({
                "name": full_name,
                "relationship": "property_owner",
                "source": f"{county}_property_appraiser",
                "phone": None,
                "address": addr.strip() + f", {county.capitalize()}, FL",
                "confidence": 0.75,
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "notes": f"Property record match in {county.capitalize()} County (F.S. 119.011)",
            })
    except Exception as e:
        log.debug("[ContactDiscovery] Property HTML parse error: %s", e)
    return results


class ContactDiscoveryService:
    """Discovers potential indemnitor contacts for a defendant via OSINT."""

    def __init__(self, db):
        self.db = db

    async def discover(self, booking_number: str, full_name: str, county: str = None,
                       address: str = None, dob: str = None):
        """
        Run discovery pipeline for a defendant.

        Args:
            booking_number: Booking number for caching results
            full_name: Defendant full name (Last, First Middle or First Last)
            county: FL county lowercase (e.g. lee)
            address: Last known address
            dob: Date of birth YYYY-MM-DD for voter record matching

        Returns:
            dict with success, cached, contacts list
        """
        contacts_col = self.db["contacts"]

        # Check for recent cached results (< 24h)
        existing = await contacts_col.find_one({"booking_number": booking_number})
        if existing:
            discovered_at = existing.get("discovered_at")
            if discovered_at:
                try:
                    dt = datetime.fromisoformat(discovered_at.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - dt).total_seconds() < 86400:
                        return {"success": True, "cached": True,
                                "contacts": existing.get("discovered_contacts", [])}
                except ValueError:
                    pass

        discovered_contacts = []

        # 1. Social media profile construction
        name_parts = _parse_name(full_name)
        if name_parts:
            first, last = name_parts
            discovered_contacts.append({
                "name": full_name,
                "relationship": "self",
                "source": "social_media_guess",
                "phone": None,
                "address": None,
                "confidence": 0.5,
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "notes": (
                    f"Possible FB: facebook.com/{first.lower()}.{last.lower()} | "
                    f"IG: @{first.lower()}{last.lower()}"
                ),
            })

        # 2. FL Voter Registration search
        try:
            voter_contacts = await self._search_voter_records(full_name, county or "", dob)
            discovered_contacts.extend(voter_contacts)
        except Exception as e:
            log.warning("[ContactDiscovery] Voter search failed for %s: %s", booking_number, e)

        # 3. County property appraiser search
        if county and county.lower() in COUNTY_APPRAISER_URLS:
            try:
                prop_contacts = await self._search_property_records(full_name, county.lower())
                discovered_contacts.extend(prop_contacts)
            except Exception as e:
                log.warning("[ContactDiscovery] Property search failed for %s: %s", booking_number, e)

        # 4. Address-based relative inference
        if address and len(address) > 5:
            discovered_contacts.append({
                "name": f"Resident at {address.split(',')[0]}",
                "relationship": "possible_family_or_roommate",
                "source": "address_inference",
                "phone": None,
                "address": address,
                "confidence": 0.6,
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "notes": "Address match — possible co-resident or family member",
            })

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

    async def _search_voter_records(self, full_name: str, county: str, dob: str = None) -> List[Dict]:
        """
        Search FL voter registration records (public under F.S. 97.0585).
        Uses the FL Division of Elections public voter lookup endpoint.
        """
        results = []
        name_parts = _parse_name(full_name)
        if not name_parts:
            return results
        first, last = name_parts
        try:
            async with aiohttp.ClientSession(headers=_HEADERS, timeout=_TIMEOUT) as session:
                payload = {
                    "LastName": last,
                    "FirstName": first,
                    "County": county.capitalize() if county else "",
                    "DateOfBirth": dob or "",
                }
                async with session.post(FL_VOTER_SEARCH_URL, data=payload) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        results.extend(_parse_voter_html(html, full_name))
        except asyncio.TimeoutError:
            log.debug("[ContactDiscovery] Voter search timed out for %s", full_name)
        except Exception as e:
            log.debug("[ContactDiscovery] Voter search error: %s", e)
        return results

    async def _search_property_records(self, full_name: str, county: str) -> List[Dict]:
        """
        Search county property appraiser records (public under F.S. 119.011).
        Returns property owners matching the defendant name in the county.
        """
        results = []
        name_parts = _parse_name(full_name)
        if not name_parts:
            return results
        first, last = name_parts
        base_url = COUNTY_APPRAISER_URLS.get(county.lower())
        if not base_url:
            return results
        try:
            async with aiohttp.ClientSession(headers=_HEADERS, timeout=_TIMEOUT) as session:
                search_name = f"{last.upper()}, {first.upper()}"
                params = {"mode": "owner", "OwnerName": search_name, "taxyr": str(datetime.now().year)}
                search_url = f"{base_url}?{urlencode(params)}"
                async with session.get(search_url) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        results.extend(_parse_property_html(html, full_name, county))
        except asyncio.TimeoutError:
            log.debug("[ContactDiscovery] Property search timed out for %s in %s", full_name, county)
        except Exception as e:
            log.debug("[ContactDiscovery] Property search error: %s", e)
        return results

    async def _reverse_phone_lookup(self, phone: str) -> Dict:
        """
        Reverse phone lookup via NumVerify (set NUMVERIFY_API_KEY in .env).
        Free tier: 100 req/month, carrier data only.
        Production: upgrade to Whitepages Pro for name data ($0.10/lookup).
        """
        if not phone:
            return {}
        clean_phone = re.sub(r"\D", "", phone)
        if len(clean_phone) == 10:
            clean_phone = "1" + clean_phone
        if len(clean_phone) != 11:
            return {}
        api_key = os.environ.get("NUMVERIFY_API_KEY")
        if not api_key:
            return {
                "phone": phone,
                "carrier": "Unknown",
                "line_type": "Unknown",
                "note": "Set NUMVERIFY_API_KEY in .env for carrier/name data",
            }
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                url = (
                    f"http://apilayer.net/api/validate"
                    f"?access_key={api_key}&number={clean_phone}&country_code=US&format=1"
                )
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "phone": phone,
                            "carrier": data.get("carrier", ""),
                            "line_type": data.get("line_type", ""),
                            "valid": data.get("valid", False),
                        }
        except Exception as e:
            log.debug("[ContactDiscovery] Phone lookup error: %s", e)
        return {}
