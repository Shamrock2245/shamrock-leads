"""
Hartford (CT) Judicial District & Municipal Arrest Scraper.
Target Courts: Hartford GA 14, Hartford JD, Hartford Community Court.
Portal: https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx
"""
from scrapers.counties_ct.statewide_docket import CTStatewideDockerScraper


class HartfordScraper(CTStatewideDockerScraper):
    @property
    def county(self) -> str:
        return "Hartford"

    @property
    def state(self) -> str:
        return "CT"

    courts_to_scrape = [
        ("H14H", "Hartford GA 14"),
        ("HHD", "Hartford JD"),
        ("H14C", "Hartford Community Court"),
        ("H12M", "Manchester GA 12"),
    ]
