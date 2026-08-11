"""
New Haven (CT) Judicial District & Municipal Arrest Scraper.
Target Courts: New Haven GA 06, New Haven GA 08, New Haven GA 23, New Haven JD.
Portal: https://www.jud2.ct.gov/crdockets/SearchByCourt.aspx
"""
from scrapers.counties_ct.statewide_docket import CTStatewideDockerScraper


class NewHavenScraper(CTStatewideDockerScraper):
    @property
    def county(self) -> str:
        return "New Haven"

    @property
    def state(self) -> str:
        return "CT"

    courts_to_scrape = [
        ("N06N", "New Haven GA 06"),
        ("N08W", "New Haven GA 08"),
        ("N23N", "New Haven GA 23"),
        ("NNH", "New Haven JD"),
        ("N07M", "Meriden GA 7"),
    ]
