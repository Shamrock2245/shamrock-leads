"""One place for pay-by-card links per source (owner rule 2026-09-27)."""
from __future__ import annotations

import os
from unittest.mock import patch

from dashboard.services import payment_links as pl

WEB = pl.WEBSITE_LINK
TG = pl.TELEGRAM_LINK


def _clean_env():
    return patch.dict(os.environ, {k: "" for k in (
        "SWIPESIMPLE_LINK_DEFAULT", "SWIPESIMPLE_PAYMENT_LINK", "SWIPESIMPLE_BOND_PAYMENT_LINK",
        "PAYMENT_LINK", "SWIPESIMPLE_LINK_TELEGRAM")})


def test_defaults():
    with _clean_env():
        assert "lnk_b6bf996f" in WEB and "lnk_07a13eb" in TG
        assert pl.payment_link_for("telegram") == TG
        assert pl.payment_link_for("telegram_mini_app") == TG
        for src in ("website", "wix_webhook", "kiosk", "manual", "shannon", "sms", None, "whatever"):
            assert pl.payment_link_for(src) == WEB, src


def test_case_invoice_link_wins():
    case = {"swipesimple_payment_link": "https://swipesimple.com/invoices/abc"}
    with _clean_env():
        assert pl.payment_link_for("telegram", case) == "https://swipesimple.com/invoices/abc"
        assert pl.payment_link_for("website", case) == "https://swipesimple.com/invoices/abc"


def test_env_overrides_per_source():
    with _clean_env(), patch.dict(os.environ, {"SWIPESIMPLE_LINK_TELEGRAM": "https://x/tg",
                                               "SWIPESIMPLE_PAYMENT_LINK": "https://x/web"}):
        assert pl.payment_link_for("telegram") == "https://x/tg"
        assert pl.payment_link_for("website") == "https://x/web"
        assert pl.payment_link_for("kiosk") == "https://x/web"


def test_source_of_record():
    assert pl.source_of({"source": "telegram_mini_app"}) == "telegram"
    assert pl.source_of({"intake_source": "website"}) == "website"
    assert pl.source_of({}) == "default"


def test_resolve_swipesimple_url_uses_rule():
    from dashboard.services.packet_payment_link_service import resolve_swipesimple_url

    with _clean_env():
        assert resolve_swipesimple_url() == WEB
        assert resolve_swipesimple_url(intake_source="telegram") == TG
        assert resolve_swipesimple_url("telegram", {"payment_link": "https://own/link"}) == "https://own/link"
