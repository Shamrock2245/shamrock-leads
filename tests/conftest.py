"""Shared test setup.

Production must provide SECRET_KEY (session signing fails closed without it,
see dashboard/auth/dev_mode.py). Mirror that here with a synthetic value so
suites exercising signed sessions behave like a correctly configured deploy.
Tests that cover the missing-key path delete it explicitly via monkeypatch.

Live-send guard
---------------
On 2026-10-29 and 2026-11-16, pytest on a host with a real ``.env`` posted
fixture text to the real Slack ``#leads`` channel (``SLACK_WEBHOOK_LEADS``).
``config/settings.py`` calls ``load_dotenv`` at import, so those variables are
in ``os.environ`` before any test body runs.

``block_live_sends`` is an autouse fixture. It runs before the test body and
before per-test ``monkeypatch.setenv`` calls, and it:

1. ``monkeypatch.delenv(..., raising=False)`` on every real-send variable
   listed in ``_REAL_SEND_ENV`` plus any ``SLACK_WEBHOOK_*`` / ``BLUEBUBBLES_*``
   key present in the process environment (dynamic BlueBubbles suffixes such
   as ``BLUEBUBBLES_URL_0178``). A test that then ``monkeypatch.setenv`` sees
   its own value. This fixture does not assign empty strings; it unsets.
2. Refuses real outbound HTTP. ``httpx.Client.send``, ``httpx.AsyncClient.send``,
   and ``requests.Session.send`` raise ``OutboundHTTPBlocked`` for a
   non-localhost host on a real transport. In-process transports stay open:
   Starlette ``TestClient``, ``httpx.ASGITransport`` / ``WSGITransport`` /
   ``MockTransport``, and any other non-network transport (including a respx
   or monkeypatch replacement of the transport ``handle_*`` method). Local
   hosts (``localhost``, ``127.*``, ``::1``, ``testserver``) on a real
   transport are allowed.

The names below were collected from ``os.getenv`` / ``os.environ`` reads in
application code (tests excluded). Template ids, channel names, and
non-credential SwipeSimple paths are not in this list.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest

os.environ.setdefault("SECRET_KEY", "synthetic-test-session-key-not-a-secret")

# Hosts the outbound guard refused during the current test. Cleared at the
# start of each test. The webhook path is never stored (it can be a secret).
_blocked_outbound_hosts: list[str] = []

# Explicit real-send variables. Keep this list aligned with production reads.
#
# Slack webhooks — writers/slack_notifier.py, config/settings.py,
# dashboard/services/{automation_digest,docuseal_completion,error_tracker,
# intake_fanout,docket_monitor,paperwork_chase_service,court_email_scheduler}.py,
# dashboard/routers/{rearrest_detector,shannon_id,webhooks,poa,
# imessage_automation,intake,bb_health_monitor,crm,data_retention}.py,
# services/alpr_matcher.py, social/config.py.
# SLACK_BOT_TOKEN is not read today; slack-sdk would pick it up, so it is
# cleared anyway. Any other SLACK_WEBHOOK_* present in the environment is
# cleared even if it is not named here.
_SLACK_ENV = (
    "SLACK_WEBHOOK_LEADS",
    "SLACK_WEBHOOK_URL",
    "SLACK_WEBHOOK_ARRESTS",
    "SLACK_WEBHOOK_ERRORS",
    "SLACK_WEBHOOK_ALERTS",
    "SLACK_WEBHOOK_INTAKE",
    "SLACK_WEBHOOK_REARREST",
    "SLACK_WEBHOOK_ALPR",
    "SLACK_WEBHOOK_SOCIAL",
    "SLACK_BOT_TOKEN",
)

# BlueBubbles — dashboard/extensions.py reads BLUEBUBBLES_URL_{suffix} and
# BLUEBUBBLES_PASSWORD_{suffix}. config/settings.py, config/tailscale.py,
# dashboard/services/{bb_client,bb_diagnostic_service,tailscale_health}.py,
# dashboard/routers/crm.py, scripts/cf_audit.py (BB_PASSWORD alias).
# Any other BLUEBUBBLES_* key present in the environment is cleared too.
_BLUEBUBBLES_ENV = (
    "BLUEBUBBLES_URL",
    "BLUEBUBBLES_URL_0178",
    "BLUEBUBBLES_URL_0314",
    "BLUEBUBBLES_FRP_URL",
    "BLUEBUBBLES_PASSWORD",
    "BLUEBUBBLES_PASSWORD_0178",
    "BLUEBUBBLES_PASSWORD_0314",
    "BB_PASSWORD",
)

# SwipeSimple live switches and credentials — dashboard/services/
# swipesimple_invoice_service.py (SWIPESIMPLE_LIVE, SWIPESIMPLE_DISPATCH_LIVE,
# SWIPESIMPLE_AUTO_SEND_AFTER_SIGNING, SWIPESIMPLE_SESSION, COOKIE_JAR,
# CSRF_TOKEN, MERCHANT_ID), swipesimple_playwright_bootstrap.py (USERNAME,
# PASSWORD), swipesimple_session_keepalive.py (SESSION_KEEPALIVE,
# SESSION_ALERT_TO), dashboard/routers/webhooks.py (SWIPESIMPLE_WEBHOOK_SECRET),
# dashboard/services/payment_links.py and dashboard/config.py (pay-link aliases).
# BASE_URL, NEW_INVOICE_PATH, SHARE_INVOICE_ON_PROMOTE, TEST_CASE_INVOICES, and
# catalog item ids are not credentials and are left alone.
_SWIPESIMPLE_ENV = (
    "SWIPESIMPLE_SESSION",
    "SWIPESIMPLE_LIVE",
    "SWIPESIMPLE_DISPATCH_LIVE",
    "SWIPESIMPLE_AUTO_SEND_AFTER_SIGNING",
    "SWIPESIMPLE_COOKIE_JAR",
    "SWIPESIMPLE_CSRF_TOKEN",
    "SWIPESIMPLE_USERNAME",
    "SWIPESIMPLE_PASSWORD",
    "SWIPESIMPLE_WEBHOOK_SECRET",
    "SWIPESIMPLE_MERCHANT_ID",
    "SWIPESIMPLE_SESSION_KEEPALIVE",
    "SWIPESIMPLE_SESSION_ALERT_TO",
    "SWIPESIMPLE_LINK_DEFAULT",
    "SWIPESIMPLE_PAYMENT_LINK",
    "SWIPESIMPLE_BOND_PAYMENT_LINK",
    "SWIPESIMPLE_LINK_TELEGRAM",
    "PAYMENT_LINK",
)

# DocuSeal API key and URL — dashboard/services/docuseal_service.py,
# scripts/docuseal_template_drift_check.py, scripts/apply_palmetto_docuseal_fields.py
# (DOCUSEAL_SERVER). Template ids are not credentials.
_DOCUSEAL_ENV = (
    "DOCUSEAL_API_KEY",
    "DOCUSEAL_URL",
    "DOCUSEAL_INTERNAL_URL",
    "DOCUSEAL_SERVER",
)

# Gmail send / mailbox-mutation credentials — dashboard/services/gmail_reader.py
# (send_email, mark_as_read), google_calendar_service.py, google_drive_service.py
# (drive token falls back to the Gmail refresh token), social/gmail_scanner.py.
_GMAIL_ENV = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_GMAIL_REFRESH_TOKEN",
    "GOOGLE_DRIVE_REFRESH_TOKEN",
)

# Twilio send credentials — dashboard/services/twilio_service.py,
# dashboard/services/ar_service.py, dashboard/config.py, config/settings.py.
# TWILIO_WEBHOOK_PUBLIC_URL is the inbound signature URL, not a send secret.
_TWILIO_ENV = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "TWILIO_MESSAGING_SERVICE_SID",
    "TWILIO_VOICE_FROM",
)

_REAL_SEND_ENV = (
    _SLACK_ENV
    + _BLUEBUBBLES_ENV
    + _SWIPESIMPLE_ENV
    + _DOCUSEAL_ENV
    + _GMAIL_ENV
    + _TWILIO_ENV
)

_ENV_PREFIXES = ("SLACK_WEBHOOK_", "BLUEBUBBLES_")

_LOCAL_HOSTS = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "testserver",
    "testclient",
})


class OutboundHTTPBlocked(RuntimeError):
    """A test tried to use a real transport against a non-localhost host."""


def _env_names_to_clear() -> list[str]:
    names = set(_REAL_SEND_ENV)
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            names.add(key)
    return sorted(names)


def _hostname(url) -> str:
    if url is None:
        return ""
    host = getattr(url, "host", None)
    if host:
        return str(host).strip().lower().rstrip(".")
    text = str(url or "")
    if "://" not in text and not text.startswith("//"):
        return ""
    return (urlparse(text).hostname or "").strip().lower().rstrip(".")


def _is_local_host(host: str) -> bool:
    if host in _LOCAL_HOSTS or host.endswith(".localhost"):
        return True
    if host.startswith("127."):
        return True
    return False


def _selected_transport(client, url):
    finder = getattr(client, "_transport_for_url", None)
    if callable(finder) and url is not None:
        try:
            return finder(url)
        except Exception:
            pass
    return getattr(client, "_transport", None)


def _transport_method_mocked(transport) -> bool:
    """True when respx or a test replaced the transport's handle method.

    A monkeypatch of ``httpx.Client.send`` itself never reaches this helper:
    the test's replacement runs instead of the guard.
    """
    for attr in ("handle_request", "handle_async_request"):
        meth = getattr(transport, attr, None)
        if meth is None:
            continue
        func = getattr(meth, "__func__", meth)
        module = getattr(func, "__module__", "") or ""
        mock_module = getattr(func.__class__, "__module__", "") or ""
        if (
            module.startswith("unittest.mock")
            or module.startswith("respx")
            or mock_module.startswith("unittest.mock")
        ):
            return True
        if module and not module.startswith("httpx"):
            return True
    return False


def _is_stub_transport(transport) -> bool:
    """In-process transports and already-mocked network transports may proceed."""
    if transport is None:
        return False
    name = type(transport).__name__
    module = type(transport).__module__ or ""
    real_network = name in {"HTTPTransport", "AsyncHTTPTransport"} and module.startswith("httpx")
    if not real_network:
        return True
    return _transport_method_mocked(transport)


def _requests_adapter_is_stub(session, url: str) -> bool:
    try:
        adapter = session.get_adapter(url)
    except Exception:
        return False
    if adapter is None:
        return False
    name = type(adapter).__name__
    module = type(adapter).__module__ or ""
    if not (name == "HTTPAdapter" and module.startswith("requests")):
        return True
    send = getattr(adapter, "send", None)
    func = getattr(send, "__func__", send)
    if func is None:
        return False
    func_module = getattr(func, "__module__", "") or ""
    mock_module = getattr(func.__class__, "__module__", "") or ""
    if (
        func_module.startswith("unittest.mock")
        or func_module.startswith("responses")
        or mock_module.startswith("unittest.mock")
    ):
        return True
    if func_module and not func_module.startswith("requests"):
        return True
    return False


def _refuse(host: str) -> None:
    shown = host or "(empty)"
    _blocked_outbound_hosts.append(shown)
    raise OutboundHTTPBlocked(
        "pytest blocked a real outbound HTTP call to host "
        f"{shown!r}. Mock the client (monkeypatch or respx), or send only "
        "to localhost. TestClient and ASGI/Mock transports are allowed."
    )


def _guard_httpx(client, request) -> None:
    url = getattr(request, "url", None)
    if _is_stub_transport(_selected_transport(client, url)):
        return
    host = _hostname(url)
    if _is_local_host(host):
        return
    _refuse(host)


def _guard_requests(session, request) -> None:
    url = str(getattr(request, "url", "") or "")
    if _requests_adapter_is_stub(session, url):
        return
    host = _hostname(url)
    if _is_local_host(host):
        return
    _refuse(host)


def _install_http_guard(monkeypatch) -> None:
    import httpx
    import requests

    orig_sync = httpx.Client.send
    orig_async = httpx.AsyncClient.send
    orig_requests = requests.Session.send

    def guarded_sync(self, request, *args, **kwargs):
        _guard_httpx(self, request)
        return orig_sync(self, request, *args, **kwargs)

    async def guarded_async(self, request, *args, **kwargs):
        _guard_httpx(self, request)
        return await orig_async(self, request, *args, **kwargs)

    def guarded_requests(self, request, **kwargs):
        _guard_requests(self, request)
        return orig_requests(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", guarded_sync)
    monkeypatch.setattr(httpx.AsyncClient, "send", guarded_async)
    monkeypatch.setattr(requests.Session, "send", guarded_requests)


@pytest.fixture(autouse=True)
def block_live_sends(monkeypatch):
    """Unset real-send env vars and refuse non-localhost HTTP for this test.

    Per-test ``monkeypatch.setenv`` and per-test replacements of
    ``httpx.Client.send`` / ``httpx.AsyncClient.send`` / ``requests.Session.send``
    run after this fixture and override it for that test only.
    """
    for name in _env_names_to_clear():
        monkeypatch.delenv(name, raising=False)
    _blocked_outbound_hosts.clear()
    _install_http_guard(monkeypatch)
    yield


@pytest.fixture
def blocked_outbound_hosts():
    """Hosts this test's outbound guard refused. Paths are not recorded."""
    return _blocked_outbound_hosts
