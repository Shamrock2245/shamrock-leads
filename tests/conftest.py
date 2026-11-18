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

Collection imports application modules before any fixture runs.
``config/settings.py`` calls ``load_dotenv`` and copies Slack and Twilio
values onto ``Settings`` at class-body time. ``writers/rearrest_checker.py``
then posts with ``urllib.request.urlopen``, and ``dashboard/routers/poa.py``
posts with ``aiohttp``. Clearing ``os.environ`` only inside a fixture is too
late for those snapshots, and the httpx/requests patches do not see those
clients.

Before test modules are imported, this file occupies every real-send variable
with an empty string (including matching keys named in the repo ``.env``,
without reading the values into the suite) and wraps ``load_dotenv`` so a
later call cannot put the host secrets back. ``Settings`` and the other
import-time copies therefore freeze empty. The autouse fixture still
``monkeypatch.delenv(..., raising=False)`` so a test body sees them unset,
then blanks those class and module attributes again.

``block_live_sends`` runs before the test body and before per-test
``monkeypatch.setenv`` calls, and it:

1. Unsets every name in ``_REAL_SEND_ENV`` plus any ``SLACK_WEBHOOK_*`` /
   ``BLUEBUBBLES_*`` key present in the process environment (dynamic
   BlueBubbles suffixes such as ``BLUEBUBBLES_URL_0178``). A test that then
   ``monkeypatch.setenv`` sees its own value.
2. Refuses real outbound HTTP from ``httpx.Client.send``,
   ``httpx.AsyncClient.send``, ``requests.Session.send``,
   ``urllib.request.urlopen``, and ``aiohttp.ClientSession._request``.
   Non-localhost hosts on a real transport raise ``OutboundHTTPBlocked``.
   In-process transports stay open: Starlette ``TestClient``,
   ``httpx.ASGITransport`` / ``WSGITransport`` / ``MockTransport``, a non-TCP
   aiohttp connector, and a respx or monkeypatch replacement of the transport
   ``handle_*`` method. Local hosts (``localhost``, ``127.*``, ``::1``,
   ``testserver``) and ``file:`` / ``data:`` URLs are allowed.

The names below were collected from ``os.getenv`` / ``os.environ`` reads in
application code (tests excluded). Template ids, channel names, and
non-credential SwipeSimple paths are not in this list.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
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

# Pay-link aliases have an in-code default when the variable is unset
# (dashboard/config.py). Occupying them with "" before import would hide that
# default. The fixture still deletes a host value, and the Config snapshot is
# put back to this default when it captured something else.
_PAYMENT_LINK_ENV = frozenset({
    "SWIPESIMPLE_LINK_DEFAULT",
    "SWIPESIMPLE_PAYMENT_LINK",
    "SWIPESIMPLE_BOND_PAYMENT_LINK",
    "SWIPESIMPLE_LINK_TELEGRAM",
    "PAYMENT_LINK",
})
_CONFIG_PAYMENT_LINK_DEFAULT = (
    "https://swipesimple.com/links/lnk_b6bf996f4c57bb340a150e297e769abd"
)

_ENV_PREFIXES = ("SLACK_WEBHOOK_", "BLUEBUBBLES_")

_SETTINGS_SNAPSHOT_ATTRS = (
    "SLACK_WEBHOOK_ARRESTS",
    "SLACK_WEBHOOK_LEADS",
    "SLACK_WEBHOOK_ERRORS",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_GMAIL_REFRESH_TOKEN",
    "GOOGLE_DRIVE_REFRESH_TOKEN",
    "BLUEBUBBLES_URL_0178",
    "BLUEBUBBLES_URL_0314",
)
_CONFIG_SNAPSHOT_ATTRS = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "TWILIO_MESSAGING_SERVICE_SID",
)
_MODULE_SNAPSHOTS = (
    ("dashboard.routers.rearrest_detector", "SLACK_REARREST_WEBHOOK"),
    ("dashboard.routers.bb_health_monitor", "_SLACK_WEBHOOK"),
    ("dashboard.routers.data_retention", "_SLACK_WEBHOOK"),
)

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


def _dotenv_send_keys(path: Path) -> list[str]:
    """Key names in a dotenv file that can send. Values are never kept."""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    found = []
    explicit = set(_REAL_SEND_ENV)
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key.startswith(_ENV_PREFIXES) or key in explicit:
            found.append(key)
    return found


def _pin_real_send_env() -> None:
    """Occupy send keys so ``load_dotenv`` cannot copy the host ``.env`` in.

    python-dotenv does not replace a variable that is already set. Deleting
    the keys here would let ``config/settings.py`` refill them during
    collection and store the live webhook on ``Settings``.
    """
    names = set(_REAL_SEND_ENV) - _PAYMENT_LINK_ENV
    names.update(key for key in os.environ if key.startswith(_ENV_PREFIXES))
    root = Path(__file__).resolve().parents[1]
    for key in _dotenv_send_keys(root / ".env"):
        if key not in _PAYMENT_LINK_ENV:
            names.add(key)
    for name in names:
        os.environ[name] = ""


def _install_dotenv_guard() -> None:
    """Drop real-send keys that a ``load_dotenv`` call just introduced."""
    try:
        import dotenv
        import dotenv.main
    except ImportError:
        return
    current = dotenv.load_dotenv
    if getattr(current, "_shamrock_test_guard", False):
        return

    def guarded(*args, **kwargs):
        watched = _env_names_to_clear()
        before = {name: os.environ.get(name) for name in watched}
        try:
            return current(*args, **kwargs)
        finally:
            for name, prev in before.items():
                if prev is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = prev

    guarded._shamrock_test_guard = True  # type: ignore[attr-defined]
    dotenv.load_dotenv = guarded
    dotenv.main.load_dotenv = guarded
    for mod_name in ("config.settings", "dashboard.main", "social.main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and getattr(mod, "load_dotenv", None) is current:
            mod.load_dotenv = guarded


def _blank_imported_send_snapshots() -> None:
    """Empty credentials copied onto classes at import, if those modules exist."""
    settings_mod = sys.modules.get("config.settings")
    settings_cls = getattr(settings_mod, "Settings", None) if settings_mod else None
    if settings_cls is not None:
        for attr in _SETTINGS_SNAPSHOT_ATTRS:
            if hasattr(settings_cls, attr):
                setattr(settings_cls, attr, "")
    config_mod = sys.modules.get("dashboard.config")
    config_cls = getattr(config_mod, "Config", None) if config_mod else None
    if config_cls is not None:
        for attr in _CONFIG_SNAPSHOT_ATTRS:
            if hasattr(config_cls, attr):
                setattr(config_cls, attr, "")
        current = getattr(config_cls, "SWIPESIMPLE_PAYMENT_LINK", None)
        if current not in (None, "", _CONFIG_PAYMENT_LINK_DEFAULT):
            config_cls.SWIPESIMPLE_PAYMENT_LINK = _CONFIG_PAYMENT_LINK_DEFAULT
    for mod_name, attr in _MODULE_SNAPSHOTS:
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, attr):
            setattr(mod, attr, "")


# Run before pytest imports test modules. A fixture is too late: collection
# already executes ``load_dotenv`` and the ``Settings`` class body.
_pin_real_send_env()
_install_dotenv_guard()
_blank_imported_send_snapshots()


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


def _network_host(url):
    """Host of an http(s) URL, or None when the URL is not a network call."""
    if hasattr(url, "full_url"):
        url = url.full_url
    text = str(url or "")
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme in {"", "file", "data"}:
        return None
    return _hostname(text)


def _refuse_unless_local(url) -> None:
    host = _network_host(url)
    if host is None or _is_local_host(host):
        return
    _refuse(host)


def _aiohttp_uses_real_connector(session) -> bool:
    connector = getattr(session, "connector", None)
    if connector is None:
        return True
    return (
        type(connector).__name__ == "TCPConnector"
        and (type(connector).__module__ or "").startswith("aiohttp")
    )


def _install_http_guard(monkeypatch) -> None:
    import httpx
    import requests
    import urllib.request

    orig_sync = httpx.Client.send
    orig_async = httpx.AsyncClient.send
    orig_requests = requests.Session.send
    orig_urlopen = urllib.request.urlopen

    def guarded_sync(self, request, *args, **kwargs):
        _guard_httpx(self, request)
        return orig_sync(self, request, *args, **kwargs)

    async def guarded_async(self, request, *args, **kwargs):
        _guard_httpx(self, request)
        return await orig_async(self, request, *args, **kwargs)

    def guarded_requests(self, request, **kwargs):
        _guard_requests(self, request)
        return orig_requests(self, request, **kwargs)

    def guarded_urlopen(url, *args, **kwargs):
        _refuse_unless_local(url)
        return orig_urlopen(url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", guarded_sync)
    monkeypatch.setattr(httpx.AsyncClient, "send", guarded_async)
    monkeypatch.setattr(requests.Session, "send", guarded_requests)
    monkeypatch.setattr(urllib.request, "urlopen", guarded_urlopen)

    try:
        import aiohttp
    except ImportError:
        return
    orig_aiohttp = aiohttp.ClientSession._request

    async def guarded_aiohttp(self, method, url, **kwargs):
        if _aiohttp_uses_real_connector(self):
            _refuse_unless_local(url)
        return await orig_aiohttp(self, method, url, **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "_request", guarded_aiohttp)


@pytest.fixture(autouse=True)
def block_live_sends(monkeypatch):
    """Unset real-send env vars and refuse non-localhost HTTP for this test.

    Per-test ``monkeypatch.setenv`` and per-test replacements of
    ``httpx.Client.send`` / ``httpx.AsyncClient.send`` /
    ``requests.Session.send`` / ``urllib.request.urlopen`` /
    ``aiohttp.ClientSession._request`` run after this fixture and override
    it for that test only.
    """
    for name in _env_names_to_clear():
        monkeypatch.delenv(name, raising=False)
    _blank_imported_send_snapshots()
    _blocked_outbound_hosts.clear()
    _install_http_guard(monkeypatch)
    yield


@pytest.fixture
def blocked_outbound_hosts():
    """Hosts this test's outbound guard refused. Paths are not recorded."""
    return _blocked_outbound_hosts
