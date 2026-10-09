"""CoS rule (2026-10-09): a Miami-Dade internal booking key is never printed.

Miami-Dade docs keep ``md_dedupe_v2:<sha256>`` in ``booking_number`` as the
routing id (unique index + dashboard routes). Every human-facing surface must
print it blank. Representative functional tests plus grep-style guards that
fail when a new sink bypasses the shared helpers.
"""
from __future__ import annotations

import ast
import asyncio
import csv
import io
import re
from pathlib import Path

import pytest

from core.booking_identity import (
    public_booking_number,
    redact_internal_keys,
    redact_workbook,
    redacting_csv_writer,
    redacting_dict_writer,
)

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
KEY = "md_dedupe_v2:" + "ab" * 32  # synthetic
PUBLIC = "2026-123456"


# ── helpers ────────────────────────────────────────────────────────────────

def test_public_booking_number_blank_for_key_only():
    assert public_booking_number(KEY) == ""
    assert public_booking_number(PUBLIC) == PUBLIC


def test_redact_removes_key_substrings_deep_and_keeps_text():
    payload = {"text": f"New bond {KEY} for Doe", "blocks": [{"t": KEY}], "n": 5, "tup": (KEY, PUBLIC)}
    out = redact_internal_keys(payload)
    assert KEY not in repr(out)
    assert out["text"] == "New bond  for Doe"
    assert out["blocks"][0]["t"] == ""
    assert out["n"] == 5 and out["tup"] == ("", PUBLIC)
    assert payload["text"].endswith("Doe") and KEY in payload["text"]  # input untouched


def test_csv_writers_redact_rows():
    buf = io.StringIO()
    w = redacting_csv_writer(buf)
    w.writerow(["booking_number", "name"])
    w.writerows([[KEY, "Doe"], [PUBLIC, "Roe"]])
    d = redacting_dict_writer(buf, fieldnames=["booking_number"])
    d.writeheader()
    d.writerow({"booking_number": KEY})
    text = buf.getvalue()
    assert "md_dedupe_v" not in text and PUBLIC in text
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1] == ["", "Doe"]


def test_workbook_cells_redacted_before_save():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Booking", "Name"])
    ws.append([KEY, f"Doe ({KEY})"])
    ws.append([PUBLIC, "Roe"])
    redact_workbook(wb)
    vals = [c.value for row in ws.iter_rows() for c in row]
    assert not any(isinstance(v, str) and "md_dedupe_v" in v for v in vals)
    assert PUBLIC in vals and "Doe ()" in vals


# ── outbound senders ───────────────────────────────────────────────────────

class _Resp:
    status_code = 200
    text = "ok"

    def json(self):
        return {"ok": True, "sid": "SMx"}

    def raise_for_status(self):
        return None


def _fake_httpx(monkeypatch, captured):
    import httpx

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            captured.append(kw)
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


def test_slack_post_slack_text_redacted(monkeypatch):
    captured: list = []
    _fake_httpx(monkeypatch, captured)
    monkeypatch.setenv("SLACK_WEBHOOK_LEADS", "https://hooks.slack.invalid/T/B/X")
    from dashboard.services import automation_digest

    asyncio.run(automation_digest.post_slack(f"Bond posted {KEY} Doe"))
    assert captured, "post_slack did not post"
    assert "md_dedupe_v" not in repr(captured)


def test_telegram_send_message_redacted(monkeypatch):
    captured: list = []
    _fake_httpx(monkeypatch, captured)
    from dashboard.services import telegram_service

    svc_cls = getattr(telegram_service, "TelegramService", None)
    if svc_cls is None:
        pytest.skip("TelegramService not present")
    svc = svc_cls.__new__(svc_cls)
    for attr, val in (("token", "x"), ("bot_token", "x"), ("base_url", "https://api.telegram.invalid/botx"),
                      ("api_url", "https://api.telegram.invalid/botx"), ("enabled", True)):
        setattr(svc, attr, val)
    try:
        asyncio.run(svc.send_message(chat_id="1", text=f"Re-arrest {KEY}"))
    except TypeError:
        asyncio.run(svc.send_message("1", f"Re-arrest {KEY}"))
    except Exception:
        pass
    assert "md_dedupe_v" not in repr(captured)


def test_twilio_send_sms_body_redacted(monkeypatch):
    captured: list = []
    _fake_httpx(monkeypatch, captured)
    from dashboard.services.twilio_service import TwilioService

    svc = TwilioService.__new__(TwilioService)
    svc.sid, svc.token, svc.from_number, svc.messaging_service_sid = "ACx", "t", "+15550100", ""
    svc.base_url = "https://api.twilio.invalid/2010-04-01/Accounts/ACx"
    try:
        asyncio.run(svc.send_sms("+15550101", f"Court date for {KEY}", booking_number=KEY))
    except Exception:
        pass
    assert captured, "send_sms did not post"
    assert captured[0]["data"]["Body"] == "Court date for "


def test_checkin_evidence_filename_never_prints_key():
    from dashboard.services.checkin_evidence_service import safe_booking_filename

    assert "md_dedupe" not in safe_booking_filename(KEY)
    assert safe_booking_filename(KEY) == "booking"
    assert safe_booking_filename(PUBLIC) == PUBLIC


# ── response middleware (server-rendered pages, text downloads, headers) ───

def test_middleware_redacts_html_text_and_headers_but_not_json_routing():
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from dashboard.booking_key_middleware import BookingKeyRedactMiddleware

    async def page(request):
        return HTMLResponse(f"<p>Booking {KEY}</p>")

    async def text(request):
        return PlainTextResponse(f"booking,{KEY}\n",
                                 headers={"Content-Disposition": f'attachment; filename="Bond_{KEY}.csv"'})

    async def data(request):
        return JSONResponse({"booking_number": KEY, "booking_number_display": ""})

    async def stream(request):
        async def gen():
            yield b"a,"
            yield b"b\n"
        return StreamingResponse(gen(), media_type="text/csv")

    app = Starlette(routes=[Route("/p", page), Route("/t", text), Route("/j", data), Route("/s", stream)])
    app.add_middleware(BookingKeyRedactMiddleware)
    c = TestClient(app)
    r = c.get("/p")
    assert r.text == "<p>Booking </p>" and int(r.headers["content-length"]) == len(r.content)
    r = c.get("/t")
    assert "md_dedupe" not in r.text and "md_dedupe" not in r.headers["content-disposition"]
    assert c.get("/j").json()["booking_number"] == KEY  # routing id survives in JSON data
    assert c.get("/s").text == "a,b\n"


# ── grep-style guards ──────────────────────────────────────────────────────

_PY_DIRS = ("dashboard", "core", "writers", "services")
_SINK_MARKERS = ("hooks.slack", "SLACK_WEBHOOK", "api.telegram.org", "slack.com/api")


def _py_files():
    for d in _PY_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            if "__pycache__" not in p.parts:
                yield p


def _call_name(node):
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def test_guard_slack_telegram_json_payloads_are_redacted():
    offenders = []
    for p in _py_files():
        src = p.read_text(encoding="utf-8", errors="ignore")
        if not any(m in src for m in _SINK_MARKERS):
            continue
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and _call_name(node) == "post":
                for kw in node.keywords:
                    if kw.arg == "json" and not (
                        isinstance(kw.value, ast.Call) and _call_name(kw.value) == "redact_internal_keys"
                    ):
                        offenders.append(f"{p.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, "Slack/Telegram json= payloads must pass redact_internal_keys: " + ", ".join(offenders)


def test_guard_no_raw_csv_writers():
    pat = re.compile(r"\bcsv\.(writer|DictWriter)\(")
    offenders = [str(p.relative_to(ROOT)) for p in _py_files()
                 if p.name != "booking_identity.py" and pat.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert not offenders, "use redacting_csv_writer / redacting_dict_writer: " + ", ".join(offenders)


def test_guard_every_workbook_save_is_redacted():
    offenders = []
    for p in _py_files():
        src = p.read_text(encoding="utf-8", errors="ignore")
        if "openpyxl" not in src:
            continue
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_src = ast.get_source_segment(src, fn) or ""
            if re.search(r"\bwb\.save\(", body_src) and "redact_workbook(" not in body_src:
                offenders.append(f"{p.relative_to(ROOT)}:{fn.name}")
    assert not offenders, "XLSX exports must call redact_workbook before save: " + ", ".join(offenders)


@pytest.mark.parametrize("path, func", [
    ("dashboard/services/twilio_service.py", "send_sms"),
    ("dashboard/services/gmail_reader.py", "send_email"),
    ("dashboard/services/bb_client.py", "send_imessage"),
    ("dashboard/services/bb_client.py", "send_message_universal"),
])
def test_guard_message_entry_points_redact(path, func):
    p = ROOT / path
    if not p.exists():
        pytest.skip(f"{path} not present")
    src = p.read_text(encoding="utf-8")
    for fn in ast.walk(ast.parse(src)):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == func:
            assert "redact_internal_keys(" in (ast.get_source_segment(src, fn) or ""), f"{path}:{func}"
            return
    pytest.skip(f"{func} not in {path}")


def test_guard_sheets_and_notification_text_redacted():
    sw = (ROOT / "writers/sheets_writer.py").read_text(encoding="utf-8")
    assert sw.count("to_sheet_row()") == sw.count("redact_internal_keys([record.to_sheet_row()")
    notif = (DASH / "routers/notifications.py").read_text(encoding="utf-8")
    assert '"title": redact_internal_keys(title)' in notif and '"message": redact_internal_keys(message)' in notif


def test_guard_response_middleware_registered():
    main = (DASH / "main.py").read_text(encoding="utf-8")
    assert "app.add_middleware(BookingKeyRedactMiddleware)" in main


def test_guard_every_dashboard_page_loads_booking_guard_first():
    for page in sorted(DASH.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        if page.name == "recovery_portal.html":
            # Recovery scope only serves the page itself; it carries its own label fn.
            assert "function bkl(" in html
            continue
        scripts = re.findall(r"<script\b[^>]*>", html)
        assert scripts, page.name
        assert "sl-booking-guard.js" in scripts[0], f"{page.name}: sl-booking-guard.js must be the first script"


def test_guard_recovery_portal_prints_labels_only():
    html = (DASH / "recovery_portal.html").read_text(encoding="utf-8")
    allowed = ("bkl(", "data-book", 'name="booking_number"', "body.booking_number")
    bad = [ln.strip() for ln in html.splitlines()
           if "booking_number" in ln and not any(a in ln for a in allowed)]
    assert not bad, bad


@pytest.mark.parametrize("name, min_count", [
    ("sl-tasks.js", 1), ("sl-relationships.js", 2), ("sl-indemnitor.js", 2),
    ("sl-active-bonds.js", 4), ("sl-active-bonds-ext.js", 2),
])
def test_guard_list_views_use_booking_label(name, min_count):
    assert (DASH / name).read_text(encoding="utf-8").count("slBookingLabel") >= min_count


def test_guard_js_downloads_redacted():
    offenders = []
    for js in DASH.glob("*.js"):
        src = js.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"new Blob\(\[([^\]]*)\]", src):
            inner = m.group(1)
            if "slRedactKeys" in inner or inner.strip() in ("svg",):
                continue
            offenders.append(f"{js.name}: {inner.strip()[:40]}")
    assert not offenders, "JS text downloads must pass window.slRedactKeys: " + ", ".join(offenders)


def test_booking_guard_js_defines_helpers():
    js = (DASH / "sl-booking-guard.js").read_text(encoding="utf-8")
    for needle in ("window.slRedactKeys", "window.slBookingLabel", "MutationObserver",
                   "md_dedupe_v\\d+:[0-9a-f]{16,64}"):
        assert needle in js, needle


def test_js_files_parse():
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    for name in ("sl-booking-guard.js", "sl-core.js", "sl-tasks.js", "sl-relationships.js",
                 "sl-indemnitor.js", "sl-active-bonds.js", "sl-active-bonds-ext.js"):
        r = subprocess.run([node, "--check", str(DASH / name)], capture_output=True, text=True)
        assert r.returncode == 0, f"{name}: {r.stderr[:300]}"
