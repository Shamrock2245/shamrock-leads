"""
Enrichment API Router — ShamrockLeads Tier 1 API Stack

Exposes email finding, phone validation, and email verification endpoints
to the dashboard frontend. All services use free-tier API keys with
built-in rate limiting awareness and MongoDB caching.

Endpoints:
  POST /api/enrichment/email/search-domain     → Search domain for emails
  POST /api/enrichment/email/find               → Find specific person's email
  POST /api/enrichment/email/verify             → Verify an email address
  POST /api/enrichment/email/harvest-attorneys  → Batch harvest attorney emails
  GET  /api/enrichment/email/usage              → API usage stats
  GET  /api/enrichment/email/attorneys           → List harvested attorneys
  POST /api/enrichment/phone/validate           → Validate a phone number
  POST /api/enrichment/phone/validate-batch     → Validate multiple phones
  GET  /api/enrichment/phone/stats              → Validation stats
  POST /api/enrichment/address/validate         → Validate & standardize address
  POST /api/enrichment/address/zip-to-county    → Zip code → county + geo
  POST /api/enrichment/address/batch-zip        → Batch zip → county
  GET  /api/enrichment/address/autocomplete     → Address autocomplete (Smarty)
  POST /api/enrichment/address/extract          → Extract addresses from text
  POST /api/enrichment/address/distance         → Distance from Shamrock office
  GET  /api/enrichment/status                   → All provider health status
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from dashboard.deps import get_db

log = logging.getLogger("shamrock.enrichment_api")

router = APIRouter(prefix="/api/enrichment", tags=["enrichment"])


# ── Request Models ──

class DomainSearchRequest(BaseModel):
    domain: str = Field(..., description="Company domain to search (e.g. smithlawfirm.com)")

class EmailFindRequest(BaseModel):
    domain: str = Field(..., description="Company domain")
    first_name: str = Field(..., description="Person's first name")
    last_name: str = Field(..., description="Person's last name")

class EmailVerifyRequest(BaseModel):
    email: str = Field(..., description="Email address to verify")

class AttorneyHarvestRequest(BaseModel):
    domains: List[str] = Field(..., description="List of law firm domains to harvest")

class PhoneValidateRequest(BaseModel):
    phone: str = Field(..., description="Phone number in any format")
    skip_cache: bool = Field(False, description="Force fresh lookup (burns a free-tier credit)")

class PhoneBatchRequest(BaseModel):
    phones: List[str] = Field(..., description="List of phone numbers (max 25)")
    skip_cache: bool = Field(False, description="Force fresh lookups")

class AddressValidateRequest(BaseModel):
    address: str = Field(..., description="Street address (e.g. 1528 Broadway)")
    city: str = Field("", description="City")
    state: str = Field("FL", description="State (default: FL)")
    zip_code: str = Field("", description="Zip code")

class ZipLookupRequest(BaseModel):
    zip_code: str = Field(..., description="5-digit US zip code")

class BatchZipRequest(BaseModel):
    zip_codes: List[str] = Field(..., description="List of zip codes (max 50)")

class AddressExtractRequest(BaseModel):
    text: str = Field(..., description="Text containing addresses to extract")

class DistanceRequest(BaseModel):
    zip_code: str = Field(..., description="Zip code to measure distance from office")


# ── Lazy Service Init ──

_email_finder = None
_phone_validator = None
_address_validator = None


def _get_email_finder():
    global _email_finder
    if _email_finder is None:
        from dashboard.services.email_finder import EmailFinderService
        _email_finder = EmailFinderService(get_db())
    return _email_finder


def _get_phone_validator():
    global _phone_validator
    if _phone_validator is None:
        from dashboard.services.phone_validator import PhoneValidatorService
        _phone_validator = PhoneValidatorService(get_db())
    return _phone_validator


def _get_address_validator():
    global _address_validator
    if _address_validator is None:
        from dashboard.services.address_validator import AddressValidatorService
        _address_validator = AddressValidatorService(get_db())
    return _address_validator


# ═══════════════════════════════════════════════════════════════════════
#  EMAIL FINDING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════

@router.post("/email/search-domain")
async def search_domain(body: DomainSearchRequest):
    """Search a domain for all associated email addresses."""
    svc = _get_email_finder()
    result = await svc.search_domain(body.domain)
    return result


@router.post("/email/find")
async def find_email(body: EmailFindRequest):
    """Find a specific person's email at a domain."""
    svc = _get_email_finder()
    result = await svc.find_email(body.domain, body.first_name, body.last_name)
    return result


@router.post("/email/verify")
async def verify_email(body: EmailVerifyRequest):
    """Verify if an email address is valid and deliverable."""
    svc = _get_email_finder()
    result = await svc.verify_email(body.email)
    return result


@router.post("/email/harvest-attorneys")
async def harvest_attorneys(body: AttorneyHarvestRequest):
    """Batch-harvest attorney emails from law firm domains."""
    svc = _get_email_finder()
    # Cap at 10 domains per request to protect free tier
    domains = body.domains[:10]
    result = await svc.harvest_attorneys(domains)
    return result


@router.get("/email/usage")
async def email_usage():
    """Get current API usage stats for email finding providers."""
    svc = _get_email_finder()
    usage = await svc.get_usage()
    return {
        "success": True,
        "usage": usage,
        "limits": {
            "tomba": {"finder": "25/mo", "verifier": "50/mo"},
            "hunter": {"searches": "25/mo", "verifications": "50/mo"},
            "eva": "unlimited",
            "kickbox": "unlimited",
            "disify": "unlimited",
        },
    }


@router.get("/email/attorneys")
async def list_attorneys(skip: int = 0, limit: int = 50,
                         domain: Optional[str] = None, status: Optional[str] = None):
    """List harvested attorney contacts from MongoDB."""
    db = get_db()
    if not db:
        return {"success": False, "error": "Database not available"}

    query = {}
    if domain:
        query["domain"] = domain
    if status:
        query["outreach_status"] = status

    cursor = db["attorney_contacts"].find(query).skip(skip).limit(limit).sort("harvested_at", -1)
    attorneys = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        attorneys.append(doc)

    total = await db["attorney_contacts"].count_documents(query)

    return {
        "success": True,
        "attorneys": attorneys,
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/email/attorney-domains")
async def list_attorney_domains():
    """Get unique domains that have been harvested."""
    db = get_db()
    if not db:
        return {"success": False, "error": "Database not available"}

    pipeline = [
        {"$group": {
            "_id": "$domain",
            "count": {"$sum": 1},
            "organization": {"$first": "$organization"},
            "last_harvested": {"$max": "$harvested_at"},
        }},
        {"$sort": {"count": -1}},
    ]
    domains = []
    async for doc in db["attorney_contacts"].aggregate(pipeline):
        domains.append({
            "domain": doc["_id"],
            "email_count": doc["count"],
            "organization": doc.get("organization", ""),
            "last_harvested": doc.get("last_harvested", ""),
        })

    return {"success": True, "domains": domains, "total": len(domains)}


# ═══════════════════════════════════════════════════════════════════════
#  PHONE VALIDATION ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════

@router.post("/phone/validate")
async def validate_phone(body: PhoneValidateRequest):
    """Validate a single phone number with carrier and line type detection."""
    svc = _get_phone_validator()
    result = await svc.validate(body.phone, skip_cache=body.skip_cache)
    return result


@router.post("/phone/validate-batch")
async def validate_phone_batch(body: PhoneBatchRequest):
    """Validate multiple phone numbers (max 25 per request)."""
    svc = _get_phone_validator()
    result = await svc.validate_batch(body.phones, skip_cache=body.skip_cache)
    return result


@router.get("/phone/stats")
async def phone_stats():
    """Get phone validation statistics from cache."""
    db = get_db()
    if not db:
        return {"success": False, "error": "Database not available"}

    total = await db["phone_validations"].count_documents({})
    valid = await db["phone_validations"].count_documents({"valid": True})
    invalid = await db["phone_validations"].count_documents({"valid": False})

    # Line type breakdown
    pipeline = [
        {"$match": {"valid": True}},
        {"$group": {"_id": "$line_type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    line_types = {}
    async for doc in db["phone_validations"].aggregate(pipeline):
        line_types[doc["_id"] or "unknown"] = doc["count"]

    # Provider breakdown
    provider_pipeline = [
        {"$group": {"_id": "$provider", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    providers = {}
    async for doc in db["phone_validations"].aggregate(provider_pipeline):
        providers[doc["_id"] or "unknown"] = doc["count"]

    # Risk signal breakdown
    risk_pipeline = [
        {"$unwind": "$risk_signals"},
        {"$group": {"_id": "$risk_signals", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    risk_signals = {}
    async for doc in db["phone_validations"].aggregate(risk_pipeline):
        risk_signals[doc["_id"]] = doc["count"]

    return {
        "success": True,
        "total_validated": total,
        "valid": valid,
        "invalid": invalid,
        "line_types": line_types,
        "providers": providers,
        "risk_signals": risk_signals,
    }


# ═══════════════════════════════════════════════════════════════════════
#  ADDRESS VALIDATION ENDPOINTS (Tier 2)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/address/zip-to-county")
async def zip_to_county(body: ZipLookupRequest):
    """Resolve a zip code to county, city, state, and coordinates."""
    svc = _get_address_validator()
    result = await svc.zip_to_county(body.zip_code)
    return result


@router.post("/address/batch-zip")
async def batch_zip(body: BatchZipRequest):
    """Batch-resolve zip codes to counties (max 50)."""
    svc = _get_address_validator()
    result = await svc.batch_zip_to_county(body.zip_codes)
    return result


@router.post("/address/validate")
async def validate_address(body: AddressValidateRequest):
    """Validate and standardize a US address with risk signal analysis."""
    svc = _get_address_validator()
    result = await svc.validate_address(
        address=body.address,
        city=body.city,
        state=body.state,
        zip_code=body.zip_code,
    )
    return result


@router.get("/address/autocomplete")
async def address_autocomplete(prefix: str, state: str = "FL"):
    """Real-time address autocomplete suggestions (requires Smarty API key)."""
    svc = _get_address_validator()
    result = await svc.autocomplete(prefix, state)
    return result


@router.post("/address/extract")
async def extract_addresses(body: AddressExtractRequest):
    """Extract postal addresses from freeform text (court docs, SMS, emails)."""
    svc = _get_address_validator()
    result = await svc.extract_addresses(body.text)
    return result


@router.post("/address/distance")
async def distance_from_office(body: DistanceRequest):
    """Calculate distance from Shamrock office (1528 Broadway, Ft Myers)."""
    svc = _get_address_validator()
    result = await svc.distance_from_office(body.zip_code)
    return result


# ═══════════════════════════════════════════════════════════════════════
#  SYSTEM STATUS
# ═══════════════════════════════════════════════════════════════════════

@router.get("/status")
async def enrichment_status():
    """Health check for all enrichment providers."""
    ef = _get_email_finder()
    pv = _get_phone_validator()
    av = _get_address_validator()

    db = get_db()
    db_connected = db is not None

    # Get cached counts
    email_cache_count = 0
    phone_cache_count = 0
    attorney_count = 0
    address_cache_count = 0
    zip_cache_count = 0
    if db:
        email_cache_count = await db["email_verifications"].count_documents({})
        phone_cache_count = await db["phone_validations"].count_documents({})
        attorney_count = await db["attorney_contacts"].count_documents({})
        address_cache_count = await db["address_validations"].count_documents({})
        zip_cache_count = await db["zip_lookups"].count_documents({})

    return {
        "success": True,
        "providers": {
            "tomba": {
                "configured": ef.tomba_available,
                "type": "email_finder",
                "free_limits": "25 searches + 50 verifications / month",
            },
            "hunter": {
                "configured": ef.hunter_available,
                "type": "email_finder",
                "free_limits": "25 searches + 50 verifications / month",
            },
            "veriphone": {
                "configured": pv.veriphone_available,
                "type": "phone_validator",
                "free_limits": "1,000 validations / month",
            },
            "numverify": {
                "configured": pv.numverify_available,
                "type": "phone_validator",
                "free_limits": "100 validations / month",
            },
            "zippopotam": {
                "configured": True,
                "type": "geo_lookup",
                "free_limits": "unlimited",
            },
            "nominatim": {
                "configured": True,
                "type": "geocoding",
                "free_limits": "unlimited (1 req/sec)",
            },
            "smarty": {
                "configured": av.smarty_available,
                "type": "address_validator",
                "free_limits": "250 lookups / month",
            },
            "eva": {"configured": True, "type": "email_verifier", "free_limits": "unlimited"},
            "kickbox": {"configured": True, "type": "email_verifier", "free_limits": "unlimited"},
            "disify": {"configured": True, "type": "email_verifier", "free_limits": "unlimited"},
        },
        "cache": {
            "db_connected": db_connected,
            "email_verifications_cached": email_cache_count,
            "phone_validations_cached": phone_cache_count,
            "attorney_contacts": attorney_count,
            "address_validations_cached": address_cache_count,
            "zip_lookups_cached": zip_cache_count,
        },
    }
