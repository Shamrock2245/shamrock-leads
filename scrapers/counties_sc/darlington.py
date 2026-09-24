"""
Darlington County (SC) Arrest Scraper — DCN DevExpress roster.

Portal (ordinary public access, 2026-09-24 Mac smoke):
  http://bookings.darlingtonsheriff.org/dcn/inmates

HTTPS to the same host connect-times out from Mac egress; HTTP serves the
public roster. Source booking key = URL ``bid`` (opaque DCN id). Rows without
``bid`` are skipped (no DAR_ / name-hash keys).
"""
from scrapers.dcn_base import DCNBaseScraper


class DarlingtonScraper(DCNBaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "bookings.darlingtonsheriff.org /dcn/inmates (HTTP); DevExpress roster "
        "with inmate-details?id=&bid=; bid is source-issued booking key."
    )
    require_source_bid = True
    max_detail_fetches = 120
    enrich_details = True

    @property
    def county(self) -> str:
        return "Darlington"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def inmates_url(self) -> str:
        return "http://bookings.darlingtonsheriff.org/dcn/inmates"

    @property
    def facility_name(self) -> str:
        return "W. Glenn Campbell Detention Center"
