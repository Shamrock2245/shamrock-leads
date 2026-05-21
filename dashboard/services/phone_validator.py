"""
Phone Validator Service — ShamrockLeads Tier 1 API Integration

Validates phone numbers at intake to detect:
  - Landline vs. mobile (only send SMS to mobiles)
  - VOIP/burner phones (flight risk signal for The Analyst)
  - Invalid/disconnected numbers (don't waste Twilio credits)
  - Carrier info (helps iMessage routing for BlueBubbles)

Provider Waterfall (free tiers):
  1. Veriphone  — 1,000/mo free, line type detection, best data
  2. Numverify  — 100/mo free, carrier + line type

All results cached in MongoDB `phone_validations` collection to avoid
burning free-tier credits on repeat lookups.
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, Optional

import aiohttp

log = logging.getLogger("shamrock.phone_validator")

# ── API Endpoints ──
VERIPHONE_VERIFY = "https://api.veriphone.io/v2/verify"
NUMVERIFY_VALIDATE = "http://apilayer.net/api/validate"  # Free tier is HTTP only

_TIMEOUT = aiohttp.ClientTimeout(total=10)


def _clean_phone(phone: str) -> str:
    """Strip a phone number to digits only, prepend US country code if needed."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        digits = "1" + digits
    return digits


def _format_e164(digits: str) -> str:
    """Format cleaned digits as E.164 (+1XXXXXXXXXX)."""
    if digits.startswith("1") and len(digits) == 11:
        return f"+{digits}"
    return f"+{digits}"


class PhoneValidatorService:
    """
    Multi-provider phone validation with caching and free-tier awareness.

    Usage:
        svc = PhoneValidatorService(db)
        result = await svc.validate("+12395550178")
        batch = await svc.validate_batch(["+12395550178", "+12395550179"])
    """

    def __init__(self, db=None):
        self.db = db
        self._veriphone_key = os.environ.get("VERIPHONE_API_KEY", "")
        self._numverify_key = os.environ.get("NUMVERIFY_API_KEY", "")

    @property
    def veriphone_available(self) -> bool:
        return bool(self._veriphone_key)

    @property
    def numverify_available(self) -> bool:
        return bool(self._numverify_key)

    async def validate(self, phone: str, skip_cache: bool = False) -> Dict:
        """
        Validate a single phone number.

        Args:
            phone: Phone number in any format
            skip_cache: Force fresh lookup (burns a free-tier credit)

        Returns:
            dict with valid, line_type, carrier, country, provider, risk_signals
        """
        if not phone or not phone.strip():
            return {"success": False, "error": "Empty phone number", "phone": phone}

        digits = _clean_phone(phone)
        e164 = _format_e164(digits)

        if len(digits) < 10 or len(digits) > 15:
            return {
                "success": True,
                "phone": phone,
                "phone_e164": e164,
                "valid": False,
                "error": "Invalid phone number length",
                "risk_signals": ["invalid_length"],
            }

        # Check cache (saves free-tier credits)
        if not skip_cache and self.db:
            cached = await self.db["phone_validations"].find_one({"phone_e164": e164})
            if cached:
                cached.pop("_id", None)
                return {**cached, "cached": True}

        # Provider waterfall: Veriphone (1000/mo) → Numverify (100/mo)
        result = None

        if self.veriphone_available:
            result = await self._veriphone_validate(e164)

        if (not result or not result.get("success")) and self.numverify_available:
            result = await self._numverify_validate(digits)

        if not result or not result.get("success"):
            return {
                "success": False,
                "phone": phone,
                "phone_e164": e164,
                "error": "All validation providers failed or unavailable",
                "providers_configured": {
                    "veriphone": self.veriphone_available,
                    "numverify": self.numverify_available,
                },
            }

        # Enrich with risk signals
        result["risk_signals"] = self._compute_risk_signals(result)
        result["phone_original"] = phone
        result["validated_at"] = datetime.now(timezone.utc).isoformat()

        # Cache the result
        if self.db:
            await self.db["phone_validations"].update_one(
                {"phone_e164": e164},
                {"$set": result},
                upsert=True,
            )

        return result

    async def validate_batch(self, phones: list, skip_cache: bool = False) -> Dict:
        """
        Validate multiple phone numbers. Returns individual results.

        Args:
            phones: List of phone numbers
            skip_cache: Force fresh lookups

        Returns:
            dict with results list, summary stats
        """
        results = []
        valid_count = 0
        mobile_count = 0
        voip_count = 0
        invalid_count = 0

        for phone in phones[:25]:  # Cap at 25 to protect free tier
            result = await self.validate(phone, skip_cache=skip_cache)
            results.append(result)

            if result.get("valid"):
                valid_count += 1
                lt = (result.get("line_type") or "").lower()
                if lt in ("mobile", "cell", "wireless"):
                    mobile_count += 1
                elif lt in ("voip", "virtual"):
                    voip_count += 1
            else:
                invalid_count += 1

        return {
            "success": True,
            "total": len(results),
            "valid": valid_count,
            "invalid": invalid_count,
            "mobile": mobile_count,
            "voip": voip_count,
            "results": results,
        }

    # ── Veriphone (Primary — 1,000/mo free) ──

    async def _veriphone_validate(self, e164: str) -> Dict:
        """Veriphone validation — best free-tier data (line type, carrier, country)."""
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                params = {"key": self._veriphone_key, "phone": e164}
                async with session.get(VERIPHONE_VERIFY, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "success": True,
                            "provider": "veriphone",
                            "phone": data.get("phone", e164),
                            "phone_e164": data.get("e164", e164),
                            "phone_national": data.get("national", ""),
                            "phone_international": data.get("international", ""),
                            "valid": data.get("phone_valid", False),
                            "line_type": data.get("phone_type", "unknown"),
                            "carrier": data.get("carrier", ""),
                            "country": data.get("country", ""),
                            "country_code": data.get("country_code", ""),
                            "country_prefix": data.get("country_prefix", ""),
                            "region": data.get("phone_region", ""),
                        }
                    elif resp.status == 429:
                        log.warning("[PhoneValidator] Veriphone rate limited")
                        return {"success": False, "error": "rate_limited", "provider": "veriphone"}
                    else:
                        body = await resp.text()
                        log.warning("[PhoneValidator] Veriphone failed (%d): %s", resp.status, body[:200])
                        return {"success": False, "error": f"HTTP {resp.status}", "provider": "veriphone"}
        except Exception as e:
            log.error("[PhoneValidator] Veriphone error: %s", e)
            return {"success": False, "error": str(e), "provider": "veriphone"}

    # ── Numverify (Backup — 100/mo free) ──

    async def _numverify_validate(self, digits: str) -> Dict:
        """Numverify validation — carrier + line type detection."""
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                params = {
                    "access_key": self._numverify_key,
                    "number": digits,
                    "country_code": "US",
                    "format": "1",
                }
                async with session.get(NUMVERIFY_VALIDATE, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        # Numverify returns error object on failure
                        if data.get("error"):
                            err = data["error"]
                            log.warning("[PhoneValidator] Numverify API error: %s", err.get("info", ""))
                            return {"success": False, "error": err.get("info", "API error"), "provider": "numverify"}

                        return {
                            "success": True,
                            "provider": "numverify",
                            "phone": data.get("number", digits),
                            "phone_e164": data.get("international_format", f"+{digits}"),
                            "phone_national": data.get("local_format", ""),
                            "phone_international": data.get("international_format", ""),
                            "valid": data.get("valid", False),
                            "line_type": data.get("line_type", "unknown"),
                            "carrier": data.get("carrier", ""),
                            "country": data.get("country_name", ""),
                            "country_code": data.get("country_code", ""),
                            "country_prefix": str(data.get("country_prefix", "")),
                            "location": data.get("location", ""),
                        }
                    else:
                        return {"success": False, "error": f"HTTP {resp.status}", "provider": "numverify"}
        except Exception as e:
            log.error("[PhoneValidator] Numverify error: %s", e)
            return {"success": False, "error": str(e), "provider": "numverify"}

    # ── Risk Signal Computation ──

    def _compute_risk_signals(self, result: Dict) -> list:
        """
        Compute risk signals from phone validation data for The Analyst's
        lead scoring pipeline.

        Returns list of string signals that can be used as scoring penalties/bonuses.
        """
        signals = []
        line_type = (result.get("line_type") or "").lower()
        carrier = (result.get("carrier") or "").lower()

        if not result.get("valid"):
            signals.append("invalid_phone")
            return signals

        # Line type signals
        if line_type in ("mobile", "cell", "wireless"):
            signals.append("mobile_phone")  # Good — can receive SMS
        elif line_type in ("voip", "virtual"):
            signals.append("voip_phone")  # Risky — possible burner
        elif line_type in ("landline", "fixed_line"):
            signals.append("landline_phone")  # Can't SMS, but stable
        elif line_type in ("toll_free",):
            signals.append("toll_free_phone")  # Suspicious for personal use
        else:
            signals.append("unknown_line_type")

        # Carrier signals
        voip_carriers = [
            "google voice", "textnow", "textfree", "pinger", "bandwidth",
            "twilio", "vonage", "ringcentral", "grasshopper", "magicjack",
            "ooma", "line2", "freedompop", "hushed", "burner",
        ]
        for vc in voip_carriers:
            if vc in carrier:
                signals.append("known_voip_carrier")
                break

        # Country signals
        country_code = (result.get("country_code") or "").upper()
        if country_code and country_code != "US":
            signals.append("non_us_phone")

        return signals

    # ── iMessage Routing Helper ──

    def is_imessage_eligible(self, result: Dict) -> bool:
        """
        Determine if a phone number is likely to support iMessage.
        Used by BlueBubbles integration to decide routing.

        Rules:
          - Must be valid
          - Must be mobile (not landline/VOIP)
          - Must be US-based
          - Known iMessage carriers: AT&T, Verizon, T-Mobile, Sprint, US Cellular
        """
        if not result.get("valid"):
            return False

        line_type = (result.get("line_type") or "").lower()
        if line_type not in ("mobile", "cell", "wireless"):
            return False

        country_code = (result.get("country_code") or "").upper()
        if country_code != "US":
            return False

        # All US mobile phones potentially support iMessage
        return True

    def is_sms_eligible(self, result: Dict) -> bool:
        """Check if a phone can receive SMS (for Twilio routing)."""
        if not result.get("valid"):
            return False
        line_type = (result.get("line_type") or "").lower()
        # Mobile and VOIP can receive SMS, landlines generally can't
        return line_type in ("mobile", "cell", "wireless", "voip", "virtual")
