"""Stacked on #168: outbound alerts/messages, response middleware and global JS guard
never print a Miami-Dade internal booking key, and leave everything else byte-identical.

CoS rule (2026-10-09). #168 keeps the key as the stored routing id and blanks
dashboard displays, documents and CSV/XLSX exports; this file covers the
surfaces moved to #170.
"""
from __future__ import annotations

import ast
import asyncio
import csv
import io
import json
import re
from pathlib import Path

import pytest

from core.booking_identity import (
    public_booking_number,
    redact_internal_keys,
    redact_workbook,
    redacting_csv_writer,
    redacting_dict_writer,
    scrub_internal_keys,
)

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
KEY = "md_dedupe_v2:" + "ab" * 32  # synthetic
PUBLIC = "2026-123456"
# Non-Miami-Dade booking numbers and look-alikes that must never be touched.
NON_MD = ["2026-123456", "BK00012345", "MD-2026-1234", "md_dedupe", "md_dedupe_v2:", "md_dedupe_v2:XYZ",
          "md_dedupe_v2:abc", "Booking #24-00981 (Lee)", "", "0"]

_PY_DIRS = ("dashboard", "core", "writers", "services")


def _py_files():
    for d in _PY_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            if "__pycache__" not in p.parts:
                yield p


def _call_name(node):
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


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


_SINK_MARKERS = ("hooks.slack", "SLACK_WEBHOOK", "api.telegram.org", "slack.com/api")


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


def test_booking_guard_js_defines_helpers():
    js = (DASH / "sl-booking-guard.js").read_text(encoding="utf-8")
    for needle in ("window.slRedactKeys", "window.slBookingLabel", "MutationObserver",
                   "md_dedupe_v\\d+:[0-9a-f]{16,64}"):
        assert needle in js, needle



def test_telegram_send_message_redacted(monkeypatch):
    captured: list = []
    _fake_httpx(monkeypatch, captured)
    from dashboard.services.telegram_service import TelegramService

    asyncio.run(TelegramService(bot_token="x").send_message("1", f"Re-arrest {KEY}"))
    assert captured and captured[0]["json"]["text"] == "Re-arrest "


# ── byte-identical pass-through for everything that is not an internal key ──

@pytest.mark.parametrize("value", NON_MD)
def test_redact_helpers_pass_non_md_through_unchanged(value):
    assert redact_internal_keys(value) is value  # same object, no copy
    assert scrub_internal_keys(value) == value
    assert public_booking_number(value) == value
    payload = {"text": f"Bond {value} posted", "blocks": [{"t": value}], "n": 7, "ok": True, "none": None,
               "tup": (value, 1.5)}
    assert json.dumps(redact_internal_keys(payload), sort_keys=True) == json.dumps(payload, sort_keys=True)


def test_redacting_writers_and_workbook_byte_identical_for_non_md():
    rows = [["booking_number", "name"]] + [[v, "Doe"] for v in NON_MD]
    a, b = io.StringIO(), io.StringIO()
    csv.writer(a).writerows(rows)
    redacting_csv_writer(b).writerows(rows)
    assert a.getvalue() == b.getvalue()
    a, b = io.StringIO(), io.StringIO()
    for buf, w in ((a, csv.DictWriter(a, fieldnames=["bk"])), (b, redacting_dict_writer(b, fieldnames=["bk"]))):
        w.writeheader()
        w.writerows([{"bk": v} for v in NON_MD])
    assert a.getvalue() == b.getvalue()
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    for v in NON_MD:
        wb.active.append([v, "Doe"])
    before = [c.value for r in wb.active.iter_rows() for c in r]
    redact_workbook(wb)
    assert [c.value for r in wb.active.iter_rows() for c in r] == before


def test_redact_bytes_same_object_when_no_key():
    from dashboard.booking_key_middleware import redact_bytes

    for v in NON_MD:
        data = f"<td>{v}</td>".encode()
        assert redact_bytes(data) is data
    assert redact_bytes(f"<td>{KEY}</td>".encode()) == b"<td></td>"


def test_outbound_senders_non_md_payloads_unchanged(monkeypatch):
    from dashboard.services import automation_digest
    from dashboard.services.telegram_service import TelegramService
    from dashboard.services.twilio_service import TwilioService

    text = "Court 10/12 for booking 2026-123456 (MD-2026-1234), md_dedupe_v2:XYZ"
    captured: list = []
    _fake_httpx(monkeypatch, captured)
    monkeypatch.setenv("SLACK_WEBHOOK_LEADS", "https://hooks.slack.invalid/T/B/X")
    asyncio.run(automation_digest.post_slack(text))
    asyncio.run(TelegramService(bot_token="x").send_message("1", text))
    svc = TwilioService.__new__(TwilioService)
    svc.sid, svc.token, svc.from_number, svc.messaging_service_sid = "ACx", "t", "+15550100", ""
    svc.base_url = "https://api.twilio.invalid/2010-04-01/Accounts/ACx"
    try:
        asyncio.run(svc.send_sms("+15550101", text))
    except Exception:
        pass
    assert captured[0]["json"] == {"text": text}
    assert captured[1]["json"]["text"] == text
    assert captured[2]["data"]["Body"] == text


# ── response middleware ────────────────────────────────────────────────────

def _mw_client(max_bytes=None):
    from starlette.applications import Starlette
    from starlette.responses import (HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
                                     Response, StreamingResponse)
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from dashboard.booking_key_middleware import BookingKeyRedactMiddleware

    def r(path, fn):
        async def ep(request):
            return fn(request)
        return Route(path, ep)

    bodies = {}

    def stream(request):
        async def gen():
            yield f"booking,{KEY}\n".encode()
            yield b"x,y\n"
        return StreamingResponse(gen(), media_type="text/csv")

    routes = [
        r("/p", lambda q: HTMLResponse(f"<p>Booking {KEY}</p>")),
        r("/t", lambda q: PlainTextResponse(f"booking,{KEY}\n",
                                            headers={"Content-Disposition": f'attachment; filename="Bond_{KEY}.txt"'})),
        r("/c", lambda q: Response(f"bk\n{KEY}\n", media_type="text/csv; charset=utf-8")),
        r("/j", lambda q: JSONResponse({"booking_number": KEY, "booking_number_display": ""})),
        r("/x", lambda q: Response(f"<b>{KEY}</b>", media_type="application/xml")),
        r("/s", stream),
        r("/redir", lambda q: RedirectResponse(f"/lead/{KEY}")),
        r("/big", lambda q: PlainTextResponse(("a" * 200) + KEY)),
        r("/plain", lambda q: q.app.state.plain_response()),
    ]
    app = Starlette(routes=routes)
    kwargs = {"max_bytes": max_bytes} if max_bytes else {}
    app.add_middleware(BookingKeyRedactMiddleware, **kwargs)
    return app, TestClient(app, follow_redirects=False)


def test_middleware_rewrites_html_csv_plain_and_download_filename():
    _, c = _mw_client()
    r = c.get("/p")
    assert r.text == "<p>Booking </p>" and int(r.headers["content-length"]) == len(r.content)
    r = c.get("/t")
    assert r.text == "booking,\n" and int(r.headers["content-length"]) == len(r.content)
    assert "md_dedupe" not in r.headers["content-disposition"]
    assert c.get("/c").text == "bk\n\n"


def test_middleware_leaves_json_xml_streams_and_location_untouched():
    _, c = _mw_client()
    assert c.get("/j").json()["booking_number"] == KEY  # routing id in API data
    assert c.get("/x").text == f"<b>{KEY}</b>"
    assert c.get("/s").text == f"booking,{KEY}\nx,y\n"  # streaming untouched (CSV writers redact rows)
    assert c.get("/redir").headers["location"] == f"/lead/{KEY}"  # routing redirect keeps working


def test_middleware_size_cap_skips_large_bodies(caplog):
    _, c = _mw_client(max_bytes=64)
    with caplog.at_level("WARNING", logger="dashboard.booking_key_middleware"):
        r = c.get("/big")
    assert r.text.endswith(KEY)  # over the cap: unchanged
    assert any("not rewritten" in m for m in caplog.messages)
    assert KEY not in " ".join(caplog.messages)  # log carries sizes only


@pytest.mark.parametrize("media_type", ["text/html; charset=utf-8", "text/csv", "text/plain",
                                        "application/json", "application/octet-stream"])
@pytest.mark.parametrize("value", NON_MD)
def test_middleware_byte_identical_for_non_md(media_type, value):
    from starlette.responses import Response

    app, c = _mw_client()
    body = (f"<tr><td>{value}</td></tr>\n" * 50).encode() + bytes([0, 159, 255])
    headers = {"Content-Disposition": f'attachment; filename="Bond_{value or "x"}.dat"', "X-Booking": value}
    app.state.plain_response = lambda: Response(body, media_type=media_type, headers=headers)
    r = c.get("/plain")
    assert r.content == body
    assert r.headers["content-length"] == str(len(body))
    assert r.headers["content-disposition"] == headers["Content-Disposition"]
    assert r.headers["x-booking"] == value


@pytest.mark.parametrize("page", ["mobile.html", "tablet.html", "start_bond_packet.html"])
def test_guarded_pages_print_booking_through_label(page):
    html = (DASH / page).read_text(encoding="utf-8")
    shown = [ln for ln in html.splitlines()
             if "booking_number" in ln and ("_esc(" in ln or "textContent" in ln)]
    assert shown and all("slBookingLabel" in ln for ln in shown), page


def test_booking_guard_js_parses():
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, "--check", str(DASH / "sl-booking-guard.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:300]


def test_booking_guard_keeps_routing_values_and_intercepts_assignments():
    """Codex #171 P1/P2: non-text values untouched; programmatic text assignments hidden but readable."""
    import json as _json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(ROOT / "tests/js/booking_guard_sim.js"), str(DASH / "sl-booking-guard.js")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:300]
    out = _json.loads(r.stdout.strip().splitlines()[-1])
    assert out["textShown"] == ""                      # programmatic el.value = key prints blank
    assert out["textRead"] == KEY == out["textKept"]   # consumers still read the routing id
    assert out["chkRead"] == KEY == out["chkShown"]    # Bulk Exonerate checkbox value untouched
    assert out["hiddenRead"] == KEY
    assert out["mixed"] == "note  end"
    assert out["plain"] == "2026-123456"               # non-MD values untouched
    assert out["afterType"] == "24-0001" and out["keyCleared"] is True
    assert out["label"] == "|2026-123456"
