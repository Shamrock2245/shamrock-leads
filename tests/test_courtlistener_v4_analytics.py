"""Tests for CourtListener REST API v4 Case Law Explorer & Analytics."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.court_dockets import court_intel_bp
from dashboard.services.courtlistener_client import (
    SE_US_COURTS,
    SE_US_STATES,
    CourtListenerClient,
)


class _MockCursor:
    def __init__(self, docs: list[dict]):
        self._docs = [dict(d) for d in docs]

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, list):
            for key, dir_val in reversed(key_or_list):
                reverse = dir_val < 0
                self._docs.sort(key=lambda d: self._extract(d, key), reverse=reverse)
        else:
            reverse = (direction or 1) < 0
            self._docs.sort(key=lambda d: self._extract(d, key_or_list), reverse=reverse)
        return self

    @staticmethod
    def _extract(doc: dict, dotted_key: str):
        cur = doc
        for part in dotted_key.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
        if cur is None:
            return (0, "")
        if isinstance(cur, (int, float)):
            return (1, cur)
        return (1, str(cur))

    def limit(self, n: int):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length: int | None = None):
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class _MockCollection:
    def __init__(self, docs: list[dict] | None = None):
        self.docs = [dict(d) for d in (docs or [])]

    @staticmethod
    def _match(doc: dict, filt: dict | None) -> bool:
        if not filt:
            return True
        for k, v in filt.items():
            if k == "$or":
                if not any(_MockCollection._match(doc, sub) for sub in v):
                    return False
                continue
            if isinstance(v, dict):
                if "$in" in v:
                    if doc.get(k) not in v["$in"]:
                        return False
                elif "$gte" in v:
                    val = _MockCursor._extract(doc, k)[1]
                    if val < v["$gte"]:
                        return False
                continue
            if doc.get(k) != v:
                return False
        return True

    async def count_documents(self, filt=None):
        return sum(1 for d in self.docs if self._match(d, filt))

    def find(self, filt=None, projection=None):
        matched = [dict(d) for d in self.docs if self._match(d, filt)]
        if projection:
            # Handle inclusive projection minimally
            inc = [k for k, v in projection.items() if v]
            if inc:
                filtered = []
                for d in matched:
                    row = {k: d[k] for k in inc if k in d}
                    if "_id" in d and projection.get("_id", 1) != 0:
                        row["_id"] = d["_id"]
                    filtered.append(row)
                matched = filtered
        return _MockCursor(matched)

    async def find_one(self, filt=None, projection=None, sort=None):
        for d in self.docs:
            if self._match(d, filt):
                return dict(d)
        return None

    async def update_one(self, filt, update, upsert=False):
        for d in self.docs:
            if self._match(d, filt):
                d.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            new_doc = dict(filt or {})
            new_doc.update(update.get("$set", {}))
            new_doc.setdefault("_id", f"mock_{len(self.docs) + 1}")
            self.docs.append(new_doc)
        return SimpleNamespace(matched_count=0, modified_count=0)


class _MockDB:
    def __init__(self, outcome_docs=None, arrest_docs=None):
        self.court_outcomes = _MockCollection(outcome_docs)
        self.arrests = _MockCollection(arrest_docs)

    def __getitem__(self, name: str):
        if name == "court_outcomes":
            return self.court_outcomes
        if name == "arrests":
            return self.arrests
        return _MockCollection()


def _mock_response(status_code: int, payload: dict, url: str = "https://www.courtlistener.com/api/rest/v4/") -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", url),
    )


def test_coverage_includes_tx_ct_oh_and_ca2():
    for st in ("FL", "GA", "TX", "CT", "OH"):
        assert st in SE_US_STATES
    for cid in ("tex", "texcrimapp", "conn", "ohio", "ca2"):
        assert cid in SE_US_COURTS
    client = CourtListenerClient()
    cov = client.get_coverage_summary()
    assert cov["total_states"] == 15
    assert cov["total_courts"] == len(SE_US_COURTS)
    assert "TX" in cov["states"] and "CT" in cov["states"] and "OH" in cov["states"]


def test_parse_cluster_id_from_url():
    assert CourtListenerClient.parse_cluster_id_from_url(
        "https://www.courtlistener.com/opinion/2812209/obergefell-v-hodges/"
    ) == 2812209
    assert CourtListenerClient.parse_cluster_id_from_url(
        "https://www.courtlistener.com/api/rest/v4/clusters/9502621/"
    ) == 9502621
    assert CourtListenerClient.parse_cluster_id_from_url("4214664") == 4214664
    assert CourtListenerClient.parse_cluster_id_from_url("not-an-id") is None


@pytest.mark.asyncio
async def test_get_full_case_dossier_assembles_hierarchy():
    client = CourtListenerClient(api_token="test-tok")
    client._in_maintenance_window = lambda: False

    async def fake_request(method, url, **kwargs):
        if url == "/clusters/2812209/":
            return _mock_response(200, {
                "id": 2812209,
                "case_name": "State v. Doe",
                "case_name_full": "State of Florida v. John Doe",
                "docket": "https://www.courtlistener.com/api/rest/v4/dockets/4214664/",
                "sub_opinions": ["https://www.courtlistener.com/api/rest/v4/opinions/9969234/"],
                "date_filed": "2025-10-01",
                "judges": "Judge Labarga",
                "precedential_status": "Published",
                "citation_count": 14,
                "citations": [{"volume": "380", "reporter": "So. 3d", "page": "120"}],
                "absolute_url": "/opinion/2812209/state-v-doe/",
            })
        if url == "/dockets/4214664/":
            return _mock_response(200, {
                "id": 4214664,
                "court_id": "fla",
                "docket_number": "SC2025-0101",
                "pacer_case_id": "",
                "date_filed": "2025-05-01",
                "date_terminated": "2025-10-01",
                "assigned_to_str": "Judge Labarga",
                "cause": "Criminal Appeal",
                "nature_of_suit": "Bail Bond Forfeiture",
            })
        if url == "/opinions/9969234/":
            return _mock_response(200, {
                "id": 9969234,
                "type": "010combined",
                "author_str": "Labarga, J.",
                "per_curiam": False,
                "html_with_citations": "<p>The surety's motion to set aside <b>bond forfeiture</b> is affirmed after failure to appear.</p>",
                "opinions_cited": ["url1", "url2", "url3"],
                "download_url": "https://www.courtlistener.com/pdf/9969234.pdf",
            })
        raise AssertionError(f"Unexpected URL: {url}")

    client._request_with_retry = fake_request
    res = await client.get_full_case_dossier("https://www.courtlistener.com/opinion/2812209/state-v-doe/")
    assert res["success"] is True
    d = res["dossier"]
    assert d["cluster_id"] == 2812209
    assert d["court"]["id"] == "fla"
    assert d["court"]["state"] == "FL"
    assert d["docket"]["docket_number"] == "SC2025-0101"
    assert d["cluster"]["citations"] == ["380 So. 3d 120"]
    assert d["opinion"]["opinions_cited_count"] == 3
    assert "<p>" not in d["opinion"]["text"]
    assert "bond forfeiture" in d["opinion"]["text"]
    assert d["disposition"] == "bond_forfeiture"
    assert d["bail_impact"]["score"] == 100  # 95 + 5 citation boost


@pytest.mark.asyncio
async def test_live_search_and_docket_lookup():
    client = CourtListenerClient()
    client._in_maintenance_window = lambda: False

    async def fake_request(method, url, **kwargs):
        params = kwargs.get("params") or {}
        if url == "/search/" and params.get("type") == "p":
            return _mock_response(200, {
                "count": 1,
                "results": [{
                    "id": 1124,
                    "name": "Paul L. Friedman",
                    "court_id": "flsd",
                    "positions": [{"position_type": "Judge", "court": "S.D. Florida"}],
                    "absolute_url": "/person/1124/paul-l-friedman/",
                }],
            })
        if url == "/search/":
            return _mock_response(200, {
                "count": 2,
                "results": [
                    {
                        "id": 9001,
                        "cluster_id": 9001,
                        "caseName": "State v. Alvarez",
                        "court_id": "fladistctapp_2",
                        "docketNumber": "2D25-88",
                        "dateFiled": "2026-02-14",
                        "judge": "Judge Morris",
                        "citeCount": 12,
                        "snippet": "Defendant's <em>failure to appear</em> triggered bond forfeiture.",
                        "absolute_url": "/opinion/9001/state-v-alvarez/",
                    },
                    {
                        "id": 9002,
                        "cluster_id": 9002,
                        "caseName": "State v. Perez",
                        "court_id": "fladistctapp_2",
                        "docketNumber": "2D25-99",
                        "dateFiled": "2026-03-01",
                        "judge": "Judge Morris",
                        "citeCount": 1,
                        "snippet": "Charges were dismissed before trial.",
                        "absolute_url": "/opinion/9002/state-v-perez/",
                    },
                ],
            })
        if url == "/dockets/":
            return _mock_response(200, {
                "results": [{
                    "id": 555,
                    "docket_number": "2D25-88",
                    "case_name": "State v. Alvarez",
                    "court_id": "fladistctapp_2",
                    "assigned_to_str": "Judge Morris",
                    "date_filed": "2025-11-01",
                }],
            })
        if url == "/clusters/":
            return _mock_response(200, {
                "results": [{
                    "id": 9001,
                    "docket": "https://www.courtlistener.com/api/rest/v4/dockets/555/",
                    "case_name": "State v. Alvarez",
                    "date_filed": "2026-02-14",
                    "citation_count": 12,
                }],
            })
        raise AssertionError(f"Unexpected URL: {url}")

    client._request_with_retry = fake_request

    s_res = await client.live_search(query='"bond forfeiture"', search_type="o", state="FL")
    assert s_res["success"] is True
    assert s_res["count"] == 2
    assert s_res["analytics"]["bail_critical_count"] == 1
    assert s_res["analytics"]["bail_critical_pct"] == 50.0
    assert s_res["analytics"]["total_citations"] == 13
    assert s_res["analytics"]["by_judge"][0]["judge"] == "Judge Morris"

    p_res = await client.live_search(query="Friedman", search_type="p")
    assert p_res["success"] is True
    assert p_res["results"][0]["case_name"] == "Paul L. Friedman"
    assert p_res["results"][0]["disposition"] == "judge_profile"

    dl_res = await client.lookup_by_docket_number("2D25-88", state="FL")
    assert dl_res["success"] is True
    assert len(dl_res["dockets"]) == 1
    assert len(dl_res["clusters"]) == 1
    assert len(dl_res["search_hits"]) == 2


def test_court_intel_endpoints_roundtrip():
    mock_db = _MockDB(
        outcome_docs=[
            {
                "_id": "id1",
                "source": "courtlistener",
                "source_id": "101",
                "cluster_id": 101,
                "court_id": "fladistctapp_2",
                "court_name": "Florida 2nd DCA",
                "state": "FL",
                "case_name": "State v. Miller",
                "docket_number": "2D26-10",
                "date_filed": "2026-04-01",
                "disposition": "bond_forfeiture",
                "judges": "Judge Morris",
                "citation_count": 5,
                "bail_impact": {"score": 95, "factors": ["bail-critical disposition: bond_forfeiture"]},
                "ingested_at": "2026-04-02T00:00:00Z",
            },
            {
                "_id": "id2",
                "source": "courtlistener",
                "source_id": "102",
                "cluster_id": 102,
                "court_id": "gactapp",
                "court_name": "Georgia Court of Appeals",
                "state": "GA",
                "case_name": "State v. Brown",
                "docket_number": "A26-22",
                "date_filed": "2026-04-05",
                "disposition": "dismissed",
                "judges": "Judge Phipps",
                "citation_count": 1,
                "bail_impact": {"score": 40, "factors": ["favorable outcome: dismissed"]},
                "ingested_at": "2026-04-06T00:00:00Z",
            },
        ],
        arrest_docs=[
            {"_id": "arr1", "full_name": "Carlos Alvarez", "county": "Lee", "defendant_id": "def_1"},
        ],
    )

    class _FakeClient:
        api_token = "tok"

        def get_api_health(self):
            return {"requests_made": 1, "errors": 0, "rate_limited": 0, "in_maintenance": False}

        def parse_cluster_id_from_url(self, val):
            return CourtListenerClient.parse_cluster_id_from_url(val)

        async def live_search(self, **kwargs):
            return {
                "success": True,
                "search_type": kwargs.get("search_type", "o"),
                "count": 1,
                "returned": 1,
                "results": [{
                    "source": "courtlistener",
                    "source_id": "5001",
                    "cluster_id": 5001,
                    "case_name": "State v. Alvarez",
                    "court_id": "fladistctapp_2",
                    "court_name": "Florida 2nd DCA",
                    "state": "FL",
                    "docket_number": "2D26-500",
                    "date_filed": "2026-05-01",
                    "disposition": "fta",
                    "snippet": "Failure to appear after release on surety bond.",
                    "bail_impact": {"score": 90, "factors": ["bail-critical disposition: fta"]},
                }],
                "analytics": {"total": 1, "bail_critical_count": 1, "bail_critical_pct": 100.0, "avg_bail_impact": 90.0},
            }

        async def lookup_by_docket_number(self, **kwargs):
            return {
                "success": True,
                "docket_number": kwargs["docket_number"],
                "dockets": [{"id": 1, "docket_number": kwargs["docket_number"], "case_name": "State v. Sample"}],
                "clusters": [],
                "search_hits": [],
            }

        async def get_full_case_dossier(self, target):
            return {"success": False, "error": "upstream timeout"}

        async def close(self):
            return None

    app = FastAPI()
    app.include_router(court_intel_bp)

    with (
        patch("dashboard.routers.court_dockets.get_db", return_value=mock_db),
        patch("dashboard.routers.court_dockets._make_cl_client", return_value=_FakeClient()),
    ):
        client = TestClient(app)

        # 1. Analytics endpoint (unfiltered + state filtered)
        r_an = client.get("/api/court-intel/analytics")
        assert r_an.status_code == 200
        data_an = r_an.json()
        assert data_an["success"] is True
        assert data_an["kpi"]["total_outcomes"] == 2
        assert data_an["kpi"]["bail_critical_count"] == 1
        assert len(data_an["by_judge"]) == 2
        assert len(data_an["presets"]) >= 5

        r_fl = client.get("/api/court-intel/analytics?state=FL")
        assert r_fl.status_code == 200
        assert r_fl.json()["kpi"]["total_outcomes"] == 1

        # 2. Live search with preset
        r_ls = client.get("/api/court-intel/live-search?preset=bond_forfeiture&state=FL")
        assert r_ls.status_code == 200
        assert r_ls.json()["analytics"]["bail_critical_pct"] == 100.0

        # 3. Docket lookup
        r_dl = client.get("/api/court-intel/docket-lookup?docket_number=2D26-10")
        assert r_dl.status_code == 200
        assert r_dl.json()["dockets"][0]["docket_number"] == "2D26-10"

        # 4. Case dossier falls back cleanly to local corpus if live API times out
        r_dos = client.get("/api/court-intel/case-dossier?cluster_id=101")
        assert r_dos.status_code == 200
        dos_data = r_dos.json()
        assert dos_data["offline_fallback"] is True
        assert dos_data["dossier"]["case_name"] == "State v. Miller"

        # 5. Save case auto-matches defendant and persists into court_outcomes
        r_save = client.post(
            "/api/court-intel/save-case",
            json={
                "source_id": "5001",
                "cluster_id": 5001,
                "case_name": "State v. Carlos Alvarez",
                "court_id": "fladistctapp_2",
                "court_name": "Florida 2nd DCA",
                "state": "FL",
                "disposition": "fta",
                "snippet": "Failure to appear",
            },
        )
        assert r_save.status_code == 200
        saved = r_save.json()["case"]
        assert saved["matched_defendant_name"] == "Carlos Alvarez"
        assert saved["bail_impact"]["score"] == 100  # 90 + 20 capped at 100
