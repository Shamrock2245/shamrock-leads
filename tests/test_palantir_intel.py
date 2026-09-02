"""Tests for Palantir intelligence hub — fail-closed graph, no synthetic identity."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.models.palantir import KnowledgeGraph, NodeType
from dashboard.routers.palantir_intel import (
    _build_live_graph,
    _mask_email,
    _mask_phone,
    palantir_router,
)


def test_mask_phone_and_email():
    assert _mask_phone("2395550199") == "(239) ***-0199"
    assert _mask_phone("1") == ""
    assert _mask_email("jane@example.com") == "j***@example.com"
    assert _mask_email("bad") == ""


@pytest.mark.asyncio
async def test_graph_empty_when_subject_missing():
    mock_col = MagicMock()
    mock_col.find_one = AsyncMock(return_value=None)

    with patch("dashboard.routers.palantir_intel.get_collection", return_value=mock_col):
        graph = await _build_live_graph("Nobody Real", "defendant")

    assert isinstance(graph, KnowledgeGraph)
    assert graph.subject_found is False
    assert graph.data_mode == "empty"
    assert graph.nodes == []
    assert graph.edges == []
    assert any("not invent" in w.lower() or "No matching" in w for w in graph.warnings)


@pytest.mark.asyncio
async def test_graph_live_from_defendant_record():
    defendant = {
        "_id": "abc123",
        "name": "Jane Doe",
        "booking_number": "BK-99",
        "county": "Lee",
        "phone": "2395550199",
        "email": "jane@example.com",
        "address": "100 Main St",
        "city": "Fort Myers",
        "state": "FL",
    }

    empty_cursor = MagicMock()
    empty_cursor.__aiter__ = lambda self: _async_iter([])

    mock_bonds = MagicMock()
    mock_bonds.find = MagicMock(return_value=empty_cursor)

    mock_packets = MagicMock()
    mock_packets.find = MagicMock(return_value=MagicMock(sort=MagicMock(return_value=empty_cursor)))

    mock_fam = MagicMock()
    mock_fam.find_one = AsyncMock(return_value=None)

    def _col(name):
        if name == "defendants":
            c = MagicMock()
            c.find_one = AsyncMock(return_value=defendant)
            return c
        if name == "active_bonds":
            return mock_bonds
        if name == "paperwork_packets":
            return mock_packets
        if name == "family_trees":
            return mock_fam
        if name == "arrests":
            c = MagicMock()
            c.find_one = AsyncMock(return_value=None)
            return c
        c = MagicMock()
        c.find_one = AsyncMock(return_value=None)
        return c

    with patch("dashboard.routers.palantir_intel.get_collection", side_effect=_col):
        graph = await _build_live_graph("Jane Doe", "defendant")

    assert graph.subject_found is True
    assert graph.data_mode == "live"
    types = {n.type for n in graph.nodes}
    assert NodeType.defendant in types
    assert NodeType.phone in types
    assert NodeType.email in types
    assert NodeType.property in types
    # Phone must be masked in the UI label
    phone_nodes = [n for n in graph.nodes if n.type == NodeType.phone]
    assert phone_nodes and "***" in phone_nodes[0].label
    # No invented LLC / fake Mary Ann Smith
    labels = " ".join(n.label for n in graph.nodes)
    assert "Gulf Coast Logistics" not in labels
    assert "Mary Ann Smith" not in labels


@pytest.mark.asyncio
async def test_graph_includes_vehicle_and_case_from_defendant_record():
    defendant = {
        "_id": "veh123",
        "name": "Jane Doe",
        "booking_number": "BK-99",
        "county": "Lee",
        "case_number": "25CF015873",
        "warrant_status": "Active warrant — FTA",
        "vehicles": [
            {"plate": "92EUIZ", "description": "2024 Black Hyundai Elantra SEL"},
        ],
    }

    empty_cursor = MagicMock()
    empty_cursor.__aiter__ = lambda self: _async_iter([])

    mock_bonds = MagicMock()
    mock_bonds.find = MagicMock(return_value=empty_cursor)

    mock_packets = MagicMock()
    mock_packets.find = MagicMock(return_value=MagicMock(sort=MagicMock(return_value=empty_cursor)))

    mock_fam = MagicMock()
    mock_fam.find_one = AsyncMock(return_value=None)

    def _col(name):
        if name == "defendants":
            c = MagicMock()
            c.find_one = AsyncMock(return_value=defendant)
            return c
        if name == "active_bonds":
            return mock_bonds
        if name == "paperwork_packets":
            return mock_packets
        if name == "family_trees":
            return mock_fam
        if name == "arrests":
            c = MagicMock()
            c.find_one = AsyncMock(return_value=None)
            return c
        c = MagicMock()
        c.find_one = AsyncMock(return_value=None)
        return c

    with patch("dashboard.routers.palantir_intel.get_collection", side_effect=_col):
        graph = await _build_live_graph("Jane Doe", "defendant")

    types = {n.type for n in graph.nodes}
    assert NodeType.vehicle in types
    assert NodeType.court_case in types
    vehicle = next(n for n in graph.nodes if n.type == NodeType.vehicle)
    assert "92EUIZ" in vehicle.label
    case = next(n for n in graph.nodes if n.type == NodeType.court_case)
    assert "25CF015873" in case.label


def test_api_routes_load():
    app = FastAPI()
    app.include_router(palantir_router)
    client = TestClient(app)

    # Health does not need Mongo
    r = client.get("/api/palantir/health")
    assert r.status_code == 200
    assert r.json().get("ok") is True

    # Breach lookup must not invent hits (Hudson Rock mocked empty)
    with patch(
        "dashboard.routers.palantir_intel._query_hudson_rock",
        new=AsyncMock(return_value=([], None)),
    ):
        r2 = client.post(
            "/api/palantir/spectra/breach-lookup",
            json={"email": "someone@example.com"},
        )
    assert r2.status_code == 200
    body = r2.json()
    assert body["found"] is False
    assert body["total_breaches"] == 0
    assert body["data_mode"] == "live"
    assert "Hudson Rock" in (body.get("message") or "")


def test_spectra_maps_hudson_rock_stealers():
    from dashboard.routers.palantir_intel import _hudson_rock_items

    items = _hudson_rock_items({
        "stealers": [{
            "date_compromised": "2024-03-01T12:00:00Z",
            "stealer_family": "RedLine",
            "operating_system": "Windows 10",
            "computer_name": "DESKTOP-X",
            "top_passwords": ["should-never-surface"],
        }]
    })
    assert len(items) == 1
    assert items[0].demo is False
    assert items[0].verified is True
    assert "Hudson Rock" in items[0].breach_name
    assert "should-never-surface" not in items[0].description
    assert "infostealer_log" in items[0].compromised_data


def test_spectra_phone_not_queried():
    app = FastAPI()
    app.include_router(palantir_router)
    client = TestClient(app)
    r = client.post("/api/palantir/spectra/breach-lookup", json={"phone": "2395550100"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False
    assert "not phone" in (body.get("message") or "").lower()


async def _async_iter(items):
    for i in items:
        yield i
