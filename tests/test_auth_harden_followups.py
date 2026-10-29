"""Auth hardening follow-ups to #176 (CoS-approved, 2026-10-10).

1. dashboard/config.py has no hardcoded DASHBOARD_PIN default.
2. Admin-key / PIN guards (AR require_staff, OSINT, ALPR, booking intake,
   bonds admin-pin-override, webhook secrets, portal debug OTP echo) fail
   closed when unconfigured; only ENV/ENVIRONMENT exactly ``development`` with
   REQUIRE_DASHBOARD_PIN unset tolerates it. Machine keys keep working.
3. No SECRET_KEY outside development: session routes (protected pages, login
   POST, kiosk scan tokens) answer 503; /health and public paths unchanged.
   The session key is never derived from the PIN or a fixed string.

Synthetic values only. No Mongo: collections are mocked.
"""
from __future__ import annotations

import ast
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from dashboard import deps
from dashboard.auth import dev_mode, pin_middleware
from dashboard.main import app

ROOT = pathlib.Path(__file__).resolve().parents[1]
SYN_PIN = "synthetic-test-pin-0000"
SYN_SECRET = "synthetic-test-secret-key-not-real"
SYN_GAS = "synthetic-gas-key-0000"

CLOSED_ENVS = [None, "production", "prod", "staging", "test", "dev", "develop", "developmnet"]
OPEN_ENVS = ["development", "Development ", "DEVELOPMENT"]

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "DASHBOARD_PIN", "ENV", "ENVIRONMENT", "REQUIRE_DASHBOARD_PIN", "REQUIRE_SECRET_KEY",
        "GAS_API_KEY", "LEADS_INTERNAL_TOKEN", "INTERNAL_API_TOKEN", "SECRET_KEY",
        "TWILIO_AUTH_TOKEN", "SWIPESIMPLE_WEBHOOK_SECRET", "TRACCAR_WEBHOOK_SECRET",
        "REQUIRE_TRACCAR_WEBHOOK_SECRET", "DOCUSEAL_WEBHOOK_SECRET",
        "REQUIRE_DOCUSEAL_WEBHOOK_SECRET", "ADOBE_PDF_WEBHOOK_SECRET", "DEBUG",
        "SELF_INDEMNITOR_PIN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(pin_middleware, "DASHBOARD_PIN", "")


def _set_env(monkeypatch, env):
    if env is not None:
        monkeypatch.setenv("ENV", env)


def _request(headers: dict | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw,
                    "query_string": b"", "client": ("127.0.0.1", 1)})


def _get(path, **kw):
    return client.get(path, follow_redirects=False, **kw)


def _health_db():
    col, db = MagicMock(), MagicMock()
    db.command = AsyncMock(return_value={"ok": 1})
    col.database = db
    return patch("dashboard.main.get_collection", return_value=col)


# ── dev-mode helper ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("env", CLOSED_ENVS)
def test_dev_helpers_closed_outside_exact_development(monkeypatch, env):
    _set_env(monkeypatch, env)
    assert dev_mode.explicit_development() is False
    assert dev_mode.no_pin_passthrough_allowed() is False
    assert dev_mode.unconfigured_secret_allowed() is False
    assert dev_mode.session_secret_fallback_allowed() is False


@pytest.mark.parametrize("env", OPEN_ENVS)
def test_dev_helpers_open_only_in_development(monkeypatch, env):
    _set_env(monkeypatch, env)
    assert dev_mode.no_pin_passthrough_allowed() is True
    assert dev_mode.session_secret_fallback_allowed() is True
    monkeypatch.setenv("REQUIRE_DASHBOARD_PIN", "true")
    assert dev_mode.no_pin_passthrough_allowed() is False
    assert dev_mode.unconfigured_secret_allowed() is False
    monkeypatch.setenv("REQUIRE_SECRET_KEY", "1")
    assert dev_mode.session_secret_fallback_allowed() is False


def test_environment_used_only_when_env_unset(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert dev_mode.explicit_development() is True
    monkeypatch.setenv("ENV", "production")
    assert dev_mode.explicit_development() is False


def test_middleware_reexports_shared_helper():
    assert pin_middleware.no_pin_passthrough_allowed is dev_mode.no_pin_passthrough_allowed


# ── 1. config.py: no hardcoded PIN default ───────────────────────────────────

def test_config_dashboard_pin_has_no_default():
    tree = ast.parse((ROOT / "dashboard" / "config.py").read_text())
    found = False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "getenv"
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "DASHBOARD_PIN"):
            found = True
            default = node.args[1] if len(node.args) > 1 else None
            assert default is None or (isinstance(default, ast.Constant) and default.value in ("", None))
    assert found


def test_self_indemnitor_pin_has_no_default(monkeypatch):
    from dashboard.services import packet_builder_service as pbs

    monkeypatch.setattr(pbs, "SELF_INDEMNITOR_PIN", "")
    for guess in ("000000", "123456", "1", ""):
        assert pbs.verify_self_indemnitor_pin(guess) is False
    monkeypatch.setenv("SELF_INDEMNITOR_PIN", "918273")
    assert pbs.verify_self_indemnitor_pin("918273") is True
    assert pbs.verify_self_indemnitor_pin("918274") is False


def test_retired_pin_absent_from_tracked_text_files():
    import subprocess

    from scripts.check_brand_contacts import contains_retired_pin

    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.split()
    hits = []
    for rel in files:
        if not rel.endswith((".py", ".md", ".txt", ".yml", ".yaml", ".json", ".html", ".js",
                             ".example", ".toml", ".cfg", ".ini", ".sh", ".env")):
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # digit runs only; separators (dashes, spaces) are stripped per line
        if contains_retired_pin(text) or any(
            contains_retired_pin("".join(ch for ch in line if ch.isdigit()))
            for line in text.splitlines() if sum(ch.isdigit() for ch in line) >= 6
        ):
            hits.append(rel)
    # Filenames only; never print the value.
    assert hits == []


# ── 2. guards: AR, OSINT, ALPR, booking intake ───────────────────────────────

@pytest.mark.parametrize("env", CLOSED_ENVS)
def test_ar_require_staff_closed_without_pin(monkeypatch, env):
    from dashboard.routers.accounts_receivable import require_staff

    _set_env(monkeypatch, env)
    assert require_staff(_request()) == ""


def test_ar_require_staff_dev_and_pin_paths(monkeypatch):
    from dashboard.routers.accounts_receivable import require_staff

    monkeypatch.setenv("ENV", "development")
    assert require_staff(_request()) == "staff"
    monkeypatch.setenv("REQUIRE_DASHBOARD_PIN", "true")
    assert require_staff(_request()) == ""
    monkeypatch.delenv("REQUIRE_DASHBOARD_PIN")
    monkeypatch.setenv("DASHBOARD_PIN", SYN_PIN)
    for env in ("production", "development"):
        monkeypatch.setenv("ENV", env)
        assert require_staff(_request({"x-admin-token": SYN_PIN})) == "staff"
        assert require_staff(_request({"x-admin-token": "wrong"})) == ""
        assert require_staff(_request()) == ""


def _admin_guards():
    from dashboard.routers import alpr, osint_api, palantir_intel

    return [
        (osint_api, "_require_admin", "OSINT_ADMIN_KEY"),
        (alpr, "_require_staff", "ALPR_ADMIN_KEY"),
        (palantir_intel, "_require_booking_intake_staff", "BOOKING_INTAKE_ADMIN_KEY"),
    ]


@pytest.mark.parametrize("env", CLOSED_ENVS + OPEN_ENVS)
def test_admin_key_guards_fail_closed_unless_development(monkeypatch, env):
    _set_env(monkeypatch, env)
    is_dev = (env or "").strip().lower() == "development"
    for mod, fn, key_attr in _admin_guards():
        monkeypatch.setattr(mod, key_attr, "")
        monkeypatch.setattr(mod, "DASHBOARD_PIN", "")
        guard = getattr(mod, fn)
        if is_dev:
            assert guard(_request()) is None, mod.__name__
        else:
            with pytest.raises(HTTPException) as exc:
                guard(_request())
            assert exc.value.status_code == 503, (mod.__name__, env)


def test_admin_key_guards_closed_in_dev_when_pin_required(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("REQUIRE_DASHBOARD_PIN", "1")
    for mod, fn, key_attr in _admin_guards():
        monkeypatch.setattr(mod, key_attr, "")
        monkeypatch.setattr(mod, "DASHBOARD_PIN", "")
        with pytest.raises(HTTPException) as exc:
            getattr(mod, fn)(_request())
        assert exc.value.status_code == 503


@pytest.mark.parametrize("env", [None, "production", "development"])
def test_admin_keys_still_work(monkeypatch, env):
    _set_env(monkeypatch, env)
    for mod, fn, key_attr in _admin_guards():
        monkeypatch.setattr(mod, key_attr, "synthetic-admin-key")
        monkeypatch.setattr(mod, "DASHBOARD_PIN", "")
        guard = getattr(mod, fn)
        assert guard(_request(), x_admin_key="synthetic-admin-key") is None
        with pytest.raises(HTTPException) as exc:
            guard(_request(), x_admin_key="wrong")
        assert exc.value.status_code == 403
        # PIN token path
        monkeypatch.setattr(mod, "DASHBOARD_PIN", SYN_PIN)
        assert guard(_request(), x_admin_token=SYN_PIN) is None


# ── 2. bonds admin-pin-override ──────────────────────────────────────────────

def _bonds_client():
    from dashboard.routers.bonds import bonds_bp

    a = FastAPI()
    a.include_router(bonds_bp)
    return TestClient(a)


def _fake_collection():
    col = MagicMock()
    col.insert_one = AsyncMock()
    col.update_one = AsyncMock()
    return col


@pytest.mark.parametrize("env", CLOSED_ENVS)
def test_admin_pin_override_503_without_pin(monkeypatch, env):
    _set_env(monkeypatch, env)
    col = _fake_collection()
    with patch("dashboard.routers.bonds.get_collection", return_value=col):
        resp = _bonds_client().post("/api/admin-pin-override", json={"pin": "", "booking_number": "SYN-1"})
    assert resp.status_code == 503
    col.insert_one.assert_not_called()
    col.update_one.assert_not_called()


def test_admin_pin_override_pin_checks(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DASHBOARD_PIN", SYN_PIN)
    col = _fake_collection()
    with patch("dashboard.routers.bonds.get_collection", return_value=col):
        c = _bonds_client()
        assert c.post("/api/admin-pin-override", json={"pin": "wrong"}).status_code == 401
        assert c.post("/api/admin-pin-override", json={"pin": ""}).status_code == 401
        col.insert_one.assert_not_called()
        assert c.post("/api/admin-pin-override", json={"pin": SYN_PIN}).status_code == 200
    col.insert_one.assert_called_once()


def test_admin_pin_override_dev_without_pin_allowed(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    col = _fake_collection()
    with patch("dashboard.routers.bonds.get_collection", return_value=col):
        assert _bonds_client().post("/api/admin-pin-override", json={"pin": ""}).status_code == 200


# ── 2. webhook secrets ───────────────────────────────────────────────────────

@pytest.mark.parametrize("env", CLOSED_ENVS)
def test_webhook_secrets_fail_closed_outside_development(monkeypatch, env):
    from dashboard.routers import webhooks
    from dashboard.routers.geo_intelligence import traccar_webhook_auth_failure

    _set_env(monkeypatch, env)
    monkeypatch.setenv("DEBUG", "true")
    assert webhooks._env_is_production() is True
    assert traccar_webhook_auth_failure(_request()) == (503, "Webhook secret not configured")
    assert webhooks.verify_docuseal_signature(b"{}", "") is False
    assert webhooks._verify_adobe_pdf_webhook(_request()) is False

    a = FastAPI()
    a.include_router(webhooks.webhooks_bp)
    c = TestClient(a)
    assert c.post("/api/webhooks/twilio", data={"From": "+12395550100", "Body": "x"}).status_code == 503
    assert c.post("/api/webhooks/payment", json={"event_type": "payment.completed"}).status_code == 503


def test_webhook_secrets_dev_tolerance(monkeypatch):
    from dashboard.routers import webhooks
    from dashboard.routers.geo_intelligence import traccar_webhook_auth_failure

    monkeypatch.setenv("ENV", "development")
    assert webhooks._env_is_production() is False
    assert traccar_webhook_auth_failure(_request()) is None
    monkeypatch.setenv("REQUIRE_TRACCAR_WEBHOOK_SECRET", "true")
    assert traccar_webhook_auth_failure(_request()) == (503, "Webhook secret not configured")
    # DocuSeal / Adobe still also need DEBUG in development
    assert webhooks.verify_docuseal_signature(b"{}", "") is False
    assert webhooks._verify_adobe_pdf_webhook(_request()) is False
    monkeypatch.setenv("DEBUG", "true")
    assert webhooks.verify_docuseal_signature(b"{}", "") is True
    assert webhooks._verify_adobe_pdf_webhook(_request()) is True
    monkeypatch.setenv("REQUIRE_DOCUSEAL_WEBHOOK_SECRET", "1")
    assert webhooks.verify_docuseal_signature(b"{}", "") is False


def test_configured_webhook_secrets_still_checked(monkeypatch):
    from dashboard.routers import webhooks
    from dashboard.routers.geo_intelligence import traccar_webhook_auth_failure

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("TRACCAR_WEBHOOK_SECRET", "synthetic-traccar")
    assert traccar_webhook_auth_failure(_request()) == (401, "Missing webhook secret")
    monkeypatch.setenv("ADOBE_PDF_WEBHOOK_SECRET", "synthetic-adobe")
    assert webhooks._verify_adobe_pdf_webhook(
        _request({"x-shamrock-adobe-webhook-secret": "synthetic-adobe"})) is True
    assert webhooks._verify_adobe_pdf_webhook(
        _request({"x-shamrock-adobe-webhook-secret": "wrong"})) is False


# ── 2. portal OTP echo only in explicit development ──────────────────────────

def test_portal_debug_pin_echo_requires_explicit_development():
    src = (ROOT / "dashboard" / "routers" / "pin_portal.py").read_text()
    assert "debug_ok = explicit_development()" in src
    assert 'debug_ok = env not in ("production", "prod")' not in src


# ── 3. SECRET_KEY ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("env", CLOSED_ENVS)
def test_missing_secret_key_fails_session_routes_closed(monkeypatch, env):
    _set_env(monkeypatch, env)
    monkeypatch.setenv("DASHBOARD_PIN", SYN_PIN)
    monkeypatch.setattr(pin_middleware, "VALID_PINS", frozenset({SYN_PIN}))
    assert pin_middleware.session_secret_configured() is False
    with pytest.raises(pin_middleware.SessionSecretMissing):
        pin_middleware._get_serializer()
    assert pin_middleware._load_session("anything") is None
    for path in ("/api/stats", "/openapi.json", "/docs", "/"):
        resp = _get(path)
        assert resp.status_code == 503, (env, path)
        assert resp.json() == {"error": "Session signing not configured"}
    # login POST cannot issue a session, even with the right PIN
    resp = client.post("/login", json={"pin": SYN_PIN}, follow_redirects=False)
    assert resp.status_code == 503
    assert "set-cookie" not in {k.lower() for k in resp.headers.keys()}
    with _health_db():
        assert _get("/health").status_code == 200
    for path in ("/login", "/robots.txt", "/kiosk"):
        assert _get(path).status_code != 503, path


def test_missing_secret_key_with_pin_set_and_key_set_keeps_401(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DASHBOARD_PIN", SYN_PIN)
    monkeypatch.setenv("SECRET_KEY", SYN_SECRET)
    assert _get("/api/stats").status_code == 401
    assert _get("/docs").status_code == 302


def test_missing_secret_key_development_uses_dev_only_key(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DASHBOARD_PIN", SYN_PIN)
    assert pin_middleware.session_secret_configured() is True
    assert pin_middleware._get_serializer().secret_key == pin_middleware.DEV_ONLY_SESSION_KEY.encode()
    assert _get("/api/stats").status_code == 401
    monkeypatch.setenv("REQUIRE_SECRET_KEY", "true")
    assert _get("/api/stats").status_code == 503


@pytest.mark.parametrize("env", [None, "production", "staging"])
def test_machine_keys_work_without_session_secret(monkeypatch, env):
    _set_env(monkeypatch, env)
    monkeypatch.setenv("DASHBOARD_PIN", SYN_PIN)
    monkeypatch.setenv("GAS_API_KEY", SYN_GAS)
    monkeypatch.setenv("LEADS_INTERNAL_TOKEN", "synthetic-internal-0000")
    assert _get("/openapi.json", headers={"X-API-Key": SYN_GAS}).status_code == 200
    assert _get("/openapi.json", headers={"X-Internal-Token": "synthetic-internal-0000"}).status_code == 200
    assert _get("/openapi.json", headers={"X-API-Key": "wrong"}).status_code == 503


@pytest.mark.parametrize("env", CLOSED_ENVS)
def test_deps_secret_never_pin_derived_or_fixed(monkeypatch, env):
    _set_env(monkeypatch, env)
    monkeypatch.setenv("DASHBOARD_PIN", SYN_PIN)
    assert deps._session_secret() == ""
    deps.get_settings.cache_clear()
    try:
        assert deps.get_settings().secret_key == ""
    finally:
        deps.get_settings.cache_clear()
    monkeypatch.setenv("SECRET_KEY", SYN_SECRET)
    assert deps._session_secret() == SYN_SECRET


def test_deps_secret_development_fallback(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DASHBOARD_PIN", SYN_PIN)
    secret = deps._session_secret()
    assert secret == pin_middleware.DEV_ONLY_SESSION_KEY
    assert SYN_PIN not in secret
    src = (ROOT / "dashboard" / "deps.py").read_text()
    assert "leads-2245" not in src
    assert '"shamrock-" + (_pin' not in src


def test_kiosk_scan_token_fails_closed_without_secret(monkeypatch):
    from dashboard.routers import pin_portal

    monkeypatch.setenv("ENV", "production")
    a = FastAPI()
    a.include_router(pin_portal.pin_portal_router)
    c = TestClient(a)
    resp = c.post("/api/portal/kiosk-id-confirm", json={"scan_token": "x.y.z"})
    assert resp.status_code == 503
    assert resp.json()["error"] == "Session signing not configured"
    # With a key, a forged token is a normal 400 (not 503).
    monkeypatch.setenv("SECRET_KEY", SYN_SECRET)
    assert c.post("/api/portal/kiosk-id-confirm", json={"scan_token": "x.y.z"}).status_code == 400
