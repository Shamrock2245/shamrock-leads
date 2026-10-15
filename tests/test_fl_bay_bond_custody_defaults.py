"""Bay (FL): unpublished bond is "", published 0 kept; Status only from source (""); honest plain HTTPS."""
import inspect
from unittest import mock

import pytest
import requests

from scrapers.counties import bay
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked


def _row(charges_html):
    return {"1": "Booking #: 2026-000777<br>Date In: 9/4/2026 7:08:00 AM<br>",
            "2": "DOE, JOHN<br>Race: W<br>Sex: M", "3": charges_html}


@pytest.mark.parametrize("html,want", [
    ("Charge 1: A<br>Bond: $1,000<br>Charge 2: B<br>Bond: $500<br>", "1500"),
    ("Charge 1: A<br>Bond: $0.00<br>", "0"),                       # published 0 kept
    ("Charge 1: A<br>Bond: $0<br>Charge 2: B<br>Bond: $0<br>", "0"),
    ("Charge 1: A<br>", ""),                                       # no bond shown
    ("Charge 1: A<br>Bond: NO BOND<br>", ""),
    ("Charge 1: A<br>Bond: HOLD<br>", ""),
    ("Charge 1: A<br>Bond: $1,000<br>Charge 2: B<br>", ""),        # one charge unpublished
    ("Charge 1: A<br>Bond: $1,000<br>Charge 2: B<br>Bond: NO BOND<br>", ""),
    ("", ""),                                                      # no charges
])
def test_bond_published_only(html, want):
    assert bay.parse_store_row(_row(html))["bond"] == want


def test_no_chrome_disguise_and_verified():
    src = inspect.getsource(bay)
    for banned in ("Mozilla/", "Chrome/", "impersonate", "curl_cffi", "verify=False"):
        assert banned not in src, banned
    sess = bay.BayUniGuiSession()
    assert sess.http.headers["User-Agent"] == HONEST_USER_AGENT and sess.http.verify is True


def test_scrape_status_blank_and_bond_unknown(monkeypatch):
    class S:
        def open(self): pass
        def search(self, last, first):
            return [_row("Charge 1: A<br>Bond: NO BOND<br>")] if (last, first) == ("A", "A") else []
    monkeypatch.setattr(bay, "BayUniGuiSession", S)
    monkeypatch.setattr(bay, "REQUEST_DELAY_S", 0)
    recs = bay.BayCountyScraper().scrape()
    assert len(recs) == 1
    assert recs[0].Status == "" and recs[0].Bond_Amount == ""


def _sess_with(resp=None, exc=None):
    http = mock.Mock()
    if exc:
        http.request.side_effect = exc
    else:
        http.request.return_value = resp
    return bay.BayUniGuiSession(http=http)


@pytest.mark.parametrize("code", [401, 403, 429])
def test_block_fails_visibly(code):
    r = mock.Mock(status_code=code, headers={"content-type": "text/html"}, text="")
    with pytest.raises(EgressBlocked, match=r"^egress_block: Bay"):
        _sess_with(r).open()


def test_bad_certificate_fails_visibly():
    with pytest.raises(SourceTLSInvalid, match=r"^tls_cert_invalid: Bay"):
        _sess_with(exc=requests.exceptions.SSLError("certificate verify failed")).open()


def test_requests_go_through_plain_http_with_verify():
    r = mock.Mock(status_code=200, headers={"content-type": "text/html"}, text="<html>no sid</html>")
    s = _sess_with(r)
    with pytest.raises(bay.BaySourceContractError):
        s.open()
    _args, kw = s.http.request.call_args
    assert kw["verify"] is True
