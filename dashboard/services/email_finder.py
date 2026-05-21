"""
Email Finder Service — ShamrockLeads Tier 1 API Integration

Discovers professional emails for criminal defense attorneys by law firm domain.
Uses Tomba.io (primary) and Hunter.io (backup) APIs in waterfall pattern.

Free Tier Limits (tracked internally):
  - Tomba:  25 finder / 50 verifier per month
  - Hunter: 25 domain searches / 50 email verifications per month

Integration: Feeds attorney contact data into MongoDB `attorney_contacts` collection
for The Closer's referral partnership outreach campaigns.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp

log = logging.getLogger("shamrock.email_finder")

# ── API Endpoints ──
TOMBA_DOMAIN_SEARCH = "https://api.tomba.io/v1/domain-search"
TOMBA_EMAIL_FINDER = "https://api.tomba.io/v1/email-finder"
TOMBA_EMAIL_VERIFY = "https://api.tomba.io/v1/email-verifier"

HUNTER_DOMAIN_SEARCH = "https://api.hunter.io/v2/domain-search"
HUNTER_EMAIL_FINDER = "https://api.hunter.io/v2/email-finder"
HUNTER_EMAIL_VERIFY = "https://api.hunter.io/v2/email-verifier"

_TIMEOUT = aiohttp.ClientTimeout(total=15)


class EmailFinderService:
    """
    Multi-provider email finder for attorney harvesting and email verification.

    Usage:
        svc = EmailFinderService(db)
        results = await svc.search_domain("smithlawfirm.com")
        verified = await svc.verify_email("john@smithlawfirm.com")
        found = await svc.find_email("smithlawfirm.com", "John", "Smith")
    """

    def __init__(self, db=None):
        self.db = db
        # Tomba credentials
        self._tomba_key = os.environ.get("TOMBA_API_KEY", "")
        self._tomba_secret = os.environ.get("TOMBA_API_SECRET", "")
        # Hunter credentials
        self._hunter_key = os.environ.get("HUNTER_API_KEY", "")

    @property
    def tomba_available(self) -> bool:
        return bool(self._tomba_key and self._tomba_secret)

    @property
    def hunter_available(self) -> bool:
        return bool(self._hunter_key)

    def _tomba_headers(self) -> Dict:
        return {
            "X-Tomba-Key": self._tomba_key,
            "X-Tomba-Secret": self._tomba_secret,
            "Accept": "application/json",
        }

    # ── Domain Search (find all emails at a domain) ──

    async def search_domain(self, domain: str) -> Dict:
        """
        Search a domain for all associated email addresses.
        Tries Tomba first, falls back to Hunter.

        Args:
            domain: Company domain (e.g. "smithlawfirm.com")

        Returns:
            dict with emails list, domain info, source provider
        """
        domain = domain.strip().lower()
        if not domain or "." not in domain:
            return {"success": False, "error": "Invalid domain"}

        # Check cache first
        if self.db:
            cached = await self.db["domain_searches"].find_one({
                "domain": domain,
                "searched_at": {"$gte": datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).isoformat()}
            })
            if cached:
                cached.pop("_id", None)
                return {**cached, "cached": True}

        # Try Tomba first
        if self.tomba_available:
            result = await self._tomba_domain_search(domain)
            if result.get("success"):
                await self._cache_result("domain_searches", domain, result)
                return result

        # Fall back to Hunter
        if self.hunter_available:
            result = await self._hunter_domain_search(domain)
            if result.get("success"):
                await self._cache_result("domain_searches", domain, result)
                return result

        return {"success": False, "error": "No API keys configured or both APIs failed"}

    async def _tomba_domain_search(self, domain: str) -> Dict:
        """Tomba domain search — finds all emails at a domain."""
        try:
            async with aiohttp.ClientSession(
                headers=self._tomba_headers(), timeout=_TIMEOUT
            ) as session:
                params = {"domain": domain}
                async with session.get(TOMBA_DOMAIN_SEARCH, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        d = data.get("data", {})
                        emails = []
                        for e in d.get("emails", []):
                            emails.append({
                                "email": e.get("email", ""),
                                "first_name": e.get("first_name", ""),
                                "last_name": e.get("last_name", ""),
                                "position": e.get("position", ""),
                                "department": e.get("department", ""),
                                "type": e.get("type", ""),
                                "confidence": e.get("confidence", 0),
                                "sources": e.get("sources", []),
                            })
                        return {
                            "success": True,
                            "provider": "tomba",
                            "domain": domain,
                            "organization": d.get("organization", ""),
                            "emails": emails,
                            "total": len(emails),
                            "searched_at": datetime.now(timezone.utc).isoformat(),
                        }
                    elif resp.status == 429:
                        log.warning("[EmailFinder] Tomba rate limited for domain %s", domain)
                        return {"success": False, "error": "rate_limited", "provider": "tomba"}
                    else:
                        body = await resp.text()
                        log.warning("[EmailFinder] Tomba domain search failed (%d): %s", resp.status, body[:200])
                        return {"success": False, "error": f"HTTP {resp.status}", "provider": "tomba"}
        except Exception as e:
            log.error("[EmailFinder] Tomba domain search error: %s", e)
            return {"success": False, "error": str(e), "provider": "tomba"}

    async def _hunter_domain_search(self, domain: str) -> Dict:
        """Hunter.io domain search — finds all emails at a domain."""
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                params = {"domain": domain, "api_key": self._hunter_key}
                async with session.get(HUNTER_DOMAIN_SEARCH, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        d = data.get("data", {})
                        emails = []
                        for e in d.get("emails", []):
                            emails.append({
                                "email": e.get("value", ""),
                                "first_name": e.get("first_name", ""),
                                "last_name": e.get("last_name", ""),
                                "position": e.get("position", ""),
                                "department": e.get("department", ""),
                                "type": e.get("type", ""),
                                "confidence": e.get("confidence", 0),
                                "sources": [s.get("domain", "") for s in e.get("sources", [])],
                            })
                        return {
                            "success": True,
                            "provider": "hunter",
                            "domain": domain,
                            "organization": d.get("organization", ""),
                            "emails": emails,
                            "total": len(emails),
                            "searched_at": datetime.now(timezone.utc).isoformat(),
                        }
                    elif resp.status == 429:
                        log.warning("[EmailFinder] Hunter rate limited for domain %s", domain)
                        return {"success": False, "error": "rate_limited", "provider": "hunter"}
                    else:
                        body = await resp.text()
                        log.warning("[EmailFinder] Hunter domain search failed (%d): %s", resp.status, body[:200])
                        return {"success": False, "error": f"HTTP {resp.status}", "provider": "hunter"}
        except Exception as e:
            log.error("[EmailFinder] Hunter domain search error: %s", e)
            return {"success": False, "error": str(e), "provider": "hunter"}

    # ── Email Finder (find specific person's email at a domain) ──

    async def find_email(self, domain: str, first_name: str, last_name: str) -> Dict:
        """
        Find a specific person's email address at a domain.

        Args:
            domain: Company domain
            first_name: Person's first name
            last_name: Person's last name

        Returns:
            dict with email, confidence, source
        """
        domain = domain.strip().lower()
        first_name = first_name.strip()
        last_name = last_name.strip()

        if not all([domain, first_name, last_name]):
            return {"success": False, "error": "Missing required fields"}

        # Try Tomba first
        if self.tomba_available:
            result = await self._tomba_find_email(domain, first_name, last_name)
            if result.get("success"):
                return result

        # Fall back to Hunter
        if self.hunter_available:
            result = await self._hunter_find_email(domain, first_name, last_name)
            if result.get("success"):
                return result

        return {"success": False, "error": "No API keys configured or both APIs failed"}

    async def _tomba_find_email(self, domain: str, first: str, last: str) -> Dict:
        try:
            async with aiohttp.ClientSession(
                headers=self._tomba_headers(), timeout=_TIMEOUT
            ) as session:
                params = {"domain": domain, "first_name": first, "last_name": last}
                async with session.get(TOMBA_EMAIL_FINDER, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        e = data.get("data", {}).get("email", "")
                        return {
                            "success": bool(e),
                            "provider": "tomba",
                            "email": e,
                            "confidence": data.get("data", {}).get("score", 0),
                            "first_name": first,
                            "last_name": last,
                            "domain": domain,
                        }
                    return {"success": False, "error": f"HTTP {resp.status}", "provider": "tomba"}
        except Exception as e:
            log.error("[EmailFinder] Tomba find error: %s", e)
            return {"success": False, "error": str(e), "provider": "tomba"}

    async def _hunter_find_email(self, domain: str, first: str, last: str) -> Dict:
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                params = {
                    "domain": domain,
                    "first_name": first,
                    "last_name": last,
                    "api_key": self._hunter_key,
                }
                async with session.get(HUNTER_EMAIL_FINDER, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        d = data.get("data", {})
                        return {
                            "success": bool(d.get("email")),
                            "provider": "hunter",
                            "email": d.get("email", ""),
                            "confidence": d.get("score", 0),
                            "first_name": first,
                            "last_name": last,
                            "domain": domain,
                        }
                    return {"success": False, "error": f"HTTP {resp.status}", "provider": "hunter"}
        except Exception as e:
            log.error("[EmailFinder] Hunter find error: %s", e)
            return {"success": False, "error": str(e), "provider": "hunter"}

    # ── Email Verification ──

    async def verify_email(self, email: str) -> Dict:
        """
        Verify if an email address is valid and deliverable.
        Uses multiple free providers in waterfall:
          1. Tomba verify (50/mo free)
          2. Hunter verify (50/mo free)
          3. EVA (unlimited free)
          4. Kickbox (unlimited free)
          5. Disify (unlimited free — disposable detection)

        Returns:
            dict with valid, deliverable, disposable, provider
        """
        email = email.strip().lower()
        if not email or "@" not in email:
            return {"success": False, "error": "Invalid email format", "email": email}

        # Check cache
        if self.db:
            cached = await self.db["email_verifications"].find_one({"email": email})
            if cached:
                cached.pop("_id", None)
                return {**cached, "cached": True}

        # 1. Try Tomba verify
        if self.tomba_available:
            result = await self._tomba_verify(email)
            if result.get("success"):
                await self._cache_verification(email, result)
                return result

        # 2. Try Hunter verify
        if self.hunter_available:
            result = await self._hunter_verify(email)
            if result.get("success"):
                await self._cache_verification(email, result)
                return result

        # 3. Free APIs (no key needed)
        # EVA — unlimited free
        result = await self._eva_verify(email)
        if result.get("success"):
            await self._cache_verification(email, result)
            return result

        # 4. Kickbox — unlimited free
        result = await self._kickbox_verify(email)
        if result.get("success"):
            await self._cache_verification(email, result)
            return result

        # 5. Disify — unlimited free (disposable detection only)
        result = await self._disify_verify(email)
        if result.get("success"):
            await self._cache_verification(email, result)
            return result

        return {"success": False, "error": "All verification providers failed", "email": email}

    async def _tomba_verify(self, email: str) -> Dict:
        try:
            async with aiohttp.ClientSession(
                headers=self._tomba_headers(), timeout=_TIMEOUT
            ) as session:
                params = {"email": email}
                async with session.get(TOMBA_EMAIL_VERIFY, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        d = data.get("data", {}).get("email", {})
                        return {
                            "success": True,
                            "provider": "tomba",
                            "email": email,
                            "deliverable": d.get("result", "") == "deliverable",
                            "valid": d.get("result", "") != "undeliverable",
                            "disposable": d.get("disposable", False),
                            "free_provider": d.get("free", False),
                            "mx_found": d.get("mx_found", False),
                            "score": d.get("score", 0),
                            "verified_at": datetime.now(timezone.utc).isoformat(),
                        }
                    return {"success": False, "provider": "tomba"}
        except Exception as e:
            log.debug("[EmailFinder] Tomba verify error: %s", e)
            return {"success": False, "provider": "tomba"}

    async def _hunter_verify(self, email: str) -> Dict:
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                params = {"email": email, "api_key": self._hunter_key}
                async with session.get(HUNTER_EMAIL_VERIFY, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        d = data.get("data", {})
                        return {
                            "success": True,
                            "provider": "hunter",
                            "email": email,
                            "deliverable": d.get("result", "") == "deliverable",
                            "valid": d.get("result", "") != "undeliverable",
                            "disposable": d.get("disposable", False),
                            "free_provider": d.get("webmail", False),
                            "mx_found": d.get("mx_records", False),
                            "score": d.get("score", 0),
                            "verified_at": datetime.now(timezone.utc).isoformat(),
                        }
                    return {"success": False, "provider": "hunter"}
        except Exception as e:
            log.debug("[EmailFinder] Hunter verify error: %s", e)
            return {"success": False, "provider": "hunter"}

    async def _eva_verify(self, email: str) -> Dict:
        """EVA — free unlimited email validation."""
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                url = f"https://api.eva.pingutil.com/email?email={email}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        d = data.get("data", {})
                        return {
                            "success": True,
                            "provider": "eva",
                            "email": email,
                            "deliverable": d.get("deliverable_email", False),
                            "valid": d.get("valid_syntax", False),
                            "disposable": d.get("disposable_email", False),
                            "free_provider": d.get("gibberish_email", False),
                            "mx_found": d.get("mx_found", False),
                            "spam_score": d.get("spam_score", 0),
                            "verified_at": datetime.now(timezone.utc).isoformat(),
                        }
                    return {"success": False, "provider": "eva"}
        except Exception as e:
            log.debug("[EmailFinder] EVA verify error: %s", e)
            return {"success": False, "provider": "eva"}

    async def _kickbox_verify(self, email: str) -> Dict:
        """Kickbox — free open-source email validation."""
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                url = f"https://open.kickbox.com/v1/disposable/{email}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "success": True,
                            "provider": "kickbox",
                            "email": email,
                            "disposable": data.get("disposable", False),
                            "valid": not data.get("disposable", True),
                            "deliverable": None,  # Kickbox free only checks disposable
                            "verified_at": datetime.now(timezone.utc).isoformat(),
                        }
                    return {"success": False, "provider": "kickbox"}
        except Exception as e:
            log.debug("[EmailFinder] Kickbox verify error: %s", e)
            return {"success": False, "provider": "kickbox"}

    async def _disify_verify(self, email: str) -> Dict:
        """Disify — free unlimited disposable email detection."""
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                url = f"https://www.disify.com/api/email/{email}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "success": True,
                            "provider": "disify",
                            "email": email,
                            "disposable": data.get("disposable", False),
                            "valid": data.get("format", False),
                            "dns_valid": data.get("dns", False),
                            "verified_at": datetime.now(timezone.utc).isoformat(),
                        }
                    return {"success": False, "provider": "disify"}
        except Exception as e:
            log.debug("[EmailFinder] Disify verify error: %s", e)
            return {"success": False, "provider": "disify"}

    # ── Usage Tracking ──

    async def get_usage(self) -> Dict:
        """Get current API usage stats from providers."""
        usage = {"tomba": None, "hunter": None}

        if self.tomba_available:
            try:
                async with aiohttp.ClientSession(
                    headers=self._tomba_headers(), timeout=_TIMEOUT
                ) as session:
                    async with session.get("https://api.tomba.io/v1/usage") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            usage["tomba"] = data.get("data", {})
            except Exception as e:
                log.debug("[EmailFinder] Tomba usage check error: %s", e)

        if self.hunter_available:
            try:
                async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                    params = {"api_key": self._hunter_key}
                    async with session.get(
                        "https://api.hunter.io/v2/account", params=params
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            d = data.get("data", {})
                            usage["hunter"] = {
                                "searches_used": d.get("requests", {}).get("searches", {}).get("used", 0),
                                "searches_available": d.get("requests", {}).get("searches", {}).get("available", 0),
                                "verifications_used": d.get("requests", {}).get("verifications", {}).get("used", 0),
                                "verifications_available": d.get("requests", {}).get("verifications", {}).get("available", 0),
                            }
            except Exception as e:
                log.debug("[EmailFinder] Hunter usage check error: %s", e)

        return usage

    # ── Attorney Harvester ──

    async def harvest_attorneys(self, domains: List[str]) -> Dict:
        """
        Batch-harvest attorney emails from a list of law firm domains.
        Stores results in MongoDB `attorney_contacts` collection.

        Args:
            domains: List of law firm domains (e.g. ["smithlaw.com", "jonesdefense.com"])

        Returns:
            dict with total found, by domain results
        """
        results = []
        total_found = 0

        for domain in domains:
            search_result = await self.search_domain(domain)
            entry = {
                "domain": domain,
                "success": search_result.get("success", False),
                "organization": search_result.get("organization", ""),
                "emails_found": search_result.get("total", 0),
                "emails": search_result.get("emails", []),
                "provider": search_result.get("provider", ""),
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }
            results.append(entry)
            total_found += entry["emails_found"]

            # Store each found attorney contact
            if self.db and entry["emails"]:
                for email_data in entry["emails"]:
                    await self.db["attorney_contacts"].update_one(
                        {"email": email_data["email"]},
                        {"$set": {
                            **email_data,
                            "domain": domain,
                            "organization": entry["organization"],
                            "harvested_at": datetime.now(timezone.utc).isoformat(),
                            "outreach_status": "new",
                        }},
                        upsert=True,
                    )

            # Rate limit between domains (be nice to free tiers)
            await asyncio.sleep(1)

        return {
            "success": True,
            "domains_searched": len(domains),
            "total_emails_found": total_found,
            "results": results,
        }

    # ── Cache Helpers ──

    async def _cache_result(self, collection: str, domain: str, result: Dict):
        if self.db:
            result_copy = {**result}
            result_copy["domain"] = domain
            await self.db[collection].update_one(
                {"domain": domain},
                {"$set": result_copy},
                upsert=True,
            )

    async def _cache_verification(self, email: str, result: Dict):
        if self.db:
            result_copy = {**result}
            result_copy["email"] = email
            await self.db["email_verifications"].update_one(
                {"email": email},
                {"$set": result_copy},
                upsert=True,
            )
