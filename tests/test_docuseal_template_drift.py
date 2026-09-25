"""
DocuSeal template drift guard (scripts/docuseal_template_drift_check.py).

No network: live fetches are replaced by synthetic template payloads.
The committed snapshot (tests/fixtures/docuseal_template_snapshot.json) holds
the code-side prefill keys (derived from the repo) and — once captured with a
valid read-only key — the live field names per template. Per-template field
lists are PENDING LIVE CAPTURE: the repo has no authoritative list and none is
fabricated here.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "docuseal_template_drift_check", ROOT / "scripts" / "docuseal_template_drift_check.py"
)
drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drift)  # type: ignore[union-attr]

FAKE_KEY = "test-key-never-printed-0123456789"


@pytest.fixture
def snapshot():
    return drift.load_snapshot()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("DOCUSEAL_TEMPLATE_ID", "DOCUSEAL_TEMPLATE_ID_OSI", "DOCUSEAL_TEMPLATE_ID_PALMETTO"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DOCUSEAL_API_KEY", FAKE_KEY)


# ── snapshot vs repo sources (code-side drift fails CI) ─────────────────────

def test_snapshot_hydration_keys_match_prefill_code(snapshot):
    keys = drift.extract_hydration_keys()
    assert len(keys) == len(set(keys)) > 200
    assert snapshot["hydration_keys"]["keys"] == keys, (
        "prefill_values_from_bond keys changed — check the DocuSeal templates, then run "
        "`python scripts/docuseal_template_drift_check.py --write-snapshot` (or update "
        "hydration_keys.keys from --print-hydration-keys)"
    )
    assert drift.offline_check(snapshot, keys, drift.code_roles()) == []


def test_snapshot_roles_match_code_role_constants(snapshot):
    from dashboard.services import docuseal_service as ds

    code = sorted({ds.ROLE_BONDSMAN, ds.ROLE_INDEMNITOR, ds.ROLE_CO_INDEMNITOR, ds.ROLE_DEFENDANT})
    assert drift.code_roles() == code
    assert snapshot["expected_roles"] == code


def test_snapshot_template_ids_osi_1_palmetto_5(snapshot):
    assert snapshot["templates"]["osi"]["template_id"] == 1
    assert snapshot["templates"]["palmetto"]["template_id"] == 5
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^DOCUSEAL_TEMPLATE_ID_OSI=1$", env_example, re.M)
    assert re.search(r"^DOCUSEAL_TEMPLATE_ID_PALMETTO=5$", env_example, re.M)


def test_ops_doc_no_longer_says_palmetto_is_template_3():
    doc = (ROOT / "docs" / "ops" / "DOCUSEAL_CONFIGURATION_VERIFICATION.md").read_text(encoding="utf-8")
    row = [l for l in doc.splitlines() if l.startswith("| `DOCUSEAL_TEMPLATE_ID_PALMETTO`")]
    assert row and "| `5` |" in row[0] and "/api/templates/5" in row[0]
    assert "(ID `3`)" not in doc


@pytest.mark.parametrize("label", ["osi", "palmetto"])
def test_template_field_snapshot(snapshot, label):
    tpl = snapshot["templates"][label]
    if tpl["status"] == drift.PENDING:
        # Honest placeholder: nothing fabricated while pending.
        assert tpl["fields"] is None and tpl["hydrated_fields"] is None
        pytest.skip(f"{label}: field snapshot pending live capture (run --write-snapshot with a valid key)")
    assert tpl["status"] == drift.CAPTURED
    assert tpl["fields"] == sorted(set(tpl["fields"]))
    assert set(tpl["hydrated_fields"]) <= set(tpl["fields"])
    assert set(tpl["hydrated_fields"]) <= set(drift.extract_hydration_keys())
    assert tpl["hydrated_fields"], "no field receives prefill"


def test_snapshot_is_sanitized():
    snap = drift.load_snapshot()
    templates = json.dumps(snap["templates"])
    assert "http" not in templates and "slug" not in templates and "default_value" not in templates
    allowed = {"template_id", "expected_name", "status", "captured_at", "fields", "hydrated_fields",
               "live_name_at_capture", "roles_at_capture"}
    for tpl in snap["templates"].values():
        assert set(tpl) <= allowed


def test_extractor_expands_loop_fstrings_and_rejects_unknown_dynamic():
    src = '''
class X:
    def prefill_values_from_bond(bond_data):
        values = {"a": 1, "b": 2}
        row = {}
        for i in range(1, 3):
            row[f"k_{i}"] = 1
        values.update({"c": 3})
        values["d"] = 4
        return values
'''
    assert drift.extract_hydration_keys(src) == ["a", "b", "c", "d", "k_1", "k_2"]
    bad = src.replace('row[f"k_{i}"] = 1', 'row[f"k_{bond_data}"] = 1')
    with pytest.raises(ValueError):
        drift.extract_hydration_keys(bad)


# ── live comparison logic (fetch mocked) ─────────────────────────────────────

def _live(name, roles=("bondsman", "indemnitor", "coindemnitor", "defendant"), fields=None, archived=None):
    return {
        "id": 1,
        "name": name,
        "archived_at": archived,
        "slug": "SECRETSLUG",
        "documents": [{"url": "https://sign.example.invalid/file/signed-token"}],
        "submitters": [{"name": r, "uuid": f"u-{r}"} for r in roles],
        "fields": [{"name": f, "type": "text", "default_value": "PII-VALUE"}
                   for f in (fields or ["defendant_name", "indemnitor_name", "Signature"])],
    }


def _fetcher(payloads):
    calls = []

    def fetch(tid):
        calls.append(tid)
        return payloads[tid]
    fetch.calls = calls
    return fetch


def _good_payloads():
    return {1: _live("shamrock-osi-paperwork-complete"), 5: _live("shamrock-palmetto-paperwork-complete")}


def test_live_pending_snapshot_is_unverified_exit_3(snapshot):
    fetch = _fetcher(_good_payloads())
    code, report = drift.run_live(snapshot, fetch=fetch)
    assert fetch.calls == [1, 5]
    assert code == drift.EXIT_UNVERIFIED
    assert all(not t["drift"] for t in report["templates"])


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda p: p[5].update(name="shamrock-palmetto-paperwork-complete BACKUP pre-tag"), "name is"),
        (lambda p: p[1].update(archived_at="2026-09-01T00:00:00Z"), "ARCHIVED"),
        (lambda p: p[5].update(submitters=[{"name": n} for n in ("bondsman", "Indemnitor", "coindemnitor", "defendant")]),
         "case differs only"),
        (lambda p: p[1].update(fields=[{"name": "Signature"}, {"name": "Initials"}]), "no template field matches"),
    ],
)
def test_live_drift_detected_exit_1(snapshot, mutate, needle):
    payloads = _good_payloads()
    mutate(payloads)
    code, report = drift.run_live(snapshot, fetch=_fetcher(payloads))
    assert code == drift.EXIT_DRIFT
    assert needle in drift.format_report(code, report)


def test_live_env_template_ids_override(snapshot, monkeypatch):
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "7")
    payloads = _good_payloads()
    payloads[7] = payloads.pop(5)
    fetch = _fetcher(payloads)
    drift.run_live(snapshot, fetch=fetch)
    assert fetch.calls == [1, 7]


def test_write_snapshot_then_field_rename_is_drift(snapshot, tmp_path):
    out = tmp_path / "snap.json"
    captured = drift.write_snapshot(snapshot, fetch=_fetcher(_good_payloads()), path=out)
    raw = out.read_text()
    for leak in ("PII-VALUE", "SECRETSLUG", "signed-token", FAKE_KEY):
        assert leak not in raw
    tpl = captured["templates"]["osi"]
    assert tpl["status"] == drift.CAPTURED and tpl["fields"] == ["Signature", "defendant_name", "indemnitor_name"]
    assert tpl["hydrated_fields"] == ["defendant_name", "indemnitor_name"]
    assert tpl["expected_name"] == "shamrock-osi-paperwork-complete"

    code, _ = drift.run_live(captured, fetch=_fetcher(_good_payloads()))
    assert code == drift.EXIT_OK

    renamed = _good_payloads()
    renamed[1]["fields"] = [{"name": "DefendantFullName"}, {"name": "indemnitor_name"}, {"name": "Signature"}]
    code, report = drift.run_live(captured, fetch=_fetcher(renamed))
    text = drift.format_report(code, report)
    assert code == drift.EXIT_DRIFT
    assert "fields removed/renamed since snapshot: ['defendant_name']" in text
    assert "no longer receive prefill: ['defendant_name']" in text


def test_fetch_error_exit_2_and_key_never_printed(snapshot, capsys, monkeypatch):
    class _Resp:
        status_code = 401
        text = '{"error":"Not authenticated"}'

    import httpx

    seen = {}

    def fake_get(url, headers=None, **_kw):
        seen["url"], seen["headers"] = url, headers
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setenv("DOCUSEAL_URL", "https://sign.example.invalid")
    rc = drift.main([])
    out = capsys.readouterr()
    assert rc == drift.EXIT_ERROR
    assert "HTTP 401" in out.out
    assert FAKE_KEY not in out.out + out.err
    assert seen["url"] == "https://sign.example.invalid/api/templates/5"
    assert seen["headers"]["X-Auth-Token"] == FAKE_KEY  # sent, never printed


def test_missing_api_key_is_error(snapshot, monkeypatch):
    monkeypatch.delenv("DOCUSEAL_API_KEY", raising=False)
    code, report = drift.run_live(snapshot)
    assert code == drift.EXIT_ERROR and "DOCUSEAL_API_KEY is not set" in report["errors"][0]


def test_offline_cli_ok(capsys):
    assert drift.main(["--offline"]) == drift.EXIT_OK
    assert "no drift" in capsys.readouterr().out
