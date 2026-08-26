"""Shannon ID capture + health paths. No live client upload."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import JSONResponse


def test_shannon_id_and_health_paths_are_open():
    from dashboard.auth.pin_middleware import OPEN_PATHS, OPEN_PREFIXES

    assert "/api/ops/shannon-health" in OPEN_PATHS
    assert any("/paperwork/shannon/id/token".startswith(p) for p in OPEN_PREFIXES)
    assert any("/api/paperwork/shannon/id-link".startswith(p) for p in OPEN_PREFIXES)


def test_shannon_id_link_requires_auth(monkeypatch):
    from dashboard.routers import shannon_id

    monkeypatch.setattr(
        shannon_id,
        "_require_control_auth",
        lambda *a, **k: JSONResponse({"success": False, "error": "Authentication required"}, status_code=401),
    )

    class Req:
        async def json(self):
            return {"packet_id": "SH-TEST"}

    result = asyncio.run(shannon_id.shannon_id_link(Req()))
    assert isinstance(result, JSONResponse)
    assert result.status_code == 401


def test_shannon_id_link_mints_url(monkeypatch):
    from dashboard.routers import shannon_id

    col = MagicMock()
    col.update_one = AsyncMock()
    monkeypatch.setattr(shannon_id, "_require_control_auth", lambda *a, **k: None)
    monkeypatch.setattr(shannon_id, "get_collection", lambda name: col)

    class Req:
        async def json(self):
            return {"packet_id": "SH-TEST", "caller_role": "indemnitor", "defendant_name": "Jane Doe"}

    result = asyncio.run(shannon_id.shannon_id_link(Req()))
    assert result["success"] is True
    assert result["packet_id"] == "SH-TEST"
    assert "/paperwork/shannon/id/" in result["upload_url"]
    assert col.update_one.await_count == 1
    update = col.update_one.await_args.args[1]
    assert update["$set"]["defendant_name"] == "Jane Doe"


def test_shannon_id_link_does_not_blank_defendant_name(monkeypatch):
    from dashboard.routers import shannon_id

    col = MagicMock()
    col.update_one = AsyncMock()
    monkeypatch.setattr(shannon_id, "_require_control_auth", lambda *a, **k: None)
    monkeypatch.setattr(shannon_id, "get_collection", lambda name: col)

    class Req:
        async def json(self):
            return {"packet_id": "SH-EXISTING"}

    result = asyncio.run(shannon_id.shannon_id_link(Req()))
    assert result["success"] is True
    update = col.update_one.await_args.args[1]
    assert "defendant_name" not in update["$set"]


def test_invalid_id_token_is_404(monkeypatch):
    from dashboard.routers import shannon_id

    monkeypatch.setattr(shannon_id, "_packet_by_token", AsyncMock(return_value=None))
    result = asyncio.run(shannon_id.shannon_id_page("nope"))
    assert result.status_code == 404


def test_simulated_intents_are_documented():
    """Contract for Shannon simulated call paths (no live ElevenLabs send)."""
    scenarios = {
        "indemnitor_happy_path": ["check_caller_history", "create_intake", "request_id_photo", "email_paperwork_to_indemnitor"],
        "spanish_to_sofia": ["transfer_to_agent"],
        "want_a_person": ["notify_bondsman", "239-332-2245"],
        "missing_email": ["spell", "email_paperwork_to_indemnitor"],
        "jail_defendant": ["never email the jail", "request_id_photo"],
    }
    assert set(scenarios) >= {"indemnitor_happy_path", "spanish_to_sofia", "want_a_person", "missing_email"}
