"""Tests for chat-agent packet staging API (/api/agent/stage-packet & /api/agent/parse-booking-url)."""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.routers.agent_packet import router as agent_packet_router
from tests.bb_fake_mongo import FakeCollection


_PUBLIC_IP = "93.184.216.34"


def _fake_resolve(mapping=None):
    """Resolver stub: hosts in ``mapping`` get those addresses, IP literals and
    numeric forms map through ``ipaddress`` semantics, anything else is public."""
    import ipaddress
    import socket

    mapping = mapping or {}

    def _resolve(host, port):
        if host in mapping:
            return list(mapping[host])
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            pass
        try:
            # inet_aton accepts decimal / hex / short IPv4 like getaddrinfo does
            return [socket.inet_ntoa(socket.inet_aton(host))]
        except OSError:
            return [_PUBLIC_IP]

    return _resolve


import pytest


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch):
    monkeypatch.setattr("dashboard.services.public_url_guard._resolve", _fake_resolve())


def _make_client():
    app = FastAPI()
    app.include_router(agent_packet_router)
    return TestClient(app)


def test_stage_packet_requires_auth(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.delenv("LEADS_INTERNAL_TOKEN", raising=False)
    client = _make_client()

    resp = client.post("/api/agent/stage-packet", json={})
    assert resp.status_code == 401

    resp_bad = client.post(
        "/api/agent/stage-packet",
        headers={"X-API-Key": "wrong"},
        json={},
    )
    assert resp_bad.status_code == 401


def test_stage_packet_blocks_ssrf_urls(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()

    for bad_url in (
        "http://127.0.0.1:8000/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/admin",
        "file:///etc/passwd",
    ):
        resp = client.post(
            "/api/agent/stage-packet",
            headers={"X-API-Key": "secret-gas-key"},
            json={"booking_url": bad_url},
        )
        assert resp.status_code == 400, f"Expected 400 for {bad_url}, got {resp.status_code}"


def test_stage_packet_merges_booking_url_id_ocr_and_chat_input(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.setenv("SUPER_CRM_URL", "https://leads.shamrockbailbonds.biz")
    client = _make_client()

    cols = {
        "arrests": FakeCollection("arrests"),
        "intake_queue": FakeCollection("intake_queue"),
        "paperwork_packets": FakeCollection("paperwork_packets"),
    }

    fake_booking = {
        "success": True,
        "parse_method": "lee_api",
        "data": {
            "full_name": "JANE MARIE DOE",
            "first_name": "Jane",
            "last_name": "Doe",
            "booking_number": "26-009911",
            "county": "Lee",
            "state": "FL",
            "dob": "1992-04-12",
            "total_bond": 5000.0,
            "charges": [
                {
                    "charge": "GRAND THEFT >$750 <$5,000",
                    "bond_amount": 5000.0,
                    "case_number": "26-CF-001122",
                }
            ],
            "case_number": "26-CF-001122",
        },
    }
    fake_ocr = {
        "success": True,
        "data": {
            "full_name": "JOHN ROBERT SMITH",
            "first_name": "JOHN",
            "last_name": "SMITH",
            "dob": "1978-09-01",
            "dl_number": "S530436783210",
            "dl_state": "FL",
            "address": "1528 BROADWAY",
            "city": "FORT MYERS",
            "state": "FL",
            "zip": "33901",
        },
    }

    fake_img_b64 = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xd9").decode()

    with patch("dashboard.routers.agent_packet.get_collection", side_effect=lambda name: cols[name]), \
         patch("dashboard.routers.agent_packet.ingest_url", AsyncMock(return_value=fake_booking)), \
         patch("dashboard.routers.agent_packet.IDScannerService.scan_id_image", AsyncMock(return_value=fake_ocr)):
        resp = client.post(
            "/api/agent/stage-packet",
            headers={"Authorization": "Bearer secret-gas-key"},
            json={
                "booking_url": "https://www.sheriffleefl.org/booking/?id=26009911",
                "indemnitor": {
                    "phone": "2395550199",
                    "email": "john@example.com",
                    "relationship": "Spouse",
                },
                "id_image_base64": fake_img_b64,
                "surety": "palmetto",
                "notes": "Staged from chat",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["status"] == "staged_ready_for_staff"
    assert data["packet_id"].startswith("PKT-")
    assert data["intake_id"].startswith("INT-AGT-")
    assert data["super_crm_url"] == f"https://leads.shamrockbailbonds.biz/?tab=paperwork&packet_id={data['packet_id']}"

    # Verbatim charge & bond preserved
    assert data["defendant"]["full_name"] == "JANE MARIE DOE"
    assert data["defendant"]["booking_number"] == "26-009911"
    assert data["defendant"]["bond_amount"] == 5000.0
    assert data["defendant"]["charges"][0]["charge"] == "GRAND THEFT >$750 <$5,000"

    # Indemnitor merged from chat + ID OCR
    assert data["indemnitor"]["full_name"] == "JOHN ROBERT SMITH"
    assert data["indemnitor"]["dl_number"] == "S530436783210"
    assert data["indemnitor"]["phone"] == "2395550199"
    assert data["indemnitor"]["email"] == "john@example.com"
    assert data["id_ocr_extracted"] is True

    # Never creates a DocuSeal submission directly
    assert len(cols["paperwork_packets"].docs) == 1
    pkt = cols["paperwork_packets"].docs[0]
    assert pkt["status"] == "staged"
    assert pkt["docuseal_submission_id"] is None
    assert pkt["surety_id"] == "palmetto"
    assert len(cols["intake_queue"].docs) == 1
    # Agent input never creates arrest records (scraper source of truth)
    assert len(cols["arrests"].docs) == 0


def test_sl_hydrate_deep_link_opens_paperwork_packet():
    js = (ROOT / "dashboard" / "sl-hydrate.js").read_text(encoding="utf-8")
    assert "packet_id" in js
    assert "SLPaperwork.startFromBond" in js


def test_stage_packet_blocks_numeric_and_wildcard_dns_ssrf(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.setattr(
        "dashboard.services.public_url_guard._resolve",
        _fake_resolve({
            "169.254.169.254.nip.io": ["169.254.169.254"],
            "internal.example.com": ["10.0.0.5"],
            "cgnat.example.com": ["100.64.0.1"],
            "mixed.example.com": [_PUBLIC_IP, "127.0.0.1"],
        }),
    )
    client = _make_client()
    ingest = AsyncMock(return_value={"success": False})
    with patch("dashboard.routers.agent_packet.ingest_url", ingest):
        for bad_url in (
            "http://2130706433/",
            "http://0x7f000001/",
            "http://127.1/",
            "http://169.254.169.254.nip.io/latest/meta-data/",
            "http://internal.example.com/admin",
            "http://cgnat.example.com/",
            "http://mixed.example.com/",
            "http://user:pw@example.com/",
        ):
            for path in ("/api/agent/stage-packet", "/api/agent/parse-booking-url"):
                resp = client.post(
                    path,
                    headers={"X-API-Key": "secret-gas-key"},
                    json={"booking_url": bad_url},
                )
                assert resp.status_code == 400, f"{path} {bad_url} -> {resp.status_code}"
    ingest.assert_not_awaited()


class _Recorder:
    """Inner transport stub: records each pinned request, answers from ``routes``."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def factory(self):
        import httpx

        def handler(request):
            self.requests.append(request)
            sni = request.extensions.get("sni_hostname")
            return self.routes[sni](request)

        return httpx.MockTransport(handler)


def _pinned_client(recorder):
    import httpx

    from dashboard.services.public_url_guard import PinnedPublicTransport

    transport = PinnedPublicTransport(verify=False, inner_factory=recorder.factory)
    return httpx.AsyncClient(transport=transport, follow_redirects=True)


def test_pinned_transport_connects_to_checked_ip_with_original_host_and_sni(monkeypatch):
    import asyncio

    import httpx

    monkeypatch.setattr(
        "dashboard.services.public_url_guard._resolve",
        _fake_resolve({"jail.example.com": ["93.184.216.34"]}),
    )
    rec = _Recorder({"jail.example.com": lambda r: httpx.Response(200, text="ok")})

    async def run():
        async with _pinned_client(rec) as client:
            return await client.get("https://jail.example.com:8443/booking/1?id=7")

    resp = asyncio.run(run())
    assert resp.status_code == 200
    assert str(resp.request.url) == "https://jail.example.com:8443/booking/1?id=7"
    (sent,) = rec.requests
    assert sent.url.host == "93.184.216.34"
    assert sent.url.port == 8443
    assert sent.url.raw_path == b"/booking/1?id=7"
    assert sent.headers["Host"] == "jail.example.com:8443"
    assert sent.extensions["sni_hostname"] == "jail.example.com"


def test_dns_rebinding_after_check_never_connects_to_loopback(monkeypatch):
    """Public on the check, 127.0.0.1 on every later lookup: no connection is made."""
    import httpx

    from dashboard.services import url_ingest_service
    from dashboard.services.public_url_guard import PinnedPublicTransport

    lookups = []

    def rebinding_resolve(host, port):
        lookups.append(host)
        return ["93.184.216.34"] if len(lookups) == 1 else ["127.0.0.1"]

    monkeypatch.setattr("dashboard.services.public_url_guard._resolve", rebinding_resolve)
    monkeypatch.setattr("config.source_guard.fail_closed_reason", lambda *a, **k: None)
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    rec = _Recorder({"rebind.example.com": lambda r: httpx.Response(200, text="<title>SECRET</title>")})
    monkeypatch.setattr(
        url_ingest_service,
        "_public_only_transport",
        lambda: PinnedPublicTransport(verify=False, inner_factory=rec.factory),
    )
    client = _make_client()
    cols = {
        "arrests": FakeCollection("arrests"),
        "intake_queue": FakeCollection("intake_queue"),
        "paperwork_packets": FakeCollection("paperwork_packets"),
    }
    with patch("dashboard.routers.agent_packet.get_collection", side_effect=lambda name: cols[name]):
        resp = client.post(
            "/api/agent/parse-booking-url",
            headers={"X-API-Key": "secret-gas-key"},
            json={"booking_url": "https://rebind.example.com/booking/1"},
        )
    assert resp.status_code == 422
    assert "SECRET" not in resp.text
    assert len(lookups) == 2  # router check + transport pin; no third lookup at connect
    assert rec.requests == []


def test_redirect_hops_are_revalidated_and_repinned(monkeypatch):
    import asyncio

    import httpx
    import pytest as _pytest

    from dashboard.services.public_url_guard import UnsafeURLError

    monkeypatch.setattr(
        "dashboard.services.public_url_guard._resolve",
        _fake_resolve({
            "a.example.com": ["93.184.216.34"],
            "b.example.org": ["93.184.216.35"],
            "evil.example.net": ["10.0.0.5"],
        }),
    )
    rec = _Recorder({
        "a.example.com": lambda r: httpx.Response(
            302,
            headers={"Location": "https://b.example.org/next" if r.url.path == "/start" else "http://evil.example.net/x"},
        ),
        "b.example.org": lambda r: httpx.Response(200, text="final"),
        "evil.example.net": lambda r: httpx.Response(200, text="SECRET"),
    })

    async def get(path):
        async with _pinned_client(rec) as client:
            return await client.get(f"https://a.example.com{path}")

    resp = asyncio.run(get("/start"))
    assert resp.text == "final"
    assert [(r.url.host, r.headers["Host"], r.extensions["sni_hostname"]) for r in rec.requests] == [
        ("93.184.216.34", "a.example.com", "a.example.com"),
        ("93.184.216.35", "b.example.org", "b.example.org"),
    ]

    rec.requests.clear()
    with _pytest.raises(UnsafeURLError):
        asyncio.run(get("/to-internal"))
    assert [r.extensions["sni_hostname"] for r in rec.requests] == ["a.example.com"]


def test_pinned_transport_real_httpcore_stack_uses_pinned_ip_and_sni(monkeypatch):
    """Through httpx/httpcore with a fake network backend: TCP goes to the pinned
    IP and TLS server_hostname (SNI + cert hostname check) is the original host."""
    import asyncio
    import ssl

    import httpcore
    import httpx
    from httpcore._backends.mock import AsyncMockBackend, AsyncMockStream

    from dashboard.services.public_url_guard import PinnedPublicTransport

    monkeypatch.setattr(
        "dashboard.services.public_url_guard._resolve",
        _fake_resolve({"jail.example.com": ["93.184.216.34"]}),
    )
    seen = {}

    class Stream(AsyncMockStream):
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            seen["server_hostname"] = server_hostname
            seen["check_hostname"] = ssl_context.check_hostname
            return self

    class Backend(AsyncMockBackend):
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            seen["tcp"] = (host, port)
            return Stream(list(self._buffer), http2=self._http2)

    raw = [b"HTTP/1.1 200 OK\r\n", b"Content-Length: 2\r\n", b"\r\n", b"ok"]

    def inner():
        t = httpx.AsyncHTTPTransport(verify=True)
        t._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(), network_backend=Backend(raw)
        )
        return t

    async def run():
        transport = PinnedPublicTransport(verify=True, inner_factory=inner)
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.get("https://jail.example.com/booking/1")

    resp = asyncio.run(run())
    assert resp.text == "ok"
    assert seen["tcp"] == ("93.184.216.34", 443)
    assert seen["server_hostname"] == "jail.example.com"
    assert seen["check_hostname"] is True


def test_ingest_url_default_is_not_pinned(monkeypatch):
    from dashboard.services import url_ingest_service

    calls = []
    monkeypatch.setattr(url_ingest_service, "_public_only_transport", lambda: calls.append(1))
    monkeypatch.setattr("config.source_guard.fail_closed_reason", lambda *a, **k: None)

    import asyncio

    import httpx

    real_client = httpx.AsyncClient
    kwargs_seen = {}

    def client_factory(*args, **kwargs):
        kwargs_seen.update(kwargs)
        kwargs["transport"] = httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>"))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(url_ingest_service.httpx, "AsyncClient", client_factory)
    asyncio.run(url_ingest_service.ingest_url("https://jail.example.com/booking/1"))
    assert calls == []
    assert kwargs_seen["transport"] is None
    assert kwargs_seen["trust_env"] is True


def test_ingest_url_public_only_refuses_redirect_to_internal_host(monkeypatch):
    import asyncio

    import httpx

    from dashboard.services import url_ingest_service
    from dashboard.services.public_url_guard import PinnedPublicTransport

    monkeypatch.setattr(
        "dashboard.services.public_url_guard._resolve",
        _fake_resolve({"jail.example.com": ["93.184.216.34"]}),
    )
    rec = _Recorder({
        "jail.example.com": lambda r: httpx.Response(302, headers={"Location": "http://127.0.0.1:8000/secret"}),
        "127.0.0.1": lambda r: httpx.Response(200, text="<html><title>SECRET</title>Name: X Y</html>"),
    })
    monkeypatch.setattr(
        url_ingest_service,
        "_public_only_transport",
        lambda: PinnedPublicTransport(verify=False, inner_factory=rec.factory),
    )
    monkeypatch.setattr("config.source_guard.fail_closed_reason", lambda *a, **k: None)

    res = asyncio.run(url_ingest_service.ingest_url("https://jail.example.com/booking/1", public_only=True))
    assert res["success"] is False
    assert "SECRET" not in str(res)
    assert [r.extensions["sni_hostname"] for r in rec.requests] == ["jail.example.com"]


def test_stage_packet_does_not_write_arrests_or_leak_exception_text(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = {
        "arrests": FakeCollection("arrests"),
        "intake_queue": FakeCollection("intake_queue"),
        "paperwork_packets": FakeCollection("paperwork_packets"),
    }
    boom = AsyncMock(side_effect=RuntimeError("mongodb://admin:hunter2@10.0.0.5 traceback"))
    with patch("dashboard.routers.agent_packet.get_collection", side_effect=lambda name: cols[name]), \
         patch("dashboard.routers.agent_packet.ingest_url", boom), \
         patch("dashboard.routers.agent_packet.IDScannerService.scan_id_image",
               AsyncMock(side_effect=RuntimeError("ocr internal /srv/secret path"))):
        resp = client.post(
            "/api/agent/stage-packet",
            headers={"X-API-Key": "secret-gas-key"},
            json={
                "booking_url": "https://jail.example.com/booking/1",
                "defendant": {"full_name": "Test Person", "booking_number": "TEST-0001", "county": "Lee"},
                "indemnitor": {"full_name": "Test Cosigner", "phone": "2395550100"},
                "id_image_base64": base64.b64encode(b"\xff\xd8\xff\xd9").decode(),
            },
        )
    assert resp.status_code == 200
    body = resp.text
    assert "hunter2" not in body and "traceback" not in body and "/srv/secret" not in body
    assert cols["arrests"].docs == []
    for doc in cols["intake_queue"].docs + cols["paperwork_packets"].docs:
        assert "Booking_Number" not in doc


def test_stage_packet_rejects_oversized_id_image(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.setattr("dashboard.routers.agent_packet.MAX_ID_IMAGE_B64_CHARS", 16)
    client = _make_client()
    cols = {
        "arrests": FakeCollection("arrests"),
        "intake_queue": FakeCollection("intake_queue"),
        "paperwork_packets": FakeCollection("paperwork_packets"),
    }
    scan = AsyncMock(return_value={"success": True, "data": {}})
    with patch("dashboard.routers.agent_packet.get_collection", side_effect=lambda name: cols[name]), \
         patch("dashboard.routers.agent_packet.IDScannerService.scan_id_image", scan):
        resp = client.post(
            "/api/agent/stage-packet",
            headers={"X-API-Key": "secret-gas-key"},
            json={
                "defendant": {"full_name": "Test Person", "booking_number": "TEST-0002"},
                "id_image_base64": "A" * 64,
            },
        )
    assert resp.status_code == 200
    scan.assert_not_awaited()
    assert any("too large" in w for w in resp.json()["warnings"])


# ── Bearer at the PIN middleware (agent-packet routes only) ─────────────────


def _middleware_client(monkeypatch, *, gas="secret-gas-key", internal=None):
    from fastapi import FastAPI

    from dashboard.auth.pin_middleware import PinAuthMiddleware

    monkeypatch.setenv("DASHBOARD_PIN", "test-pin-not-real")
    monkeypatch.setattr("dashboard.auth.pin_middleware.DASHBOARD_PIN", "test-pin-not-real")
    monkeypatch.setenv("SECRET_KEY", "agent-packet-test-secret")
    for name in ("GAS_API_KEY", "LEADS_INTERNAL_TOKEN", "INTERNAL_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    if gas is not None:
        monkeypatch.setenv("GAS_API_KEY", gas)
    if internal is not None:
        monkeypatch.setenv("INTERNAL_API_TOKEN", internal)

    app = FastAPI()
    app.include_router(agent_packet_router)

    @app.post("/api/other/thing")
    async def other():
        return {"ok": True}

    app.add_middleware(PinAuthMiddleware)
    return TestClient(app)


_PARSE_OK = {"success": True, "data": {"full_name": "TEST PERSON"}, "parse_method": "stub"}


def test_bearer_machine_key_passes_middleware_and_router(monkeypatch):
    client = _middleware_client(monkeypatch)
    with patch("dashboard.routers.agent_packet.ingest_url", AsyncMock(return_value=_PARSE_OK)):
        for prefix in ("Bearer", "bearer"):
            resp = client.post(
                "/api/agent/parse-booking-url",
                headers={"Authorization": f"{prefix} secret-gas-key"},
                json={"booking_url": "https://jail.example.com/b/1"},
            )
            assert resp.status_code == 200, resp.text
    # stage-packet and issue-packet are let through too (router answers, not middleware)
    for path in ("/api/agent/stage-packet", "/api/agent/issue-packet"):
        resp = client.post(path, headers={"Authorization": "Bearer secret-gas-key"}, json={"surety": "nope"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "surety_unsupported"


def test_bearer_other_configured_machine_key_is_accepted(monkeypatch):
    client = _middleware_client(monkeypatch, gas=None, internal="secret-internal")
    with patch("dashboard.routers.agent_packet.ingest_url", AsyncMock(return_value=_PARSE_OK)):
        resp = client.post(
            "/api/agent/parse-booking-url",
            headers={"Authorization": "Bearer secret-internal"},
            json={"booking_url": "https://jail.example.com/b/1"},
        )
    assert resp.status_code == 200


def test_bearer_wrong_missing_or_empty_is_401(monkeypatch):
    client = _middleware_client(monkeypatch)
    ingest = AsyncMock(return_value=_PARSE_OK)
    with patch("dashboard.routers.agent_packet.ingest_url", ingest):
        for headers in (
            {},
            {"Authorization": "Bearer wrong"},
            {"Authorization": "Bearer "},
            {"Authorization": "Bearer"},
            {"Authorization": "secret-gas-key"},
            {"Authorization": "Basic secret-gas-key"},
            {"Authorization": "Bearer secret-gas-key-extra"},
            {"Authorization": "Bearer s\u00e9cret".encode("latin-1")},
        ):
            resp = client.post(
                "/api/agent/parse-booking-url",
                headers=headers,
                json={"booking_url": "https://jail.example.com/b/1"},
            )
            assert resp.status_code == 401, headers
    ingest.assert_not_awaited()


def test_bearer_fails_closed_when_no_machine_key_configured(monkeypatch):
    client = _middleware_client(monkeypatch, gas=None)
    for token in ("", "secret-gas-key", "None"):
        resp = client.post(
            "/api/agent/stage-packet",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        assert resp.status_code == 401


def test_bearer_is_not_accepted_on_other_routes(monkeypatch):
    client = _middleware_client(monkeypatch)
    for path in ("/api/other/thing", "/api/agent/stage-packet/", "/api/agent/other"):
        resp = client.post(path, headers={"Authorization": "Bearer secret-gas-key"}, json={})
        assert resp.status_code == 401, path


def test_x_api_key_behavior_unchanged(monkeypatch):
    client = _middleware_client(monkeypatch)
    assert client.post("/api/other/thing", headers={"X-API-Key": "secret-gas-key"}).status_code == 200
    assert client.post("/api/other/thing", headers={"X-API-Key": "wrong"}).status_code == 401
    with patch("dashboard.routers.agent_packet.ingest_url", AsyncMock(return_value=_PARSE_OK)):
        ok = client.post(
            "/api/agent/parse-booking-url",
            headers={"X-API-Key": "secret-gas-key"},
            json={"booking_url": "https://jail.example.com/b/1"},
        )
        bad = client.post(
            "/api/agent/parse-booking-url",
            headers={"X-API-Key": "wrong"},
            json={"booking_url": "https://jail.example.com/b/1"},
        )
    assert ok.status_code == 200
    assert bad.status_code == 401


# ── Surety ────────────────────────────────────────────────────────────────────


def _stage(client, cols, json_body, headers=None):
    hdrs = {"X-API-Key": "secret-gas-key"}
    hdrs.update(headers or {})
    with patch("dashboard.routers.agent_packet.get_collection", side_effect=lambda name: cols[name]):
        return client.post("/api/agent/stage-packet", headers=hdrs, json=json_body)


def _cols(intake_cls=None):
    return {
        "arrests": FakeCollection("arrests"),
        "intake_queue": (intake_cls or FakeCollection)("intake_queue"),
        "paperwork_packets": FakeCollection("paperwork_packets"),
    }


_DEF = {"defendant": {"full_name": "Test Person", "booking_number": "TEST-0100", "county": "Lee"}}


def test_surety_is_normalized_to_canonical_id(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    for raw, expected in (("PALMETTO", "palmetto"), (" Palmetto ", "palmetto"), ("OSI", "osi"), ("osi", "osi")):
        cols = _cols()
        resp = _stage(client, cols, {**_DEF, "surety": raw})
        assert resp.status_code == 200, raw
        assert resp.json()["surety"] == expected
        pkt = cols["paperwork_packets"].docs[0]
        assert pkt["surety_id"] == expected and pkt["template"] == expected


def test_missing_surety_is_staged_unconfirmed_not_osi(monkeypatch):
    import asyncio

    import pytest as _pytest

    from dashboard.services.surety_registry import UnsupportedSuretyError, require_surety
    from tests.bb_fake_mongo import FakeDB

    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    for body in (dict(_DEF), {**_DEF, "surety": None}, {**_DEF, "surety": ""}, {**_DEF, "surety": "  "}):
        cols = _cols()
        resp = _stage(client, cols, body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["surety"] is None and data["surety_confirmed"] is False
        assert any(w.startswith("surety_unconfirmed") for w in data["warnings"])
        pkt = cols["paperwork_packets"].docs[0]
        assert pkt["surety_id"] is None and pkt["template"] is None
        assert "osi" not in {str(pkt.get("surety_id")), str(pkt.get("template"))}

    # Readiness: packet context resolves no surety, so "Surety Confirmed" stays red
    # (sl-paperwork.js marks it ready only for osi/palmetto) and DocuSeal paths that
    # call require_surety fail closed.
    db = FakeDB()
    db.collections["paperwork_packets"] = cols["paperwork_packets"]
    db.collections["intake_queue"] = cols["intake_queue"]
    monkeypatch.setattr("dashboard.extensions.get_collection", db.get_collection)
    from dashboard.services.packet_builder_service import resolve_case_context

    ctx = asyncio.run(resolve_case_context(packet_id=pkt["packet_id"]))
    assert not ctx.get("surety_id")
    with _pytest.raises(UnsupportedSuretyError):
        require_surety(ctx.get("surety_id"))
    js = (ROOT / "dashboard" / "sl-paperwork.js").read_text(encoding="utf-8")
    assert "ready: surety === 'osi' || surety === 'palmetto'" in js


def test_explicit_osi_and_palmetto_still_confirmed(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    for raw in ("osi", "palmetto"):
        cols = _cols()
        resp = _stage(client, cols, {**_DEF, "surety": raw})
        data = resp.json()
        assert resp.status_code == 200 and data["surety"] == raw and data["surety_confirmed"] is True
        assert not any(w.startswith("surety_unconfirmed") for w in data["warnings"])
        assert cols["paperwork_packets"].docs[0]["surety_id"] == raw


def test_docuseal_push_refuses_agent_packet_without_surety(monkeypatch):
    from fastapi import FastAPI

    from dashboard.routers import paperwork as paperwork_mod

    app = FastAPI()
    app.include_router(paperwork_mod.paperwork_bp)
    client = TestClient(app)
    denial = AsyncMock(return_value=None)
    monkeypatch.setattr(
        paperwork_mod, "_load_packet",
        AsyncMock(return_value={"packet_id": "PKT-T1", "staged_by_agent": True, "surety_id": None, "template": None}),
    )
    with patch("dashboard.services.surety_entitlements.entitlement_denial", denial):
        resp = client.post("/api/paperwork/PKT-T1/docuseal", json={})
    assert resp.status_code == 422
    assert resp.json()["code"] == "surety_required"
    denial.assert_not_awaited()


def test_unknown_or_inactive_surety_is_422_before_any_write(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.setattr("dashboard.services.surety_registry._published_doc", lambda sid: None)
    client = _make_client()
    ingest = AsyncMock(return_value=_PARSE_OK)
    for raw in ("acme", "lexington", "osi; drop", "{'$ne': 1}"):
        cols = _cols()
        with patch("dashboard.routers.agent_packet.ingest_url", ingest):
            resp = _stage(client, cols, {**_DEF, "booking_url": "https://jail.example.com/b/1", "surety": raw})
        assert resp.status_code == 422, raw
        assert resp.json()["code"] == "surety_unsupported"
        assert all(not c.docs for c in cols.values())
    ingest.assert_not_awaited()


# ── Idempotency ───────────────────────────────────────────────────────────────


class _AtomicFakeCollection(FakeCollection):
    """FakeCollection + find_one_and_update($setOnInsert, upsert, ReturnDocument.BEFORE)."""

    calls = 0

    async def find_one_and_update(self, flt, update, upsert=False, return_document=None, projection=None):
        type(self).calls += 1
        import copy

        for d in self.docs:
            from tests.bb_fake_mongo import matches

            if matches(d, flt):
                before = self._project(d, projection)
                self._apply(d, update)
                return before
        if upsert:
            new = {k: v for k, v in flt.items() if not k.startswith("$")}
            new.update(copy.deepcopy(update.get("$setOnInsert") or {}))
            self._apply(new, update)
            self.docs.append(new)
        return None


def test_idempotency_repeat_returns_original_ids_and_creates_nothing(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _cols(_AtomicFakeCollection)
    body = {**_DEF, "surety": "palmetto"}
    first = _stage(client, cols, body, {"Idempotency-Key": "chat-42"})
    assert first.status_code == 200
    ingest = AsyncMock(return_value=_PARSE_OK)
    with patch("dashboard.routers.agent_packet.ingest_url", ingest):
        again = _stage(client, cols, body, {"Idempotency-Key": "chat-42"})
        via_body = _stage(client, cols, {**body, "idempotency_key": "chat-42"})
    for resp in (again, via_body):
        assert resp.status_code == 200
        assert resp.json()["idempotent_replay"] is True
        assert resp.json()["packet_id"] == first.json()["packet_id"]
        assert resp.json()["intake_id"] == first.json()["intake_id"]
        assert resp.json()["surety"] == "palmetto"
    ingest.assert_not_awaited()
    assert len(cols["intake_queue"].docs) == 1
    assert len(cols["paperwork_packets"].docs) == 1
    intake = cols["intake_queue"].docs[0]
    pkt = cols["paperwork_packets"].docs[0]
    assert intake["agent_idempotency_key"] == pkt["agent_idempotency_key"]
    assert "chat-42" not in intake["agent_idempotency_key"]  # stored hashed + caller-scoped


def test_idempotency_same_key_different_payload_is_409(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _cols(_AtomicFakeCollection)
    assert _stage(client, cols, _DEF, {"Idempotency-Key": "k1"}).status_code == 200
    changed = {"defendant": {**_DEF["defendant"], "booking_number": "TEST-0101"}}
    resp = _stage(client, cols, changed, {"Idempotency-Key": "k1"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "idempotency_key_conflict"
    assert len(cols["intake_queue"].docs) == 1 and len(cols["paperwork_packets"].docs) == 1


def test_idempotency_claim_is_atomic_when_precheck_races(monkeypatch):
    """Both requests miss the read-only pre-check; only the upsert winner writes a packet."""
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _cols(_AtomicFakeCollection)

    async def miss(*a, **k):
        return None

    cols["intake_queue"].find_one = miss
    first = _stage(client, cols, _DEF, {"Idempotency-Key": "race"})
    second = _stage(client, cols, _DEF, {"Idempotency-Key": "race"})
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["idempotent_replay"] is True
    assert second.json()["packet_id"] == first.json()["packet_id"]
    assert len(cols["intake_queue"].docs) == 1
    assert len(cols["paperwork_packets"].docs) == 1


def test_idempotency_keys_are_scoped_by_caller(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    monkeypatch.setenv("LEADS_INTERNAL_TOKEN", "secret-internal")
    client = _make_client()
    cols = _cols(_AtomicFakeCollection)
    a = _stage(client, cols, _DEF, {"Idempotency-Key": "shared"})
    b = _stage(client, cols, _DEF, {"Idempotency-Key": "shared", "X-API-Key": "secret-internal"})
    assert a.status_code == 200 and b.status_code == 200
    assert a.json()["packet_id"] != b.json()["packet_id"]
    assert len(cols["paperwork_packets"].docs) == 2


def test_idempotency_key_validation(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _cols(_AtomicFakeCollection)
    mismatch = _stage(client, cols, {**_DEF, "idempotency_key": "a"}, {"Idempotency-Key": "b"})
    too_long = _stage(client, cols, {**_DEF, "idempotency_key": "x" * 201})
    assert mismatch.status_code == 400 and too_long.status_code == 400
    assert all(not c.docs for c in cols.values())
    # No key: unchanged behavior, every call stages a new packet
    _stage(client, cols, _DEF)
    _stage(client, cols, _DEF)
    assert len(cols["paperwork_packets"].docs) == 2
    assert all("agent_idempotency_key" not in d for d in cols["intake_queue"].docs)


# ── Idempotency claim lifecycle (pending -> complete | failed) ───────────────


class _FlakyPackets(FakeCollection):
    """paperwork_packets whose upsert raises ``fail_times`` times.

    ``land_before_raise`` simulates a write that reached Mongo before the error.
    """

    def __init__(self, name, fail_times=1, land_before_raise=False):
        super().__init__(name)
        self.fail_times = fail_times
        self.land_before_raise = land_before_raise

    async def update_one(self, flt, update, upsert=False):
        if self.fail_times > 0:
            self.fail_times -= 1
            if self.land_before_raise:
                await super().update_one(flt, update, upsert=upsert)
            raise RuntimeError("mongo write timeout")
        return await super().update_one(flt, update, upsert=upsert)


def _flaky_cols(**kw):
    cols = _cols(_AtomicFakeCollection)
    cols["paperwork_packets"] = _FlakyPackets("paperwork_packets", **kw)
    return cols


def test_packet_write_failure_marks_claim_failed_and_retry_recovers(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _flaky_cols(fail_times=1)
    hdr = {"Idempotency-Key": "flaky-1"}

    first = _stage(client, cols, _DEF, hdr)
    assert first.status_code == 503
    assert first.json()["code"] == "packet_write_failed"
    assert "packet_id" not in first.json()
    assert "mongo write timeout" not in first.text
    (intake,) = cols["intake_queue"].docs
    assert intake["idempotency_state"] == "failed"
    assert intake["idempotency_error"] == "packet_write_failed"
    assert intake["idempotency_failed_at"]
    assert cols["paperwork_packets"].docs == []

    retry = _stage(client, cols, _DEF, hdr)
    assert retry.status_code == 200
    data = retry.json()
    assert data["idempotent_replay"] is True and data["idempotency_recovered"] is True
    assert data["intake_id"] == intake["intake_id"]
    assert data["packet_id"] == intake["paperwork_packet_id"]
    assert [p["packet_id"] for p in cols["paperwork_packets"].docs] == [data["packet_id"]]
    assert len(cols["intake_queue"].docs) == 1  # same intake reused, nothing deleted
    assert cols["intake_queue"].docs[0]["idempotency_state"] == "complete"

    again = _stage(client, cols, _DEF, hdr)
    assert again.status_code == 200 and again.json()["packet_id"] == data["packet_id"]
    assert "idempotency_recovered" not in again.json()
    assert len(cols["paperwork_packets"].docs) == 1


def test_retry_that_fails_again_errors_cleanly_without_phantom_id(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _flaky_cols(fail_times=2)
    hdr = {"Idempotency-Key": "flaky-2"}
    assert _stage(client, cols, _DEF, hdr).status_code == 503
    second = _stage(client, cols, _DEF, hdr)
    assert second.status_code == 503
    assert "packet_id" not in second.json()
    assert cols["paperwork_packets"].docs == []
    assert cols["intake_queue"].docs[0]["idempotency_state"] == "failed"
    third = _stage(client, cols, _DEF, hdr)
    assert third.status_code == 200
    assert len(cols["paperwork_packets"].docs) == 1


def test_write_that_landed_before_error_is_not_duplicated(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _flaky_cols(fail_times=1, land_before_raise=True)
    hdr = {"Idempotency-Key": "landed"}
    assert _stage(client, cols, _DEF, hdr).status_code == 503
    retry = _stage(client, cols, _DEF, hdr)
    assert retry.status_code == 200
    assert len(cols["paperwork_packets"].docs) == 1
    assert retry.json()["packet_id"] == cols["paperwork_packets"].docs[0]["packet_id"]


def _seed_claim(cols, state, claimed_at, packet=False):
    """Stage once to get a real claim, then rewrite its state/timestamps."""
    import datetime as _dt

    intake = cols["intake_queue"].docs[0]
    intake["idempotency_state"] = state
    intake["idempotency_claimed_at"] = claimed_at
    if not packet:
        cols["paperwork_packets"].docs.clear()
    return intake, _dt


def test_fresh_pending_claim_answers_in_progress(monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _cols(_AtomicFakeCollection)
    hdr = {"Idempotency-Key": "pending-fresh"}
    assert _stage(client, cols, _DEF, hdr).status_code == 200
    _seed_claim(cols, "pending", datetime.now(timezone.utc) - timedelta(seconds=10))
    resp = _stage(client, cols, _DEF, hdr)
    assert resp.status_code == 409
    assert resp.json()["code"] == "idempotency_in_progress"
    assert resp.headers["Retry-After"]
    assert "packet_id" not in resp.json()
    assert cols["paperwork_packets"].docs == []


def test_stale_pending_claim_is_retaken(monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _cols(_AtomicFakeCollection)
    hdr = {"Idempotency-Key": "pending-stale"}
    first = _stage(client, cols, _DEF, hdr)
    # naive datetime, as pymongo returns by default
    _seed_claim(cols, "pending", datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5))
    resp = _stage(client, cols, _DEF, hdr)
    assert resp.status_code == 200
    assert resp.json()["idempotency_recovered"] is True
    assert resp.json()["packet_id"] == first.json()["packet_id"]
    assert len(cols["paperwork_packets"].docs) == 1
    assert cols["intake_queue"].docs[0]["idempotency_state"] == "complete"
    assert cols["intake_queue"].docs[0]["idempotency_retaken_from"] == "pending"


def test_complete_claim_without_packet_is_not_replayed_as_phantom(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _cols(_AtomicFakeCollection)
    hdr = {"Idempotency-Key": "complete-missing"}
    first = _stage(client, cols, _DEF, hdr)
    _seed_claim(cols, "complete", datetime.now(timezone.utc))
    resp = _stage(client, cols, _DEF, hdr)
    assert resp.status_code == 200
    assert resp.json()["idempotency_recovered"] is True
    assert [p["packet_id"] for p in cols["paperwork_packets"].docs] == [first.json()["packet_id"]]


def test_retake_loses_race_answers_in_progress(monkeypatch):
    """If another request changed the claim between read and retake, no write happens."""
    from datetime import datetime, timezone

    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _cols(_AtomicFakeCollection)
    hdr = {"Idempotency-Key": "retake-race"}
    _stage(client, cols, _DEF, hdr)
    intake, _ = _seed_claim(cols, "failed", datetime.now(timezone.utc))
    real_find_one = cols["intake_queue"].find_one

    async def stale_read(*a, **k):
        doc = await real_find_one(*a, **k)
        intake["idempotency_attempt"] = "someone-else"  # concurrent retake won
        return doc

    cols["intake_queue"].find_one = stale_read
    resp = _stage(client, cols, _DEF, hdr)
    assert resp.status_code == 409 and resp.json()["code"] == "idempotency_in_progress"
    assert cols["paperwork_packets"].docs == []


def test_retake_requires_matching_fingerprint(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "secret-gas-key")
    client = _make_client()
    cols = _flaky_cols(fail_times=1)
    hdr = {"Idempotency-Key": "fp"}
    assert _stage(client, cols, _DEF, hdr).status_code == 503
    changed = {"defendant": {**_DEF["defendant"], "booking_number": "TEST-0199"}}
    resp = _stage(client, cols, changed, hdr)
    assert resp.status_code == 409 and resp.json()["code"] == "idempotency_key_conflict"
    assert cols["paperwork_packets"].docs == []
    assert cols["intake_queue"].docs[0]["idempotency_state"] == "failed"
