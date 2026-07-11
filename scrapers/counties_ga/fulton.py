"""
Fulton County (GA) Arrest Scraper.
Uses the Socrata Open Data API which updates daily.
Fulton is the largest county in Georgia (1.1M population).
"""

from scrapers.socrata_base import SocrataBaseScraper

class FultonScraper(SocrataBaseScraper):
    @property
    def county(self) -> str:
        return "Fulton"
        
    @property
    def socrata_url(self) -> str:
        # Fulton County Jail Inmates Open Data dataset
        return "https://sharefulton.fultoncountyga.gov/resource/3vfv-9mmr.json"
