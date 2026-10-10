"""Manatee Clerk (FL) court-filing scraper (owner exception, Brendan 2026-10-10).

Synthetic fixtures shaped like the live page labels (records.manateeclerk.com
CourtRecords, 2026-10-10 masked shape check); no real names, no network.

Covers: list/detail parse, bond rules (bond rows only; totals row ignored;
published $0 counts; none -> ""), the internal key never in booking or display
fields, a challenge stopping the run, the source guard, and every other
county's blank-booking guard unchanged.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.booking_identity import (
    INTERNAL_NATURAL_KEY_FIELDS,
    INTERNAL_NATURAL_KEY_SCOPES,
    MC_INTERNAL_KEY_RE,
    internal_natural_key,
    is_internal_booking_key,
    public_booking_number,
    redact_internal_keys,
    scrub_internal_keys,
)
from scrapers.base_scraper import BaseScraper
from scrapers.counties import manatee_clerk as mc
from scrapers.counties.manatee import ManateeCountyScraper
from scrapers.counties.manatee_clerk import ManateeClerkScraper
from scrapers.scraper_resilience import EgressBlocked, ParseDriftError, classify_exception
from tests.test_staff_edits_survive_rescrape import FakeArrests, _writer

ROOT = Path(__file__).resolve().parents[1]
CASE_A = "2026CF009901AX"   # synthetic
CASE_B = "2026MM009902AX"   # synthetic
OBTS_A = "5800000001"       # synthetic


def _list_row(i, case, name, ptype="Defendant", ctype="Felony", filed="10/09/2026"):
    return f"""
    <tr><td>{i}</td><td><form action="/CourtRecords/Case/Details" class="form-inline" method="post">
      <input name="__RequestVerificationToken" type="hidden" value="tok{i}"/>
      <input type="hidden" id="caseId" name="caseId" value="99{i:05d}"/>
      <input type="hidden" id="searchAddress" name="searchAddress" value="https://records.manateeclerk.com/x"/>
      <button class="btn" type="submit" title="View Case"></button></form></td>
      <td>{case}</td><td>{name}</td><td>{ptype}</td><td>{ctype}</td><td>OPEN</td><td>{filed}</td><td>1990</td></tr>"""


def list_page(rows, matching=None, empty=False):
    head = """<tr><th colspan="9">Page: 1</th></tr><tr><th></th><th>View</th><th>Case Number</th>
      <th>Party Name</th><th>Party Type</th><th>Case Type</th><th>Case Status</th><th>File Date</th><th>DOB</th></tr>"""
    count = "" if empty else f"<div>Matching Results: {len(rows) if matching is None else matching}</div>"
    none = "<p>We could not find any results that match your search.</p><p>NO RECORDS FOUND</p>" if empty else ""
    return f"""<html><body><h2><a class="site-name">Public Records Hub</a></h2>
      <h3>Search Court Records for a Cases by Type</h3>{count}
      <table id="results-table" class="display table table-primary">{head}{''.join(rows)}</table>{none}</body></html>"""


def bonds_table(rows, total_count=None, total="$0.00"):
    body = "".join(f"<tr><td>{t}</td><td>{a}</td></tr>" for t, a in rows)
    n = len(rows) if total_count is None else total_count
    return f"""<div class="panel panel-primary"><div class="panel-heading panel-title">Bonds:</div>
      <div class="panel-body table-responsive"><table class="table"><thead><tr><th>Bond Type</th><th>Active Amount</th></tr></thead>
      {body}<tr><td>{n} Bond(s)</td><td>{total}</td></tr></table></div></div>"""


def charge_row(n, offense, statute, desc, degree):
    def pair(label, value, cls):
        return (f'<div class="visible-xs-block faux-label"><strong>{label}</strong></div>'
                f'<div class="col-xs-7 {cls} faux-td">{value}</div>')
    return (f'<div class="row data-faux-row"><div class="sr-only">Charge {n}</div>'
            + pair("Offense Date", offense, "col-md-2") + pair("Statute", statute, "col-md-1")
            + pair("Description", desc, "col-md-6") + pair("Degree", degree, "col-md-2")
            + pair("Citation", "", "col-md-1") + "</div>")


def obts_block(n, served):
    return f"""<div class="panel obts-heading"><div class="panel-body">
      <div class="main">OBTS {OBTS_A} - Charge {n}</div><div class="sub">Agency: ZZPD - 2026Z000{n}</div></div></div>
      <div class="panel panel-primary"><div class="panel-heading">Initial Phase - Charge</div><div class="panel-body">
      <div class="offset-list">Offense Begin:<div class="offset-list-data">10/07/2026</div></div>
      <div class="offset-list">Arrest Summons Served:<div class="offset-list-data">{served}</div></div></div></div>"""


def detail_page(case=CASE_A, bonds=None, charges=None, served=("10/07/2026", "10/08/2026"),
                event="1: 10/20/2026 at 08:30AM - ARRAIGNMENT (SYNTH)"):
    charges = charges if charges is not None else [
        charge_row(1, "10/07/2026", "999.01.1a", "SYNTHETIC OFFENSE ALPHA (VERBATIM)", "Third Degree"),
        charge_row(2, "10/06/2026", "999.02", "SYNTH/OFFENSE BRAVO W/ SLASH", "First Degree"),
    ]
    bonds = bonds if bonds is not None else bonds_table([])
    return f"""<html><body><h2><a class="site-name">Public Records Hub</a></h2>
    <div class="panel panel-info"><div class="panel-heading"><div class="row">
      <div class="col-xs-7"><strong>Case:</strong> <span>{case}</span></div>
      <div class="col-xs-5 half"><strong>Filed:</strong> <span>10/09/2026</span></div></div></div>
      <div class="panel-body"><ul class="list-group">
      <li class="list-group-item"><span class="line-label">Status:</span> <span class="line-value">OPEN</span></li>
      <li class="list-group-item"><span class="line-label">Type:</span> <span class="line-value">Felony</span></li>
      <li class="list-group-item"><span class="line-label">Judge:</span> <span class="line-value">SYNTH JUDGE</span></li>
      </ul></div></div>
    <div class="panel panel-primary table-responsive"><div class="panel-heading panel-title">Parties:</div>
      <div class="panel-body"><table class="table"><thead><tr><th>Party Type</th><th>Name</th><th>Gender</th><th>DOB</th></tr></thead>
      <tbody><tr><td>Defendant</td><td>ALPHA ZED ZZSYNTH<br/><strong>Mailing Address:</strong>1 SYNTH ST ZZTOWN FL 00000<br/>
      <strong>Attorney:</strong>SYNTH COUNSEL</td><td>Female</td><td>01/02/1990</td></tr></tbody></table></div></div>
    <div id="charges-list" class="row section-identity"><div class="col-xs-12"><div class="panel panel-primary">
      <div class="panel-heading">Charges</div><div class="panel-body">{''.join(charges)}</div></div></div></div>
    <div id="event-details"><div class="event-list-item"><h4><strong>0: 10/01/2026 at 01:30PM - FIRST APPEARANCE</strong></h4></div>
      <div class="event-list-item"><h4><strong>{event}</strong></h4>
      <div><span class="event-location"><strong>Location:</strong> SYNTH JUDICIAL CENTER</span></div>
      <div><span class="event-room"><strong>Room:</strong> COURTROOM 0-Z</span></div></div></div>
    <div id="financial-details" class="row section-identity">{bonds}</div>
    <div id="OBTS" class="row section-identity">{''.join(obts_block(i + 1, s) for i, s in enumerate(served))}</div>
    </body></html>"""


class FakeSession:
    """Scripted responses: list GETs by caseTypeId, detail POSTs by caseId."""

    def __init__(self, lists, details, challenge_on=None):
        self.lists, self.details, self.challenge_on = lists, details, challenge_on
        self.headers = {}
        self.calls = []

    def request(self, method, url, timeout=None, allow_redirects=None, data=None, headers=None):
        self.calls.append((method, url, dict(data or {})))
        if self.challenge_on and self.challenge_on(method, url, data):
            return SimpleNamespace(status_code=403, headers={"cf-mitigated": "challenge", "server": "cloudflare"},
                                   text="<title>Just a moment...</title>")
        if method == "GET":
            tid = int(re.search(r"caseTypeId=(\d+)", url).group(1))
            return SimpleNamespace(status_code=200, headers={}, text=self.lists.get(tid, list_page([], empty=True)))
        return SimpleNamespace(status_code=200, headers={}, text=self.details[data["caseId"]])


def _scraper(monkeypatch, lists, details, challenge_on=None):
    sessions = []

    def factory():
        s = FakeSession(lists, details, challenge_on)
        sessions.append(s)
        return s

    sc = ManateeClerkScraper(session_factory=factory)
    sc._sleep = lambda s: None
    monkeypatch.setattr("config.source_guard._health_state", lambda label: "unverified")
    return sc, sessions


LISTS = {
    10: list_page([_list_row(1, CASE_A, "ZZSYNTH, ALPHA ZED")]),
    35: list_page([_list_row(2, CASE_B, "ZZSYNTH, BRAVO", ctype="Misdemeanor"),
                   _list_row(3, CASE_B, "ZZVICTIM, NOTA", ptype="Plaintiff", ctype="Misdemeanor")]),
}
DETAILS = {
    "9900001": detail_page(CASE_A, bonds=bonds_table([("SURETY BOND", "$1,500.00"), ("SURETY BOND", "$250.00")], total="$1,750.00")),
    "9900002": detail_page(CASE_B, bonds=bonds_table([]), served=("10/08/2026",),
                           charges=[charge_row(1, "10/08/2026", "999.03", "SYNTH MISDEMEANOR CHARLIE", "Second Degree")]),
}


# ── parse ─────────────────────────────────────────────────────────────────

def test_list_parse_rows_count_and_empty_page():
    rows, count = mc.parse_list(LISTS[35])
    assert count == 2 and [r["party_type"] for r in rows] == ["Defendant", "Plaintiff"]
    assert rows[0]["case_number"] == CASE_B and rows[0]["file_date"] == "2026-10-09"
    assert rows[0]["form"]["caseId"] == "9900002" and "__RequestVerificationToken" in rows[0]["form"]
    assert mc.parse_list(list_page([], empty=True)) == ([], 0)


def test_list_drift_fails_loud():
    with pytest.raises(ParseDriftError):
        mc.parse_list("<html><body>Public Records Hub</body></html>")
    with pytest.raises(ParseDriftError):  # empty table, no NO RECORDS FOUND, no count
        mc.parse_list(list_page([]).replace("Matching Results: 0", ""))


def test_detail_parse_fields_verbatim_and_minimised():
    d = mc.parse_detail(DETAILS["9900001"], today=date(2026, 10, 10))
    assert d["case_number"] == CASE_A and d["filed"] == "2026-10-09" and d["judge"] == "SYNTH JUDGE"
    assert [c["description"] for c in d["charges"]] == ["SYNTHETIC OFFENSE ALPHA (VERBATIM)", "SYNTH/OFFENSE BRAVO W/ SLASH"]
    assert [c["offense_date"] for c in d["charges"]] == ["2026-10-07", "2026-10-06"]
    assert d["charges"][0]["statute"] == "999.01.1a" and d["charges"][1]["degree"] == "First Degree"
    assert d["obts"] == {"obts": [OBTS_A], "agencies": ["ZZPD"], "served": ["2026-10-07", "2026-10-08"]}
    assert d["defendants"] == [{"name": "ALPHA ZED ZZSYNTH", "gender": "Female", "dob": "1990-01-02"}]
    assert "SYNTH ST" not in repr(d) and "COUNSEL" not in repr(d)  # no address, no attorney
    assert d["next_event"] == {"date": "2026-10-20", "time": "08:30AM", "event": "ARRAIGNMENT (SYNTH)",
                               "location": "SYNTH JUDICIAL CENTER, COURTROOM 0-Z"}


# ── bond rules ────────────────────────────────────────────────────────────

def _bond(html):
    from bs4 import BeautifulSoup
    return mc.parse_bonds(BeautifulSoup(html, "html.parser"))


def test_bond_from_bond_rows_only_totals_row_ignored():
    b = _bond(bonds_table([("SURETY BOND", "$1,500.00"), ("CASH BOND", "$250.00")], total="$9,999.00"))
    assert b["amount"] == "1750.00" and b["types"] == ["SURETY BOND", "CASH BOND"] and b["rows"] == 2


def test_no_bond_rows_is_unknown_never_zero():
    b = _bond(bonds_table([], total_count=0, total="$0.00"))  # the live "0 Bond(s) $0.00" shape
    assert b["amount"] == "" and b["rows"] == 0
    assert _bond("<div>no bonds panel</div>")["amount"] == ""


def test_published_zero_bond_row_counts():
    assert _bond(bonds_table([("ROR", "$0.00")]))["amount"] == "0.00"


def test_bond_header_drift_fails_loud():
    with pytest.raises(ParseDriftError):
        _bond(bonds_table([]).replace("Active Amount", "Amount Due"))


# ── full run: records, keys, separation ───────────────────────────────────

def test_scrape_builds_records_with_keys_out_of_booking_fields(monkeypatch):
    sc, sessions = _scraper(monkeypatch, LISTS, DETAILS)
    recs = sc.scrape()
    assert len(recs) == 2  # the Plaintiff row is skipped
    assert len(sessions) == len(mc.CASE_TYPES)  # one session per case type
    a, b = recs
    assert a.County == "Manatee Clerk" and a.State == "FL" and a.Facility == "Manatee Clerk (court filing)"
    assert a.Booking_Number == "" and b.Booking_Number == ""
    assert a.Case_Number == CASE_A and a.extra_data["obts_number"] == OBTS_A
    assert a.extra_data["filing_date"] == "2026-10-09" and a.Arrest_Date == "2026-10-07" and a.Booking_Date == ""
    assert a.Charges == "SYNTHETIC OFFENSE ALPHA (VERBATIM) | SYNTH/OFFENSE BRAVO W/ SLASH"
    assert [c["arrest_summons_served"] for c in a.extra_data["charge_details"]] == ["2026-10-07", "2026-10-08"]
    assert a.Bond_Amount == "1750.00" and a.Bond_Type == "SURETY BOND"
    assert b.Bond_Amount == "" and b.Court_Type == "Misdemeanor"
    assert a.Sex == "F" and a.DOB == "1990-01-02" and a.Agency == "ZZPD"
    for r in recs:
        key = r.extra_data["mc_case_key"]
        assert MC_INTERNAL_KEY_RE.fullmatch(key) and internal_natural_key(r) == key
        assert r.Case_Number not in key and r.extra_data["obts_number"] not in key
    # Detail POSTs carry the row's anti-forgery form; the honest UA is set.
    posts = [c for s in sessions for c in s.calls if c[0] == "POST"]
    assert {p[2]["caseId"] for p in posts} == {"9900001", "9900002"}
    assert all(p[2]["__RequestVerificationToken"].startswith("tok") for p in posts)
    assert sessions[0].headers["User-Agent"].startswith("ShamrockLeadsBot/1.0")


def test_writer_stores_key_case_and_obts_in_their_own_fields(monkeypatch):
    sc, _ = _scraper(monkeypatch, LISTS, DETAILS)
    recs = sc.scrape()
    arrests = FakeArrests([])
    stats = _writer(arrests).write_records(recs, "Manatee Clerk")
    assert stats["new_records"] == 2 and stats["skipped_invalid"] == 0
    for doc in arrests.docs:
        assert doc["county"] == "Manatee Clerk" and doc["booking_key_internal"] is True
        assert MC_INTERNAL_KEY_RE.fullmatch(doc["booking_number"]) and doc["mc_case_key"] == doc["booking_number"]
        assert "md_dedupe" not in doc and "md_key_fallback" not in doc
        assert doc["case_number"] in (CASE_A, CASE_B) and doc["case_number"] not in doc["booking_number"]
        assert doc["obts_number"] and doc["filing_date"] == "2026-10-09"
        assert doc["source_label"] == "Manatee Clerk (court filing)"
    by_case = {d["case_number"]: d for d in arrests.docs}
    assert by_case[CASE_B]["bond_amount_raw"] == ""  # unknown bond, never "0"
    # Rescrape: same keys, no new rows.
    again = _writer(arrests).write_records(_scraper(monkeypatch, LISTS, DETAILS)[0].scrape(), "Manatee Clerk")
    assert again["new_records"] == 0 and len(arrests.docs) == 2


def test_key_and_case_number_never_in_display_fields(monkeypatch):
    from dashboard.routers.helpers import serialize_doc

    sc, _ = _scraper(monkeypatch, LISTS, DETAILS)
    recs = sc.scrape()
    arrests = FakeArrests([])
    _writer(arrests).write_records(recs, "Manatee Clerk")
    for doc in arrests.docs:
        key = doc["booking_number"]
        assert is_internal_booking_key(key) and public_booking_number(key) == ""
        out = serialize_doc(dict(doc))
        assert out["booking_key_internal"] is True and out["booking_number_display"] == ""
        assert doc["case_number"] not in str(out["booking_number_display"])
        assert key not in repr(redact_internal_keys({"text": f"Lead {key} filed", "rows": [key]}))
        assert scrub_internal_keys({"booking_number": key})["booking_number"] == ""
    js = (ROOT / "dashboard" / "sl-core.js").read_text()
    assert "mc_case_v\\d+" in js and js.count("mc_case_v") >= 2  # slBookingLabel + slRedactKeys


def test_case_number_is_never_copied_to_booking_number():
    src = (ROOT / "scrapers" / "counties" / "manatee_clerk.py").read_text()
    assert re.search(r"Booking_Number\s*=\s*\"\"", src)
    assert not re.search(r"Booking_Number\s*=\s*(?!\"\")", src)


def test_co_defendants_on_one_case_get_separate_keys():
    assert mc.mc_case_key(CASE_A, "ZZSYNTH, ALPHA") != mc.mc_case_key(CASE_A, "ZZSYNTH, BRAVO")
    assert mc.mc_case_key(CASE_A, "zzsynth,  alpha") == mc.mc_case_key(CASE_A.lower(), "ZZSYNTH, ALPHA")
    assert mc.mc_case_key("", "X Y") == "" and mc.mc_case_key(CASE_A, "") == ""


def test_detail_case_mismatch_fails_loud(monkeypatch):
    details = dict(DETAILS, **{"9900001": detail_page("2026CF000000AX")})
    sc, _ = _scraper(monkeypatch, LISTS, details)
    with pytest.raises(ParseDriftError):
        sc.scrape()


def test_run_cap_limits_detail_posts(monkeypatch):
    rows = [_list_row(i, f"2026CF0{i:05d}AX", f"ZZSYNTH, P{i}") for i in range(1, 6)]
    details = {f"99{i:05d}": detail_page(f"2026CF0{i:05d}AX") for i in range(1, 6)}
    monkeypatch.setattr(mc, "MAX_DETAILS_PER_RUN", 3)
    sc, sessions = _scraper(monkeypatch, {10: list_page(rows)}, details)
    assert len(sc.scrape()) == 3
    assert sum(1 for s in sessions for c in s.calls if c[0] == "POST") == 3


def test_requests_are_paced(monkeypatch):
    sc, _ = _scraper(monkeypatch, LISTS, DETAILS)
    sleeps = []
    sc._sleep = sleeps.append
    sc.scrape()
    assert sleeps and all(s == mc.REQUEST_DELAY_S for s in sleeps)


# ── challenge stops the run ───────────────────────────────────────────────

def test_challenge_on_list_stops_the_run_egress_blocked(monkeypatch):
    sc, sessions = _scraper(monkeypatch, LISTS, DETAILS, challenge_on=lambda m, u, d: m == "GET")
    with pytest.raises(EgressBlocked) as exc:
        sc.scrape()
    assert str(exc.value).startswith("egress_block:")
    v = classify_exception(exc.value)
    assert v.error_class == "anti_bot" and v.egress_block and not v.retryable
    assert sum(len(s.calls) for s in sessions) == 1  # stopped at the first challenge, no retry


def test_base_retry_skips_challenges_but_retries_transient_5xx(monkeypatch):
    assert ManateeClerkScraper.BASE_RETRY_ENABLED is True
    # A challenge through the base retry wrapper: one request, no retry.
    sc, sessions = _scraper(monkeypatch, LISTS, DETAILS, challenge_on=lambda m, u, d: True)
    sc._retry_sleep = lambda s: None
    monkeypatch.setattr("scrapers.base_scraper.base_retry_enabled", lambda: True)
    with pytest.raises(EgressBlocked):
        sc._scrape_with_retry()
    assert sum(len(s.calls) for s in sessions) == 1
    # A transient 503 is classified network/retryable and retried.
    v = classify_exception(RuntimeError("Manatee Clerk: HTTP 503"))
    assert v.error_class == "network" and v.retryable
    flaky = {"n": 0}

    def once_503(method, url, data):
        flaky["n"] += 1
        return flaky["n"] == 1

    sc2, sessions2 = _scraper(monkeypatch, LISTS, DETAILS)
    sc2._retry_sleep = lambda s: None
    real_request = FakeSession.request

    def request(self, method, url, **kw):
        if once_503(method, url, kw.get("data")):
            self.calls.append((method, url, {}))
            return SimpleNamespace(status_code=503, headers={"server": "Microsoft-IIS/10.0"}, text="busy")
        return real_request(self, method, url, **kw)

    monkeypatch.setattr(FakeSession, "request", request)
    assert len(sc2._scrape_with_retry()) == 2


def _codef_detail(parties):
    rows = "".join(f"<tr><td>Defendant</td><td>{n}<br/><strong>Attorney:</strong>X</td><td>{g}</td><td>{dob}</td></tr>"
                   for n, g, dob in parties)
    html = detail_page(CASE_A)
    return re.sub(r"<tbody><tr><td>Defendant</td>.*?</tr></tbody>", f"<tbody>{rows}</tbody>", html, flags=re.S)


def test_co_defendants_each_get_their_own_identity_fields(monkeypatch):
    detail = _codef_detail([("ALPHA ZED ZZSYNTH", "Female", "01/02/1990"), ("BRAVO ZZSYNTH", "Male", "03/04/1985")])
    rows = [_list_row(1, CASE_A, "ZZSYNTH, ALPHA ZED"), _list_row(2, CASE_A, "ZZSYNTH, BRAVO")]
    sc, _ = _scraper(monkeypatch, {10: list_page(rows)}, {"9900001": detail, "9900002": detail})
    a, b = sc.scrape()
    assert (a.DOB, a.Sex, b.DOB, b.Sex) == ("1990-01-02", "F", "1985-03-04", "M")
    assert a.extra_data["mc_case_key"] != b.extra_data["mc_case_key"]


def test_unmatched_or_ambiguous_defendant_leaves_identity_blank(monkeypatch, caplog):
    two_same = _codef_detail([("BRAVO ZZSYNTH", "Male", "03/04/1985"), ("BRAVO ZZSYNTH", "Male", "05/06/1999")])
    other = _codef_detail([("CHARLIE ZZSYNTH", "Male", "03/04/1985")])
    rows = [_list_row(1, CASE_A, "ZZSYNTH, BRAVO"), _list_row(2, CASE_A, "ZZSYNTH, DELTA")]
    sc, _ = _scraper(monkeypatch, {10: list_page(rows)}, {"9900001": two_same, "9900002": other})
    with caplog.at_level(logging.INFO):
        recs = sc.scrape()
    assert [(r.DOB, r.Sex) for r in recs] == [("", ""), ("", "")]
    assert "defendant_unmatched=2" in caplog.text
    assert mc.match_defendant([{"name": "ALPHA ZED ZZSYNTH"}], "ZZSYNTH, ALPHA ZED") == {"name": "ALPHA ZED ZZSYNTH"}


def test_challenge_on_detail_stops_the_run(monkeypatch):
    sc, sessions = _scraper(monkeypatch, LISTS, DETAILS, challenge_on=lambda m, u, d: m == "POST")
    with pytest.raises(EgressBlocked):
        sc.scrape()
    assert sum(1 for s in sessions for c in s.calls if c[0] == "POST") == 1


@pytest.mark.parametrize("status,headers,body", [
    (403, {}, "<html>forbidden</html>"),
    (429, {}, ""),
    (200, {"cf-mitigated": "challenge"}, "ok"),
    (200, {}, "<div class='g-recaptcha' data-sitekey='x'></div>"),
    (200, {}, "<div class='cf-turnstile'></div>"),
    (200, {}, "<title>Just a moment...</title>"),
    (200, {}, "<html>Please complete the CAPTCHA</html>"),
])
def test_detect_challenge_markers(status, headers, body):
    assert mc.detect_challenge(status, headers, body)


def test_normal_clerk_pages_are_not_challenges():
    assert mc.detect_challenge(200, {}, LISTS[10]) == ""
    assert mc.detect_challenge(200, {}, DETAILS["9900001"].replace("ALPHA", "ACCESS DENIED CAPTCHA")) == ""


def test_logs_carry_no_names_or_keys(monkeypatch, caplog):
    sc, _ = _scraper(monkeypatch, LISTS, DETAILS)
    with caplog.at_level(logging.DEBUG):
        sc.scrape()
    assert "ZZSYNTH" not in caplog.text and "mc_case_v1:" not in caplog.text and CASE_A not in caplog.text


# ── guards and separation ─────────────────────────────────────────────────

def test_source_guard_fail_closed_makes_no_request(monkeypatch):
    sc, sessions = _scraper(monkeypatch, LISTS, DETAILS)
    monkeypatch.setattr("config.source_guard._health_state",
                        lambda label: "fail_closed" if label == "Manatee Clerk (FL)" else "unverified")
    assert sc.scrape() == [] and sessions == []


def test_labels_health_and_separation_from_the_jail_scraper():
    from config.source_guard import fail_closed_reason, label_for_url
    from dashboard.extensions import REGISTERED_COUNTIES, scraper_source_state

    sc = ManateeClerkScraper()
    assert sc.county_label == "Manatee Clerk (FL)" and sc.scraper_id == "scraper_manatee_clerk"
    assert "Manatee Clerk (FL)" in REGISTERED_COUNTIES and "Manatee (FL)" in REGISTERED_COUNTIES
    assert scraper_source_state("Manatee Clerk (FL)") == "unverified"
    assert scraper_source_state("Manatee (FL)") == "fail_closed"  # the jail scraper stays closed
    assert ManateeCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert ManateeCountyScraper.ALLOWS_INTERNAL_NATURAL_KEY is False
    assert label_for_url("https://records.manateeclerk.com/CourtRecords/Case/Details") == "Manatee Clerk (FL)"
    assert label_for_url("https://manatee-sheriff.revize.com/bookings") == "Manatee (FL)"
    assert fail_closed_reason(scraper=ManateeCountyScraper()) is not None
    assert fail_closed_reason(scraper=sc, url=mc.BASE_URL) is None


def test_other_counties_blank_booking_guard_unchanged(monkeypatch):
    sc, _ = _scraper(monkeypatch, LISTS, DETAILS)
    rec = sc.scrape()[0]
    key = rec.extra_data["mc_case_key"]
    assert set(INTERNAL_NATURAL_KEY_SCOPES) == {("FL", "Miami-Dade"), ("FL", "Manatee Clerk")}
    assert INTERNAL_NATURAL_KEY_FIELDS[("FL", "Manatee Clerk")] == "mc_case_key"
    # The same record under any other label is skipped by the writer.
    for county, state in (("Manatee", "FL"), ("Manatee Clerk", "GA"), ("Lee", "FL"), ("Miami-Dade", "FL")):
        rec.County, rec.State = county, state
        arrests = FakeArrests([])
        assert _writer(arrests).write_records([rec], county)["skipped_invalid"] == 1, (county, state)
        assert arrests.docs == [] and internal_natural_key(rec) == ""
    rec.County, rec.State = "Manatee Clerk", "FL"
    # An MD-shaped key (or a malformed one) is refused for Manatee Clerk.
    for bad in ("", "md_dedupe_v2:" + "a" * 64, "mc_case_v2:" + "a" * 64, key[:-1], key.upper(), CASE_A):
        rec.extra_data["mc_case_key"] = bad
        assert internal_natural_key(rec) == "", bad
        assert _writer(FakeArrests([])).write_records([rec], "Manatee Clerk")["skipped_invalid"] == 1
    # An mc key in md_dedupe does not open Miami-Dade.
    rec.County, rec.extra_data["md_dedupe"] = "Miami-Dade", key
    assert internal_natural_key(rec) == ""
    # BaseScraper default stays off; only the opted-in scrapers keep blank bookings.
    assert BaseScraper.ALLOWS_INTERNAL_NATURAL_KEY is False
    rec.County, rec.extra_data["mc_case_key"] = "Manatee Clerk", key

    class _Other(BaseScraper):
        pass

    assert _Other._filter_records_without_source_booking([rec]) == []
    assert ManateeClerkScraper._filter_records_without_source_booking([rec]) == [rec]
