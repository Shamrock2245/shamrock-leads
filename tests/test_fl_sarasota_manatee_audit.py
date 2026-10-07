"""Sarasota / Manatee FL audit (2026-10-07): no network.

Manatee fixtures follow the documented Revize roster columns
(Booking # | Last Name | First Name | Middle | Charge | Arrest Date | Released);
the box egress gets a Cloudflare challenge, so the live table itself is
re-checked by the Leads Ops residential run. The CF fixture is trimmed from
the live 2026-10-07 403 response. Sarasota fixtures follow the live
current-inmate listing shape with synthetic names.
"""
from __future__ import annotations

import pytest

from core.models import ArrestRecord
from dashboard.services.packet_builder_service import (
    arrest_bond_value,
    build_adaptive_field_map,
    charge_details_from_sources,
)
from scoring.lead_scorer import LeadScorer
from scrapers.counties import manatee
from scrapers.counties.manatee import (
    ManateeCountyScraper,
    build_records,
    is_egress_block,
    parse_released,
    parse_roster_page,
    walk_roster,
)
from scrapers.counties.sarasota import SarasotaCountyScraper
from scrapers.counties.sarasota_contract import assess_listing
from scrapers.scraper_resilience import (
    ERROR_ANTI_BOT,
    EgressBlocked,
    ParseDriftError,
    classify_exception,
)

HEADERS = ["Booking #", "Last Name", "First Name", "Middle", "Charge", "Arrest Date", "Released"]


def _row(booking="2026012345", last="DOE", first="JANE", middle="Q",
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


# ── Manatee: keys, charges, bond, released ──────────────────────────────────
def test_manatee_maps_by_header_and_groups_charge_rows_per_booking():
    rows = parse_roster_page(_page([
        _row(charge="BATTERY (DOMESTIC)"),
        _row(charge="RESIST OFFICER WITHOUT VIOLENCE"),
        _row(booking="2026012346", last="ROE", first="RICHARD", middle="", charge="DUI", released="Released"),
    ]))
    recs = build_records(rows)
    assert [r.Booking_Number for r in recs] == ["2026012345", "2026012346"]
    jane = recs[0]
    assert (jane.Full_Name, jane.First_Name, jane.Middle_Name, jane.Last_Name) == ("DOE, JANE Q", "JANE", "Q", "DOE")
    assert jane.Charges == "BATTERY (DOMESTIC) | RESIST OFFICER WITHOUT VIOLENCE"
    assert [c["charge"] for c in jane.extra_data["charge_details"]] == [
        "BATTERY (DOMESTIC)", "RESIST OFFICER WITHOUT VIOLENCE"]
    assert (jane.Arrest_Date, jane.Arrest_Time, jane.Booking_Date) == ("2026-10-06", "14:35", "2026-10-06")
    assert jane.Status == "In Custody" and jane.Release_Date == ""
    assert jane.Detail_URL == "https://manatee-sheriff.revize.com/bookings/2026012345"
    assert jane.extra_data["booking_key_origin"] == "source-issued Revize Booking #"
    assert recs[1].Status == "Released"


def test_manatee_columns_mapped_by_header_not_position():
    headers = ["Released", "Charge", "Booking #", "Arrest Date", "First Name", "Last Name", "Middle"]
    row = {"cells": ["", "THEFT", "2026099999", "10/07/2026", "ANN", "LEE", ""], "href": "", "img": ""}
    recs = build_records(parse_roster_page(_page([row], headers=headers)))
    assert recs[0].Booking_Number == "2026099999" and recs[0].Full_Name == "LEE, ANN"


def test_manatee_bond_is_empty_not_zero_and_scorer_skips_it():
    rec = build_records(parse_roster_page(_page([_row()])))[0]
    assert rec.Bond_Amount == "" and rec.Bond_Type == ""
    assert rec.extra_data["bond_published"] is False
    scorer = LeadScorer()
    scorer.score_arrest(rec)
    assert not any("Bond amount" in b for b in scorer.get_score_breakdown())
    assert rec.to_mongo_doc()["bond_amount_raw"] == ""


def test_manatee_malformed_booking_dropped_and_name_key_page_fails_closed():
    rows = parse_roster_page(_page([_row(), _row(booking="DOE", href="")]))
    assert [r["booking"] for r in rows] == ["2026012345"]
    with pytest.raises(ParseDriftError, match="no source Booking"):
        parse_roster_page(_page([_row(booking="SMITH", href=""), _row(booking="", href="")]))


def test_manatee_detail_link_mismatch_is_column_shift():
    with pytest.raises(ParseDriftError, match="column shift"):
        parse_roster_page(_page([_row(booking="2026012345", href="/bookings/2026000001")]))


def test_manatee_booking_key_collision_fails_closed():
    rows = parse_roster_page(_page([_row(), _row(last="SMITH", first="JOHN")]))
    with pytest.raises(ParseDriftError, match="collision"):
        build_records(rows)


@pytest.mark.parametrize("value,expected", [
    ("", ("In Custody", "")),
    ("In Custody", ("In Custody", "")),
    ("No", ("In Custody", "")),
    ("Yes", ("Released", "")),
    ("10/07/2026", ("Released", "2026-10-07")),
    ("10/07/2026 09:15", ("Released", "2026-10-07")),
])
def test_manatee_released_values(value, expected):
    assert parse_released(value) == expected


@pytest.mark.parametrize("value", ["Transferred", "Bonded Out?", "Pending"])
def test_manatee_unrecognised_released_value_fails_closed(value):
    with pytest.raises(ParseDriftError, match="Released"):
        parse_released(value)


def test_manatee_missing_released_column_fails_closed():
    with pytest.raises(ParseDriftError, match="released"):
        parse_roster_page(_page([_row()], headers=HEADERS[:-1]))


def test_manatee_mugshot_only_when_source_offers_it():
    rec = build_records(parse_roster_page(_page([_row(img="/images/mug/2026012345.jpg")])))[0]
    assert rec.Mugshot_URL == "https://manatee-sheriff.revize.com/images/mug/2026012345.jpg"
    assert build_records(parse_roster_page(_page([_row()])))[0].Mugshot_URL == ""


# ── Manatee: paging drift guards ────────────────────────────────────────────
def _two_page_walk(page2_rows, *, text1="", text2=""):
    p1 = _page([_row(booking="2026010001"), _row(booking="2026010002", last="ROE")],
               text=text1, next_href="/bookings?page=2", max_page=2)
    p2 = _page(page2_rows, text=text2, max_page=2)
    return _fetcher([p1, p2])


def test_manatee_walks_all_pages_and_matches_published_total():
    fetch = _two_page_walk([_row(booking="2026010003", last="POE")],
                           text1="Showing 1 to 2 of 3 bookings", text2="Showing 3 to 3 of 3 bookings")
    recs, meta = walk_roster(fetch, sleep=lambda s: None)
    assert [r.Booking_Number for r in recs] == ["2026010001", "2026010002", "2026010003"]
    assert meta == {"pages": 2, "rows": 3, "bookings": 3, "published_total": 3}
    assert fetch.calls[1] == "https://manatee-sheriff.revize.com/bookings?page=2"


def test_manatee_count_mismatch_against_published_total_fails_closed():
    fetch = _two_page_walk([_row(booking="2026010003", last="POE")], text1="Showing 1 to 2 of 40 bookings")
    with pytest.raises(ParseDriftError, match="publishes a total of 40"):
        walk_roster(fetch, sleep=lambda s: None)


def test_manatee_page_size_selector_is_not_a_published_total():
    fetch = _fetcher([_page([_row()], text="Show 10 25 50 100 entries")])
    recs, meta = walk_roster(fetch, sleep=lambda s: None)
    assert len(recs) == 1 and meta["published_total"] is None


def test_manatee_repeated_page_fails_closed():
    fetch = _two_page_walk([_row(booking="2026010001"), _row(booking="2026010002", last="ROE")])
    with pytest.raises(ParseDriftError, match="repeats page 1"):
        walk_roster(fetch, sleep=lambda s: None)


def test_manatee_max_pages_with_next_offered_fails_closed():
    pages = [
        _page([_row(booking=f"20260100{i:02d}", last=f"P{i}")], next_href=f"/bookings?page={i + 1}", max_page=i + 1)
        for i in range(1, 4)
    ]
    with pytest.raises(ParseDriftError, match="MAX_PAGES=3"):
        walk_roster(_fetcher(pages), max_pages=3, sleep=lambda s: None)


def test_manatee_missing_table_and_empty_pages_fail_closed():
    with pytest.raises(ParseDriftError, match="no roster table"):
        walk_roster(_fetcher([_page([], has_table=False)]), sleep=lambda s: None)
    with pytest.raises(ParseDriftError, match="empty on page 1"):
        walk_roster(_fetcher([_page([])]), sleep=lambda s: None)
    with pytest.raises(ParseDriftError, match="page 2 is empty"):
        walk_roster(_two_page_walk([]), sleep=lambda s: None)


def test_manatee_pager_advertising_more_pages_is_followed():
    p1 = _page([_row(booking="2026010001")], max_page=2)  # no "next" link, numbered pager only
    p2 = _page([_row(booking="2026010002", last="ROE")], max_page=2)
    recs, meta = walk_roster(_fetcher([p1, p2]), sleep=lambda s: None)
    assert meta["pages"] == 2 and len(recs) == 2


# ── Manatee: egress-block classification and residential path ──────────────
LIVE_CF_TITLE = "Just a moment..."
LIVE_CF_HEADERS = {"server": "cloudflare", "cf-mitigated": "challenge", "content-type": "text/html; charset=UTF-8"}
LIVE_CF_BODY = ('<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title></head><body>'
                '<noscript>Enable JavaScript and cookies to continue</noscript>'
                '<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script></body></html>')


def test_manatee_live_cf_challenge_is_classified_as_egress_block():
    assert is_egress_block(status=403, headers=LIVE_CF_HEADERS)
    assert is_egress_block(title=LIVE_CF_TITLE)
    assert is_egress_block(body=LIVE_CF_BODY)
    assert not is_egress_block(status=200, title="Bookings | Manatee County Sheriff", headers={"server": "cloudflare"})
    verdict = classify_exception(EgressBlocked("egress_block: test"))
    assert verdict.error_class == ERROR_ANTI_BOT and verdict.egress_block and not verdict.retryable


def test_manatee_direct_mode_refuses_non_residential_host(monkeypatch):
    monkeypatch.setenv("MANATEE_EGRESS_MODE", "direct")
    import scrapers.socks_proxy as sp
    monkeypatch.setattr(sp, "validate_residential_proxy",
                        lambda url, require_residential_exit=True: (False, {"ip": "203.0.113.9", "org": "Hetzner"}))
    with pytest.raises(EgressBlocked, match="egress_block: MANATEE_EGRESS_MODE=direct"):
        manatee.resolve_egress()


def test_manatee_direct_mode_uses_host_egress_without_proxy(monkeypatch):
    monkeypatch.setenv("MANATEE_EGRESS_MODE", "direct")
    import scrapers.socks_proxy as sp
    called = []
    monkeypatch.setattr(sp, "validate_residential_proxy",
                        lambda url, require_residential_exit=True: (True, {"ip": "198.51.100.7", "org": "Comcast"}))
    monkeypatch.setattr(sp, "resolve_residential_proxy", lambda *a, **k: called.append(1))
    assert manatee.resolve_egress() == (None, "direct")
    assert called == []


def test_manatee_auto_mode_no_residential_exit_is_egress_block(monkeypatch):
    monkeypatch.delenv("MANATEE_EGRESS_MODE", raising=False)
    import scrapers.socks_proxy as sp

    def boom(*a, **k):
        raise RuntimeError("No healthy residential egress available for WAF/CF scrapers.")

    monkeypatch.setattr(sp, "resolve_residential_proxy", boom)
    with pytest.raises(EgressBlocked, match="egress_block: no residential exit"):
        manatee.resolve_egress()


def test_manatee_bad_egress_mode_fails_loudly(monkeypatch):
    monkeypatch.setenv("MANATEE_EGRESS_MODE", "proxyservice")
    with pytest.raises(ValueError):
        manatee.egress_mode()


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


class _FakeBrowser:
    def close(self):
        pass


class _FakePW:
    def stop(self):
        pass


def test_manatee_scrape_raises_egress_block_instead_of_silent_empty(monkeypatch):
    monkeypatch.setenv("MANATEE_EGRESS_MODE", "direct")
    monkeypatch.setattr(manatee, "resolve_egress", lambda scraper=None: (None, "direct"))
    import scrapers.cf_browser as cfb
    monkeypatch.setattr(cfb, "launch_cf_browser", lambda *a, **k: (_FakePW(), _FakeBrowser(), "playwright"))
    monkeypatch.setattr(cfb, "new_stealth_context", lambda b: type("C", (), {"new_page": lambda self: _FakePage()})())
    monkeypatch.setattr(cfb, "wait_past_cloudflare", lambda page, label="", max_wait=45: False)
    with pytest.raises(EgressBlocked, match="egress_block: Manatee page 1"):
        ManateeCountyScraper().scrape()


# ── Hydrate: unknown bond stays unknown, $0 stays $0 ────────────────────────
def test_hydrate_unknown_bond_is_not_coerced_to_zero():
    rec = build_records(parse_roster_page(_page([_row(), _row(charge="RESIST OFFICER")])))[0]
    doc = rec.to_mongo_doc()
    doc["extra"] = rec.extra_data
    assert doc["bond_amount"] == 0.0  # numeric column unchanged for existing queries
    assert arrest_bond_value(doc) == ""
    rows = charge_details_from_sources(arrest=doc, charges_text=doc["charges"], default_bond=None)
    assert [r["charge"] for r in rows] == ["BATTERY (DOMESTIC)", "RESIST OFFICER"]
    assert [r["bond_amount"] for r in rows] == [None, None]
    bare = charge_details_from_sources(charges_text="A | B", default_bond=None)
    assert [r["bond_amount"] for r in bare] == [None, None]
    fields = build_adaptive_field_map({"defendant": {"name": rec.Full_Name}, "booking_number": rec.Booking_Number,
                                       "county": "Manatee", "bond_amount": 0.0, "charge_details": rows})
    assert "bond_amount" not in fields
    assert fields["booking_number"] == "2026012345"
    assert (fields["offense_1"], fields["offense_2"]) == ("BATTERY (DOMESTIC)", "RESIST OFFICER")


def test_hydrate_published_bond_and_published_zero_preserved():
    assert arrest_bond_value({"bond_amount": 0.0, "bond_amount_raw": "0"}) == "0"
    assert arrest_bond_value({"bond_amount": 2500.0, "bond_amount_raw": "2500"}) == "2500"
    assert arrest_bond_value({"bond_amount": 500.0}) == 500.0  # legacy docs without raw
    rows = charge_details_from_sources(arrest={"extra": {"charge_details": [
        {"charge": "A", "bond_amount": "0"},
        {"charge": "B", "bond_amount": "1,500"},
        {"charge": "C", "bond_amount": ""},
    ]}})
    assert [r["bond_amount"] for r in rows] == [0.0, 1500.0, None]
    assert charge_details_from_sources(charges_text="A | B", default_bond=1000.0)[0]["bond_amount"] == 1000.0


@pytest.mark.asyncio
async def test_resolve_case_context_marks_manatee_bond_unknown(monkeypatch):
    rec = build_records(parse_roster_page(_page([_row()])))[0]
    doc = rec.to_mongo_doc()
    doc["extra"] = rec.extra_data
    doc["charge_details"] = rec.extra_data["charge_details"]

    class _Cursor:
        def __init__(self, docs):
            self.docs = docs

        async def to_list(self, length=None):
            return self.docs

    class _Coll:
        def __init__(self, name):
            self.name = name

        def find(self, q, proj=None):
            return _Cursor([dict(doc)] if self.name == "arrests" else [])

        async def find_one(self, q, proj=None):
            return None

    import dashboard.extensions as ext
    monkeypatch.setattr(ext, "get_collection", lambda name: _Coll(name))
    import dashboard.services.past_bond_search as pbs
    async def _no_prior(*a, **k):
        return None
    monkeypatch.setattr(pbs, "find_prior_bond_for_defendant", _no_prior)

    from dashboard.services.packet_builder_service import resolve_case_context
    ctx = await resolve_case_context(booking_number="2026012345", county="Manatee", state="FL")
    assert ctx["booking_number"] == "2026012345"
    assert ctx["bond_amount"] == 0.0 and ctx["bond_amount_known"] is False
    assert [r["bond_amount"] for r in ctx["charge_details"]] == [None]
    assert ctx["charges"] == "BATTERY (DOMESTIC)"
    assert ctx["defendant"]["last_name"] == "DOE" and ctx["defendant"]["first_name"] == "JANE"


def test_staff_bond_edits_win_over_stale_blank_raw():
    # Codex P1: update-bond-amount / update-charge-bonds set the numeric
    # bond_amount but leave bond_amount_raw="" from the Manatee scrape.
    scraped = {"bond_amount": 0.0, "bond_amount_raw": ""}
    assert arrest_bond_value(scraped) == ""
    assert arrest_bond_value({**scraped, "bond_amount": 7500.0, "bond_override": True}) == 7500.0
    # A zero next to the staff flags is ambiguous: the next scrape rewrites
    # bond_amount to 0.0 but leaves the flags, so it stays unknown (blank).
    assert arrest_bond_value({**scraped, "bond_amount": 0.0, "bond_override": True}) == ""
    charge_edit = {**scraped, "bond_amount": 3000.0, "last_checked_mode": "MANUAL_CHARGE_BONDS"}
    assert arrest_bond_value(charge_edit) == 3000.0
    # Any later positive numeric with a blank raw is still a real amount.
    assert arrest_bond_value({**scraped, "bond_amount": 1200.0}) == 1200.0


def test_staff_edited_charge_rows_win_over_scraped_extra():
    # Codex P1: update-charge-bonds writes top-level charge_details; the scraped
    # extra.charge_details must not shadow the staff amounts / case / POA.
    rec = build_records(parse_roster_page(_page([_row(), _row(charge="RESIST OFFICER")])))[0]
    doc = rec.to_mongo_doc()
    doc["extra"] = rec.extra_data
    doc["charge_details"] = [
        {"charge": "BATTERY (DOMESTIC)", "bond_amount": 2500.0, "bond_type": "Surety",
         "case_number": "2026-MM-000111", "poa_number": "POA-77"},
        {"charge": "RESIST OFFICER", "bond_amount": 500.0, "bond_type": "Surety",
         "case_number": "2026-MM-000111"},
    ]
    rows = charge_details_from_sources(arrest=doc, default_case="", default_bond=None)
    assert [r["bond_amount"] for r in rows] == [2500.0, 500.0]
    assert rows[0]["case_number"] == "2026-MM-000111" and rows[0]["poa_number"] == "POA-77"
    # Writer's promoted copy (same as extra) still hydrates unknown, not $0.
    doc["charge_details"] = rec.extra_data["charge_details"]
    assert [r["bond_amount"] for r in charge_details_from_sources(arrest=doc)] == [None, None]


# ── Sarasota: stays fail_closed; reopen gate on the live listing shape ──────
SARASOTA_LIVE_SHAPED = """
<h1 class="page-title">Current Inmate Population</h1>
<div class="alert alert-info">This database contains current inmates that are held in the Sarasota County Jail.</div>
<div class="dropdown-menu" aria-labelledby="dropdownMenuButton">
  <a class="dropdown-item" href="viewInmate.php?id=0200000001     ">DOE,JANE QUINN                      - 01/02/1990</a>
  <a class="dropdown-item" href="viewInmate.php?id=0000000002     ">ROE,RICHARD                         - 03/04/1985</a>
  <a class="dropdown-item" href="viewInmate.php?id=0201000003     ">POE,EDGAR A                         - 05/06/2001</a>
</div>
<form action="personSearch.php" method="GET"><input type="hidden" name="type" value="date"></form>
"""


def test_sarasota_live_listing_shape_fails_reopen_gate():
    verdict = assess_listing(SARASOTA_LIVE_SHAPED)
    assert verdict.listed == 3 and verdict.unique_link_ids == 3
    assert verdict.reopen_ok is False
    assert "no source booking number on the listing (link id only)" in verdict.reasons
    assert "no booking date/time on the listing" in verdict.reasons


def test_sarasota_gate_ignores_page_wide_booking_labels():
    # Codex P2: an empty "Booking Number / Booking Date" header elsewhere on the
    # page must not reopen while every entry is still link id + name + DOB.
    decoy = SARASOTA_LIVE_SHAPED + "<table><tr><th>Booking Number</th><th>Booking Date</th></tr></table>"
    verdict = assess_listing(decoy)
    assert verdict.reopen_ok is False
    assert "no source booking number on the listing (link id only)" in verdict.reasons
    assert assess_listing("<html>nothing</html>").reopen_ok is False


def test_sarasota_gate_opens_only_when_every_entry_has_booking_and_timestamp():
    def entry(i, booking, when):
        return (
            f'<a class="dropdown-item" href="viewInmate.php?id=02000000{i:02d}">'
            f"DOE,JANE - Booking #: {booking} - Booked: {when}</a>"
        )

    full = entry(1, "2026-012345", "10/06/2026 21:14") + entry(2, "2026-012346", "10/07/2026 03:02")
    assert assess_listing(full).reopen_ok is True
    # One entry with a date but no time, or without a booking number -> closed.
    assert assess_listing(full + entry(3, "2026-012347", "10/07/2026")).reopen_ok is False
    no_key = full + '<a href="viewInmate.php?id=0200000009">ROE,RICHARD - Booked: 10/07/2026 04:00</a>'
    assert assess_listing(no_key).reopen_ok is False


def test_sarasota_still_fail_closed_with_documented_reason():
    assert SarasotaCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert "no booking number" in SarasotaCountyScraper.SOURCE_CONTRACT_REASON
    assert SarasotaCountyScraper().scrape() == []
    from dashboard.extensions import SCRAPER_SOURCE_STATES
    assert SCRAPER_SOURCE_STATES["Sarasota (FL)"] == "fail_closed"
    assert "Manatee (FL)" not in SCRAPER_SOURCE_STATES  # Health default: unverified


# ── Residential preflight: an unknown exit is not a residential exit ────────
class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _fake_httpx(responses):
    class _Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            for prefix, resp in responses.items():
                if url.startswith(prefix):
                    return resp
            raise RuntimeError("unexpected url " + url)

    return _Client


def test_exit_check_rate_limited_lookup_is_not_residential(monkeypatch):
    # Live 2026-10-07: ipinfo 429, ipify ok, ipapi 429 -> org/country empty.
    import httpx
    from scrapers.cf_browser import check_exit_ip, require_residential_exit

    monkeypatch.setattr(httpx, "Client", _fake_httpx({
        "https://ipinfo.io": _Resp(429, {"status": 429}),
        "https://api.ipify.org": _Resp(200, {"ip": "140.248.50.100"}),
        "https://ipapi.co": _Resp(429, {"error": True, "reason": "RateLimited"}),
    }))
    info = check_exit_ip(None, timeout=1, retries=1)
    assert info["ok"] and info["exit_unverified"] and not info["residential_likely"]
    import scrapers.cf_browser as cfb
    monkeypatch.setattr(cfb, "check_exit_ip", lambda *a, **k: info)
    with pytest.raises(RuntimeError, match="could not verify the exit IP's org/country"):
        require_residential_exit(None, label="Manatee")


def test_exit_check_known_us_isp_is_residential(monkeypatch):
    import httpx
    from scrapers.cf_browser import check_exit_ip

    monkeypatch.setattr(httpx, "Client", _fake_httpx({
        "https://ipinfo.io": _Resp(200, {"ip": "73.1.2.3", "org": "AS7922 Comcast Cable", "country": "US"}),
    }))
    info = check_exit_ip(None, timeout=1, retries=1)
    assert info["residential_likely"] and not info["exit_unverified"]
