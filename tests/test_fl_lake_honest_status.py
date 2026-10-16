"""Lake (FL): honest UA via plain_http; status/bond published-only; SolveCaptcha kept."""
import inspect

import pytest

from scrapers import plain_http
from scrapers.counties import lake
from scrapers.scraper_resilience import EgressBlocked
from tests.test_fl_gap_queue import _lake_row


def test_no_chrome_ua_or_in_custody_default():
    src = inspect.getsource(lake)
    body = src.split('"""', 2)[-1] if src.count('"""') >= 2 else src
    assert "Chrome/" not in body
    assert 'Status="In Custody"' not in body
    assert "USER_AGENT" not in body


def test_session_is_honest():
    s = plain_http.make_session(lake.API_HEADERS)
    assert s.headers["User-Agent"] == plain_http.HONEST_USER_AGENT
    assert s.verify is True


@pytest.mark.parametrize("row,want", [
    (_lake_row(), ""),
    (_lake_row(status="Released"), "Released"),
    (_lake_row(inCustody=True), "In Custody"),
    (_lake_row(in_custody=False), "Released"),
    (_lake_row(inCustody="true"), "In Custody"),
])
def test_status_only_from_source(row, want):
    assert lake.published_status(row) == want
    assert lake.parse_record(row).Status == want


def test_bond_published_only():
    assert lake.parse_record(_lake_row(total_bond_amount=1500)).Bond_Amount == "1500"
    assert lake.parse_record(_lake_row(total_bond_amount=0)).Bond_Amount == "0"
    nobond = lake.parse_record(_lake_row(total_bond_amount=-1))
    assert nobond.Bond_Amount == "" and nobond.Bond_Type == "NO BOND"
    missing = {k: v for k, v in _lake_row().items() if k != "total_bond_amount"}
    assert lake.parse_record(missing).Bond_Amount == ""


def test_solvecaptcha_path_still_required(monkeypatch):
    monkeypatch.setenv("SOLVECAPTCHA_KEY", "")
    with pytest.raises(RuntimeError, match="SOLVECAPTCHA_KEY"):
        lake.LakeCountyScraper().scrape()


def test_landing_block_raises_egress(monkeypatch):
    monkeypatch.setenv("SOLVECAPTCHA_KEY", "k")
    monkeypatch.setattr(lake.plain_http, "make_session", lambda *a, **k: object())
    monkeypatch.setattr(
        lake.plain_http, "request",
        lambda *a, **k: (_ for _ in ()).throw(EgressBlocked("egress_block: Lake connection_reset: x")),
    )
    with pytest.raises(EgressBlocked, match="egress_block"):
        lake.LakeCountyScraper().scrape()
