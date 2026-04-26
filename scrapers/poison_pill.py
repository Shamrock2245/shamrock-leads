"""
PoisonPillDetector — WAF / CAPTCHA / Rate-Limit detection for ShamrockLeads scrapers.

Inspired by the web-scraping skill from skills.sh (jamditis/claude-skills-journalism).
Detects paywalls, CAPTCHAs, Cloudflare blocks, rate limits, and login walls using
pattern matching and HTTP status code analysis.

Usage:
    from scrapers.poison_pill import PoisonPillDetector, PoisonPillType
    detector = PoisonPillDetector()
    result = detector.detect(url, response_text, status_code)
    if result.detected:
        raise ScraperError(f"Blocked: {result.type.value} — {result.details}")
"""
import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse


class PoisonPillType(Enum):
    CAPTCHA = "captcha"
    CLOUDFLARE = "cloudflare"
    RATE_LIMIT = "rate_limit"
    LOGIN_REQUIRED = "login_required"
    WAF_BLOCK = "waf_block"
    EMPTY_ROSTER = "empty_roster"
    NONE = "none"


@dataclass
class PoisonPillResult:
    detected: bool
    type: PoisonPillType
    confidence: float
    details: str


class PoisonPillDetector:
    """
    Detect anti-bot / WAF responses from Florida county jail roster pages.
    Call detect() after every HTTP response before parsing.
    """

    # Pattern groups — all case-insensitive
    PATTERNS = {
        PoisonPillType.CAPTCHA: [
            r"verify you are human",
            r"captcha",
            r"robot verification",
            r"prove you.re not a robot",
            r"are you a robot",
            r"recaptcha",
            r"hcaptcha",
        ],
        PoisonPillType.CLOUDFLARE: [
            r"checking your browser",
            r"cloudflare",
            r"ddos protection",
            r"please wait while we verify",
            r"ray id:",
            r"cf-ray",
            r"attention required.*cloudflare",
        ],
        PoisonPillType.RATE_LIMIT: [
            r"too many requests",
            r"rate limit exceeded",
            r"slow down",
            r"request limit",
            r"throttled",
        ],
        PoisonPillType.LOGIN_REQUIRED: [
            r"sign in to continue",
            r"log in required",
            r"create an account",
            r"session expired",
            r"unauthorized access",
        ],
        PoisonPillType.WAF_BLOCK: [
            r"access denied",
            r"forbidden",
            r"your ip.*blocked",
            r"blocked by.*security",
            r"web application firewall",
            r"this site is protected",
            r"incapsula",
            r"sucuri",
            r"akamai",
            r"barracuda",
        ],
    }

    # Florida county jail roster URLs that are known to have WAF issues
    WAF_KNOWN_DOMAINS = {
        "marionso.com": PoisonPillType.WAF_BLOCK,
        "ocso.com": PoisonPillType.WAF_BLOCK,
    }

    def detect(self, url: str, content: str, status_code: int = 200) -> PoisonPillResult:
        """
        Analyze an HTTP response for poison pills.

        Args:
            url: The URL that was fetched
            content: The response body text
            status_code: HTTP status code

        Returns:
            PoisonPillResult — check .detected before parsing
        """
        # ── HTTP status code checks ──
        if status_code == 429:
            return PoisonPillResult(True, PoisonPillType.RATE_LIMIT, 1.0, "HTTP 429 Too Many Requests")
        if status_code == 403:
            return PoisonPillResult(True, PoisonPillType.WAF_BLOCK, 0.9, "HTTP 403 Forbidden")
        if status_code == 401:
            return PoisonPillResult(True, PoisonPillType.LOGIN_REQUIRED, 1.0, "HTTP 401 Unauthorized")
        if status_code == 503:
            # Could be Cloudflare challenge
            if content and "cloudflare" in content.lower():
                return PoisonPillResult(True, PoisonPillType.CLOUDFLARE, 0.95, "HTTP 503 + Cloudflare")
            return PoisonPillResult(True, PoisonPillType.RATE_LIMIT, 0.7, "HTTP 503 Service Unavailable")

        # ── Known WAF domains ──
        domain = urlparse(url).netloc.replace("www.", "").lower()
        for waf_domain, pill_type in self.WAF_KNOWN_DOMAINS.items():
            if waf_domain in domain and content and len(content) < 1000:
                return PoisonPillResult(True, pill_type, 0.85, f"Known WAF domain: {domain}")

        if not content:
            return PoisonPillResult(True, PoisonPillType.EMPTY_ROSTER, 0.8, "Empty response body")

        # ── Pattern matching ──
        content_lower = content.lower()
        for pill_type, patterns in self.PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, content_lower):
                    return PoisonPillResult(True, pill_type, 0.75, f"Pattern match: {pattern}")

        # ── Suspiciously short response ──
        if len(content.strip()) < 200 and status_code == 200:
            return PoisonPillResult(True, PoisonPillType.EMPTY_ROSTER, 0.6, f"Suspiciously short response: {len(content)} chars")

        return PoisonPillResult(False, PoisonPillType.NONE, 0.0, "")

    def is_empty_roster(self, content: str, expected_keywords: list = None) -> bool:
        """
        Check if a roster page loaded but has no inmates.
        Pass expected_keywords like ['inmate', 'booking', 'arrest'] to verify content type.
        """
        if not content or len(content.strip()) < 100:
            return True
        if expected_keywords:
            content_lower = content.lower()
            return not any(kw.lower() in content_lower for kw in expected_keywords)
        return False


# ── Rotating User Agent Pool ──────────────────────────────────────────────────
# Based on skills.sh web-scraping best practices: rotate UAs to avoid fingerprinting

SCRAPER_USER_AGENTS = [
    # Chrome on Windows (most common)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Safari on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

_ua_index = 0

def get_next_user_agent() -> str:
    """Return the next user agent in rotation (round-robin)."""
    global _ua_index
    ua = SCRAPER_USER_AGENTS[_ua_index % len(SCRAPER_USER_AGENTS)]
    _ua_index += 1
    return ua


def get_scraper_headers(referer: str = None, accept_json: bool = False) -> dict:
    """
    Return a realistic browser header set for scraping.
    Rotates user agents on every call.
    """
    headers = {
        "User-Agent": get_next_user_agent(),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }
    if accept_json:
        headers["Accept"] = "application/json, text/javascript, */*; q=0.01"
        headers["X-Requested-With"] = "XMLHttpRequest"
    else:
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    if referer:
        headers["Referer"] = referer
    return headers
