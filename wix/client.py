"""
WixClient — Shared HTTP Client for all Wix REST APIs
=====================================================
Handles authentication, retry logic, rate limiting, and error parsing
for the Wix REST API v2/v3/v4 surface.

Auth Strategy:
    API Key + Site ID headers (same key works for CMS, CRM, Blog).
    See: https://dev.wix.com/docs/api-reference/account-level/about-account-level-apis

Usage:
    from wix.client import WixClient
    client = WixClient()
    response = client.post("/wix-data/v2/items", json=payload)
"""

import os
import time
import logging
from typing import Optional, Dict, Any
from functools import wraps

import requests

logger = logging.getLogger("wix.client")

# ── Constants ──────────────────────────────────────────────────────────────────
WIX_SITE_ID = os.getenv("WIX_SITE_ID", "7dd020de-a409-4a2c-bcc8-f81e3a7b6cc1")
WIX_API_BASE = "https://www.wixapis.com"

# Rate limiting: Wix recommends max 1 update/second per item
MAX_RETRIES = 3
RETRY_BACKOFF = [1.0, 2.0, 4.0]  # seconds between retries


class WixAPIError(Exception):
    """Raised when Wix API returns a non-2xx response."""

    def __init__(self, status_code: int, message: str, details: Optional[Dict] = None):
        self.status_code = status_code
        self.message = message
        self.details = details or {}
        super().__init__(f"Wix API {status_code}: {message}")


class WixClient:
    """
    Base HTTP client for all Wix REST API calls.

    Provides:
      - Unified auth headers (API Key + Site ID)
      - Automatic retry with exponential backoff (429, 500, 502, 503)
      - Structured error parsing
      - Request/response logging
    """

    def __init__(self, api_key: Optional[str] = None, site_id: Optional[str] = None):
        self.api_key = api_key or os.getenv("WIX_BLOG_API_KEY", "")
        self.site_id = site_id or WIX_SITE_ID

        if not self.api_key:
            logger.warning(
                "WIX_BLOG_API_KEY not set — Wix API calls will fail. "
                "Generate at: Wix Dashboard → Settings → API Keys"
            )

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": self.api_key,
            "wix-site-id": self.site_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    @property
    def is_configured(self) -> bool:
        """Check if the client has valid credentials."""
        return bool(self.api_key and self.site_id)

    def _build_url(self, path: str) -> str:
        """Build full URL from relative path."""
        if path.startswith("http"):
            return path
        return f"{WIX_API_BASE}{path}"

    def _parse_error(self, response: requests.Response) -> WixAPIError:
        """Parse Wix error response into structured exception."""
        try:
            body = response.json()
            message = body.get("message", body.get("description", response.text[:200]))
            details = body.get("details", {})
        except Exception:
            message = response.text[:500] if response.text else f"HTTP {response.status_code}"
            details = {}

        return WixAPIError(
            status_code=response.status_code,
            message=message,
            details=details,
        )

    def request(
        self,
        method: str,
        path: str,
        retries: int = MAX_RETRIES,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute an HTTP request to the Wix API with retry logic.

        Args:
            method: HTTP method (GET, POST, PATCH, PUT, DELETE)
            path: API path (e.g., "/wix-data/v2/items")
            retries: Number of retry attempts for transient errors
            **kwargs: Passed directly to requests (json, params, etc.)

        Returns:
            Parsed JSON response as dict

        Raises:
            WixAPIError: On non-2xx responses after all retries
        """
        url = self._build_url(path)

        for attempt in range(retries):
            try:
                response = self.session.request(method, url, timeout=30, **kwargs)

                # Success
                if response.status_code in (200, 201, 204):
                    if response.status_code == 204 or not response.text:
                        return {"status": "ok"}
                    return response.json()

                # Rate limited or server error — retry
                if response.status_code in (429, 500, 502, 503) and attempt < retries - 1:
                    wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                    # Respect Retry-After header if present
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = max(float(retry_after), wait)
                        except ValueError:
                            pass
                    logger.warning(
                        f"Wix API {response.status_code} on {method} {path} — "
                        f"retrying in {wait}s (attempt {attempt + 1}/{retries})"
                    )
                    time.sleep(wait)
                    continue

                # Non-retryable error
                error = self._parse_error(response)
                logger.error(f"Wix API error: {error}")
                raise error

            except requests.exceptions.ConnectionError as e:
                if attempt < retries - 1:
                    wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                    logger.warning(f"Connection error to Wix API — retrying in {wait}s: {e}")
                    time.sleep(wait)
                    continue
                raise WixAPIError(0, f"Connection failed after {retries} attempts: {e}")

            except requests.exceptions.Timeout as e:
                if attempt < retries - 1:
                    wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                    logger.warning(f"Timeout on Wix API — retrying in {wait}s: {e}")
                    time.sleep(wait)
                    continue
                raise WixAPIError(0, f"Timeout after {retries} attempts: {e}")

        # Should not reach here, but safety net
        raise WixAPIError(0, "All retries exhausted")

    def get(self, path: str, **kwargs) -> Dict[str, Any]:
        """Shortcut for GET requests."""
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Dict[str, Any]:
        """Shortcut for POST requests."""
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs) -> Dict[str, Any]:
        """Shortcut for PATCH requests."""
        return self.request("PATCH", path, **kwargs)

    def put(self, path: str, **kwargs) -> Dict[str, Any]:
        """Shortcut for PUT requests."""
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs) -> Dict[str, Any]:
        """Shortcut for DELETE requests."""
        return self.request("DELETE", path, **kwargs)
