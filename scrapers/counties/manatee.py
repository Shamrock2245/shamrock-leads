"""
Manatee County Arrest Scraper — JailTracker (public-safety-cloud.com)
=====================================================================
Source: Manatee County Sheriff's Office via JailTracker Blazor WASM roster
URL: https://omsweb.public-safety-cloud.com/jtclientweb/.../Manatee_County_FL
Method: Playwright (local Chromium) + OpenAI vision CAPTCHA solver

HISTORY:
- v1 (DrissionPage): Chromium headless → Cloudflare challenge timeout every run
- v2 (Obscura): Attempted Cloudflare bypass → still blocked by WAF
- v3 (JailTracker): Direct JailTracker API — NO Cloudflare! Simple CAPTCHA only.
  Discovered that Manatee also hosts data on JailTracker (public-safety-cloud.com)
  in addition to the CF-protected Revize page.
"""

from scrapers.jailtracker_base import JailTrackerBaseScraper


class ManateeCountyScraper(JailTrackerBaseScraper):

    county_jt_id = "Manatee_County_FL"
    facility_name = "Manatee County Jail"

    @property
    def county(self) -> str:
        return "Manatee"
