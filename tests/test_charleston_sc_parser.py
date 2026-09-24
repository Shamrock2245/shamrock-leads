"""Charleston SC ListView parser — source Inmate # contract."""
from pathlib import Path

from scrapers.base_scraper import BaseScraper
from scrapers.counties_sc.charleston import CharlestonScraper

FIXTURE = Path(__file__).parent / "fixtures" / "charleston_results_sample.html"


def test_charleston_listview_parses_source_inmate_numbers():
    html = FIXTURE.read_text(encoding="utf-8")
    records = CharlestonScraper._parse_listview(html)
    assert len(records) >= 2
    for rec in records:
        assert BaseScraper._has_source_booking_identifier(rec.Booking_Number)
        assert not str(rec.Booking_Number).startswith("CHS_")
        assert rec.Full_Name
        assert rec.Booking_Date
    assert any("BOYLES" in r.Full_Name.upper() for r in records)


def test_charleston_rejects_legacy_synthetic_prefix():
    assert not BaseScraper._has_source_booking_identifier("CHS_abcdef1234")


def test_charleston_bond_prefers_source_total_else_sums_charges():
    html = """
    <span id="MainContent_ListViewMaster_lblInmateNumber_0">1001</span>
    <span id="MainContent_ListViewMaster_lblfullname_0">DOE, JANE Q</span>
    <span id="MainContent_ListViewMaster_Label1_0">09/20/2026</span>
    <ul id="MainContent_ListViewMaster_lstviewchargedet_0_ulchargedet_0">
    Bond Amount: $100.00 Charge Description: CHARGE A
    </ul>
    <ul id="MainContent_ListViewMaster_lstviewchargedet_0_ulchargedet_1">
    Bond Amount: $50.50 Charge Description: CHARGE B
    </ul>
    <span id="MainContent_ListViewMaster_lstviewchargedet_0_lblbondamttot_0">$175.50</span>
    <span id="MainContent_ListViewMaster_lblInmateNumber_1">1002</span>
    <span id="MainContent_ListViewMaster_lblfullname_1">ROE, JOHN</span>
    <span id="MainContent_ListViewMaster_Label1_1">09/21/2026</span>
    <ul id="MainContent_ListViewMaster_lstviewchargedet_1_ulchargedet_0">
    Bond Amount: $100.00 Charge Description: CHARGE A
    </ul>
    <ul id="MainContent_ListViewMaster_lstviewchargedet_1_ulchargedet_1">
    Bond Amount: $50.50 Charge Description: CHARGE B
    </ul>
    """
    records = {r.Booking_Number: r for r in CharlestonScraper._parse_listview(html)}
    assert records["1001"].Bond_Amount == "175.5"
    assert records["1002"].Bond_Amount == "150.5"
    assert "CHARGE A" in records["1002"].Charges and "CHARGE B" in records["1002"].Charges
