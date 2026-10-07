"""York (SC) Inmates in Jail parser — synthetic fixtures shaped like the live grid (no network)."""
from __future__ import annotations

import pytest

from scrapers.counties_sc import york
from scrapers.counties_sc.york import YorkScraper
from scrapers.scraper_resilience import ParseDriftError


def _item(booking="DC202600001", photo=None, release="*In Jail", bond="$1,250.00", cls="dgItem",
          name="Doe , Jane Marie", charges=("PUBLIC CHARGE ONE", "PUBLIC CHARGE TWO")):
    photo = booking if photo is None else photo
    charge_rows = "".join(
        f"<tr><td> {i} </td><td> {c} </td><td> Public Agency </td></tr>" for i, c in enumerate(charges, 1)
    )
    return f"""
<tr class="{cls}">
 <td class="dgSubItem"><table cellspacing="5" class="table2">
  <tr><td class="cell1" colspan="2"> {name} </td></tr>
  <tr><td class="text1">Booking Date: </td><td class="medBoldBlack"> 10/7/2026 2:36:25 PM </td></tr>
  <tr><td class="text1">City: </td><td class="medBoldBlack"> SAMPLETOWN </td></tr>
  <tr><td class="text1">Race/Sex: </td><td class="medBoldBlack"> W F </td></tr>
  <tr><td class="text1">Age: </td><td class="medBoldBlack"> 30 </td></tr>
 </table></td>
 <td class="dgHorizontalAlign"><table class="table3"><tr><td>
  <a href="/photos/{photo}.jpg"><img class="image" src="/photos/{photo}.jpg"/></a>
 </td></tr></table></td>
 <td class="dgSubItem"><table class="table5">
  <tr><td class="medBoldBlack2a" colspan="4"> York County Detention Center </td></tr>
  <tr><td><b>Booking Number</b></td><td><b>Booking Date</b></td><td><b>Release Date</b></td><td><b>Total Bond</b></td></tr>
  <tr><td> {booking} </td><td> 10/7/2026 2:36:25 PM </td><td>{release} </td><td> {bond} </td></tr>
  <tr><td class="cell2" colspan="4"><table class="table4">
   <tr><td><b>Sequence#</b></td><td><b>Charge Description</b></td><td><b>Arresting Agency</b></td></tr>
   {charge_rows}
  </table></td></tr>
 </table></td>
</tr>"""


def _page(*items, current=1, pages=3):
    links = "".join(
        f"<span>{n}</span> " if n == current else f"<a href=\"javascript:__doPostBack('dgJackets$ctl01$ctl{n-1:02d}','')\">{n}</a> "
        for n in range(1, pages + 1)
    )
    return f"""<html><body><form>
<input type="hidden" name="__VIEWSTATE" value="vs"/><input type="hidden" name="__EVENTVALIDATION" value="ev"/>
Results Count: 2
<table id="dgJackets">
<tr class="pager"><td colspan="3">{links}</td></tr>
<tr class="dgHeader"><td>Inmate Information</td><td>Inmate Photo</td><td>Bookings/Charges</td></tr>
{''.join(items)}
</table></form></body></html>"""


def test_maps_live_shaped_row_to_source_booking_number():
    recs, people = york.parse_page(_page(_item()))
    assert people == 1 and len(recs) == 1
    r = recs[0]
    assert r.Booking_Number == "DC202600001"
    assert (r.Full_Name, r.First_Name, r.Middle_Name, r.Last_Name) == ("Doe, Jane Marie", "Jane", "Marie", "Doe")
    assert (r.Booking_Date, r.Booking_Time) == ("10/7/2026", "2:36:25 PM")
    assert r.Status == "In Custody" and r.Release_Date == ""
    assert r.Bond_Amount == "1250"
    assert r.Charges == "PUBLIC CHARGE ONE | PUBLIC CHARGE TWO"
    assert r.Agency == "Public Agency"
    assert r.Facility == "York County Detention Center"
    assert (r.County, r.State, r.Race, r.Sex, r.Age_At_Arrest) == ("York", "SC", "W", "F", "30")
    assert r.Mugshot_URL.endswith("/photos/DC202600001.jpg")
    assert r.extra_data["booking_key_origin"] == "source-issued public Booking Number"


def test_zero_bond_is_zero_not_invented():
    recs, _ = york.parse_page(_page(_item(bond="$0.00")))
    assert recs[0].Bond_Amount == "0"


@pytest.mark.parametrize("booking", ["", "Doe", "12345", "DC2026"])
def test_missing_or_malformed_booking_number_is_dropped(booking):
    recs, people = york.parse_page(_page(_item(booking=booking, photo="DC202600009")))
    assert people == 1 and recs == []


def test_photo_key_mismatch_drops_row():
    recs, _ = york.parse_page(_page(_item(booking="DC202600001", photo="DC202600002")))
    assert recs == []


def test_missing_grid_is_parse_drift():
    with pytest.raises(ParseDriftError):
        york.parse_page("<html><body>maintenance</body></html>")


def test_next_page_target_follows_pager():
    assert york._next_page_target(_page(_item(), current=1)) == "dgJackets$ctl01$ctl01"
    assert york._next_page_target(_page(_item(), current=3)) is None


def test_scrape_walks_pages_and_dedups(monkeypatch):
    pages = [
        _page(_item("DC202600001"), _item("DC202600002", cls="dgAltItem"), current=1, pages=2),
        _page(_item("DC202600002"), _item("DC202600003"), current=2, pages=2),
    ]
    calls = []

    def fake_fetch(self, session, data=None):
        calls.append(data)
        return pages[len(calls) - 1]

    monkeypatch.setattr(YorkScraper, "_fetch", fake_fetch)
    monkeypatch.setattr(york, "PAGE_DELAY_S", 0)
    recs = YorkScraper().scrape()
    assert [r.Booking_Number for r in recs] == ["DC202600001", "DC202600002", "DC202600003"]
    assert calls[0] is None and calls[1]["__EVENTTARGET"] == "dgJackets$ctl01$ctl01"


def test_rows_without_any_keyed_booking_raise_drift(monkeypatch):
    monkeypatch.setattr(YorkScraper, "_fetch", lambda self, s, data=None: _page(_item(booking="", photo="X"), pages=1))
    with pytest.raises(ParseDriftError):
        YorkScraper().scrape()


def test_source_contract_is_validated_and_https():
    assert YorkScraper.SOURCE_CONTRACT_VALIDATED is True
    assert YorkScraper().roster_url.startswith("https://")
