"""
Hendry County Arrest Scraper — JailTracker (public-safety-cloud.com)
====================================================================
Source: Hendry County Sheriff's Office
URL: https://omsweb.public-safety-cloud.com/jtclientweb/jailtracker/index/Hendry_County_FL
Method: Playwright + CAPTCHA solving (ddddocr → SolveCaptcha → OpenAI fallback)

Extracts full offender roster from JailTracker Blazor WASM app including:
  - Name, DOB, Race, Sex
  - Booking # and date
  - Charges with descriptions
  - Bond amounts per charge
  - Mugshot URLs

NO Cloudflare protection — direct access from VPS datacenter IP.
NO SOCKS proxy needed.

HISTORY:
  - v1 (original): OCV S3 JSON + curl_cffi detail page enrichment
    → Phase 1 (demographics) worked, Phase 2 (charges/bonds) unreliable
      because detail pages are React SPA requiring JS rendering
  - v2 (current): JailTracker rewrite — Blazor WASM with CAPTCHA solving
    → Gets charges + bonds reliably from API JSON responses
"""

from scrapers.jailtracker_base import JailTrackerBaseScraper


class HendryCountyScraper(JailTrackerBaseScraper):
    """Hendry County scraper using JailTracker public-safety-cloud.com."""

    county_jt_id = "Hendry_County_FL"
    facility_name = "Hendry County Jail"

    @property
    def county(self) -> str:
        return "Hendry"
