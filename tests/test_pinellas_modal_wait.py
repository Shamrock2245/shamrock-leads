"""Pinellas Subject Charge Report modal: wait/retry/close logic and
names-free diagnostics, with fake page objects (no Playwright, no network).

Relay smoke 2026-10-09 (main 6c9303d): 10/10 modals "did not render". Root
cause: the old row-click script carried a raw newline inside a JS regex
literal, so every evaluate threw a SyntaxError that was logged only at DEBUG.
All fixtures here are synthetic.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess

import pytest

import scrapers.counties.pinellas as mod
from scrapers.counties.pinellas import PinellasCountyScraper as P
from scrapers.scraper_resilience import classify_exception

SYNTH_NAME = "TESTPERSON, ALPHA BRAVO"
MODAL_TEXT = (
    "Subject Charge Report\nOffense Description:\nSYNTHETIC BATTERY\n"
    "Court Case Number:\n26-0001-MM\nBond Assessed:\n$1,500.00\n"
)
CLOSED = {"containers": 0, "text_len": 0, "has_marker": False, "text": "", "reconnect": False}


def opened(text=MODAL_TEXT):
    marker = bool(re.search(r"Bond Assessed|Offense Description", text))
    return {"containers": 1, "text_len": len(text), "has_marker": marker,
            "text": text if marker else "", "reconnect": False}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeLocator:
    def __init__(self, page, kind):
        self.page, self.kind = page, kind

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.page.modal_open else 0

    def scroll_into_view_if_needed(self, **_):
        return None

    def click(self, **_):
        if self.kind == "target":
            if self.page.trusted_click_fails:
                raise RuntimeError("element is not attached")
            self.page.open_next()
        else:  # close button inside the modal
            self.page.modal_open = False

    def get_by_role(self, *a, **k):
        return FakeLocator(self.page, "close")


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        if key == "Escape" and self.page.escape_closes:
            self.page.modal_open = False


class FakePage:
    """Each click opens the next scenario: a list of probe results, one per
    poll (the last one repeats). ``clock_per_poll`` advances time per probe."""

    def __init__(self, scenarios, clock, *, row_present=True, trusted_click_fails=False):
        self.scenarios = list(scenarios)
        self.clock = clock
        self.row_present = row_present
        self.trusted_click_fails = trusted_click_fails
        self.escape_closes = True
        self.modal_open = False
        self.current: list = []
        self.clicks = 0
        self.js_clicks = 0
        self.keyboard = FakeKeyboard(self)

    def open_next(self):
        self.clicks += 1
        self.current = list(self.scenarios.pop(0)) if self.scenarios else [CLOSED]
        self.modal_open = True

    def locator(self, selector):
        return FakeLocator(self, "target" if mod._TARGET_ATTR in selector else "modal")

    def evaluate(self, js, arg=None):
        if js is mod._JS_MARK_ROW_LINK:
            return self.row_present
        if js is mod._JS_CLICK_MARKED:
            self.js_clicks += 1
            self.open_next()
            return True
        if js is mod._JS_PROBE_MODAL:
            if not self.modal_open:
                return dict(CLOSED)
            probe = self.current.pop(0) if len(self.current) > 1 else self.current[0]
            return dict(probe)
        if js is mod._JS_DOM_SHAPE:
            return {"tag_counts": {"div": 40, "td": 90}, "class_counts": {"td-name": 10},
                    "modal_candidates": [], "rows": 10, "blazor": True}
        if js is mod._JS_MODAL_STRUCTURE:
            return [{"root": "div.modal", "structure": "<div.modal-body> Bond Assessed: #text(9)"}]
        raise AssertionError(f"unexpected evaluate: {js[:40]!r}")


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(mod, "_now", c.now)
    monkeypatch.setattr(mod, "_sleep", c.sleep)
    monkeypatch.delenv(mod.MODAL_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(mod.MODAL_DEBUG_ENV, raising=False)
    return c


def _scraper():
    s = P()
    s._known_names = frozenset({SYNTH_NAME})
    return s


# ── Root cause: every JS snippet must be valid JavaScript ───────────────────
JS_CONSTANTS = ["_JS_MARK_ROW_LINK", "_JS_CLICK_MARKED", "_JS_PROBE_MODAL",
                "_JS_DOM_SHAPE", "_JS_MODAL_STRUCTURE"]


@pytest.mark.parametrize("name", JS_CONSTANTS)
def test_js_has_no_raw_newline_inside_a_regex_literal(name):
    js = getattr(mod, name)
    # A regex literal opened after "(" or "split(" must not span a raw newline
    # (the 2026-10-09 bug: Python turned "\n" in /\n/ into a real newline).
    assert not re.search(r"\(/[^/\n]*\n", js), name


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("name", JS_CONSTANTS)
def test_js_parses_with_node(tmp_path, name):
    f = tmp_path / "snippet.js"
    f.write_text("const f = " + getattr(mod, name) + ";\n")
    proc = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ── Wait / retry behaviour ─────────────────────────────────────────────────
def test_late_render_succeeds_on_first_attempt(clock):
    # Container shows empty for ~3s, then the charge fields render.
    page = FakePage([[opened("")] * 12 + [opened()]], clock)
    s = _scraper()
    detail = s._read_detail_modal(page, "2600000001")
    assert detail == {"charges": "SYNTHETIC BATTERY", "bond_amount": "1500", "case_numbers": "26-0001-MM"}
    assert page.clicks == 1
    assert not s._modal_failure_reasons
    assert page.modal_open is False  # closed before the next booking


def test_never_rendered_is_selector_not_found_after_one_retry(clock, caplog):
    page = FakePage([[CLOSED], [CLOSED]], clock)
    s = _scraper()
    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        assert s._read_detail_modal(page, "2600000002") is None
    assert page.clicks == 2  # one retry
    assert s._modal_failure_reasons == {mod.REASON_SELECTOR: 1}
    lines = [r.getMessage() for r in caplog.records]
    assert any("reason=selector_not_found" in l and "attempt=1/2" in l and "timeout_ms=20000" in l
               for l in lines)
    assert any("dom_shape=" in l for l in lines)
    assert not any("TESTPERSON" in l or "ALPHA" in l for l in lines)


def test_empty_modal_is_reported_as_empty_content(clock):
    page = FakePage([[opened("")], [opened("")]], clock)
    s = _scraper()
    assert s._read_detail_modal(page, "2600000003") is None
    assert s._modal_failure_reasons == {mod.REASON_EMPTY: 1}


def test_modal_without_charge_fields_is_reported_distinctly(clock):
    page = FakePage([[opened("Loading...")], [opened("Loading...")]], clock)
    s = _scraper()
    assert s._read_detail_modal(page, "2600000004") is None
    assert s._modal_failure_reasons == {mod.REASON_NO_FIELDS: 1}


def test_success_after_one_retry(clock):
    page = FakePage([[CLOSED], [opened()]], clock)
    s = _scraper()
    detail = s._read_detail_modal(page, "2600000005")
    assert detail["bond_amount"] == "1500"
    assert page.clicks == 2
    assert not s._modal_failure_reasons and s._modal_fail_streak == 0


def test_circuit_disconnect_is_reported(clock):
    rc = dict(CLOSED, reconnect=True)
    page = FakePage([[rc], [rc]], clock)
    s = _scraper()
    assert s._read_detail_modal(page, "2600000006") is None
    assert s._modal_failure_reasons == {mod.REASON_CIRCUIT: 1}


def test_missing_row_link_is_not_retried(clock):
    page = FakePage([], clock, row_present=False)
    s = _scraper()
    assert s._read_detail_modal(page, "2600000007") is None
    assert page.clicks == 0
    assert s._modal_failure_reasons == {mod.REASON_CLICK_TARGET: 1}


def test_trusted_click_failure_falls_back_to_dom_click(clock):
    page = FakePage([[opened()]], clock, trusted_click_fails=True)
    s = _scraper()
    assert s._read_detail_modal(page, "2600000008")["bond_amount"] == "1500"
    assert page.js_clicks == 1


def test_exception_text_is_redacted_and_typed(clock, caplog):
    class Boom(FakePage):
        def evaluate(self, js, arg=None):
            if js is mod._JS_MARK_ROW_LINK:
                raise RuntimeError(f"Target closed while reading {SYNTH_NAME}")
            return super().evaluate(js, arg)

    page = Boom([], clock)
    s = _scraper()
    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        assert s._read_detail_modal(page, "2600000009") is None
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "reason=exception" in text and "RuntimeError: Target closed" in text
    assert "TESTPERSON" not in text and "ALPHA" not in text and "BRAVO" not in text


def test_timeout_env_is_clamped(monkeypatch):
    monkeypatch.setenv(mod.MODAL_TIMEOUT_ENV, "999999")
    assert mod.modal_timeout_ms() == mod.MODAL_TIMEOUT_MS_CAP == 45000
    monkeypatch.setenv(mod.MODAL_TIMEOUT_ENV, "10")
    assert mod.modal_timeout_ms() == 2000
    monkeypatch.setenv(mod.MODAL_TIMEOUT_ENV, "junk")
    assert mod.modal_timeout_ms() == 20000


def test_wait_respects_timeout(clock, monkeypatch):
    monkeypatch.setenv(mod.MODAL_TIMEOUT_ENV, "5000")
    page = FakePage([[CLOSED], [CLOSED]], clock)
    start = clock.t
    _scraper()._read_detail_modal(page, "2600000010")
    # two 5s attempts + retry pause + inter-booking pause (+ close waits)
    assert 10.0 <= clock.t - start <= 20.0


def test_consecutive_failures_stop_opening_modals(clock):
    s = _scraper()
    page = FakePage([[CLOSED]] * 20, clock)
    for i in range(mod.MODAL_ABORT_AFTER):
        assert s._read_detail_modal(page, f"26000001{i:02d}") is None
    clicks = page.clicks
    assert s._read_detail_modal(page, "2600000199") is None
    assert page.clicks == clicks  # no more clicks this run
    assert s._modal_failure_reasons[mod.REASON_ABORTED] == 1


def test_all_failed_raises_with_reasons_and_is_not_retried_as_network():
    s = _scraper()
    s._modal_attempts = s._modal_failures = 3
    from collections import Counter
    s._modal_failure_reasons = Counter({mod.REASON_SELECTOR: 2, mod.REASON_NO_FIELDS: 1})
    with pytest.raises(RuntimeError, match="every Subject Charge Report modal failed to render") as ei:
        s._check_modal_failures()
    assert "selector_not_found=2" in str(ei.value)
    # Must not be classified retryable (would re-run the whole relay scrape 3x).
    assert classify_exception(ei.value).retryable is False


# ── Debug mode ─────────────────────────────────────────────────────────────
class FakeWS:
    def __init__(self):
        self.url = "wss://whosinjail.example.invalid/_blazor?id=SECRET-TOKEN"
        self.handlers = {}

    def on(self, event, fn):
        self.handlers[event] = fn


class FakeMsg:
    type = "error"
    text = f"Error: circuit failed while rendering {SYNTH_NAME}"


def test_debug_recorder_writes_names_free_jsonl(tmp_path, clock, monkeypatch):
    monkeypatch.setenv(mod.MODAL_DEBUG_ENV, "1")
    s = _scraper()
    rec = mod.ModalDebugRecorder(s, log_dir=tmp_path)
    handlers = {}

    class ListenPage(FakePage):
        def on(self, event, fn):
            handlers[event] = fn

    page = ListenPage([[CLOSED], [CLOSED]], clock)
    rec.attach(page)
    s._debug = rec
    ws = FakeWS()
    handlers["websocket"](ws)
    handlers["console"](FakeMsg())
    ws.handlers["close"]()
    assert s._read_detail_modal(page, "2600000011") is None
    rec.finish({"modal_attempts": 1, "modal_failures": 1})

    assert rec.path.parent == tmp_path and rec.path.name.startswith("pinellas-modal-debug-")
    raw = rec.path.read_text()
    assert "TESTPERSON" not in raw and "ALPHA" not in raw and "SECRET-TOKEN" not in raw
    rows = [json.loads(l) for l in raw.splitlines()]
    kinds = [r["kind"] for r in rows]
    assert kinds[0] == "start" and kinds[-1] == "summary" and kinds.count("modal") == 2
    first = rows[1]
    assert first["reason"] == mod.REASON_SELECTOR
    assert first["selector"] == mod.MODAL_CONTAINER_SELECTOR and first["timeout_ms"] == 20000
    assert "modal_structure" in first and "dom_shape" in first
    ev = {e["kind"] for e in first["events"]}
    assert {"ws_open", "console_error", "ws_close"} <= ev


def test_debug_dir_is_repo_logs():
    assert mod.DEBUG_LOG_DIR.name == "logs"


def test_debug_is_off_by_default(monkeypatch):
    monkeypatch.delenv(mod.MODAL_DEBUG_ENV, raising=False)
    assert mod.modal_debug_enabled() is False


def test_redact_handles_name_shapes():
    out = mod._redact("x DOE, JANE Q y and Smith, John", names=())
    assert "DOE" not in out and "Smith" not in out


def test_smoke_script_debug_flag_sets_env(monkeypatch):
    import importlib.util
    from pathlib import Path

    path = Path(mod.__file__).resolve().parents[2] / "scripts" / "pinellas_relay_smoke.py"
    spec = importlib.util.spec_from_file_location("pinellas_relay_smoke", path)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    # setenv first so monkeypatch restores (removes) the var main() sets.
    monkeypatch.setenv(mod.MODAL_DEBUG_ENV, "0")

    class Boom(P):
        def scrape(self):
            self._modal_attempts = self._modal_failures = 2
            from collections import Counter
            self._modal_failure_reasons = Counter({"selector_not_found": 2})
            raise RuntimeError("Pinellas: every Subject Charge Report modal failed to render (reasons: selector_not_found=2)")

    monkeypatch.setattr(mod, "PinellasCountyScraper", Boom)
    monkeypatch.setattr(mod, "egress_mode", lambda: "direct")
    assert smoke.main(["--debug"]) == 3
    import os
    assert os.environ[mod.MODAL_DEBUG_ENV] == "1"


def test_no_js_string_in_the_module_has_a_raw_newline_regex():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(mod.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "=>" in node.value:
            assert not re.search(r"\(/[^/\n]*\n", node.value), node.value[:80]
