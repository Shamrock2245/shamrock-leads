"""Duval (FL): the ATIMS "I'm not a robot" gate is never clicked.

Clicking a CAPTCHA gate is CAPTCHA solving (owner-approved only for Broward
and Lake). When the gate shows, the run stops with AntiBotBlocked
("captcha_gate: ...") so Health records an anti-bot block, not an empty run.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

from scrapers.counties.duval import DuvalCountyScraper
from scrapers.scraper_resilience import AntiBotBlocked

ROOT = Path(__file__).resolve().parents[1]


class _Btn:
    def __init__(self):
        self.clicked = False

    def click(self):  # pragma: no cover - must never run
        self.clicked = True


class _Listen:
    def start(self, *_a, **_k):
        pass

    def stop(self):
        pass

    def steps(self, timeout=0):
        return []


class _Page:
    def __init__(self, gate: bool):
        self.btn = _Btn() if gate else None
        self.listen = _Listen()
        self.visited = []
        self.inputs = 0

    def get(self, url):
        self.visited.append(url)

    def ele(self, selector, timeout=0):
        if "not a robot" in selector:
            return self.btn
        return None

    def quit(self):
        pass


@pytest.fixture()
def fake_browser(monkeypatch):
    monkeypatch.setitem(sys.modules, "DrissionPage", types.SimpleNamespace(ChromiumPage=object))
    monkeypatch.setattr("scrapers.counties.duval.time.sleep", lambda *_a, **_k: None)

    def _make(gate):
        page = _Page(gate)
        monkeypatch.setattr(DuvalCountyScraper, "_setup_browser", lambda self: page)
        return page

    return _make


def test_gate_present_raises_and_is_never_clicked(fake_browser):
    page = fake_browser(gate=True)
    with pytest.raises(AntiBotBlocked, match=r"^captcha_gate:"):
        DuvalCountyScraper().scrape()
    assert page.btn.clicked is False


def test_no_gate_runs_normally(fake_browser, monkeypatch):
    page = fake_browser(gate=False)
    monkeypatch.setattr(DuvalCountyScraper, "_parse_dom", lambda self, p: [])
    assert DuvalCountyScraper().scrape() == []
    assert page.visited


def test_module_never_clicks_a_captcha_element():
    src = (ROOT / "scrapers" / "counties" / "duval.py").read_text()
    assert not re.search(r"captcha\w*\.click\(", src, re.I)
    assert "solvecaptcha" not in src.lower()
