"""Southern Software Citizen Connect base: honest plain_http, published-only bond/charges/status.

Covers every module built on scrapers/southern_sw_base.py.
"""
import importlib
import inspect
import re

import pytest
import requests

from scrapers import southern_sw_base as ssw
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked

LIVE = {
    "counties_sc": ["chesterfield", "dorchester"],
    "counties_nc": ["anson", "caswell", "chowan", "duplin", "edgecombe", "granville", "harnett", "henderson",
                    "nash", "perquimans", "person", "polk", "robeson", "rockingham", "rowan", "sampson",
                    "scotland", "stokes", "surry", "transylvania", "vance", "warren", "wayne", "wilkes"],
    "counties_ga": ["appling", "banks", "bleckley", "coffee", "crisp", "decatur", "effingham", "laurens",
                    "lee", "oglethorpe", "tift", "ware"],
}
GUARDED = {  # fail-closed guards: override scrape() and never fetch
    "counties_al": ["baldwin", "cullman", "dekalb", "houston", "jackson", "morgan"],
    "counties_tn": ["bedford", "bradley", "coffee", "giles", "lincoln", "robertson"],
}


def _cls(pkg, mod):
    m = importlib.import_module(f"scrapers.{pkg}.{mod}")
    for _, obj in inspect.getmembers(m, inspect.isclass):
        if issubclass(obj, ssw.SouthernSWBaseScraper) and obj is not ssw.SouthernSWBaseScraper and obj.__module__ == m.__name__:
            return obj
    raise AssertionError(f"no SouthernSW subclass in {pkg}.{mod}")


LIVE_IDS = [(p, m) for p, ms in LIVE.items() for m in ms]
GUARD_IDS = [(p, m) for p, ms in GUARDED.items() for m in ms]


def test_inventory_is_complete():
    import pathlib
    found = set()
    for f in pathlib.Path("scrapers").glob("counties_*/*.py"):
        if "southern_sw_base" in f.read_text(errors="ignore"):
            found.add((f.parent.name, f.stem))
    assert found == set(LIVE_IDS) | set(GUARD_IDS)


CARD = """
<div class="card booking-card"><div class="card-body">
  <div class="booking-header"><h5>DOE, JANE Q</h5></div>
  <div>Demographics: 40 years / W / F</div>
  <div>Booked: 10/01/2026</div>
  {bond_total}
  {charges}
  <img class="booking-mugshot" data-bookingid="{bk}" data-json-nameid="9999" />
</div></div>
"""


def card(bk="4242", bond_total="<div>Bond Total: $5,000.00</div>",
         charges='<div class="charge-item">1 LARCENY Bond: $5,000.00</div>'):
    return CARD.format(bk=bk, bond_total=bond_total, charges=charges)


def _resp(code=200, body=""):
    r = requests.Response()
    r.status_code = code
    r._content = body.encode()
    r.headers["content-type"] = "text/html"
    r.url = "https://cc.southernsoftware.com/bookingsearch/"
    return r


INDEX = '<html><input name="JMSAgencyID" value="NC0000000"></html>'


@pytest.fixture()
def fake(monkeypatch):
    calls = []

    def install(current_pages, last7="", index_code=200, post_exc=None):
        def _req(sess, method, url, **kw):
            calls.append({"m": method, "url": url, "kw": kw, "ua": sess.headers.get("User-Agent")})
            if post_exc is not None and method == "POST":
                raise post_exc
            if "index.php" in url:
                return _resp(index_code, INDEX if index_code == 200 else "denied")
            if "fetch_current_confinements" in url:
                idx = int(kw["data"]["IDX"])
                return _resp(200, current_pages[idx - 1] if idx <= len(current_pages) else "")
            if "fetch_last7days" in url:
                return _resp(200, last7)
            return _resp(404, "")

        monkeypatch.setattr(requests.Session, "request", _req)
        monkeypatch.setattr(ssw.time, "sleep", lambda *_: None)
        return calls

    return install


@pytest.mark.parametrize("pkg,mod", LIVE_IDS, ids=[f"{p}.{m}" for p, m in LIVE_IDS])
def test_every_live_county_is_honest_and_published_only(fake, pkg, mod):
    calls = fake([card()])
    recs = _cls(pkg, mod)().scrape()
    assert len(recs) == 1
    r = recs[0]
    assert r.Booking_Number == "4242"
    assert r.Status == "In Custody"
    assert r.extra_data["status_source"] == "published_current_confinements_view"
    assert r.Bond_Amount == "5000"
    assert r.Charges == "LARCENY"
    assert calls and all(c["ua"] == HONEST_USER_AGENT for c in calls)
    assert all(c["kw"].get("verify") is True for c in calls)
    assert all("impersonate" not in c["kw"] for c in calls)


@pytest.mark.parametrize("pkg,mod", GUARD_IDS, ids=[f"{p}.{m}" for p, m in GUARD_IDS])
def test_guarded_counties_never_fetch(fake, pkg, mod):
    calls = fake([card()])
    cls = _cls(pkg, mod)
    assert cls.SOURCE_CONTRACT_VALIDATED is False
    assert cls().scrape() == []
    assert calls == []


def test_no_chrome_ua_or_unknown_defaults_in_base():
    src = inspect.getsource(ssw)
    assert "Chrome/" not in src and "impersonate" not in src
    assert 'else "Unknown"' not in src and 'Status="Unknown"' not in src and 'or "0"' not in src


def _parse(html, current=True):
    return _cls("counties_nc", "harnett")()._parse_booking_cards(html, current_view=current)


def test_published_zero_bond_total_kept():
    assert _parse(card(bond_total="<div>Bond Total: $0.00</div>"))[0].Bond_Amount == "0"


def test_no_bond_total_sums_only_when_every_charge_has_bond():
    two = ('<div class="charge-item">1 A Bond: $1,000.00</div>'
           '<div class="charge-item">2 B Bond: $500.00</div>')
    assert _parse(card(bond_total="", charges=two))[0].Bond_Amount == "1500"
    partial = ('<div class="charge-item">1 A Bond: $1,000.00</div>'
               '<div class="charge-item">2 B</div>')
    assert _parse(card(bond_total="", charges=partial))[0].Bond_Amount == ""


def test_no_bond_anywhere_is_blank():
    r = _parse(card(bond_total="", charges='<div class="charge-item">1 A</div>'))[0]
    assert r.Bond_Amount == ""


def test_no_charge_items_means_empty_charges_no_heuristic():
    r = _parse(card(bond_total="", charges="<div>SOME FREE TEXT LINE</div>"))[0]
    assert r.Charges == "" and r.Bond_Amount == ""


def test_non_current_parse_has_no_status():
    r = _parse(card(), current=False)[0]
    assert r.Status == "" and "status_source" not in r.extra_data


def test_last7days_fallback_has_no_custody_status(fake):
    fake([], last7=card(bk="777"))
    recs = _cls("counties_sc", "dorchester")().scrape()
    assert [r.Booking_Number for r in recs] == ["777"]
    assert recs[0].Status == "" and "status_source" not in recs[0].extra_data


def test_missing_booking_id_means_no_record():
    html = re.sub(r'<img[^>]*/>', "", card())
    assert _parse(html) == []


@pytest.mark.parametrize("code", [401, 403, 429])
def test_index_block_raises(fake, code):
    fake([card()], index_code=code)
    with pytest.raises(EgressBlocked):
        _cls("counties_sc", "chesterfield")().scrape()


@pytest.mark.parametrize("exc", [EgressBlocked("egress_block: x connection_reset: y"),
                                 SourceTLSInvalid("tls_cert_invalid: x")])
def test_roster_block_not_swallowed(fake, exc):
    fake([card()], post_exc=exc)
    with pytest.raises(type(exc)):
        _cls("counties_nc", "wayne")().scrape()


def test_other_transport_error_propagates_not_empty(fake):
    fake([card()], post_exc=requests.exceptions.ConnectionError("refused"))
    with pytest.raises(requests.exceptions.ConnectionError):
        _cls("counties_nc", "nash")().scrape()


def test_card_parse_error_log_has_no_name(caplog, monkeypatch):
    s = _cls("counties_nc", "harnett")()
    monkeypatch.setattr(s, "_parse_one_card", lambda *a, **k: (_ for _ in ()).throw(ValueError("DOE, JANE")))
    import logging
    with caplog.at_level(logging.DEBUG):
        assert s._parse_booking_cards(card()) == []
    assert not any("DOE" in r.getMessage() for r in caplog.records)
