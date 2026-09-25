"""
Darlington County (SC) Arrest Scraper — DCN DevExpress roster.

Portal (ordinary public access, 2026-09-24 Mac smoke):
  http://bookings.darlingtonsheriff.org/dcn/inmates

HTTPS to the same host connect-times out from Mac egress; HTTP serves the
public roster. Source booking key = URL ``bid`` (opaque DCN id, stable across
sessions). Rows without ``bid`` are skipped (no DAR_ / name-hash keys).

2026-09-25: the server-rendered page holds only the first 100 of ~233 rows.
Pages 2..N are now fetched with the grid's own DevExpress pager callback
(``paginate_callbacks``; see scrapers/dcn_base.py), and the detail budget
covers the full roster.
"""
from scrapers.dcn_base import DCNBaseScraper


class DarlingtonScraper(DCNBaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "bookings.darlingtonsheriff.org /dcn/inmates (HTTP); DevExpress roster "
        "with inmate-details?id=&bid=; bid is source-issued booking key; "
        "pages 2..N via the grid's DevExpress PAGERONCLICK callback."
    )
    require_source_bid = True
    paginate_callbacks = True
    max_callback_pages = 10
    max_detail_fetches = 400
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
