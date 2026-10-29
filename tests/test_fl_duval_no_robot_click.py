"""Duval (FL): the ATIMS "I'm not a robot" gate is never clicked.

Clicking a CAPTCHA gate is CAPTCHA solving (owner-approved only for Broward
and Lake). On the plain_http reopen path a gate page raises AntiBotBlocked
("captcha_gate: ...") so Health records an anti-bot block, not an empty run.
The module has no browser, so there is nothing that could click.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from scrapers import plain_http
from scrapers.counties.duval import DuvalCountyScraper
from scrapers.scraper_resilience import AntiBotBlocked

ROOT = Path(__file__).resolve().parents[1]


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code, self.url = text, status, "https://inmatesearch.jaxsheriff.org/"
        self.headers = {"content-type": "text/html"}


class _Session:
    def __init__(self, resp):
        self.resp, self.calls, self.headers, self.verify = resp, [], {}, True

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        return self.resp


def test_gate_page_raises_captcha_gate():
    sess = _Session(_Resp("<html><button>I'm not a robot</button></html>"))
    with pytest.raises(AntiBotBlocked, match=r"^captcha_gate:"):
        DuvalCountyScraper()._scrape_with_session(sess)
    assert sess.calls == [("GET", "https://inmatesearch.jaxsheriff.org/")]  # one GET, nothing posted


def test_module_never_clicks_a_captcha_element():
    src = (ROOT / "scrapers" / "counties" / "duval.py").read_text()
    assert ".click(" not in src
    assert "solvecaptcha" not in src.lower()
    assert not re.search(r"DrissionPage|ChromiumPage|_get_browser_options", src.split('"""', 2)[2])
