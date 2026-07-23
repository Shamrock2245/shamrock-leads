"""BlueBubbles server config must stay shared after init (no rebind)."""
from __future__ import annotations

import os

import pytest


def test_init_bluebubbles_mutates_shared_dict(monkeypatch):
    """Modules that import BB_SERVERS by name must see servers after init.

    Regression: ``BB_SERVERS = {}`` inside init_bluebubbles re-bound the
    module global and left importers holding an empty discarded dict.
    """
    from dashboard import extensions as ext

    monkeypatch.setenv("BLUEBUBBLES_URL_0178", "http://127.0.0.1:1234")
    monkeypatch.setenv("BLUEBUBBLES_PASSWORD_0178", "test-pw")
    monkeypatch.delenv("BLUEBUBBLES_URL_0314", raising=False)
    monkeypatch.delenv("BLUEBUBBLES_PASSWORD_0314", raising=False)

    # Simulate router import pattern: bind name, then init
    from dashboard.extensions import BB_SERVERS, init_bluebubbles

    pre_id = id(BB_SERVERS)
    BB_SERVERS.clear()
    assert len(BB_SERVERS) == 0

    init_bluebubbles()

    assert id(BB_SERVERS) == pre_id, "BB_SERVERS object identity must not change"
    assert BB_SERVERS is ext.BB_SERVERS
    assert "2399550178" in BB_SERVERS
    assert BB_SERVERS["2399550178"]["url"] == "http://127.0.0.1:1234"
    assert BB_SERVERS["2399550178"]["password"] == "test-pw"


def test_init_bluebubbles_clears_stale_servers(monkeypatch):
    from dashboard.extensions import BB_SERVERS, init_bluebubbles

    monkeypatch.setenv("BLUEBUBBLES_URL_0178", "http://127.0.0.1:1234")
    monkeypatch.setenv("BLUEBUBBLES_PASSWORD_0178", "a")
    init_bluebubbles()
    assert "2399550178" in BB_SERVERS

    monkeypatch.delenv("BLUEBUBBLES_URL_0178", raising=False)
    monkeypatch.delenv("BLUEBUBBLES_URL", raising=False)
    monkeypatch.delenv("BLUEBUBBLES_PASSWORD_0178", raising=False)
    init_bluebubbles()
    assert BB_SERVERS == {}
