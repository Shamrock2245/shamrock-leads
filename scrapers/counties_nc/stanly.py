"""
Stanly County (NC) Arrest Scraper — OCV inmates.json.

Portal: https://www.stanlysheriff.us/inmateList
Feed:   https://myocv.s3.amazonaws.com/ocvapps/a109928001/inmates.json
"""
from scrapers.ocv_inmates_base import OCVInmatesBaseScraper


class StanlyScraper(OCVInmatesBaseScraper):
    @property
    def county(self) -> str:
        return "Stanly"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def app_id(self) -> str:
        return "a109928001"

    @property
    def portal_url(self) -> str:
        return "https://www.stanlysheriff.us/inmateList"
