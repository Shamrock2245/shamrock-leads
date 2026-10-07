"""
Sampson County (NC) Arrest Scraper — Southern Software Citizen Connect.

Source contract (recon 2026-10-07, docs/recon/NC_SSW_FIVE_CITIZEN_CONNECT_2026-10-07.md):
  * Portal: https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID=SampsonCoNC
  * Ordinary public HTTPS (no login / CAPTCHA / proxy / stealth).
  * Roster: POST fetch_current_confinements.php with JMSAgencyID=NC0820000
    → booking-card HTML; BookingID in View Full Details href, data-bookingid,
    and mugshot debug comments (distinct from person NameID).
  * Booking_Number is source **BookingID** only. Never invent keys; never use
    NameID alone (person-level / multi-booking unsafe).
  * Bond_Amount / Charges only when the card publishes Bond Total / charge lines.
  * Health stays unverified until a write smoke — do not set verified_public yet.
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class SampsonScraper(SouthernSWBaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "cc.southernsoftware.com Citizen Connect AgencyID=SampsonCoNC; ordinary "
        "POST fetch_current_confinements; Booking_Number is source BookingID "
        "(href / data-bookingid / mugshot debug); never invent keys; never use "
        "NameID alone; charges/bond only when published on the card."
    )

    @property
    def county(self) -> str:
        return "Sampson"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "SampsonCoNC"
