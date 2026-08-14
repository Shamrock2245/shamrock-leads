"""Lincoln County, North Carolina public OCV roster scraper.

Portal: https://www.lincolnsheriff.org/inmateSearch
Feed:   https://myocv.s3.amazonaws.com/ocvapps/a46428092/inmates.json

The official paginated OCV roster exposes complete names, a source-issued Inmate ID,
and a Booked Date for each validated feed item. The shared parser fails closed when
the required source identity is absent.
"""
from scrapers.ocv_inmates_base import OCVInmatesBaseScraper


class LincolnScraper(OCVInmatesBaseScraper):
    @property
    def county(self) -> str:
        return "Lincoln"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def app_id(self) -> str:
        return "a46428092"

    @property
    def portal_url(self) -> str:
        return "https://www.lincolnsheriff.org/inmateSearch"
