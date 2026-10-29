"""Brevard: plain_http with the honest UA; blocks raise typed errors."""
import pytest

from scrapers.counties import brevard
from scrapers.plain_http import HONEST_USER_AGENT, EgressBlocked


def test_honest_ua_no_chrome():
    assert brevard.HEADERS["User-Agent"] == HONEST_USER_AGENT
    assert "Chrome" not in brevard.HEADERS["User-Agent"]


@pytest.mark.parametrize("code", [403, 429])
def test_landing_block_raises_egress_blocked(monkeypatch, code):
    class R:
        status_code = code
        headers = {"content-type": "text/html"}
        text = ""

    class S:
        headers = {}

        def request(self, *a, **k):
            return R()

    monkeypatch.setattr(brevard.requests, "Session", S)
    with pytest.raises(EgressBlocked):
        brevard.BrevardCountyScraper().scrape()
