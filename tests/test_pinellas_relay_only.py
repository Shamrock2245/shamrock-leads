"""Pinellas (FL) relay-only with a non-stealth browser (owner exception 2026-10-08).

Brendan, 2026-10-08 1:38 PM ET: "we will connect at home, with residential
egress." Pinellas runs only on the Leads Ops home relay with stock Playwright
Chromium and an honest User-Agent; no patchright, stealth, proxy, impersonation
or challenge solving. Synthetic fixtures only; no network.
"""
from __future__ import annotations

import ast
import builtins
import inspect
import json
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PINELLAS_SRC = ROOT / "scrapers" / "counties" / "pinellas.py"
FORBIDDEN_MODULES = {
    "patchright", "undetected_chromedriver", "nodriver", "DrissionPage", "selenium",
    "playwright_stealth", "curl_cffi", "cloudscraper", "tls_client",
}


@pytest.fixture()
def no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)


@pytest.fixture()
def no_stealth_imports(monkeypatch):
    real_import = builtins.__import__

    def _guard(name, *a, **k):
        if name.split(".")[0] in FORBIDDEN_MODULES or "stealth" in name.lower():
            raise AssertionError(f"forbidden import attempted: {name}")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _guard)


def _exit(monkeypatch, info=None, exc=None):
    import scrapers.cf_browser as cf

    def fake(*_a, **_k):
        if exc:
            raise exc
        return info

    monkeypatch.setattr(cf, "check_exit_ip", fake)


def _no_browser(monkeypatch):
    from scrapers.counties import pinellas

    def boom():
        raise AssertionError("browser started off the relay")

    monkeypatch.setattr(pinellas, "launch_plain_browser", boom)


# ── Relay-only gating ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "info,exc",
    [
        ({"ok": True, "ip": "203.0.113.5", "org": "AS24940 Hetzner Online GmbH", "country": "DE",
          "residential_likely": False}, None),
        ({"ok": True, "ip": "203.0.113.6", "org": "", "country": "", "residential_likely": False,
          "exit_unverified": True}, None),
        (None, RuntimeError("all IP endpoints failed")),
    ],
)
def test_off_relay_raises_before_any_browser_or_source_request(monkeypatch, no_network, info, exc):
    from scrapers.counties.pinellas import PinellasCountyScraper
    from scrapers.scraper_resilience import EgressBlocked

    _exit(monkeypatch, info, exc)
    _no_browser(monkeypatch)
    with pytest.raises(EgressBlocked, match="egress_block"):
        PinellasCountyScraper().scrape()


def test_only_direct_egress_mode_is_accepted(monkeypatch, no_network):
    from scrapers.counties.pinellas import PinellasCountyScraper

    monkeypatch.setenv("PINELLAS_EGRESS_MODE", "proxy")
    _exit(monkeypatch, {"residential_likely": True, "ip": "x", "org": "Comcast", "country": "US"})
    _no_browser(monkeypatch)
    with pytest.raises(ValueError, match="only 'direct'"):
        PinellasCountyScraper().scrape()


def test_pinellas_is_relay_only_with_no_vps_interval():
    from config.relay_only import RELAY_ONLY_LABELS, is_relay_only
    from core.scheduler import ScraperScheduler
    from scrapers.counties.pinellas import PinellasCountyScraper

    assert "Pinellas (FL)" in RELAY_ONLY_LABELS
    scraper = PinellasCountyScraper()
    assert is_relay_only(scraper)
    sched = ScraperScheduler(max_workers=1)
    sched.register_scraper(scraper, interval_minutes=90)
    assert sched.scheduler.get_jobs() == []  # a scheduled VPS run never happens
    assert scraper.scraper_id in sched.relay_only_job_ids()


def test_main_registers_pinellas_without_an_interval():
    import main

    tree = ast.parse(inspect.getsource(main.register_scrapers))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "register_scraper"
        and getattr(getattr(n.args[0], "func", None), "id", "") == "PinellasCountyScraper"
    ]
    assert len(calls) == 1 and not calls[0].keywords


# ── Stock browser + honest User-Agent on the relay ──────────────────────────
class _FakePage:
    def goto(self, *_a, **_k):
        raise RuntimeError("synthetic: stop after launch")

    def close(self):
        pass


class _FakeContext:
    def new_page(self):
        return _FakePage()


class _FakeBrowser:
    def __init__(self):
        self.context_kwargs = None
        self.closed = False

    def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return _FakeContext()

    def close(self):
        self.closed = True


class _FakePW:
    stopped = False

    def stop(self):
        self.stopped = True


def test_on_relay_uses_stock_launcher_and_honest_user_agent(monkeypatch, no_network, no_stealth_imports):
    from scrapers.counties import pinellas

    _exit(monkeypatch, {"ok": True, "ip": "198.51.100.7", "org": "AS7922 Comcast Cable", "country": "US",
                        "residential_likely": True})
    pw, browser = _FakePW(), _FakeBrowser()
    monkeypatch.setattr(pinellas, "launch_plain_browser", lambda: (pw, browser))
    with pytest.raises(RuntimeError, match="synthetic"):
        pinellas.PinellasCountyScraper().scrape()
    ua = browser.context_kwargs["user_agent"]
    assert ua == pinellas.USER_AGENT
    assert "ShamrockLeadsBot" in ua and "shamrockbailbonds.biz" in ua
    for spoof in ("Mozilla", "AppleWebKit", "Chrome/", "Safari", "Windows NT", "Macintosh"):
        assert spoof not in ua, spoof
    assert browser.closed and pw.stopped


def test_shared_launcher_is_stock_playwright_without_proxy():
    from scrapers.counties import manatee

    src = inspect.getsource(manatee.launch_plain_browser)
    assert "from playwright.sync_api import sync_playwright" in src
    assert "patchright" not in src and "channel" not in src
    assert "--no-proxy-server" in src and "headless=True" in src
    assert manatee.browser_env({"HTTPS_PROXY": "x", "ALL_PROXY": "y", "PATH": "/bin"}) == {"PATH": "/bin"}


def test_pinellas_module_has_no_patchright_or_stealth():
    src = PINELLAS_SRC.read_text()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            imported.add(node.module)
    assert not imported & FORBIDDEN_MODULES, imported & FORBIDDEN_MODULES
    assert "scrapers.chromium_flags" not in imported
    assert "scrapers.socks_proxy" not in imported and "scrapers.solvecaptcha" not in imported
    # Code only (docstrings and comments removed): no stealth, proxy or spoofing knobs.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree).lower()
    for banned in ("patchright", "stealth", "impersonat", "channel=", "proxy", "captcha", "add_init_script"):
        assert banned not in code, banned


# ── #142 bond rules unchanged ───────────────────────────────────────────────
def test_bond_rules_unknown_is_empty_never_zero():
    from scrapers.counties.pinellas import PinellasCountyScraper as P

    assert P._format_bond_amount(None) == ""
    assert P._format_bond_amount("") == ""
    assert P._format_bond_amount("NO BOND") == ""
    assert P._format_bond_amount("$0.00") == "0"  # a published $0.00 is a real 0
    assert P._format_bond_amount("$1,500.00") == "1500"


# ── Evidence consistency ────────────────────────────────────────────────────
def test_health_evidence_and_docs_agree_on_relay_only_unverified():
    from dashboard.extensions import SCRAPER_SOURCE_STATES, scraper_source_state

    assert "Pinellas (FL)" not in SCRAPER_SOURCE_STATES
    assert scraper_source_state("Pinellas (FL)") == "unverified"
    ext = (ROOT / "dashboard" / "extensions.py").read_text()
    assert "Brendan 2026-10-08 1:38 PM ET" in ext

    ev = json.loads((ROOT / "docs" / "recon" / "county_source_contract_evidence.json").read_text())
    rec = [r for r in ev["records"] if r["state"] == "FL" and r["county_fips"] == "103"]
    assert len(rec) == 1 and rec[0]["passive_recommendation"] == "recon_only"
    assert "Brendan 2026-10-08 1:38 PM ET" in rec[0]["access_posture"]
    assert "PINELLAS_EGRESS_MODE=direct" in rec[0]["access_posture"]

    live = json.loads((ROOT / "docs" / "recon" / "live_emitter_evidence.json").read_text())
    rows = [r for r in live["records"] if r["label"] == "Pinellas (FL)"]
    assert len(rows) == 1 and rows[0]["emitter"] == "live_write" and "relay-only" in rows[0]["evidence"]

    matrix = (ROOT / "docs" / "recon" / "COUNTY_SOURCE_CONTRACT_MATRIX.md").read_text()
    assert "| Pinellas (FL) | unverified | live_write |" in matrix
    assert "| FL | 103 | Pinellas County | OSI + Palmetto | registered | recon_only |" in matrix

    doc = (ROOT / "docs" / "recon" / "FL_PINELLAS_RELAY_ONLY_2026-10-08.md").read_text()
    assert "Brendan, 2026-10-08 1:38 PM ET" in doc and "5,971 bytes" in doc
    assert not (ROOT / "docs" / "recon" / "FL_PINELLAS_FAIL_CLOSED_2026-10-08.md").exists()
    assert (ROOT / "docs" / "ops" / "PINELLAS_RELAY_RUN.md").exists()
    assert (ROOT / "scripts" / "pinellas_relay_smoke.py").exists()
