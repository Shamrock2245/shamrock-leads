"""Hillsborough/Marion: no impersonation/proxy/solver/login; published-only defaults; still fail_closed."""
import pathlib

import pytest
from bs4 import BeautifulSoup

from scrapers.counties import hillsborough, marion
from scrapers.counties.hillsborough import HillsboroughCountyScraper
from scrapers.counties.marion import MarionCountyScraper
from scrapers.plain_http import HONEST_USER_AGENT

BANNED = ("curl_cffi", "impersonate", "solvecaptcha", "_solve_recaptcha", "socks_proxy",
          "resolve_residential_proxy", "proxies=", "Chrome/", "HCSO_PASSWORD", "_login")


@pytest.mark.parametrize("mod", [hillsborough, marion])
def test_no_banned_code(mod):
    import ast
    tree = ast.parse(pathlib.Path(mod.__file__).read_text())
    for node in ast.walk(tree):  # drop docstrings (they record the retired history)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    body = ast.unparse(tree)
    for word in BANNED:
        assert word not in body, word


@pytest.mark.parametrize("cls", [HillsboroughCountyScraper, MarionCountyScraper])
def test_still_fail_closed(cls):
    assert cls.SOURCE_CONTRACT_VALIDATED is False and cls().scrape() == []


def test_marion_honest_headers():
    assert marion.HEADERS["User-Agent"] == HONEST_USER_AGENT


def _hcso(release="", bonds=("$1,000.00", "$500.00")):
    charges = "".join(f"<tr><td>F</td><td>CHARGE {i}</td><td>x</td><td>26-CF-{i}</td><td>{b}</td></tr>"
                      for i, b in enumerate(bonds))
    html = f"""<table class="table-striped">
<tr><td><a href="/arrestinquiry/x">DOE, JOHN</a></td><td>26012345</td><td>HCSO</td><td>-</td><td>W/M/30/01-01-1996</td></tr>
<tr><td>ADDRESS: 1 MAIN ST</td></tr>
<tr><td>RELEASE DATE:{release}</td><td>BOOKING DATE:10/15/2026</td></tr>
<tr><td><table><tr><td>Charge Type</td><td>Charge(s)</td></tr>{charges}</table></td></tr>
<tr><td></td></tr></table>"""
    soup = BeautifulSoup(html, "html.parser")
    return HillsboroughCountyScraper()._parse_results_table(soup)[0]


def test_hcso_status_blank_unless_release_published():
    r = _hcso()
    assert r.Status == "" and "status_source" not in r.extra_data
    r = _hcso(release="10/16/2026")
    assert (r.Status, r.Release_Date, r.extra_data["status_source"]) == ("Released", "10/16/2026", "published_release_date")


def test_hcso_bond_published_only():
    assert _hcso().Bond_Amount == "1500"
    assert _hcso(bonds=("$0.00",)).Bond_Amount == "0"
    assert _hcso(bonds=("$500.00", "NO BOND")).Bond_Amount == ""


def test_marion_status_and_bond():
    s = MarionCountyScraper()
    base = dict(booking_num="1", inmate_id="9", full_name="X, Y", first_name="Y", middle_name="",
                last_name="X", dob="", sex="", race="", booking_date="", detail_url="",
                charges="", bond_amount="")
    assert s.to_record(dict(base, in_custody="Y", release_date="")).Status == "In Custody"
    r = s.to_record(dict(base, in_custody="", release_date=""))
    assert r.Status == "" and r.Bond_Amount == "" and "status_source" not in r.extra_data
    r = s.to_record(dict(base, in_custody="", release_date="10/1/2026"))
    assert (r.Status, r.extra_data["status_source"]) == ("Released", "published_release_date")
    p = MarionCountyScraper.parse_detail_charges(
        "<table><tr><th>Offense</th><th>Bond Amount</th></tr><tr><td>A</td><td>$0.00</td></tr></table>")
    assert p == {"charges": "A", "bond_amount": "0"}
    p = MarionCountyScraper.parse_detail_charges(
        "<table><tr><th>Offense</th><th>Bond Amount</th></tr><tr><td>A</td><td>$5.00</td></tr>"
        "<tr><td>B</td><td>NO BOND</td></tr></table>")
    assert p["bond_amount"] == ""
