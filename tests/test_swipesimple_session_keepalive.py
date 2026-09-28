"""
Offline tests for the SwipeSimple session keep-alive probe.

No network. Does not set SWIPESIMPLE_LIVE or SWIPESIMPLE_DISPATCH_LIVE.
Cookie fixtures are fake sentinels and must never appear in results or logs.
"""
from __future__ import annotations

import ast
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import dashboard.services.swipesimple_session_keepalive as ka
from dashboard.services.swipesimple_session_keepalive import (
    ALERT_TEXT,
    DEFAULT_ALERT_TO,
    probe_session,
    run_session_keepalive,
)

SENTINEL = "sskeepalive-test-cookie-not-a-secret"
CSRF_SENTINEL = "csrf-sentinel-should-not-log"
ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)


class MemoryCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query, *args, **kwargs):
        doc = self.docs.get(query.get("_id"))
        return None if doc is None else dict(doc)

    async def update_one(self, query, update, upsert=False):
        doc_id = query.get("_id")
        current = self.docs.get(doc_id)
        if current is None:
            if not upsert:
                return
            current = {"_id": doc_id}
        else:
            current = dict(current)
        current.update(update.get("$set") or {})
        current["_id"] = doc_id
        self.docs[doc_id] = current


class MemoryDB:
    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        if name not in self.cols:
            self.cols[name] = MemoryCollection()
        return self.cols[name]


class _Resp:
    def __init__(self, status_code, payload=None, text="", headers=None, json_raises=False):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._payload = payload
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not json")
        return self._payload


class _Client:
    def __init__(self, resp, sink):
        self._resp = resp
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self._sink.append({"url": url, "headers": dict(headers or {})})
        return self._resp


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    ka._once_flags.clear()
    for key in (
        "SWIPESIMPLE_LIVE",
        "SWIPESIMPLE_DISPATCH_LIVE",
        "SWIPESIMPLE_SESSION",
        "SWIPESIMPLE_COOKIE_JAR",
        "SWIPESIMPLE_CSRF_TOKEN",
        "SWIPESIMPLE_SESSION_KEEPALIVE",
        "SWIPESIMPLE_SESSION_ALERT_TO",
        "SWIPESIMPLE_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    ka._once_flags.clear()


def _assert_clean(caplog, *blobs):
    text = caplog.text
    assert SENTINEL not in text
    assert CSRF_SENTINEL not in text
    for blob in blobs:
        rendered = str(blob)
        assert SENTINEL not in rendered
        assert CSRF_SENTINEL not in rendered


def _http(monkeypatch, resp):
    sink = []
    ctor = []

    def factory(*args, **kwargs):
        ctor.append(kwargs)
        return _Client(resp, sink)

    monkeypatch.setattr("httpx.AsyncClient", factory)
    return sink, ctor


def _enable(monkeypatch, *, session=SENTINEL, phone=None):
    monkeypatch.setenv("SWIPESIMPLE_SESSION_KEEPALIVE", "1")
    monkeypatch.setenv("SWIPESIMPLE_SESSION", session)
    monkeypatch.setenv("SWIPESIMPLE_CSRF_TOKEN", CSRF_SENTINEL)
    if phone is not None:
        monkeypatch.setenv("SWIPESIMPLE_SESSION_ALERT_TO", phone)


@pytest.fixture
def caplog_debug(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


def test_cron_registered_default_off():
    from dashboard.cron import CRON_REGISTRY
    from dashboard.routers.automation_control import ALL_SERVICE_KEYS
    from dashboard.services.automation_config import DEFAULT_CONFIG, _FAIL_CLOSED_KEYS

    matches = [c for c in CRON_REGISTRY if c.name == "swipesimple_session_keepalive"]
    assert len(matches) == 1
    cron = matches[0]
    assert cron.label == "SS-SessionKeepalive"
    assert cron.interval == 1200
    assert 90 <= cron.initial_delay <= 180
    assert cron.default_enabled is False
    assert "swipesimple_session_keepalive" in ALL_SERVICE_KEYS
    section = DEFAULT_CONFIG["swipesimple_session_keepalive"]
    assert section["enabled"] is False
    assert section["interval_seconds"] == 1200
    assert "swipesimple_session_keepalive" in _FAIL_CLOSED_KEYS


def test_module_does_not_create_invoices_or_auto_login():
    source = (ROOT / "dashboard/services/swipesimple_session_keepalive.py").read_text()
    tree = ast.parse(source)
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    joined = " ".join(modules).lower()
    assert "playwright" not in joined
    assert "swipesimple_playwright" not in joined
    assert "create_locked_invoice" not in source
    assert "maybe_issue_share_invoice" not in source
    assert "copy_link" not in source
    assert 'os.environ["SWIPESIMPLE_LIVE"]' not in source
    assert 'os.environ["SWIPESIMPLE_DISPATCH_LIVE"]' not in source


def test_docs_name_the_opt_in_gates():
    checklist = (ROOT / "dashboard/services/SWIPESIMPLE_PRODUCTION_CHECKLIST.md").read_text()
    example = (ROOT / ".env.example").read_text()
    assert "SWIPESIMPLE_SESSION_KEEPALIVE" in checklist
    assert "swipesimple_session_keepalive" in checklist
    assert "239-955-0178" in checklist
    assert "Auto-relogin is **not** enabled" in checklist
    assert "SWIPESIMPLE_LIVE" in checklist
    assert "SWIPESIMPLE_SESSION_KEEPALIVE=" in example
    assert "SWIPESIMPLE_SESSION_ALERT_TO=" in example
    assert "SWIPESIMPLE_SESSION=" in example


@pytest.mark.asyncio
async def test_probe_200_json(monkeypatch, caplog_debug):
    _enable(monkeypatch)
    sink, ctor = _http(monkeypatch, _Resp(200, payload={"customers": []}))
    result = await probe_session()
    assert result == {"ok": True, "status": 200, "reason": "ok"}
    assert sink[0]["url"] == "https://swipesimple.com/api/v4/customers?name=Brendan"
    assert sink[0]["headers"]["Accept"] == "application/json"
    assert sink[0]["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert sink[0]["headers"]["Cookie"] == SENTINEL
    assert ctor[0]["follow_redirects"] is False
    _assert_clean(caplog_debug, result)
    assert os.getenv("SWIPESIMPLE_LIVE") in (None, "")
    assert os.getenv("SWIPESIMPLE_DISPATCH_LIVE") in (None, "")


@pytest.mark.asyncio
async def test_probe_401_and_redirect_and_html(monkeypatch, caplog_debug):
    _enable(monkeypatch)
    sink, _ctor = _http(monkeypatch, _Resp(401, text="nope", json_raises=True))
    denied = await probe_session()
    assert denied == {"ok": False, "status": 401, "reason": "unauthorized"}

    _http(
        monkeypatch,
        _Resp(302, text="", headers={"Location": "https://swipesimple.com/login"}, json_raises=True),
    )
    redirected = await probe_session()
    assert redirected == {"ok": False, "status": 302, "reason": "redirect_login"}

    _http(
        monkeypatch,
        _Resp(
            200,
            text="<!DOCTYPE html><form action='/users/sign_in'>login</form>",
            headers={"Content-Type": "text/html"},
            json_raises=True,
        ),
    )
    html = await probe_session()
    assert html == {"ok": False, "status": 200, "reason": "html_login"}
    assert sink  # first client was used
    _assert_clean(caplog_debug, denied, redirected, html)


@pytest.mark.asyncio
async def test_missing_session_skips_http_and_alerts_once_log(monkeypatch, caplog_debug):
    monkeypatch.setenv("SWIPESIMPLE_SESSION_KEEPALIVE", "1")
    sink, _ctor = _http(monkeypatch, _Resp(200, payload={"customers": []}))
    bb = AsyncMock()
    with patch("dashboard.services.bb_client.send_message_universal", bb):
        first = await run_session_keepalive(db=MemoryDB(), now=T0)
        second = await run_session_keepalive(db=MemoryDB(), now=T0)
    assert first["ok"] is False
    assert first["reason"] == "missing_session"
    assert first["alerted"] is False
    assert second["reason"] == "missing_session"
    assert sink == []
    bb.assert_not_called()
    assert caplog_debug.text.count("reason=missing_session") == 1
    _assert_clean(caplog_debug, first, second)


@pytest.mark.asyncio
async def test_disabled_env_does_not_probe(monkeypatch, caplog_debug):
    monkeypatch.setenv("SWIPESIMPLE_SESSION", SENTINEL)
    sink, _ctor = _http(monkeypatch, _Resp(200, payload={"customers": []}))
    bb = AsyncMock()
    with patch("dashboard.services.bb_client.send_message_universal", bb):
        result = await run_session_keepalive(db=MemoryDB(), now=T0)
    assert result["reason"] == "keepalive_disabled"
    assert result["alerted"] is False
    assert sink == []
    bb.assert_not_called()
    _assert_clean(caplog_debug, result)


@pytest.mark.asyncio
async def test_success_writes_health_without_cookie(monkeypatch, caplog_debug):
    _enable(monkeypatch)
    _http(monkeypatch, _Resp(200, payload={"customers": [{"id": "c"}]}))
    db = MemoryDB()
    bb = AsyncMock()
    with patch("dashboard.services.bb_client.send_message_universal", bb):
        result = await run_session_keepalive(db=db, now=T0)
    assert result["ok"] is True
    assert result["alerted"] is False
    bb.assert_not_called()
    doc = db["swipesimple_session_health"].docs["merchant_session"]
    assert doc["last_reason"] == "ok"
    assert doc["last_ok_at"]
    assert "cookie" not in doc
    _assert_clean(caplog_debug, result, doc)


@pytest.mark.asyncio
async def test_alert_once_then_cooldown_then_recovery(monkeypatch, caplog_debug):
    _enable(monkeypatch)
    db = MemoryDB()
    bb = AsyncMock(return_value={"success": True, "sent": True, "queued": False, "channel": "imessage"})
    tg = AsyncMock()
    tg.send_staff_alert = AsyncMock(return_value={"success": True})

    def use(resp):
        _http(monkeypatch, resp)

    use(_Resp(401, text="expired", json_raises=True))
    with patch("dashboard.services.bb_client.send_message_universal", bb), patch(
        "dashboard.services.telegram_service.get_telegram_service", return_value=tg
    ):
        first = await run_session_keepalive(db=db, now=T0)
        use(_Resp(401, text="expired", json_raises=True))
        second = await run_session_keepalive(db=db, now=T0 + timedelta(minutes=20))
        use(_Resp(200, payload={"customers": []}))
        recovered = await run_session_keepalive(db=db, now=T0 + timedelta(minutes=40))
        use(_Resp(401, text="expired", json_raises=True))
        again = await run_session_keepalive(db=db, now=T0 + timedelta(minutes=50))

    assert first["alerted"] is True
    assert first["reason"] == "unauthorized"
    assert second["alerted"] is False
    assert second["alert_suppressed"] is True
    assert recovered["ok"] is True
    assert recovered["alerted"] is False
    assert again["alerted"] is True
    assert bb.await_count == 2
    assert tg.send_staff_alert.await_count == 2
    phone, message = bb.await_args_list[0].args[:2]
    assert phone == DEFAULT_ALERT_TO == "2399550178"
    assert message == ALERT_TEXT
    assert SENTINEL not in message
    assert bb.await_args_list[0].kwargs.get("purpose") == "swipesimple_session_alert"
    doc = db["swipesimple_session_health"].docs["merchant_session"]
    assert doc["last_alert_at"]
    assert "cookie" not in doc
    _assert_clean(caplog_debug, first, second, recovered, again, doc, message)


@pytest.mark.asyncio
async def test_cooldown_expires_after_six_hours(monkeypatch):
    _enable(monkeypatch)
    db = MemoryDB()
    bb = AsyncMock(return_value={"success": True, "sent": True, "channel": "imessage"})
    tg = AsyncMock()
    tg.send_staff_alert = AsyncMock(return_value={"success": False, "error": "no_staff_chat_configured"})
    with patch("dashboard.services.bb_client.send_message_universal", bb), patch(
        "dashboard.services.telegram_service.get_telegram_service", return_value=tg
    ):
        _http(monkeypatch, _Resp(401, json_raises=True))
        await run_session_keepalive(db=db, now=T0)
        _http(monkeypatch, _Resp(401, json_raises=True))
        suppressed = await run_session_keepalive(db=db, now=T0 + timedelta(hours=5))
        _http(monkeypatch, _Resp(302, headers={"Location": "https://swipesimple.com/users/sign_in"}, json_raises=True))
        later = await run_session_keepalive(db=db, now=T0 + timedelta(hours=6, minutes=1))
    assert suppressed["alert_suppressed"] is True
    assert later["reason"] == "redirect_login"
    assert later["alerted"] is True
    assert bb.await_count == 2


@pytest.mark.asyncio
async def test_transient_http_and_probe_error_do_not_alert(monkeypatch, caplog_debug):
    _enable(monkeypatch)
    db = MemoryDB()
    bb = AsyncMock()

    def boom(*args, **kwargs):
        raise RuntimeError(SENTINEL)

    with patch("dashboard.services.bb_client.send_message_universal", bb):
        _http(monkeypatch, _Resp(503, text="down", json_raises=True))
        unavailable = await run_session_keepalive(db=db, now=T0)
        monkeypatch.setattr("httpx.AsyncClient", boom)
        errored = await run_session_keepalive(db=db, now=T0 + timedelta(minutes=20))
    assert unavailable["reason"] == "http_503"
    assert unavailable["alerted"] is False
    assert errored["reason"] == "probe_error"
    assert errored["alerted"] is False
    bb.assert_not_called()
    _assert_clean(caplog_debug, unavailable, errored)


@pytest.mark.asyncio
async def test_rejected_bb_send_does_not_start_cooldown(monkeypatch):
    _enable(monkeypatch)
    db = MemoryDB()
    bb = AsyncMock(return_value={"success": False, "sent": False, "queued": False, "channel": "failed"})
    tg = AsyncMock()
    tg.send_staff_alert = AsyncMock(return_value={"success": False})
    with patch("dashboard.services.bb_client.send_message_universal", bb), patch(
        "dashboard.services.telegram_service.get_telegram_service", return_value=tg
    ):
        _http(monkeypatch, _Resp(401, json_raises=True))
        first = await run_session_keepalive(db=db, now=T0)
        _http(monkeypatch, _Resp(401, json_raises=True))
        second = await run_session_keepalive(db=db, now=T0 + timedelta(minutes=20))
    assert first["alerted"] is False
    assert second["alerted"] is False
    assert second["alert_suppressed"] is False
    assert bb.await_count == 2
    tg.send_staff_alert.assert_not_called()
    doc = db["swipesimple_session_health"].docs["merchant_session"]
    assert "last_alert_at" not in doc


@pytest.mark.asyncio
async def test_alert_phone_override(monkeypatch):
    _enable(monkeypatch, phone="+12399550178")
    db = MemoryDB()
    bb = AsyncMock(return_value={"success": True, "sent": True, "channel": "imessage"})
    tg = AsyncMock()
    tg.send_staff_alert = AsyncMock(return_value={"success": True})
    with patch("dashboard.services.bb_client.send_message_universal", bb), patch(
        "dashboard.services.telegram_service.get_telegram_service", return_value=tg
    ):
        _http(monkeypatch, _Resp(401, json_raises=True))
        result = await run_session_keepalive(db=db, now=T0)
    assert result["alerted"] is True
    assert bb.await_args.args[0] == "+12399550178"
    assert SENTINEL not in bb.await_args.args[1]


@pytest.mark.asyncio
async def test_cookie_jar_fallback_used_when_session_empty(monkeypatch, caplog_debug):
    monkeypatch.setenv("SWIPESIMPLE_SESSION_KEEPALIVE", "1")
    monkeypatch.setenv("SWIPESIMPLE_COOKIE_JAR", SENTINEL)
    sink, _ctor = _http(monkeypatch, _Resp(200, payload=[]))
    result = await probe_session()
    assert result["ok"] is True
    assert sink[0]["headers"]["Cookie"] == SENTINEL
    _assert_clean(caplog_debug, result)


@pytest.mark.asyncio
async def test_cron_soft_fail_and_disabled_skip(monkeypatch, caplog_debug):
    from dashboard.cron import _run_swipesimple_session_keepalive

    sink, _ctor = _http(monkeypatch, _Resp(200, payload={"customers": []}))
    await _run_swipesimple_session_keepalive()
    await _run_swipesimple_session_keepalive()
    assert sink == []
    assert caplog_debug.text.count("keep-alive disabled") == 1

    monkeypatch.setenv("SWIPESIMPLE_SESSION_KEEPALIVE", "1")

    async def boom():
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(ka, "run_session_keepalive", boom)
    await _run_swipesimple_session_keepalive()
    assert "soft-fail error_type=RuntimeError" in caplog_debug.text
    _assert_clean(caplog_debug)


@pytest.mark.asyncio
async def test_cron_run_log_drops_secret_fields(monkeypatch, caplog_debug):
    from dashboard.cron import _run_swipesimple_session_keepalive

    monkeypatch.setenv("SWIPESIMPLE_SESSION_KEEPALIVE", "1")
    inserted = []

    class LogColl:
        async def insert_one(self, doc):
            inserted.append(doc)

    class DB:
        def __getitem__(self, name):
            assert name == "automation_run_log"
            return LogColl()

    async def poisoned():
        return {
            "ok": False,
            "status": 401,
            "reason": "unauthorized",
            "alerted": True,
            "alert_suppressed": False,
            "cookie": SENTINEL,
            "session": CSRF_SENTINEL,
        }

    monkeypatch.setattr(ka, "run_session_keepalive", poisoned)
    monkeypatch.setattr("dashboard.extensions.get_db", lambda: DB())
    await _run_swipesimple_session_keepalive()
    assert len(inserted) == 1
    assert inserted[0]["automation"] == "swipesimple_session_keepalive"
    assert inserted[0]["result"]["reason"] == "unauthorized"
    assert "cookie" not in inserted[0]["result"]
    _assert_clean(caplog_debug, inserted)
