"""
Stamford (CT) Judicial District & Municipal Arrest Scraper.
Target Courts: Stamford GA 1, Stamford JD, Norwalk GA 20.
Portal: https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx
"""
from scrapers.counties_ct.statewide_docket import CTStatewideDockerScraper


class StamfordScraper(CTStatewideDockerScraper):
    @property
    def county(self) -> str:
        return "Stamford"

    @property
    def state(self) -> str:
        return "CT"

    courts_to_scrape = [
        ("S01S", "Stamford GA 1"),
        ("FST", "Stamford JD"),
        ("S20N", "Norwalk GA 20"),
    ]
