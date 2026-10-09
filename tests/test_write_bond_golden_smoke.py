"""Offline Write Bond golden smoke for OSI and Palmetto.

One synthetic staff test case runs ``POST /api/paperwork/packet/finalize``
with ``STAFF_TEST_CASE_MODE`` enabled only inside this file. The real
packet builder, binding gate, and DocuSeal payload builder run. The HTTP
client is patched to raise, so no submission is created or sent.

Goldens live in ``tests/golden/`` and are keyed by field name. Each entry
is the value, readonly flag, and the submitter roles that receive that
field. Rewrite them only with ``WRITE_BOND_REGEN_GOLDEN=1`` (see
``scripts/regen_write_bond_goldens.py``). CI does not set that flag.

The identity refusal uses the gate Write Bond actually enforces:
``validate_docuseal_packet_binding`` treats an empty name or a placeholder
(``unknown``, ``test``, ``to be named``, and the rest of
``_PLACEHOLDER_PARTY_NAMES``) as not an identity. This path does not read
an ID-scan status, ``id_verified``, or a KYC flag.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token
from dashboard.services.docuseal_service import BOND_AGENTS, DocuSealService
from dashboard.services.staff_test_case import DEFAULT_SIGNER_EMAIL

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "tests" / "golden"
REGEN_ENV = "WRITE_BOND_REGEN_GOLDEN"
FROZEN = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
HOUSE_LICENSE = "P139768"
HOUSE_NAME = BOND_AGENTS[HOUSE_LICENSE]["agent_name"]

# Character-for-character charge text. Pipe-separated so a comma inside a
# statute citation is not a charge break. Apostrophe, em dash, and section
# sign are intentional.
CHARGE_1 = "Grand theft — occupant's dwelling (Fla. Stat. § 812.014(2)(c), 2nd degree)"
CHARGE_2 = "Burglary of a conveyance (Fla. Stat. § 810.02(3)(d), 3rd degree)"
CHARGES = f"{CHARGE_1} | {CHARGE_2}"

# Names the binding gate treats as non-identity. Staff test mode still
# forces the signer email, so the refusal is the name, not a missing inbox.
UNVERIFIED_NAMES = ("Unknown", "test")


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FROZEN.replace(tzinfo=None)
        return FROZEN.astimezone(tz)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "1")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "5")
    monkeypatch.setenv("DOCUSEAL_URL", "https://sign.example.invalid")
    monkeypatch.setenv("DOCUSEAL_API_KEY", "test-not-a-real-key")
    monkeypatch.delenv("STAFF_TEST_CASE_MODE", raising=False)
    # Do not clear WRITE_BOND_REGEN_GOLDEN. The regen script sets it, and
    # CI does not. Clearing it here would make the opt-in flag a no-op.
    monkeypatch.delenv("BOND_AGENT_NAME", raising=False)
    monkeypatch.delenv("BOND_AGENT_LICENSE", raising=False)
    # The prefill default is the office street. Goldens stay on a sample address.
    monkeypatch.setenv("BOND_AGENCY_ADDRESS", "1 Sample Street, Sample City, FL 00000")
    monkeypatch.setenv("BOND_AGENT_PHONE", "5550100000")
    monkeypatch.delenv("PAPERWORK_PUBLIC_URL", raising=False)
    monkeypatch.setattr(
        "dashboard.services.docuseal_service.datetime",
        _FrozenDateTime,
    )


def _cookie():
    token = _sign_token(
        email="office@example.invalid",
        role="god_admin",
        agent_name=None,
        license_number=None,
        is_admin=True,
    )
    return {COOKIE_NAME: token}


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)


class _Store:
    def __init__(self):
        self.docs = []
        self.finds = []
        self.inserts = []
        self.updates = []

    async def find_one(self, query, projection=None):
        self.finds.append(query)
        if isinstance(query, dict) and "packet_id" in query and "$or" not in query:
            for doc in self.docs:
                if doc.get("packet_id") == query["packet_id"]:
                    return doc
        return None

    def find(self, query, projection=None):
        self.finds.append(("find", query))
        return _Cursor([])

    async def insert_one(self, doc):
        self.inserts.append(doc)
        self.docs.append(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        self.updates.append((query, update))
        return type("R", (), {"matched_count": 1})()


def _stores():
    stores = {}
    touched = []

    def collections(name):
        touched.append(name)
        if name not in stores:
            stores[name] = _Store()
        return stores[name]

    return stores, touched, collections


def _client():
    from dashboard.routers.paperwork import paperwork_bp

    app = FastAPI()
    app.include_router(paperwork_bp)
    client = TestClient(app)
    client.cookies.update(_cookie())
    return client


def _http_guard(monkeypatch):
    """Any real DocuSeal HTTP method raises and is counted."""
    calls = []

    async def _blocked(self, method, url, *args, **kwargs):
        calls.append({"method": str(method).upper(), "url": str(url)})
        raise RuntimeError("DocuSeal network is blocked in this test")

    # AsyncClient only. TestClient uses the sync httpx.Client, and replacing
    # that method with a coroutine breaks the in-process request.
    monkeypatch.setattr(httpx.AsyncClient, "request", _blocked)
    return calls


def _service(captured):
    svc = DocuSealService(
        base_url="https://sign.example.invalid",
        api_key="test-not-a-real-key",
    )

    async def _request(self, method, path, *, json=None, params=None):
        captured.append({
            "method": str(method).upper(),
            "path": path,
            "json": json,
            "params": params,
        })
        # The HTTP client is still armed to raise. This returns a local
        # stub so the route can persist the test packet without a network call.
        if str(method).upper() == "POST" and str(path).rstrip("/").endswith("/submissions"):
            return [
                {
                    "id": 1,
                    "submission_id": 44,
                    "role": "bondsman",
                    "slug": "bondsman-stub",
                    "email": DEFAULT_SIGNER_EMAIL,
                },
                {
                    "id": 2,
                    "submission_id": 44,
                    "role": "indemnitor",
                    "slug": "indemnitor-stub",
                    "email": DEFAULT_SIGNER_EMAIL,
                },
                {
                    "id": 3,
                    "submission_id": 44,
                    "role": "defendant",
                    "slug": "defendant-stub",
                    "email": DEFAULT_SIGNER_EMAIL,
                },
            ]
        raise RuntimeError(f"Unexpected DocuSeal call {method} {path}")

    svc._request = _request.__get__(svc, DocuSealService)
    return svc


def _body(surety_id, *, indemnitor_name="Sample Party Two", suffix=""):
    tag = surety_id.upper() + suffix
    return {
        "test_case": True,
        "surety_id": surety_id,
        "booking_number": f"TEST-GOLDEN-{tag}",
        "case_number": f"TEST-CASE-{tag}",
        "packet_id": f"PKT-TEST-GOLDEN-{tag}",
        "defendant_name": "Sample Party One",
        "indemnitor_name": indemnitor_name,
        "bond_amount": 5000,
        "county": "Lee",
        "state": "FL",
        "charges": CHARGES,
        "send_email": True,
        "send_sms": True,
    }


def _post(monkeypatch, body):
    captured = []
    http_calls = _http_guard(monkeypatch)
    svc = _service(captured)
    stores, touched, collections = _stores()
    delivery = AsyncMock()
    payment = AsyncMock()
    ensure = AsyncMock()
    client = _client()
    with patch(
        "dashboard.services.packet_builder_service.resolve_client_esign_provider",
        new=AsyncMock(return_value="docuseal"),
    ), patch(
        "dashboard.services.staff_chain_service.ensure_match_bondcase",
        new=ensure,
    ), patch(
        "dashboard.routers.paperwork.get_collection",
        side_effect=collections,
    ), patch(
        "dashboard.extensions.get_collection",
        side_effect=collections,
    ), patch(
        "dashboard.services.docuseal_service.get_docuseal_service",
        return_value=svc,
    ), patch(
        "dashboard.services.docuseal_initial_delivery.deliver_initial_docuseal_links",
        new=delivery,
    ), patch(
        "dashboard.routers.paperwork._finalize_auto_payment_link",
        new=payment,
    ):
        response = client.post("/api/paperwork/packet/finalize", json=body)
    return {
        "response": response,
        "captured": captured,
        "http_calls": http_calls,
        "stores": stores,
        "touched": touched,
        "delivery": delivery,
        "payment": payment,
        "ensure": ensure,
    }


def _payload(captured):
    posts = [
        row for row in captured
        if row["method"] in {"POST", "PUT", "DELETE", "PATCH"}
    ]
    assert len(posts) == 1, posts
    assert posts[0]["method"] == "POST"
    assert posts[0]["path"].rstrip("/").endswith("/submissions")
    body = posts[0]["json"]
    assert isinstance(body, dict)
    return body


def _assert_no_network(result):
    mutating = [
        row for row in result["http_calls"]
        if row["method"] in {"POST", "PUT", "DELETE", "PATCH"}
    ]
    assert mutating == [], mutating
    assert result["http_calls"] == []
    assert "poa_inventory" not in result["touched"]


def _assert_send_flags(payload):
    assert payload["send_email"] is False
    assert payload["send_sms"] is False
    submitters = payload["submitters"]
    assert submitters
    for submitter in submitters:
        assert submitter["email"] == DEFAULT_SIGNER_EMAIL
        assert submitter["send_email"] is False
        assert submitter["send_sms"] is False
        assert not submitter.get("phone")


def _assert_test_poa(payload):
    blob = json.dumps(payload, ensure_ascii=False)
    assert "TEST-POA-0001" in blob
    for submitter in payload["submitters"]:
        values = submitter.get("values") or {}
        for key, value in values.items():
            if "poa" in key.lower() or key in ("PowerNum", "BondNumbers", "bond_numbers"):
                text = str(value)
                assert text.startswith("TEST-"), (key, text)


def _field_map(payload):
    """One entry per field name. Roles must agree on value and readonly."""
    by_name = {}
    for submitter in payload["submitters"]:
        role = str(submitter.get("role") or "")
        assert role
        for field in submitter.get("fields") or []:
            name = str(field.get("name") or "")
            assert name, field
            entry = {
                "value": field.get("default_value"),
                "readonly": bool(field.get("readonly")),
                "submitter_role": role,
            }
            previous = by_name.get(name)
            if previous is None:
                by_name[name] = {
                    "value": entry["value"],
                    "readonly": entry["readonly"],
                    "submitter_role": [role],
                }
                continue
            if previous["value"] != entry["value"] or previous["readonly"] != entry["readonly"]:
                raise AssertionError(
                    f"changed: {name}\n"
                    f"  value: {previous['value']!r} -> {entry['value']!r}\n"
                    f"  readonly: {previous['readonly']!r} -> {entry['readonly']!r}\n"
                    f"  submitter_role: {previous['submitter_role']} vs {role}"
                )
            if role not in previous["submitter_role"]:
                previous["submitter_role"].append(role)
    for entry in by_name.values():
        entry["submitter_role"] = sorted(entry["submitter_role"])
    return by_name


def _diff(expected, actual):
    lines = []
    for name in sorted(set(expected) | set(actual)):
        if name not in actual:
            lines.append(f"missing: {name}")
            continue
        if name not in expected:
            lines.append(f"extra: {name}")
            continue
        if expected[name] != actual[name]:
            lines.append(f"changed: {name}")
            for key in ("value", "readonly", "submitter_role"):
                left = expected[name].get(key)
                right = actual[name].get(key)
                if left != right:
                    lines.append(f"  {key}: {left!r} -> {right!r}")
    return "\n".join(lines)


def _round_trip(fields):
    return json.loads(json.dumps(fields, ensure_ascii=False))


def _golden_path(surety_id):
    return GOLDEN_DIR / f"write_bond_{surety_id}.json"


def _load_golden(surety_id):
    path = _golden_path(surety_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    fields = {key: value for key, value in payload.items() if not key.startswith("_")}
    return payload.get("_meta") or {}, fields


def _write_golden(surety_id, template_id, fields):
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    document = {
        "_meta": {
            "surety_id": surety_id,
            "template_id": template_id,
            "frozen_at": FROZEN.isoformat(),
            "note": (
                "Opt-in golden. Rewrite only with WRITE_BOND_REGEN_GOLDEN=1 "
                "via scripts/regen_write_bond_goldens.py. CI must not set that flag."
            ),
        },
    }
    document.update(fields)
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _golden_path(surety_id).write_text(text, encoding="utf-8")


@pytest.mark.parametrize("surety_id,template_id", [("osi", 1), ("palmetto", 5)])
def test_write_bond_golden_fields(monkeypatch, surety_id, template_id):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    result = _post(monkeypatch, _body(surety_id))
    response = result["response"]

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["is_test"] is True
    assert body["real_power_consumed"] is False

    _assert_no_network(result)
    payload = _payload(result["captured"])
    assert payload["template_id"] == template_id
    _assert_send_flags(payload)
    _assert_test_poa(payload)

    fields = _round_trip(_field_map(payload))
    joined = f"{CHARGE_1}, {CHARGE_2}"
    # charge_N and charges are not fields on template 1 or template 5.
    assert "charge_1" not in fields
    assert "charge_2" not in fields
    assert "charges" not in fields
    assert "statute_1" not in fields
    assert "charge_line_2" not in fields
    assert fields["charges_summary"]["value"] == joined
    if surety_id == "palmetto":
        # Template 5 has no offense grid. The 140-character join fits the
        # 200-character render-proven cap.
        assert "offense_1" not in fields
        assert "offense_2" not in fields
        assert "§" in fields["charges_summary"]["value"]
        assert "—" in fields["charges_summary"]["value"]
        assert "'" in fields["charges_summary"]["value"]
    else:
        assert fields["offense_1"]["value"] == CHARGE_1
        assert fields["offense_2"]["value"] == CHARGE_2
        assert "§" in fields["offense_1"]["value"]
        assert "—" in fields["offense_1"]["value"]
        assert "'" in fields["offense_1"]["value"]

    assert fields["agent_name"]["value"] == HOUSE_NAME
    assert fields["agent_license"]["value"] == HOUSE_LICENSE
    assert fields["AgentName"]["value"] == HOUSE_NAME
    assert fields["AgentLicense"]["value"] == HOUSE_LICENSE
    assert BOND_AGENTS[HOUSE_LICENSE]["agent_name"] == HOUSE_NAME
    bondsman = next(row for row in payload["submitters"] if row["role"] == "bondsman")
    assert bondsman["name"] == HOUSE_NAME

    audits = result["stores"]["audit_events"].inserts
    assert audits
    for row in audits:
        assert row["is_test"] is True
        assert row["test_case"] is True
    packets = result["stores"]["paperwork_packets"].inserts
    assert packets
    for packet in packets:
        assert packet["is_test"] is True
        assert str(packet.get("poa_number") or "").startswith("TEST-")
    assert result["delivery"].await_count == 0
    assert result["payment"].await_count == 0
    assert result["ensure"].await_count == 0

    if os.environ.get(REGEN_ENV) == "1":
        _write_golden(surety_id, template_id, fields)

    meta, expected = _load_golden(surety_id)
    assert meta.get("template_id") == template_id
    assert meta.get("surety_id") == surety_id
    diff = _diff(expected, fields)
    assert not diff, diff


@pytest.mark.parametrize("surety_id", ["osi", "palmetto"])
@pytest.mark.parametrize("indemnitor_name", UNVERIFIED_NAMES)
def test_unverified_indemnitor_refuses_before_payload(monkeypatch, surety_id, indemnitor_name):
    """No identity, as the binding gate defines it, builds nothing.

    ``Unknown`` and ``test`` are placeholder names. A separate ID-scan
    failure flag is not consulted on this route.
    """
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    result = _post(
        monkeypatch,
        _body(surety_id, indemnitor_name=indemnitor_name, suffix="-NOID"),
    )
    response = result["response"]
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "docuseal_packet_binding_invalid"
    assert "indemnitor" in body["message"].lower()
    assert result["captured"] == []
    _assert_no_network(result)
    assert result["stores"].get("paperwork_packets") is None or (
        result["stores"]["paperwork_packets"].inserts == []
    )
    assert result["stores"].get("audit_events") is None or (
        result["stores"]["audit_events"].inserts == []
    )
    assert result["delivery"].await_count == 0
    assert result["payment"].await_count == 0
    assert result["ensure"].await_count == 0
