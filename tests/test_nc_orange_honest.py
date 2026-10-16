"""Orange NC: honest plain_http identity, published-only charges/bond/status."""
from unittest import mock

import pytest
import requests

from scrapers.counties_nc import orange
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked

HDR = "User: X, ORANGE COUNTY SHERIFF`S OFFICE 10/15/2026 06:39:41\n"
COLS = "Name A/J R/S Bk # Charge(s) / Agcy / Status / Docket / Bond $ / Status / Type\n"
ROWS = (
    "DOE, John A W M 80001 Larceny / X / PRET / 26CR000001 / $1,500 / X / X 10/14/2026 1631 1\n"
    "Resisting / X / PRET / 26CR000001 / $0 / X / X\n"
    "ROE, Jane A B F 80002 Assault / X / PRET / 26CR000002 / / NOBD / NOBD 10/13/2026 0202 2\n"
    "POE, Sam A B M 80003 Trespass / X / PRET / 26CR000003 / $500 / X / X 10/12/2026 0900 3\n"
    "Probation Violation / X / PRET / 26CR000004 / / NOBD / NOBD\n"
)
CUSTODY = HDR + "Daily In Custody Report by Facility\nFacility: NEW JAIL\n" + COLS + ROWS
LEGACY = HDR + "Detainees In Confinement Report by Facility\n" + COLS + ROWS
UNLABELED = HDR + "Daily Booking Report by Facility\n" + COLS + ROWS


def _recs(text):
    s = orange.OrangeScraper()
    rows = orange.parse_report_text(text)
    st = orange.status_fields(text)
    return [s._to_record(r, "https://x.usrfiles.com/ugd/a.pdf", st) for r in rows]


def test_honest_session_identity():
    s = orange._HonestSession()
    assert s.headers["User-Agent"] == HONEST_USER_AGENT
    assert "Chrome" not in s.headers["User-Agent"]
    assert s.verify is True


def test_no_chrome_ua_in_source():
    import inspect
    assert "Chrome/" not in inspect.getsource(orange)


def test_session_routes_through_plain_http():
    with mock.patch.object(orange, "plain_request") as pr:
        pr.return_value = "ok"
        assert orange._HonestSession().get("https://www.ocsonc.com/x") == "ok"
        assert pr.call_args.kwargs["county"] == "Orange"


def test_custody_label_sets_in_custody_with_source():
    recs = _recs(CUSTODY)
    assert {r.Status for r in recs} == {"In Custody"}
    assert all(r.extra_data["status_source"] == "published_in_custody_report_label" for r in recs)


def test_legacy_confinement_label_also_current_only():
    assert orange.status_fields(LEGACY) == ("In Custody", "published_in_custody_report_label")


def test_no_label_no_status():
    recs = _recs(UNLABELED)
    assert {r.Status for r in recs} == {""}
    assert all("status_source" not in (r.extra_data or {}) for r in recs)


def test_label_must_be_title_line_not_charge_text():
    assert orange.status_fields(HDR + COLS + "DOE, J A W M 1 Escape In Custody Report / X\n") == ("", "")


def test_bond_sum_keeps_published_zero():
    assert _recs(CUSTODY)[0].Bond_Amount == "1500"


def test_bond_unknown_when_no_amount_printed():
    assert _recs(CUSTODY)[1].Bond_Amount == ""


def test_bond_unknown_when_any_charge_lacks_amount():
    assert _recs(CUSTODY)[2].Bond_Amount == ""


def test_only_zero_bond_is_kept():
    t = CUSTODY.replace("$1,500", "$0")
    assert _recs(t)[0].Bond_Amount == "0"


def test_charges_published_or_empty():
    recs = _recs(CUSTODY)
    assert recs[0].Charges == "Larceny | Resisting"
    r = {"name": "A, B", "first": "B", "middle": "", "last": "A", "race": "W", "sex": "M", "booking": "1",
         "booking_date": "", "booking_time": "", "charges": [], "bond": "", "dockets": []}
    rec = orange.OrangeScraper()._to_record(r, None)
    assert rec.Charges == "" and rec.Bond_Amount == "" and rec.Status == ""


def test_booking_keys_are_source_bk_numbers():
    assert [r.Booking_Number for r in _recs(CUSTODY)] == ["80001", "80002", "80003"]


@pytest.mark.parametrize("exc", [EgressBlocked("egress_block: 403"), SourceTLSInvalid("tls_cert_invalid: x")])
def test_pdf_loop_reraises_blocks(exc):
    sess = mock.Mock()
    sess.get.side_effect = exc
    with pytest.raises(type(exc)):
        orange.OrangeScraper()._newest_report(sess, ["https://x.usrfiles.com/ugd/a.pdf"])


def test_pdf_loop_soft_skips_other_errors():
    sess = mock.Mock()
    sess.get.side_effect = requests.ConnectionError("x")
    assert orange.OrangeScraper()._newest_report(sess, ["https://x/a.pdf"]) == (None, "", None)


def test_portal_block_raises_from_scrape():
    with mock.patch.object(orange, "plain_request", side_effect=EgressBlocked("egress_block: 403")):
        with pytest.raises(EgressBlocked):
            orange.OrangeScraper().scrape()
