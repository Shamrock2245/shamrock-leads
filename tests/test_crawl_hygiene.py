"""Crawl hygiene for the staff CRM host.

Unauthenticated `/robots.txt` must be a real text/plain 200 (Disallow: /),
not a PIN redirect. `/` and `/login` must send noindex. Auth stays fail-closed.
"""
import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token
from dashboard.main import app

client = TestClient(app)

_PIN_ENV = {"DASHBOARD_PIN": "test-pin-not-real", "ENV": "test", "ENVIRONMENT": "test"}
_BINGBOT = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"


def _noindex(value: str) -> bool:
    folded = (value or "").lower().replace(" ", "")
    return "noindex" in folded and "nofollow" in folded


def test_robots_txt_is_public_plain_text_and_disallows_all():
    with patch.dict(os.environ, _PIN_ENV, clear=False):
        resp = client.get("/robots.txt", follow_redirects=False)
    assert resp.status_code == 200
    assert "location" not in {k.lower() for k in resp.headers}
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text.replace("\r\n", "\n")
    assert "User-agent: *" in body
    assert "Disallow: /" in body
    assert "login" not in body.lower()


def test_robots_txt_head_and_bingbot_do_not_redirect():
    with patch.dict(os.environ, _PIN_ENV, clear=False):
        head = client.head("/robots.txt", follow_redirects=False)
        bing = client.get(
            "/robots.txt",
            headers={"User-Agent": _BINGBOT},
            follow_redirects=False,
        )
    assert head.status_code == 200
    assert "text/plain" in head.headers["content-type"]
    assert bing.status_code == 200
    assert "Disallow: /" in bing.text


def test_root_redirect_sends_noindex_and_login_page_does_too():
    with patch.dict(os.environ, _PIN_ENV, clear=False):
        root = client.get("/", follow_redirects=False)
        login = client.get(
            "/login",
            headers={"User-Agent": _BINGBOT},
            follow_redirects=False,
        )
        # Crawlers HEAD /login. A GET-only route 404s as JSON; that must not happen.
        head_login = client.head(
            "/login",
            headers={"User-Agent": _BINGBOT},
            follow_redirects=False,
        )
        head_root = client.head("/", follow_redirects=False)
    assert root.status_code == 302
    assert root.headers["location"].startswith("/login")
    assert _noindex(root.headers.get("x-robots-tag", ""))
    assert login.status_code == 200
    assert "text/html" in login.headers["content-type"]
    assert _noindex(login.headers.get("x-robots-tag", ""))
    assert _noindex(login.text)
    assert 'name="robots"' in login.text
    assert head_login.status_code == 200
    assert "text/html" in head_login.headers["content-type"]
    assert _noindex(head_login.headers.get("x-robots-tag", ""))
    assert head_root.status_code == 302
    assert _noindex(head_root.headers.get("x-robots-tag", ""))


def test_authenticated_dashboard_html_is_noindex_and_api_stays_gated():
    token = _sign_token(email="admin@shamrockbailbonds.biz", role="god_admin", is_admin=True)
    with patch.dict(os.environ, _PIN_ENV, clear=False):
        page = client.get("/", cookies={COOKIE_NAME: token}, follow_redirects=False)
        denied = client.get("/api/stats", follow_redirects=False)
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert _noindex(page.headers.get("x-robots-tag", ""))
    assert _noindex(page.text)
    assert denied.status_code == 401
    assert denied.json() == {"error": "Authentication required"}


def test_nginx_leads_vhost_serves_robots_before_the_proxy():
    """Edge short-circuit for when this vhost file is the one nginx runs."""
    conf_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "nginx",
        "leads.shamrockbailbonds.biz.conf",
    )
    text = open(conf_path, encoding="utf-8").read()
    assert "location = /robots.txt" in text
    assert "Disallow: /" in text
