"""
Charlotte County Arrest Scraper — JailTracker (public-safety-cloud.com)
=======================================================================
Source: Charlotte County Sheriff's Office (CCSO) via JailTracker Blazor WASM roster
URL: https://omsweb.public-safety-cloud.com/jtclientweb/.../Charlotte_County_FL
Method: Playwright (local Chromium) + OpenAI vision CAPTCHA solver

HISTORY:
- v1 (DrissionPage): Blocked by Cloudflare JA3 fingerprinting on datacenter IPs
- v2 (curl_cffi): TLS impersonation worked initially, then Cloudflare upgraded
- v3 (Obscura): Real V8 engine, still blocked by Cloudflare WAF "Attention Required"
- v4 (Residential proxy): iMac SOCKS tunnel, CF still blocks with WAF even on
  residential IP — the WAF detects automated browser fingerprints
- v5 (JailTracker): Direct JailTracker API — NO Cloudflare! Simple CAPTCHA only.
  Discovered that CCSO also hosts data on JailTracker (public-safety-cloud.com)
  at https://omsweb.public-safety-cloud.com/jtclientweb/.../Charlotte_County_FL
  in addition to the CF-protected Revize page at inmates.charlottecountyfl.revize.com
"""

from scrapers.jailtracker_base import JailTrackerBaseScraper


class CharlotteCountyScraper(JailTrackerBaseScraper):

    county_jt_id = "Charlotte_County_FL"
    facility_name = "Charlotte County Jail"

    @property
    def county(self) -> str:
        return "Charlotte"
