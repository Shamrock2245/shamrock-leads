"""Miami-Dade: honest plain_http, typed blocks, no invented status."""
import pytest

from scrapers.counties import miami_dade
from scrapers.counties.miami_dade import MiamiDadeCountyScraper
from scrapers.plain_http import HONEST_USER_AGENT, EgressBlocked, SourceTLSInvalid


def test_honest_ua():
    assert miami_dade.HEADERS["User-Agent"] == HONEST_USER_AGENT
    assert "Chrome" not in miami_dade.HEADERS["User-Agent"]


class _Blocked:
    headers = {}

    def __init__(self, exc):
        self.exc = exc

    def request(self, *a, **k):
        raise self.exc


@pytest.mark.parametrize("exc", [EgressBlocked("egress_block: Miami-Dade HTTP 403"),
                                 SourceTLSInvalid("tls_cert_invalid: Miami-Dade")])
def test_blocks_propagate_typed(exc):
    with pytest.raises(type(exc)):
        MiamiDadeCountyScraper()._get_json(_Blocked(exc), {"f": "json"})


class _R:
    status_code = 403
    headers = {"content-type": "text/html"}
    text = ""


class _S403:
    headers = {}

    def request(self, *a, **k):
        return _R()


def test_http_403_is_egress_block():
    with pytest.raises(EgressBlocked):
        MiamiDadeCountyScraper()._get_json(_S403(), {"f": "json"})


def test_status_blank_not_unknown():
    rec = MiamiDadeCountyScraper()._parse_record(
        {"ObjectId": 1, "BookDate": 1791345600000, "Defendant": "DOE JOHN", "DOB": None,
         "Charge1": "X", "Code2": None, "Charge3": None})
    assert rec is not None and rec.Status == "" and "status_source" not in rec.extra_data
