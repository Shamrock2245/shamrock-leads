"""DocuSeal apply merges uncovered template fields instead of replacing them.

The fixture is tests/fixtures/docuseal/template_5.json. The live account
export attached for this rebuild was not on the agent filesystem, so this
file is the template 5 document list from that export (header, both FAQ
pages, master waiver, two SSA releases, and the four covered carrier PDFs)
with the live field objects the merge has to preserve or classify.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from dashboard.palmetto_docuseal_apply import PalmettoApplyError, plan_merge
from dashboard.palmetto_field_placement import fields_for

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "docuseal" / "template_5.json"
SCRIPT = ROOT / "scripts" / "apply_palmetto_docuseal_fields.py"

UNCOVERED_NAMES = {
    "HeaderAgency",
    "FaqCosignerAck",
    "FaqDefendantAck",
    "WaiverDefendant",
    "SsaReleaseDefendant",
    "SsaReleaseIndemnitor",
}


def _load():
    template = json.loads(FIXTURE.read_text(encoding="utf-8"))
    originals = {field["name"]: field for field in template["fields"]}
    return template, originals


def _script():
    spec = importlib.util.spec_from_file_location("apply_palmetto_docuseal_fields", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doc(plan, slug):
    return next(row for row in plan["documents"] if row["slug"] == slug)


def test_fixture_is_template_5_document_list():
    template, _originals = _load()
    assert template["id"] == 5
    assert template["name"] == "shamrock-palmetto-paperwork-complete"
    names = [row["name"] for row in template["schema"]]
    assert names == [
        "shamrock-paperwork-header",
        "faq-cosigners",
        "faq-defendants",
        "master-waiver",
        "indemnity-agreement-palmetto",
        "defendant-application-palmetto",
        "surety-terms-palmetto",
        "collateral-receipt-palmetto",
        "ssa-release",
        "ssa-release",
    ]
    assert [row["name"] for row in template["submitters"]] == [
        "bondsman",
        "indemnitor",
        "defendant",
        "coindemnitor",
    ]


def test_merge_keeps_uncovered_fields_byte_for_byte():
    template, originals = _load()
    before = {name: json.dumps(field) for name, field in originals.items() if name in UNCOVERED_NAMES}
    plan = plan_merge(template)
    payload_by_name = {field.get("name"): field for field in plan["fields"]}
    for name, raw in before.items():
        assert payload_by_name[name] is originals[name]
        assert json.dumps(payload_by_name[name]) == raw
    for row in plan["documents"]:
        if row["coverage"] == "keep":
            assert row["added"] == []
            assert row["moved"] == []
            assert row["removed"] == []
            assert row["kept"]


def test_merge_classifies_added_moved_removed_and_kept():
    template, _originals = _load()
    plan = plan_merge(template)
    application = _doc(plan, "defendant-application")
    assert application["coverage"] == "replace"
    assert application["kept"] == ["app_agent_name"]
    assert application["moved"] == ["app_defendant_name"]
    assert application["removed"] == ["app_removed_legacy"]
    spec_names = [field["name"] for field in fields_for("defendant-application")]
    assert application["added"] == [
        name for name in spec_names if name not in {"app_agent_name", "app_defendant_name"}
    ]

    indemnity = _doc(plan, "indemnity-agreement")
    assert indemnity["kept"] == ["ind_agent_name"]
    assert indemnity["moved"] == []
    assert indemnity["removed"] == []
    assert "ind_power_number" in indemnity["added"]

    collateral = _doc(plan, "collateral-receipt")
    assert collateral["moved"] == ["note_amount_numeric"]
    assert collateral["kept"] == []
    assert collateral["removed"] == []
    assert "note_amount_words" in collateral["added"]

    sheet = _doc(plan, "bail-bond-information-sheet-palmetto")
    assert sheet["removed"] == ["bbis_old_defendant"]
    assert "bbis_defendant" in sheet["added"]
    assert sheet["kept"] == []

    covered_names = {
        field["name"]
        for field in plan["fields"]
        if field.get("areas")
        and field["areas"][0]["attachment_uuid"] in {
            "att-application",
            "att-indemnity",
            "att-collateral",
            "att-704",
        }
    }
    assert "app_removed_legacy" not in covered_names
    assert "bbis_old_defendant" not in covered_names
    assert "app_agent_name" in covered_names
    kept_agent = next(field for field in plan["fields"] if field.get("name") == "app_agent_name")
    assert kept_agent["uuid"] == "live-app-agent"
    assert kept_agent["areas"][0]["page"] == 0
    assert kept_agent["submitter_uuid"] == "sub-bondsman"


def test_appearance_bond_is_not_written_onto_template_5():
    template, _originals = _load()
    plan = plan_merge(template)
    appearance = _doc(plan, "appearance-bond")
    assert appearance["coverage"] == "not_on_template"
    assert "chargestField1" in appearance["added"]
    assert "AgentField" in appearance["added"]
    written = {field.get("name") for field in plan["fields"]}
    assert "chargestField1" not in written
    assert "writtenPremiumAmountField" not in written
    assert "AgentField" not in written


def test_field_spanning_covered_and_uncovered_documents_fails_closed():
    template, _originals = _load()
    mixed = {
        "uuid": "mixed-1",
        "submitter_uuid": "sub-defendant",
        "name": "SpansTwoDocuments",
        "type": "text",
        "required": False,
        "areas": [
            {"page": 0, "attachment_uuid": "att-header", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.02},
            {"page": 0, "attachment_uuid": "att-application", "x": 0.1, "y": 0.2, "w": 0.2, "h": 0.02},
        ],
    }
    template["fields"].append(mixed)
    with pytest.raises(PalmettoApplyError, match="spans a covered document"):
        plan_merge(template)


def test_live_dry_run_lists_every_document_and_sends_nothing(monkeypatch, capsys):
    module = _script()

    def _explode(*_args, **_kwargs):
        raise AssertionError("dry-run sent a request")

    monkeypatch.setattr(module.urllib.request, "urlopen", _explode)
    code = module.main(["--live", str(FIXTURE)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry-run"
    assert payload["recommended"] == "clone"
    by_coverage = {}
    for row in payload["documents"]:
        for key in ("added", "moved", "removed", "kept"):
            assert isinstance(row[key], list)
        by_coverage.setdefault(row["coverage"], []).append(row["document"])
    assert "shamrock-paperwork-header" in by_coverage["keep"]
    assert by_coverage["keep"].count("ssa-release") == 2
    assert "defendant-application-palmetto" in by_coverage["replace"]
    assert "appearance-bond" in by_coverage["not_on_template"]


def test_clone_puts_the_clone_and_prints_its_id(monkeypatch, capsys):
    module = _script()
    template, originals = _load()
    clone = json.loads(json.dumps(template))
    clone["id"] = 99
    calls = []

    def _fake(method, url, token, body=None):
        calls.append((method, url, body))
        if method == "POST" and url.endswith("/templates/5/clone"):
            assert body["name"] == module.CLONE_NAME
            return {"id": 99, "name": body["name"]}
        if method == "GET" and url.endswith("/templates/99"):
            return clone
        if method == "PUT" and url.endswith("/templates/99"):
            return {"id": 99}
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setenv("DOCUSEAL_URL", "https://sign.example.invalid")
    monkeypatch.setenv("DOCUSEAL_API_KEY", "test-token")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "5")
    monkeypatch.setattr(module, "_request", _fake)
    code = module.main(["--clone"])
    assert code == 0
    methods = [(method, url) for method, url, _body in calls]
    assert methods == [
        ("POST", "https://sign.example.invalid/api/templates/5/clone"),
        ("GET", "https://sign.example.invalid/api/templates/99"),
        ("PUT", "https://sign.example.invalid/api/templates/99"),
    ]
    put_fields = calls[2][2]["fields"]
    header = next(field for field in put_fields if field["name"] == "HeaderAgency")
    assert header == originals["HeaderAgency"]
    assert "chargestField1" not in {field["name"] for field in put_fields}
    out = capsys.readouterr().out
    assert "clone_template_id=99" in out
    assert "source_template_id=5" in out
