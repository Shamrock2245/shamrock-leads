"""
Bridgeport (CT) Judicial District & Municipal Arrest Scraper.
Target Courts: Bridgeport GA 2, Bridgeport JD.
Portal: https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx
"""
from scrapers.counties_ct.statewide_docket import CTStatewideDockerScraper


class BridgeportScraper(CTStatewideDockerScraper):
    @property
    def county(self) -> str:
        return "Bridgeport"

    @property
    def state(self) -> str:
        return "CT"

    courts_to_scrape = [
        ("F02B", "Bridgeport GA 2"),
        ("FBT", "Bridgeport JD"),
    ]
