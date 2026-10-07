"""Charlotte FL Revize roster hardening (2026-10-07): no network.

Fixtures follow the Revize ``/bookings`` roster columns shared with Manatee
(Booking # | Last Name | First Name | Middle | Charge | Arrest Date | Released)
with synthetic people. The box gets a Cloudflare challenge on every Charlotte
URL, so the live table is re-checked by the Leads Ops residential run. The CF
fixture is trimmed from the live 2026-10-07 7:12 PM ET 403 response.
"""
from __future__ import annotations

import pytest

from dashboard.services.packet_builder_service import (
    arrest_bond_value,
    charge_details_from_sources,
)
from scoring.lead_scorer import LeadScorer
from scrapers.counties import charlotte
from scrapers.counties.charlotte import ROSTER, CharlotteCountyScraper
from scrapers.revize_roster import extract_published_total, is_egress_block
from scrapers.scraper_resilience import (
    ERROR_ANTI_BOT,
    EgressBlocked,
    ParseDriftError,
    classify_exception,
)

HEADERS = ["Booking #", "Last Name", "First Name", "Mid.", "Charge", "Arrest Date", "Released"]

LIVE_CF_TITLE = "Just a moment..."
LIVE_CF_HEADERS = {"server": "cloudflare", "cf-mitigated": "challenge",
                   "content-type": "text/html; charset=UTF-8"}
LIVE_CF_BODY = ('<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title></head><body>'
                '<noscript>Enable JavaScript and cookies to continue</noscript>'
                '<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1?ray=a470969"></script>'
                '</body></html>')


def _row(booking="26-004512", last="DOE", first="JANE", middle="Q",
         charge="BATTERY (DOMESTIC)", arrest="10/06/2026 14:35", released="", href=None, img=""):
    href = f"/bookings/{booking}" if href is None else href
    return {"cells": [booking, last, first, middle, charge, arrest, released], "href": href, "img": img}


def _page(rows, *, headers=HEADERS, text="", next_href="", max_page=1, has_table=True):
    return {"has_table": has_table, "headers": headers, "rows": rows, "text": text,
            "next_href": next_href, "max_page": max_page, "title": "Bookings"}


def _fetcher(pages):
    calls = []

    def fetch(url, pg):
        calls.append(url)
        return pages[pg - 1]

    fetch.calls = calls
    return fetch


def _walk(pages, **kw):
    return ROSTER.walk(_fetcher(pages), sleep=lambda s: None, **kw)


# ── Keys, charges, bond ─────────────────────────────────────────────────────
def test_charlotte_groups_every_charge_under_the_source_booking_number():
    rows = ROSTER.parse_roster_page(_page([
        _row(charge="BATTERY (DOMESTIC)"),
        _row(charge="BATTERY (DOMESTIC)"),  # second count = second bond: keep it
        _row(booking="26-004513", last="ROE", first="RICHARD", middle="", charge="DUI",
             released="10/07/2026"),
    ]))
    recs = ROSTER.build_records(rows)
    assert [r.Booking_Number for r in recs] == ["26-004512", "26-004513"]
    jane, rich = recs
    assert jane.County == "Charlotte" and jane.Facility == "Charlotte County Jail"
    assert jane.Full_Name == "DOE, JANE Q"
    assert jane.Charges == "BATTERY (DOMESTIC) | BATTERY (DOMESTIC)"
    assert len(jane.extra_data["charge_details"]) == 2
    assert (jane.Arrest_Date, jane.Arrest_Time) == ("2026-10-06", "14:35")
    assert jane.Detail_URL == "https://inmates.charlottecountyfl.revize.com/bookings/26-004512"
    assert jane.extra_data["booking_key_origin"] == "source-issued Revize Booking #"
    assert (rich.Status, rich.Release_Date) == ("Released", "2026-10-07")


def test_charlotte_bond_is_empty_not_zero_and_scorer_does_not_penalise():
    rec = ROSTER.build_records(ROSTER.parse_roster_page(_page([_row()])))[0]
    assert rec.Bond_Amount == "" and rec.Bond_Type == ""
    assert rec.extra_data["bond_published"] is False
    scorer = LeadScorer()
    unknown = scorer.score_arrest(rec)
    assert not any("Bond amount" in b for b in scorer.get_score_breakdown())
    rec.Bond_Amount = "0"
    zero = LeadScorer().score_arrest(rec)
    assert unknown > zero  # the old "0" cost a -50 "no bond" penalty


def test_charlotte_columns_mapped_by_header_not_position():
    headers = ["Released", "Charge", "Arrest Date", "Booking #", "First Name", "Last Name"]
    row = {"cells": ["", "DUI", "10/06/2026", "26-004514", "SAM", "POE"], "href": "/bookings/26-004514"}
    rec = ROSTER.build_records(ROSTER.parse_roster_page(_page([row], headers=headers)))[0]
    assert (rec.Booking_Number, rec.Last_Name, rec.First_Name, rec.Charges) == ("26-004514", "POE", "SAM", "DUI")


def test_charlotte_booking_link_mismatch_is_a_column_shift():
    with pytest.raises(ParseDriftError, match="column shift"):
        ROSTER.parse_roster_page(_page([_row(href="/bookings/26-009999")]))


def test_charlotte_duplicate_booking_for_two_people_fails_closed():
    rows = ROSTER.parse_roster_page(_page([_row(), _row(last="ROE", first="RICHARD")]))
    with pytest.raises(ParseDriftError, match="key collision"):
        ROSTER.build_records(rows)


def test_charlotte_name_in_booking_column_fails_closed():
    with pytest.raises(ParseDriftError, match="no source Booking #"):
        ROSTER.parse_roster_page(_page([_row(booking="DOE")]))


# ── Released ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value,expected", [
    ("", ("In Custody", "")), ("In Custody", ("In Custody", "")), ("N/A", ("In Custody", "")),
    ("Yes", ("Released", "")), ("10/07/2026 09:15 AM", ("Released", "2026-10-07")),
])
def test_charlotte_released_values(value, expected):
    assert ROSTER.parse_released(value) == expected


def test_charlotte_released_drift_and_missing_column_fail_closed():
    with pytest.raises(ParseDriftError, match="Charlotte: unrecognised Released value"):
        ROSTER.parse_released("Transferred")
    # The pre-audit parser read six positional cells with no Released column.
    with pytest.raises(ParseDriftError, match=r"Charlotte: missing column\(s\) \['released'\]"):
        ROSTER.parse_roster_page(_page([_row()], headers=HEADERS[:6]))


# ── Paging guards ───────────────────────────────────────────────────────────
def test_charlotte_walks_all_pages_and_matches_published_total():
    pages = [
        _page([_row()], text="Showing 1 to 1 of 2 entries", next_href="/bookings?page=2"),
        _page([_row(booking="26-004513", last="ROE", first="RICHARD")], text="Showing 2 to 2 of 2 entries"),
    ]
    recs, meta = _walk(pages)
    assert len(recs) == 2 and meta == {"pages": 2, "rows": 2, "bookings": 2, "published_total": 2}


def test_charlotte_published_total_mismatch_fails_closed():
    with pytest.raises(ParseDriftError, match="publishes a total of 3"):
        _walk([_page([_row()], text="Showing 1 to 1 of 3 entries")])
    assert extract_published_total("Show 100 entries") is None


def test_charlotte_repeated_page_fails_closed():
    pages = [_page([_row()], next_href="/bookings?page=2"), _page([_row()])]
    with pytest.raises(ParseDriftError, match="repeats page 1"):
        _walk(pages)


def test_charlotte_max_pages_with_next_offered_fails_closed():
    pages = [_page([_row(booking=f"26-00{4500 + i}")], next_href=f"/bookings?page={i + 2}") for i in range(3)]
    with pytest.raises(ParseDriftError, match="MAX_PAGES=2"):
        _walk(pages, max_pages=2)


def test_charlotte_missing_table_and_empty_pages_fail_closed():
    with pytest.raises(ParseDriftError, match="no roster table"):
        _walk([_page([], has_table=False)])
    with pytest.raises(ParseDriftError, match="empty on page 1"):
        _walk([_page([])])
    with pytest.raises(ParseDriftError, match="page 2 is empty"):
        _walk([_page([_row()], max_page=2), _page([])])


# ── Egress ──────────────────────────────────────────────────────────────────
def test_charlotte_live_cf_challenge_is_an_egress_block():
    assert is_egress_block(status=403, headers=LIVE_CF_HEADERS)
    assert is_egress_block(title=LIVE_CF_TITLE)
    assert is_egress_block(body=LIVE_CF_BODY)
    verdict = classify_exception(EgressBlocked("egress_block: Charlotte"))
    assert verdict.error_class == ERROR_ANTI_BOT and verdict.egress_block and not verdict.retryable


def test_charlotte_direct_mode_refuses_unverified_exit(monkeypatch):
    monkeypatch.setenv("CHARLOTTE_EGRESS_MODE", "direct")
    import scrapers.socks_proxy as sp
    # Rate-limited exit lookup -> org/country unknown -> not residential (#113 fix).
    monkeypatch.setattr(sp, "validate_residential_proxy", lambda url, require_residential_exit=True: (
        False, {"ip": "203.0.113.9", "org": "", "country": "", "error": "could not verify the exit IP"}))
    with pytest.raises(EgressBlocked, match="egress_block: CHARLOTTE_EGRESS_MODE=direct"):
        charlotte.resolve_egress()


def test_charlotte_direct_mode_never_resolves_a_proxy(monkeypatch):
    monkeypatch.setenv("CHARLOTTE_EGRESS_MODE", "direct")
    import scrapers.socks_proxy as sp
    called = []
    monkeypatch.setattr(sp, "validate_residential_proxy", lambda url, require_residential_exit=True: (
        True, {"ip": "198.51.100.7", "org": "Comcast", "country": "US"}))
    monkeypatch.setattr(sp, "resolve_residential_proxy", lambda *a, **k: called.append(1))
    assert charlotte.resolve_egress() == (None, "direct")
    assert called == []


def test_charlotte_auto_mode_without_residential_exit_is_egress_block(monkeypatch):
    monkeypatch.delenv("CHARLOTTE_EGRESS_MODE", raising=False)
    import scrapers.socks_proxy as sp

    def boom(*a, **k):
        raise RuntimeError("No healthy residential egress available for WAF/CF scrapers.")

    monkeypatch.setattr(sp, "resolve_residential_proxy", boom)
    with pytest.raises(EgressBlocked, match="egress_block: no residential exit for Charlotte"):
        charlotte.resolve_egress()
    monkeypatch.setenv("CHARLOTTE_EGRESS_MODE", "proxyservice")
    with pytest.raises(ValueError):
        charlotte.egress_mode()


class _FakeResp:
    status = 403
    headers = LIVE_CF_HEADERS


class _FakePage:
    def goto(self, url, **kw):
        return _FakeResp()

    def evaluate(self, js):
        return {"has_table": False, "headers": [], "rows": [], "text": "", "title": LIVE_CF_TITLE}

    def content(self):
        return LIVE_CF_BODY

    def title(self):
        return LIVE_CF_TITLE


class _Closable:
    def close(self):
        pass

    def stop(self):
        pass


def test_charlotte_scrape_raises_egress_block_and_returns_nothing(monkeypatch):
    monkeypatch.setattr(charlotte, "resolve_egress", lambda scraper=None: (None, "direct"))
    monkeypatch.setenv("CHARLOTTE_EGRESS_MODE", "direct")
    import scrapers.cf_browser as cfb
    monkeypatch.setattr(cfb, "launch_cf_browser", lambda *a, **k: (_Closable(), _Closable(), "playwright"))
    monkeypatch.setattr(cfb, "new_stealth_context",
                        lambda b: type("C", (), {"new_page": lambda self: _FakePage()})())
    monkeypatch.setattr(cfb, "wait_past_cloudflare", lambda page, label="", max_wait=45: False)
    with pytest.raises(EgressBlocked, match="egress_block: Charlotte page 1 .*Nothing written"):
        CharlotteCountyScraper().scrape()


# ── Hydrate: unknown bond is never $0 ───────────────────────────────────────
def test_charlotte_new_record_hydrates_bond_unknown():
    rec = ROSTER.build_records(ROSTER.parse_roster_page(_page([_row(), _row(charge="DUI")])))[0]
    doc = rec.to_mongo_doc()
    doc["extra"] = rec.extra_data
    assert arrest_bond_value(doc) == ""
    rows = charge_details_from_sources(arrest=doc, default_bond=None)
    assert [r["bond_amount"] for r in rows] == [None, None]


def test_legacy_charlotte_scraped_zero_hydrates_unknown_not_zero():
    # Docs written before this fix carry bond_amount_raw "0" for a bond the
    # roster never published.
    legacy = {"county": "Charlotte", "state": "FL", "bond_amount": 0.0, "bond_amount_raw": "0"}
    assert arrest_bond_value(legacy) == ""
    assert arrest_bond_value({**legacy, "county": "Charlotte County"}) == ""
    assert arrest_bond_value({**legacy, "county": "Manatee"}) == ""
    assert arrest_bond_value({"county": "Charlotte", "state": "FL", "bond_amount": 0.0}) == ""
    # Other counties publishing $0 keep it; a staff-set positive amount wins.
    assert arrest_bond_value({**legacy, "county": "Lee"}) == "0"
    assert arrest_bond_value({**legacy, "county": "Charlotte", "state": "GA"}) == "0"
    assert arrest_bond_value({**legacy, "bond_amount": 5000.0, "bond_override": True}) == 5000.0


def test_staff_flag_with_zero_after_rescrape_is_not_a_known_zero():
    # update-bond-amount sets bond_override=True; the next scrape $sets
    # bond_amount back to 0.0 and leaves the flag. That must not hydrate $0.
    rescraped = {"county": "Lee", "state": "FL", "bond_amount": 0.0, "bond_amount_raw": "",
                 "bond_override": True}
    assert arrest_bond_value(rescraped) == ""
