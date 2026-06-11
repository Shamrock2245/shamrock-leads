"""
Sarasota County Arrest Scraper — JailTracker Blazor WASM
========================================================
Source: Sarasota County Sheriff's Office
URL: https://omsweb.public-safety-cloud.com/jtclientweb/jailtracker/index/SARASOTA_COUNTY_FL
Method: JailTracker Base Scraper (Playwright + OpenAI CAPTCHA OCR)

NOTE: The cms.revize.com endpoint is permanently Cloudflare-blocked.
      JailTracker is the only viable data source.

KNOWN ISSUE: JailTracker Blazor app occasionally crashes after CAPTCHA
      with "An unhandled error has occurred." — this is a server-side
      issue. The scraper handles retries gracefully.
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class SarasotaCountyScraper(JailTrackerBaseScraper):
    county_jt_id = "SARASOTA_COUNTY_FL"
    facility_name = "Sarasota County Jail"

    @property
    def county(self) -> str:
        return "Sarasota"
