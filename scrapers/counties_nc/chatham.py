"""
Chatham County (NC) Arrest Scraper — OCV inmates.json.

Portal: https://www.chathamsheriff.com/inmateSearch
Feed:   https://myocv.s3.amazonaws.com/ocvapps/a104027312/inmates.json
"""
from scrapers.ocv_inmates_base import OCVInmatesBaseScraper


class ChathamScraper(OCVInmatesBaseScraper):

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Configured Chatham public paths did not establish a complete booking-safe broad listing through ordinary access."
    )
    @property
    def county(self) -> str:
        return "Chatham"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def app_id(self) -> str:
        return "a104027312"

    @property
    def portal_url(self) -> str:
        return "https://www.chathamsheriff.com/inmateSearch"
