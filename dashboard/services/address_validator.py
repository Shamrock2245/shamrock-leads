"""
Address Validator & Geo Intelligence — ShamrockLeads Tier 2 API Integration

Validates, standardizes, and enriches addresses for the intake pipeline.
Provides zip → county detection critical for jurisdiction matching.

Provider Stack (all free tier):
  1. Zippopotam.us  — FREE unlimited, zip → city/state/lat/lon
  2. Nominatim/OSM  — FREE (1 req/sec), geocoding + reverse → county/FIPS
  3. Smarty Streets  — 250/mo free, USPS-certified address validation (optional)

Key Use Cases:
  - Auto-detect county from zip code at intake (jurisdiction matching)
  - Validate defendant/indemnitor addresses before SignNow
  - Detect fake/invalid addresses (flight risk signal)
  - Standardize address format for paperwork hydration
  - Distance calculation for bail conditions
"""

import asyncio
import logging
import math
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp

log = logging.getLogger("shamrock.address_validator")

# ── API Endpoints ──
ZIPPOPOTAM_BASE = "https://api.zippopotam.us/us"
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
SMARTY_STREET_API = "https://us-street.api.smarty.com/street-address"
SMARTY_AUTOCOMPLETE_API = "https://us-autocomplete-pro.api.smarty.com/lookup"
SMARTY_EXTRACT_API = "https://us-extract.api.smarty.com"

_TIMEOUT = aiohttp.ClientTimeout(total=10)
_NOMINATIM_HEADERS = {
    "User-Agent": "ShamrockLeads/2.0 (admin@shamrockbailbonds.biz)",
    "Accept": "application/json",
}

# ── Florida County → FIPS mapping (all 67 counties) ──
FL_COUNTY_FIPS = {
    "alachua": "12001", "baker": "12003", "bay": "12005", "bradford": "12007",
    "brevard": "12009", "broward": "12011", "calhoun": "12013", "charlotte": "12015",
    "citrus": "12017", "clay": "12019", "collier": "12021", "columbia": "12023",
    "desoto": "12027", "dixie": "12029", "duval": "12031", "escambia": "12033",
    "flagler": "12035", "franklin": "12037", "gadsden": "12039", "gilchrist": "12041",
    "glades": "12043", "gulf": "12045", "hamilton": "12047", "hardee": "12049",
    "hendry": "12051", "hernando": "12053", "highlands": "12055",
    "hillsborough": "12057", "holmes": "12059", "indian river": "12061",
    "jackson": "12063", "jefferson": "12065", "lafayette": "12067", "lake": "12069",
    "lee": "12071", "leon": "12073", "levy": "12075", "liberty": "12077",
    "madison": "12079", "manatee": "12081", "marion": "12083", "martin": "12085",
    "miami-dade": "12086", "monroe": "12087", "nassau": "12089", "okaloosa": "12091",
    "okeechobee": "12093", "orange": "12095", "osceola": "12097",
    "palm beach": "12099", "pasco": "12101", "pinellas": "12103", "polk": "12105",
    "putnam": "12107", "santa rosa": "12113", "sarasota": "12115",
    "seminole": "12117", "st. johns": "12109", "st. lucie": "12111",
    "sumter": "12119", "suwannee": "12121", "taylor": "12123", "union": "12125",
    "volusia": "12127", "wakulla": "12129", "walton": "12131",
    "washington": "12133",
}

# ── Florida zip code → county mapping (major ranges) ──
# This covers the most common ranges; Nominatim handles edge cases
FL_ZIP_COUNTY_FAST = {
    # Lee County (our home turf)
    range(33901, 33976): "Lee",
    range(33990, 33995): "Lee",
    # Collier County
    range(34101, 34120): "Collier",
    range(34140, 34146): "Collier",
    # Charlotte County
    range(33947, 33956): "Charlotte",
    range(33980, 33984): "Charlotte",
    # Hendry County
    range(33440, 33441): "Hendry",
    range(33930, 33936): "Hendry",
    # DeSoto County
    range(34265, 34270): "DeSoto",
    # Manatee County
    range(34201, 34222): "Manatee",
    # Sarasota County
    range(34228, 34244): "Sarasota",
    range(34272, 34278): "Sarasota",
    # Hillsborough County
    range(33601, 33698): "Hillsborough",
    # Pinellas County
    range(33701, 33786): "Pinellas",
    # Polk County
    range(33801, 33870): "Polk",
    # Orange County
    range(32789, 32840): "Orange",
    # Duval County (Jacksonville)
    range(32099, 32277): "Duval",
    # Broward County
    range(33301, 33399): "Broward",
    # Miami-Dade County
    range(33101, 33299): "Miami-Dade",
    # Palm Beach County
    range(33401, 33499): "Palm Beach",
    # Brevard County
    range(32901, 32976): "Brevard",
    # Volusia County
    range(32114, 32180): "Volusia",
    # Seminole County
    range(32701, 32779): "Seminole",
    # Leon County (Tallahassee)
    range(32301, 32399): "Leon",
    # Escambia County (Pensacola)
    range(32501, 32599): "Escambia",
}


def _fast_zip_to_county(zip_code: str) -> Optional[str]:
    """O(1) lookup of Florida zip → county from hardcoded ranges."""
    try:
        z = int(zip_code[:5])
        for r, county in FL_ZIP_COUNTY_FAST.items():
            if z in r:
                return county
    except (ValueError, TypeError):
        pass
    return None


class AddressValidatorService:
    """
    Multi-provider address validation and geo intelligence.

    Usage:
        svc = AddressValidatorService(db)
        result = await svc.validate_address("1528 Broadway, Ft Myers, FL 33901")
        county = await svc.zip_to_county("33901")
        distance = svc.haversine_distance(26.62, -81.87, 26.14, -81.79)
    """

    def __init__(self, db=None):
        self.db = db
        self._smarty_auth_id = os.environ.get("SMARTY_AUTH_ID", "")
        self._smarty_auth_token = os.environ.get("SMARTY_AUTH_TOKEN", "")
        # Rate limiter for Nominatim (max 1 req/sec per their policy)
        self._nominatim_lock = asyncio.Lock()
        self._last_nominatim_call = 0.0

    @property
    def smarty_available(self) -> bool:
        return bool(self._smarty_auth_id and self._smarty_auth_token)

    # ═══════════════════════════════════════════════════════════════
    #  ZIP → COUNTY (Critical for jurisdiction matching)
    # ═══════════════════════════════════════════════════════════════

    async def zip_to_county(self, zip_code: str) -> Dict:
        """
        Resolve a US zip code to county, city, state, and coordinates.
        Uses fast local lookup first, then Zippopotam, then Nominatim.

        Args:
            zip_code: 5-digit US zip code

        Returns:
            dict with county, city, state, lat, lon, fips, source
        """
        zip_code = re.sub(r"\D", "", str(zip_code))[:5]
        if len(zip_code) != 5:
            return {"success": False, "error": "Invalid zip code", "zip": zip_code}

        # Check cache
        if self.db:
            cached = await self.db["zip_lookups"].find_one({"zip": zip_code})
            if cached:
                cached.pop("_id", None)
                return {**cached, "cached": True}

        result = {"success": False, "zip": zip_code}

        # 1. Fast local lookup (instant, no API call)
        fast_county = _fast_zip_to_county(zip_code)

        # 2. Zippopotam.us — free, unlimited, gets city/state/coords
        zipp_data = await self._zippopotam_lookup(zip_code)

        # 3. If we need county and don't have it from fast lookup,
        #    use Nominatim reverse geocoding from Zippopotam coords
        county = fast_county
        nominatim_data = None

        if zipp_data and not county:
            lat = zipp_data.get("latitude")
            lon = zipp_data.get("longitude")
            if lat and lon:
                nominatim_data = await self._nominatim_reverse(float(lat), float(lon))
                if nominatim_data:
                    county = nominatim_data.get("county", "").replace(" County", "")

        if zipp_data:
            fips = FL_COUNTY_FIPS.get((county or "").lower(), "")
            result = {
                "success": True,
                "zip": zip_code,
                "city": zipp_data.get("city", ""),
                "state": zipp_data.get("state", ""),
                "state_abbr": zipp_data.get("state_abbr", ""),
                "county": county or "",
                "fips": fips,
                "latitude": float(zipp_data.get("latitude", 0)),
                "longitude": float(zipp_data.get("longitude", 0)),
                "source": "fast_lookup+zippopotam" if fast_county else "zippopotam+nominatim",
                "looked_up_at": datetime.now(timezone.utc).isoformat(),
            }
        elif fast_county:
            # At minimum we have the county from local lookup
            result = {
                "success": True,
                "zip": zip_code,
                "city": "",
                "state": "Florida",
                "state_abbr": "FL",
                "county": fast_county,
                "fips": FL_COUNTY_FIPS.get(fast_county.lower(), ""),
                "latitude": 0,
                "longitude": 0,
                "source": "fast_lookup_only",
                "looked_up_at": datetime.now(timezone.utc).isoformat(),
            }

        # Cache successful lookups
        if result.get("success") and self.db:
            await self.db["zip_lookups"].update_one(
                {"zip": zip_code},
                {"$set": result},
                upsert=True,
            )

        return result

    async def batch_zip_to_county(self, zip_codes: List[str]) -> Dict:
        """Batch resolve zip codes to counties. Max 50."""
        results = []
        county_counts = {}
        for z in zip_codes[:50]:
            r = await self.zip_to_county(z)
            results.append(r)
            if r.get("county"):
                c = r["county"]
                county_counts[c] = county_counts.get(c, 0) + 1
        return {
            "success": True,
            "total": len(results),
            "results": results,
            "county_distribution": county_counts,
        }

    # ═══════════════════════════════════════════════════════════════
    #  ADDRESS VALIDATION
    # ═══════════════════════════════════════════════════════════════

    async def validate_address(self, address: str, city: str = "",
                                state: str = "", zip_code: str = "") -> Dict:
        """
        Validate and standardize a US address.

        Provider waterfall:
          1. Smarty Streets (250/mo free) — USPS-certified, best data
          2. Nominatim/OSM (free, 1/sec) — geocoding + address parsing

        Returns:
            dict with standardized address, validity, components, lat/lon
        """
        full_input = f"{address} {city} {state} {zip_code}".strip()
        if not full_input or len(full_input) < 5:
            return {"success": False, "error": "Address too short", "input": full_input}

        # Check cache
        cache_key = re.sub(r"\s+", " ", full_input.lower().strip())
        if self.db:
            cached = await self.db["address_validations"].find_one({"cache_key": cache_key})
            if cached:
                cached.pop("_id", None)
                return {**cached, "cached": True}

        result = None

        # 1. Try Smarty Streets (best quality, USPS-certified)
        if self.smarty_available:
            result = await self._smarty_validate(address, city, state, zip_code)

        # 2. Fall back to Nominatim (free)
        if not result or not result.get("success"):
            result = await self._nominatim_geocode(full_input)

        if result and result.get("success"):
            result["cache_key"] = cache_key
            result["input"] = full_input
            result["validated_at"] = datetime.now(timezone.utc).isoformat()

            # Enrich with county from zip if we got one
            if result.get("zip") and not result.get("county"):
                zip_result = await self.zip_to_county(result["zip"])
                if zip_result.get("county"):
                    result["county"] = zip_result["county"]
                    result["fips"] = zip_result.get("fips", "")

            # Compute risk signals
            result["risk_signals"] = self._address_risk_signals(result)

            # Cache
            if self.db:
                await self.db["address_validations"].update_one(
                    {"cache_key": cache_key},
                    {"$set": result},
                    upsert=True,
                )

        return result or {"success": False, "error": "All providers failed", "input": full_input}

    async def _smarty_validate(self, street: str, city: str,
                                state: str, zip_code: str) -> Dict:
        """Smarty Streets US Street API — USPS-certified address validation."""
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                params = {
                    "auth-id": self._smarty_auth_id,
                    "auth-token": self._smarty_auth_token,
                    "street": street,
                    "city": city,
                    "state": state or "FL",
                    "zipcode": zip_code,
                    "candidates": "1",
                }
                async with session.get(SMARTY_STREET_API, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if not data:
                            return {"success": False, "error": "No match found", "provider": "smarty"}
                        match = data[0]
                        components = match.get("components", {})
                        metadata = match.get("metadata", {})
                        analysis = match.get("analysis", {})
                        return {
                            "success": True,
                            "provider": "smarty",
                            "valid": analysis.get("dpv_match_code", "") in ("Y", "S", "D"),
                            "delivery_line": match.get("delivery_line_1", ""),
                            "last_line": match.get("last_line", ""),
                            "street": components.get("primary_number", "") + " " + components.get("street_name", "") + " " + components.get("street_suffix", ""),
                            "city": components.get("city_name", ""),
                            "state": components.get("state_abbreviation", ""),
                            "zip": components.get("zipcode", ""),
                            "zip4": components.get("plus4_code", ""),
                            "county": metadata.get("county_name", ""),
                            "fips": metadata.get("county_fips", ""),
                            "latitude": metadata.get("latitude", 0),
                            "longitude": metadata.get("longitude", 0),
                            "rdi": metadata.get("rdi", ""),  # Residential Delivery Indicator
                            "record_type": metadata.get("record_type", ""),
                            "dpv_match": analysis.get("dpv_match_code", ""),
                            "dpv_footnotes": analysis.get("dpv_footnotes", ""),
                            "vacant": analysis.get("dpv_vacant", "") == "Y",
                            "active": analysis.get("active", "") == "Y",
                        }
                    elif resp.status == 401:
                        log.warning("[AddressValidator] Smarty auth failed — check SMARTY_AUTH_ID/TOKEN")
                        return {"success": False, "error": "auth_failed", "provider": "smarty"}
                    elif resp.status == 429:
                        log.warning("[AddressValidator] Smarty rate limited")
                        return {"success": False, "error": "rate_limited", "provider": "smarty"}
                    else:
                        return {"success": False, "error": f"HTTP {resp.status}", "provider": "smarty"}
        except Exception as e:
            log.error("[AddressValidator] Smarty error: %s", e)
            return {"success": False, "error": str(e), "provider": "smarty"}

    async def _nominatim_geocode(self, query: str) -> Dict:
        """Nominatim (OSM) forward geocoding — free, 1 req/sec."""
        await self._rate_limit_nominatim()
        try:
            async with aiohttp.ClientSession(
                headers=_NOMINATIM_HEADERS, timeout=_TIMEOUT
            ) as session:
                params = {
                    "q": query,
                    "format": "jsonv2",
                    "addressdetails": "1",
                    "countrycodes": "us",
                    "limit": "1",
                }
                async with session.get(NOMINATIM_SEARCH, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if not data:
                            return {"success": False, "error": "No results", "provider": "nominatim"}
                        hit = data[0]
                        addr = hit.get("address", {})
                        county_raw = addr.get("county", "")
                        county = county_raw.replace(" County", "").strip()
                        return {
                            "success": True,
                            "provider": "nominatim",
                            "valid": True,  # Nominatim found it
                            "delivery_line": hit.get("display_name", "").split(",")[0],
                            "last_line": f"{addr.get('city', addr.get('town', addr.get('village', '')))} {addr.get('state', '')} {addr.get('postcode', '')}",
                            "street": addr.get("road", ""),
                            "house_number": addr.get("house_number", ""),
                            "city": addr.get("city", addr.get("town", addr.get("village", ""))),
                            "state": addr.get("state", ""),
                            "zip": addr.get("postcode", ""),
                            "county": county,
                            "fips": FL_COUNTY_FIPS.get(county.lower(), ""),
                            "latitude": float(hit.get("lat", 0)),
                            "longitude": float(hit.get("lon", 0)),
                            "osm_type": hit.get("osm_type", ""),
                            "osm_id": hit.get("osm_id", ""),
                            "confidence": float(hit.get("importance", 0)),
                        }
                    return {"success": False, "error": f"HTTP {resp.status}", "provider": "nominatim"}
        except Exception as e:
            log.error("[AddressValidator] Nominatim geocode error: %s", e)
            return {"success": False, "error": str(e), "provider": "nominatim"}

    async def _nominatim_reverse(self, lat: float, lon: float) -> Optional[Dict]:
        """Nominatim reverse geocoding — coordinates → address + county."""
        await self._rate_limit_nominatim()
        try:
            async with aiohttp.ClientSession(
                headers=_NOMINATIM_HEADERS, timeout=_TIMEOUT
            ) as session:
                params = {
                    "lat": str(lat),
                    "lon": str(lon),
                    "format": "jsonv2",
                    "addressdetails": "1",
                }
                async with session.get(NOMINATIM_REVERSE, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        addr = data.get("address", {})
                        return {
                            "county": addr.get("county", ""),
                            "city": addr.get("city", addr.get("town", "")),
                            "state": addr.get("state", ""),
                            "zip": addr.get("postcode", ""),
                        }
        except Exception as e:
            log.debug("[AddressValidator] Nominatim reverse error: %s", e)
        return None

    async def _zippopotam_lookup(self, zip_code: str) -> Optional[Dict]:
        """Zippopotam.us — free unlimited zip code lookup."""
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                url = f"{ZIPPOPOTAM_BASE}/{zip_code}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        places = data.get("places", [])
                        if places:
                            p = places[0]
                            return {
                                "city": p.get("place name", ""),
                                "state": p.get("state", ""),
                                "state_abbr": p.get("state abbreviation", ""),
                                "latitude": p.get("latitude", ""),
                                "longitude": p.get("longitude", ""),
                            }
        except Exception as e:
            log.debug("[AddressValidator] Zippopotam error: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════
    #  ADDRESS AUTOCOMPLETE
    # ═══════════════════════════════════════════════════════════════

    async def autocomplete(self, prefix: str, state: str = "FL") -> Dict:
        """
        Address autocomplete suggestions as user types.

        Uses Smarty Autocomplete Pro if available (250/mo free),
        otherwise returns empty (no free alternative for autocomplete).
        """
        if not prefix or len(prefix) < 3:
            return {"success": True, "suggestions": [], "provider": "none"}

        if self.smarty_available:
            return await self._smarty_autocomplete(prefix, state)

        return {"success": True, "suggestions": [], "provider": "none",
                "note": "Set SMARTY_AUTH_ID and SMARTY_AUTH_TOKEN for autocomplete"}

    async def _smarty_autocomplete(self, prefix: str, state: str) -> Dict:
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                params = {
                    "auth-id": self._smarty_auth_id,
                    "auth-token": self._smarty_auth_token,
                    "search": prefix,
                    "include_only_states": state,
                    "max_results": "8",
                    "prefer_geolocation": "none",
                }
                async with session.get(SMARTY_AUTOCOMPLETE_API, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        suggestions = []
                        for s in data.get("suggestions", []):
                            suggestions.append({
                                "street_line": s.get("street_line", ""),
                                "secondary": s.get("secondary", ""),
                                "city": s.get("city", ""),
                                "state": s.get("state", ""),
                                "zipcode": s.get("zipcode", ""),
                                "entries": s.get("entries", 0),
                            })
                        return {"success": True, "suggestions": suggestions, "provider": "smarty"}
                    return {"success": False, "error": f"HTTP {resp.status}", "provider": "smarty"}
        except Exception as e:
            log.error("[AddressValidator] Smarty autocomplete error: %s", e)
            return {"success": False, "error": str(e), "provider": "smarty"}

    # ═══════════════════════════════════════════════════════════════
    #  ADDRESS EXTRACTION FROM TEXT
    # ═══════════════════════════════════════════════════════════════

    async def extract_addresses(self, text: str) -> Dict:
        """
        Extract postal addresses from freeform text (court docs, SMS, emails).
        Uses regex patterns for Florida addresses. Smarty Extract API if available.
        """
        addresses = []

        # Regex-based extraction (always available)
        fl_patterns = [
            # Standard: 1234 Main St, City, FL 33901
            r"(\d+\s+[\w\s\.]+(?:St|Ave|Dr|Rd|Blvd|Ln|Ct|Way|Cir|Pl|Ter|Hwy|Pkwy|Loop|Trail|Run|Pass|Pt|Cv|Bay|Key)[\w\s\.]*,?\s*[\w\s]+,?\s*FL\s*\d{5}(?:-\d{4})?)",
            # PO Box
            r"(P\.?O\.?\s*Box\s+\d+[\w\s,]*FL\s*\d{5}(?:-\d{4})?)",
        ]
        for pat in fl_patterns:
            for match in re.findall(pat, text, re.IGNORECASE):
                clean = re.sub(r"\s+", " ", match.strip())
                if len(clean) > 10:
                    addresses.append({
                        "raw": clean,
                        "source": "regex",
                        "confidence": 0.7,
                    })

        # If Smarty Extract is available, use it for better results
        if self.smarty_available and text and len(text) > 20:
            try:
                smarty_addrs = await self._smarty_extract(text)
                addresses.extend(smarty_addrs)
            except Exception as e:
                log.debug("[AddressValidator] Smarty extract error: %s", e)

        # Deduplicate
        seen = set()
        unique = []
        for a in addresses:
            key = re.sub(r"\s+", "", a["raw"].lower())
            if key not in seen:
                seen.add(key)
                unique.append(a)

        return {
            "success": True,
            "addresses_found": len(unique),
            "addresses": unique,
        }

    async def _smarty_extract(self, text: str) -> List[Dict]:
        """Smarty Extract API — pull addresses from freeform text."""
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                params = {
                    "auth-id": self._smarty_auth_id,
                    "auth-token": self._smarty_auth_token,
                }
                async with session.post(
                    SMARTY_EXTRACT_API, params=params,
                    data=text, headers={"Content-Type": "text/plain"}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = []
                        for addr in data.get("addresses", []):
                            results.append({
                                "raw": addr.get("text", ""),
                                "verified": bool(addr.get("api_output", [])),
                                "source": "smarty_extract",
                                "confidence": 0.95 if addr.get("api_output") else 0.6,
                            })
                        return results
        except Exception:
            pass
        return []

    # ═══════════════════════════════════════════════════════════════
    #  DISTANCE CALCULATION
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculate distance between two points in miles using Haversine formula.
        Useful for bail condition distance checks (e.g. "must stay within 50mi").
        """
        R = 3959  # Earth radius in miles
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    async def distance_from_office(self, zip_code: str) -> Dict:
        """
        Calculate distance from Shamrock office (1528 Broadway, Ft Myers 33901).
        Used for bail condition proximity checks.
        """
        OFFICE_LAT = 26.6204
        OFFICE_LON = -81.8725

        zip_data = await self.zip_to_county(zip_code)
        if not zip_data.get("success") or not zip_data.get("latitude"):
            return {"success": False, "error": "Could not geocode zip code"}

        dist = self.haversine_distance(
            OFFICE_LAT, OFFICE_LON,
            zip_data["latitude"], zip_data["longitude"],
        )

        return {
            "success": True,
            "from_zip": "33901",
            "to_zip": zip_code,
            "distance_miles": round(dist, 1),
            "to_city": zip_data.get("city", ""),
            "to_county": zip_data.get("county", ""),
            "within_50_miles": dist <= 50,
            "within_100_miles": dist <= 100,
        }

    # ═══════════════════════════════════════════════════════════════
    #  RISK SIGNALS
    # ═══════════════════════════════════════════════════════════════

    def _address_risk_signals(self, result: Dict) -> List[str]:
        """Compute address-based risk signals for The Analyst."""
        signals = []

        if not result.get("valid"):
            signals.append("invalid_address")
            return signals

        # Vacant address
        if result.get("vacant"):
            signals.append("vacant_address")

        # PO Box (can't verify physical residence)
        street = (result.get("street") or result.get("delivery_line") or "").lower()
        if "po box" in street or "p.o." in street:
            signals.append("po_box_address")

        # Out-of-state
        state = (result.get("state") or "").upper()
        if state and state not in ("FL", "FLORIDA"):
            signals.append("out_of_state")

        # Not in our service counties
        county = (result.get("county") or "").lower()
        our_counties = ["lee", "collier", "charlotte", "hendry", "desoto", "manatee", "sarasota"]
        if county and county not in our_counties:
            signals.append("outside_service_area")

        provider = result.get("provider", "")
        if provider == "smarty":
            # Smarty-specific signals
            rdi = (result.get("rdi") or "").upper()
            if rdi == "Commercial":
                signals.append("commercial_address")
            dpv = result.get("dpv_match", "")
            if dpv == "N":
                signals.append("no_mail_delivery")
        elif provider == "nominatim":
            # Lower confidence from Nominatim
            if result.get("confidence", 1.0) < 0.5:
                signals.append("low_geocode_confidence")

        return signals

    # ═══════════════════════════════════════════════════════════════
    #  RATE LIMITING
    # ═══════════════════════════════════════════════════════════════

    async def _rate_limit_nominatim(self):
        """Enforce 1 req/sec for Nominatim per their usage policy."""
        async with self._nominatim_lock:
            import time
            now = time.time()
            elapsed = now - self._last_nominatim_call
            if elapsed < 1.1:
                await asyncio.sleep(1.1 - elapsed)
            self._last_nominatim_call = time.time()
