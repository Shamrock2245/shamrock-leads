"""Writing-agent name and license stay one pair on every live packet path.

No DocuSeal network. The house pair is the BOND_AGENTS row, which is what
main printed when the packet carried no agent.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token
from dashboard.bond_pdf_service import (
    AGENT_LICENSE,
    AGENT_NAME,
    build_osi_field_values,
    build_palmetto_field_values,
    writing_agent_license,
    writing_agent_name,
)
from dashboard.palmetto_packet_fill import build_palmetto_context
from dashboard.routers.bonds import _attach_session_writing_agent, _build_appearance_bond_data
from dashboard.services.docuseal_service import (
    BOND_AGENTS,
    DocuSealService,
    _pair_from_agent_source,
    apply_writing_agent,
    build_bond_data_from_dashboard,
    house_default_agent,
    resolve_writing_agent,
)
from dashboard.services.bond_packet_start import start_indemnitor_bond_packet

HOUSE_LICENSE = "P139768"
HOUSE_NAME = BOND_AGENTS[HOUSE_LICENSE]["agent_name"]
SAMPLE = "Brendan ONeal"
AGENTS = (
    ("Brendan O'Neal", "P139768"),
    ("Kayla Lukesic", "G356764"),
    ("Jason Taylor", "W214323"),
)


@pytest.fixture(autouse=True)
def _assume_verified_indemnitor(monkeypatch):
    """These tests cover the writing-agent pair. Identity is a separate gate."""

    async def _ok(*_args, **_kwargs):
        return {"ok": True, "exempt": "test_fixture", "issues": [], "methods": ["scan"]}

    monkeypatch.setattr(
        "dashboard.services.identity_verification_service.require_verified_indemnitors",
        _ok,
    )


@pytest.fixture(autouse=True)
def _clear_house_env(monkeypatch):
    monkeypatch.delenv("BOND_AGENT_NAME", raising=False)
    monkeypatch.delenv("BOND_AGENT_LICENSE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "1")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "5")


def _house():
    assert house_default_agent() == (HOUSE_NAME, HOUSE_LICENSE)
    assert HOUSE_NAME == "Brendan O'Neal"
    assert SAMPLE not in HOUSE_NAME
    return HOUSE_NAME, HOUSE_LICENSE


def _no_sample(payload) -> None:
    blob = json.dumps(payload, default=str)
    assert SAMPLE not in blob


def _session(role, name="", license_no=""):
    return {
        "role": role,
        "agent_name": name,
        "license_number": license_no,
        "email": "office@example.invalid",
    }


def _cookie(role, name="", license_no=""):
    token = _sign_token(
        email="office@example.invalid",
        role=role,
        agent_name=name or None,
        license_number=license_no or None,
        is_admin=role == "god_admin",
    )
    return {COOKIE_NAME: token}


class _Request:
    def __init__(self, cookies):
        self.cookies = cookies


def _bound(**overrides):
    data = {
        "bond_case_id": "BC-1",
        "match_id": "M-1",
        "defendant_id": "D-1",
        "indemnitor_id": "I-1",
        "case_number": "26CF1",
        "poa_number": "POA1",
        "booking_number": "B1",
        "match_status": "validated",
        "surety_id": "osi",
        "bond_amount": 5000,
        "county": "Lee",
        "defendant_name": "Jordan Lee",
        "indemnitor_name": "Alex Rivera",
        "indemnitor_email": "alex@example.invalid",
        "indemnitor": {"name": "Alex Rivera", "email": "alex@example.invalid"},
        "defendant": {"name": "Jordan Lee", "email": "jordan@example.invalid"},
        "indemnitors": [{"name": "Alex Rivera", "email": "alex@example.invalid"}],
    }
    data.update(overrides)
    return data


def _prefill(data):
    return DocuSealService(
        base_url="https://sign.example.invalid",
        api_key="test-not-a-real-key",
    ).prefill_values_from_bond(data)


def _bondsman(submitters):
    for row in submitters:
        if row.get("role") == "bondsman":
            return row
    raise AssertionError("bondsman submitter missing")


@pytest.mark.asyncio
async def _submit(bond_data):
    svc = DocuSealService(
        base_url="https://sign.example.invalid",
        api_key="test-not-a-real-key",
    )
    captured = {}

    async def _create(self, **kwargs):
        captured["kwargs"] = kwargs
        return [{
            "id": 1,
            "submission_id": 9,
            "role": "bondsman",
            "slug": "bond",
            "email": "admin@shamrockbailbonds.biz",
            "name": "captured",
        }]

    svc.create_submission = _create.__get__(svc, DocuSealService)
    await svc.create_submission_for_packet(
        template_id=1,
        packet_id="PKT-AGENT-1",
        bond_data=bond_data,
        indemnitors=bond_data.get("indemnitors"),
        defendant=bond_data.get("defendant"),
        send_email=False,
        include_defendant=True,
        skip_bond_binding=bond_data.get("shannon_voice", False),
    )
    return captured["kwargs"]


def test_house_default_reads_the_registry_row():
    _house()
    assert house_default_agent(tenant="shamrock") == (HOUSE_NAME, HOUSE_LICENSE)
    assert house_default_agent(tenant="later-tenant") == (HOUSE_NAME, HOUSE_LICENSE)


def test_env_pair_overrides_only_when_both_are_set(monkeypatch):
    monkeypatch.setenv("BOND_AGENT_NAME", "Pat Example")
    monkeypatch.setenv("BOND_AGENT_LICENSE", "X100000")
    assert house_default_agent() == ("Pat Example", "X100000")
    values = _prefill(_bound())
    assert values["agent_name"] == "Pat Example"
    assert values["bondsman_name"] == "Pat Example"
    assert values["agent_license"] == "X100000"
    assert values["bondsman_license"] == "X100000"
    _no_sample(values)

    monkeypatch.delenv("BOND_AGENT_LICENSE")
    assert house_default_agent() == (HOUSE_NAME, HOUSE_LICENSE)
    monkeypatch.setenv("BOND_AGENT_LICENSE", "X100000")
    monkeypatch.delenv("BOND_AGENT_NAME")
    assert house_default_agent() == (HOUSE_NAME, HOUSE_LICENSE)


def test_half_set_env_does_not_mix_with_the_house_license(monkeypatch):
    monkeypatch.setenv("BOND_AGENT_NAME", "Pat Example")
    name, license_no = resolve_writing_agent({})
    assert (name, license_no) == (HOUSE_NAME, HOUSE_LICENSE)
    assert name != "Pat Example"


def test_explicit_registry_agent_beats_env(monkeypatch):
    monkeypatch.setenv("BOND_AGENT_NAME", "Pat Example")
    monkeypatch.setenv("BOND_AGENT_LICENSE", "X100000")
    name, license_no = resolve_writing_agent({"agent_name": "Kayla Lukesic"})
    assert (name, license_no) == ("Kayla Lukesic", "G356764")


@pytest.mark.parametrize(
    "label",
    ["Master Admin", "master admin", "dashboard", "staff", "staff_direct", "unknown", SAMPLE],
)
def test_filtered_label_falls_through_to_house_default(label):
    name, license_no = resolve_writing_agent({"agent_name": label, "bondsman_name": label})
    assert (name, license_no) == (HOUSE_NAME, HOUSE_LICENSE)
    assert label != name or label == HOUSE_NAME
    assert SAMPLE not in name


def test_sub_agent_sessions_use_their_own_entry():
    kayla = resolve_writing_agent(session=_session("sub_agent", "Kayla Lukesic", "G356764"))
    jason = resolve_writing_agent(session=_session("sub_agent", "Jason Taylor", "W214323"))
    assert kayla == ("Kayla Lukesic", "G356764")
    assert jason == ("Jason Taylor", "W214323")
    by_license = resolve_writing_agent(session=_session("sub_agent", "", "G356764"))
    assert by_license == ("Kayla Lukesic", "G356764")


def test_pin_admin_and_machine_paths_use_the_house_pair():
    admin = resolve_writing_agent(session=_session("god_admin"))
    machine = resolve_writing_agent()
    assert admin == machine == (HOUSE_NAME, HOUSE_LICENSE)


def test_shannon_machine_prefill_and_submitter_match_main():
    bond = _bound(shannon_voice=True, include_bondsman=True)
    for key in (
        "agent_name", "bondsman_name", "writing_agent_name", "writing_agent",
        "agent_license", "bondsman_license", "license_number",
    ):
        bond.pop(key, None)
    values = _prefill(bond)
    assert values["agent_name"] == HOUSE_NAME
    assert values["bondsman_name"] == HOUSE_NAME
    assert values["AgentName"] == HOUSE_NAME
    assert values["agent_license"] == HOUSE_LICENSE
    assert values["bondsman_license"] == HOUSE_LICENSE
    assert values["AgentLicense"] == HOUSE_LICENSE
    _no_sample(values)


@pytest.mark.asyncio
async def test_shannon_submitter_name_is_the_house_agent():
    bond = _bound(shannon_voice=True, include_bondsman=True)
    kwargs = await _submit(bond)
    bondsman = _bondsman(kwargs["submitters"])
    assert bondsman["name"] == HOUSE_NAME
    assert bondsman["name"]
    _no_sample(kwargs["submitters"])


def test_pin_admin_finalize_shape_matches_main():
    bond = build_bond_data_from_dashboard(
        ctx={
            "defendant": {"name": "Jordan Lee"},
            "indemnitor": {"name": "Alex Rivera", "email": "alex@example.invalid"},
            "county": "Lee",
            "bond_amount": 5000,
            "match_status": "validated",
            "bond_case_id": "BC-1",
            "match_id": "M-1",
            "defendant_id": "D-1",
            "indemnitor_id": "I-1",
            "case_number": "26CF1",
            "poa_number": "POA1",
            "booking_number": "B1",
        },
        intake_doc={},
        body={},
        session=_session("god_admin"),
    )
    values = _prefill(bond)
    assert bond["bondsman_name"] == HOUSE_NAME
    assert bond["bondsman_license"] == HOUSE_LICENSE
    assert values["agent_name"] == HOUSE_NAME
    assert values["bondsman_name"] == HOUSE_NAME
    assert values["agent_license"] == HOUSE_LICENSE
    _no_sample(values)


@pytest.mark.asyncio
async def test_finalize_start_submitter_matches_main():
    built = build_bond_data_from_dashboard(
        ctx=_bound(),
        body={},
        session=_session("god_admin"),
    )
    svc = DocuSealService(
        base_url="https://sign.example.invalid",
        api_key="test-not-a-real-key",
    )
    captured = {}

    async def _create(self, **kwargs):
        captured["kwargs"] = kwargs
        return [{"id": 1, "submission_id": 9, "role": "bondsman", "slug": "b", "email": "a@example.invalid"}]

    svc.create_submission = _create.__get__(svc, DocuSealService)
    await start_indemnitor_bond_packet(
        packet_id="PKT-FIN-1",
        surety_id="osi",
        bond_data=built,
        session=_session("god_admin"),
        poa_record={"max_bond_value": 25000},
        docuseal=svc,
    )
    bondsman = _bondsman(captured["kwargs"]["submitters"])
    assert bondsman["name"] == HOUSE_NAME
    values = bondsman["values"]
    assert values["agent_name"] == HOUSE_NAME
    assert values["bondsman_name"] == HOUSE_NAME
    assert values["agent_license"] == HOUSE_LICENSE
    assert values["bondsman_license"] == HOUSE_LICENSE
    _no_sample(values)


def test_kayla_and_jason_logins_keep_their_own_pair():
    for name, license_no in (("Kayla Lukesic", "G356764"), ("Jason Taylor", "W214323")):
        bond = build_bond_data_from_dashboard(
            ctx=_bound(),
            body={},
            session=_session("sub_agent", name, license_no),
        )
        values = _prefill(bond)
        assert bond["bondsman_name"] == name
        assert bond["bondsman_license"] == license_no
        assert values["agent_name"] == name
        assert values["agent_license"] == license_no
        assert values["bondsman_license"] == license_no
        _no_sample(values)


@pytest.mark.parametrize("name,own", AGENTS)
@pytest.mark.parametrize("other_name,other_license", AGENTS)
def test_no_path_prints_a_name_with_another_agents_license(name, own, other_name, other_license):
    if other_license == own:
        return
    payload = {"agent_name": name, "agent_license": other_license, "bondsman_license": other_license}
    resolved = resolve_writing_agent(payload)
    assert resolved == (name, own)
    values = _prefill(payload)
    assert values["agent_name"] == name
    assert values["bondsman_name"] == name
    assert values["agent_license"] == own
    assert values["bondsman_license"] == own
    built = build_bond_data_from_dashboard(ctx=_bound(), body=payload)
    assert built["bondsman_name"] == name
    assert built["bondsman_license"] == own
    recipe = build_palmetto_field_values({"name": "SAMPLE", "bond_amount": 1000, **payload})[0]
    assert recipe["AgentField"] == name
    assert recipe["agentBailLicNumField"] == own
    ctx = build_palmetto_context({"defendant_name": "SAMPLE", **payload})
    assert ctx["agent_name"] == name
    assert ctx["agent_license"] == own
    _no_sample({"resolved": resolved, "values": values, "recipe": recipe, "ctx": ctx})


def test_palmetto_print_does_not_borrow_the_house_license():
    recipe = build_palmetto_field_values({
        "name": "SAMPLE",
        "bond_amount": 1000,
        "agent_name": "Kayla Lukesic",
    })[0]
    assert recipe["AgentField"] == "Kayla Lukesic"
    assert recipe["agentBailLicNumField"] == "G356764"
    blank = build_palmetto_field_values({"name": "SAMPLE", "bond_amount": 1000})[0]
    assert blank["AgentField"] == HOUSE_NAME
    assert blank["agentBailLicNumField"] == HOUSE_LICENSE
    # A registered license with no name prints that holder's pair.
    cleared = build_palmetto_field_values({
        "name": "SAMPLE",
        "bond_amount": 1000,
        "agent_license": "G356764",
    })[0]
    assert cleared["AgentField"] == "Kayla Lukesic"
    assert cleared["agentBailLicNumField"] == "G356764"
    assert SAMPLE not in cleared["AgentField"]


def test_attach_session_uses_house_or_the_sub_agent():
    admin = _attach_session_writing_agent(_Request(_cookie("god_admin")), {"county": "Lee"})
    assert admin["agent_name"] == HOUSE_NAME
    assert admin["writing_agent_license"] == HOUSE_LICENSE
    kayla = _attach_session_writing_agent(
        _Request(_cookie("sub_agent", "Kayla Lukesic", "G356764")),
        {"county": "Lee"},
    )
    assert kayla["agent_name"] == "Kayla Lukesic"
    assert kayla["agent_license"] == "G356764"
    labeled = _attach_session_writing_agent(
        _Request(_cookie("god_admin")),
        {"agent_name": "Master Admin"},
    )
    assert labeled["agent_name"] == HOUSE_NAME
    assert labeled["agent_license"] == HOUSE_LICENSE
    _no_sample(admin)
    _no_sample(kayla)


@pytest.mark.parametrize(
    "license_no,owner_name",
    (("G356764", "Kayla Lukesic"), ("W214323", "Jason Taylor")),
)
def test_license_only_record_prints_the_registered_holder(license_no, owner_name):
    payload = {"agent_license": license_no, "county": "Lee"}
    recipe = build_palmetto_field_values({"name": "SAMPLE", "bond_amount": 1000, **payload})[0]
    assert recipe["AgentField"] == owner_name
    assert recipe["agentBailLicNumField"] == license_no
    printed = _attach_session_writing_agent(_Request(None), payload)
    assert printed["agent_name"] == owner_name
    assert printed["writing_agent_name"] == owner_name
    assert printed["agent_license"] == license_no
    assert printed["bondsman_license"] == license_no
    ctx = build_palmetto_context({"defendant_name": "SAMPLE", **payload})
    assert ctx["agent_name"] == owner_name
    assert ctx["agent_license"] == license_no
    assert SAMPLE not in recipe["AgentField"]
    assert HOUSE_NAME not in recipe["AgentField"] or owner_name == HOUSE_NAME


@pytest.mark.parametrize("label", ("Shamrock Bail Bonds", "Master Admin"))
def test_filtered_label_does_not_hide_the_next_name(label):
    payload = {"writing_agent_name": label, "agent_name": "Kayla Lukesic"}
    assert writing_agent_name(payload) == "Kayla Lukesic"
    assert resolve_writing_agent(payload) == ("Kayla Lukesic", "G356764")
    recipe = build_palmetto_field_values({"name": "SAMPLE", "bond_amount": 1000, **payload})[0]
    assert recipe["AgentField"] == "Kayla Lukesic"
    assert recipe["agentBailLicNumField"] == "G356764"
    ctx = build_palmetto_context({"defendant_name": "SAMPLE", **payload})
    assert ctx["agent_name"] == "Kayla Lukesic"
    assert ctx["agent_license"] == "G356764"
    printed = _attach_session_writing_agent(_Request(None), payload)
    assert printed["agent_name"] == "Kayla Lukesic"
    assert printed["agent_license"] == "G356764"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        {"agent_license": "X100000"},
        {"writing_agent_name": "Master Admin", "agent_license": "X100000"},
    ),
)
@pytest.mark.parametrize(
    "kind",
    ("machine", "pin_admin", "kayla"),
)
async def test_unregistered_license_without_a_name_uses_session_or_house(payload, kind):
    """An unknown license is dropped. Session, then the house pair, fills the agent."""
    if kind == "kayla":
        session = _session("sub_agent", "Kayla Lukesic", "G356764")
        cookies = _cookie("sub_agent", "Kayla Lukesic", "G356764")
        name, license_no = "Kayla Lukesic", "G356764"
    elif kind == "pin_admin":
        session = _session("god_admin")
        cookies = _cookie("god_admin")
        name, license_no = HOUSE_NAME, HOUSE_LICENSE
    else:
        session = None
        cookies = None
        name, license_no = HOUSE_NAME, HOUSE_LICENSE

    assert resolve_writing_agent(payload, session=session) == (name, license_no)
    built = build_bond_data_from_dashboard(ctx=_bound(), body=payload, session=session)
    assert built["bondsman_name"] == name
    assert built["bondsman_license"] == license_no
    values = _prefill(built)
    assert values["agent_name"] == name
    assert values["bondsman_name"] == name
    assert values["agent_license"] == license_no
    assert values["bondsman_license"] == license_no
    bond = _bound(**payload, shannon_voice=True)
    for key in (
        "agent_name", "bondsman_name", "writing_agent_name", "writing_agent",
        "agent_license", "bondsman_license", "writing_agent_license", "license_number",
    ):
        bond[key] = built[key]
    if kind == "machine":
        bond = _bound(**payload, shannon_voice=True)
    submitted = await _submit(bond)
    bondsman = _bondsman(submitted["submitters"])
    assert bondsman["name"] == name
    assert bondsman["values"]["agent_name"] == name
    assert bondsman["values"]["agent_license"] == license_no
    printed = _attach_session_writing_agent(_Request(cookies), dict(payload))
    assert printed["agent_name"] == name
    assert printed["agent_license"] == license_no
    recipe = build_palmetto_field_values({"name": "SAMPLE", "bond_amount": 1000, **printed})[0]
    assert recipe["AgentField"] == name
    assert recipe["agentBailLicNumField"] == license_no
    ctx = build_palmetto_context({"defendant_name": "SAMPLE", **printed})
    assert ctx["agent_name"] == name
    assert ctx["agent_license"] == license_no
    for blob in (
        resolved_blob := f"{name} {license_no}",
        bondsman["name"],
        bondsman["values"]["agent_name"],
        bondsman["values"]["agent_license"],
        recipe["AgentField"],
        recipe["agentBailLicNumField"],
        ctx["agent_name"],
        ctx["agent_license"],
        values["agent_name"],
        values["agent_license"],
    ):
        assert blob
        assert "X100000" not in str(blob)
    assert "X100000" not in resolved_blob


def test_osi_appearance_output_is_unchanged():
    values = build_osi_field_values({
        "name": "SAMPLE, NOT A PERSON",
        "bond_amount": 1000,
        "charge": "SAMPLE CHARGE ONLY",
        "agent_name": "Kayla Lukesic",
        "agent_license": "G356764",
    })[0]
    assert values["BondAgentName"] == AGENT_NAME
    assert values["BondAgentLicenseNum"] == AGENT_LICENSE


def test_explicit_non_registry_name_on_prefill_is_kept():
    """An unregistered license keeps the name already stored on the bond."""
    values = _prefill({
        "writing_agent_name": "FAKE AGENT RIVERA",
        "writing_agent_license": "X100000",
        "defendant_name": "SAMPLE NOT A PERSON",
        "county": "Lee",
    })
    assert values["agent_name"] == "FAKE AGENT RIVERA"
    assert values["agent_license"] == "X100000"


def _assert_printed_pair(name, license_no, expected_name, expected_license):
    """One agent entry. A name never prints next to a blank or another license."""
    assert name
    assert license_no
    assert (name, license_no) == (expected_name, expected_license)
    for reg_license, entry in BOND_AGENTS.items():
        reg_name = str(entry.get("agent_name") or "")
        if name == reg_name:
            assert license_no == reg_license
        if str(license_no).upper() == reg_license:
            assert name == reg_name


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("machine", "pin_admin", "kayla"))
async def test_unregistered_name_without_a_license_uses_session_or_house(kind):
    """'Kayla L' with no license is not a pair. Session, then house, fills it."""
    payload = {"agent_name": "Kayla L", "writing_agent_name": "Kayla L"}
    assert _pair_from_agent_source(payload) is None
    assert writing_agent_name(payload) == "Kayla L"
    assert writing_agent_license(payload) == ""
    if kind == "kayla":
        session = _session("sub_agent", "Kayla Lukesic", "G356764")
        cookies = _cookie("sub_agent", "Kayla Lukesic", "G356764")
        name, license_no = "Kayla Lukesic", "G356764"
    elif kind == "pin_admin":
        session = _session("god_admin")
        cookies = _cookie("god_admin")
        name, license_no = HOUSE_NAME, HOUSE_LICENSE
    else:
        session = None
        cookies = None
        name, license_no = HOUSE_NAME, HOUSE_LICENSE

    _assert_printed_pair(*resolve_writing_agent(payload, session=session), name, license_no)
    built = build_bond_data_from_dashboard(ctx=_bound(), body=payload, session=session)
    _assert_printed_pair(built["bondsman_name"], built["bondsman_license"], name, license_no)
    values = _prefill(built)
    _assert_printed_pair(values["agent_name"], values["agent_license"], name, license_no)
    _assert_printed_pair(values["bondsman_name"], values["bondsman_license"], name, license_no)
    assert values["agent_name"] != "Kayla L"
    bond = _bound(**payload, shannon_voice=True)
    for key in (
        "agent_name", "bondsman_name", "writing_agent_name", "writing_agent",
        "agent_license", "bondsman_license", "writing_agent_license", "license_number",
    ):
        bond[key] = built[key]
    if kind == "machine":
        bond = _bound(**payload, shannon_voice=True)
    submitted = await _submit(bond)
    bondsman = _bondsman(submitted["submitters"])
    _assert_printed_pair(bondsman["name"], bondsman["values"]["agent_license"], name, license_no)
    _assert_printed_pair(
        bondsman["values"]["agent_name"], bondsman["values"]["agent_license"], name, license_no,
    )
    printed = _attach_session_writing_agent(_Request(cookies), dict(payload))
    _assert_printed_pair(printed["agent_name"], printed["agent_license"], name, license_no)
    print_data, err = _build_appearance_bond_data({
        "surety": "palmetto",
        "name": "SAMPLE",
        "charge": "SAMPLE CHARGE ONLY",
        "bond": 1000,
        "county": "Lee",
        **printed,
    })
    assert err is None
    _assert_printed_pair(print_data["agent_name"], print_data["agent_license"], name, license_no)
    recipe = build_palmetto_field_values({"name": "SAMPLE", "bond_amount": 1000, **printed})[0]
    _assert_printed_pair(recipe["AgentField"], recipe["agentBailLicNumField"], name, license_no)
    ctx = build_palmetto_context({"defendant_name": "SAMPLE", **printed})
    _assert_printed_pair(ctx["agent_name"], ctx["agent_license"], name, license_no)
    if kind == "machine":
        raw_recipe = build_palmetto_field_values({"name": "SAMPLE", "bond_amount": 1000, **payload})[0]
        _assert_printed_pair(raw_recipe["AgentField"], raw_recipe["agentBailLicNumField"], name, license_no)
        raw_ctx = build_palmetto_context({"defendant_name": "SAMPLE", **payload})
        _assert_printed_pair(raw_ctx["agent_name"], raw_ctx["agent_license"], name, license_no)
    assert bondsman["name"] != "Kayla L"
    assert recipe["AgentField"] != "Kayla L"
    assert print_data["writing_agent_license"] == license_no


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("machine", "pin_admin", "kayla"))
async def test_non_registry_name_keeps_a_non_registry_license(kind):
    """A future tenant agent stays when both the name and the license are present."""
    payload = {
        "agent_name": "Some Tenant Agent",
        "writing_agent_name": "Some Tenant Agent",
        "agent_license": "X100000",
        "writing_agent_license": "X100000",
    }
    assert _pair_from_agent_source(payload) == ("Some Tenant Agent", "X100000")
    if kind == "kayla":
        session = _session("sub_agent", "Kayla Lukesic", "G356764")
        cookies = _cookie("sub_agent", "Kayla Lukesic", "G356764")
    elif kind == "pin_admin":
        session = _session("god_admin")
        cookies = _cookie("god_admin")
    else:
        session = None
        cookies = None
    name, license_no = "Some Tenant Agent", "X100000"
    _assert_printed_pair(*resolve_writing_agent(payload, session=session), name, license_no)
    built = build_bond_data_from_dashboard(ctx=_bound(), body=payload, session=session)
    _assert_printed_pair(built["bondsman_name"], built["bondsman_license"], name, license_no)
    values = _prefill(built)
    _assert_printed_pair(values["agent_name"], values["agent_license"], name, license_no)
    bond = _bound(**payload, shannon_voice=True)
    for key in (
        "agent_name", "bondsman_name", "writing_agent_name", "writing_agent",
        "agent_license", "bondsman_license", "writing_agent_license", "license_number",
    ):
        bond[key] = built[key]
    submitted = await _submit(bond)
    bondsman = _bondsman(submitted["submitters"])
    _assert_printed_pair(bondsman["name"], bondsman["values"]["agent_license"], name, license_no)
    _assert_printed_pair(
        bondsman["values"]["agent_name"], bondsman["values"]["agent_license"], name, license_no,
    )
    printed = _attach_session_writing_agent(_Request(cookies), dict(payload))
    _assert_printed_pair(printed["agent_name"], printed["agent_license"], name, license_no)
    recipe = build_palmetto_field_values({"name": "SAMPLE", "bond_amount": 1000, **payload})[0]
    _assert_printed_pair(recipe["AgentField"], recipe["agentBailLicNumField"], name, license_no)
    ctx = build_palmetto_context({"defendant_name": "SAMPLE", **payload})
    _assert_printed_pair(ctx["agent_name"], ctx["agent_license"], name, license_no)
    assert HOUSE_LICENSE not in (license_no, recipe["agentBailLicNumField"], ctx["agent_license"])


STRAY_NAMES = ("Kayla Lukesik", "FAKE AGENT RIVERA", "Shamrock Bail Bonds")


@pytest.mark.asyncio
@pytest.mark.parametrize("stray_name", STRAY_NAMES)
@pytest.mark.parametrize("owner_name,owner_license", AGENTS)
async def test_no_path_prints_a_name_with_another_agents_license_for_unregistered_names(
    stray_name, owner_name, owner_license,
):
    """A name that misses BOND_AGENTS yields the registered license's own entry."""
    payload = {
        "agent_name": stray_name,
        "writing_agent_name": stray_name,
        "agent_license": owner_license,
        "writing_agent_license": owner_license,
        "bondsman_license": owner_license,
    }
    resolved = resolve_writing_agent(payload)
    assert resolved == (owner_name, owner_license)
    assert resolved != (stray_name, owner_license)
    values = _prefill(payload)
    assert values["agent_name"] == owner_name
    assert values["bondsman_name"] == owner_name
    assert values["agent_license"] == owner_license
    assert values["bondsman_license"] == owner_license
    assert values["agent_name"] != stray_name or stray_name == owner_name
    built = build_bond_data_from_dashboard(ctx=_bound(), body=payload)
    assert built["bondsman_name"] == owner_name
    assert built["bondsman_license"] == owner_license
    recipe = build_palmetto_field_values({"name": "SAMPLE", "bond_amount": 1000, **payload})[0]
    assert recipe["AgentField"] == owner_name
    assert recipe["agentBailLicNumField"] == owner_license
    ctx = build_palmetto_context({"defendant_name": "SAMPLE", **payload})
    assert ctx["agent_name"] == owner_name
    assert ctx["agent_license"] == owner_license
    bond = _bound(**payload, shannon_voice=True, defendant_name="Jordan Lee", indemnitor_name="Alex Rivera")
    submitted = await _submit(bond)
    bondsman = _bondsman(submitted["submitters"])
    assert bondsman["name"] == owner_name
    assert bondsman["values"]["agent_name"] == owner_name
    assert bondsman["values"]["agent_license"] == owner_license
    assert bondsman["name"] != stray_name or stray_name == owner_name
    _no_sample({"resolved": resolved, "values": values, "recipe": recipe, "ctx": ctx})


class _Col:
    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, query, projection=None):
        return self.docs[0] if self.docs else None

    async def update_one(self, query, update, upsert=False):
        return None

    async def insert_one(self, doc):
        self.docs.append(doc)
        return None


@pytest.mark.asyncio
async def test_shannon_route_machine_call_uses_the_house_pair(monkeypatch):
    from dashboard.routers.paperwork import paperwork_bp

    monkeypatch.setenv("GAS_API_KEY", "gas-test-key")
    captured = {}
    svc = DocuSealService(
        base_url="https://sign.example.invalid",
        api_key="test-not-a-real-key",
    )

    async def _create(self, **kwargs):
        captured["kwargs"] = kwargs
        return [
            {"id": 1, "submission_id": 42, "role": "bondsman", "slug": "b", "email": "admin@shamrockbailbonds.biz"},
            {"id": 2, "submission_id": 42, "role": "indemnitor", "slug": "i", "email": "alex@example.invalid"},
            {"id": 3, "submission_id": 42, "role": "defendant", "slug": "d", "email": "jordan@example.invalid"},
        ]

    svc.create_submission = _create.__get__(svc, DocuSealService)
    app = FastAPI()
    app.include_router(paperwork_bp)
    client = TestClient(app)
    with patch("dashboard.routers.paperwork.get_collection", return_value=_Col([])), \
         patch("dashboard.services.docuseal_service.get_docuseal_service", return_value=svc):
        response = client.post(
            "/api/paperwork/shannon/email",
            headers={"X-API-Key": "gas-test-key"},
            json={
                "packet_id": "SH-AGENT-1",
                "surety_id": "osi",
                "defendant_name": "Jordan Lee",
                "indemnitor_name": "Alex Rivera",
                "indemnitor_email": "alex@example.invalid",
                "county": "Lee",
            },
        )
    assert response.status_code == 200, response.text
    bondsman = _bondsman(captured["kwargs"]["submitters"])
    assert bondsman["name"] == HOUSE_NAME
    assert captured["kwargs"]["submitters"][0]["values"]["agent_name"] == HOUSE_NAME
    assert captured["kwargs"]["submitters"][0]["values"]["agent_license"] == HOUSE_LICENSE
    assert captured["kwargs"]["submitters"][0]["values"]["bondsman_name"] == HOUSE_NAME
    _no_sample(captured["kwargs"]["submitters"])


@pytest.mark.asyncio
async def test_push_route_pin_admin_matches_main(monkeypatch):
    from dashboard.routers.paperwork import paperwork_bp

    captured = {}
    svc = DocuSealService(
        base_url="https://sign.example.invalid",
        api_key="test-not-a-real-key",
    )

    async def _create(self, **kwargs):
        captured["kwargs"] = kwargs
        return [
            {"id": 1, "submission_id": 43, "role": "bondsman", "slug": "b", "email": "admin@shamrockbailbonds.biz"},
            {"id": 2, "submission_id": 43, "role": "indemnitor", "slug": "i", "email": "alex@example.invalid"},
            {"id": 3, "submission_id": 43, "role": "defendant", "slug": "d", "email": "jordan@example.invalid"},
        ]

    svc.create_submission = _create.__get__(svc, DocuSealService)
    packet = _bound(packet_id="PKT-PUSH-1")
    app = FastAPI()
    app.include_router(paperwork_bp)
    client = TestClient(app)
    client.cookies.update(_cookie("god_admin"))
    with patch("dashboard.routers.paperwork.get_collection", return_value=_Col([packet])), \
         patch("dashboard.services.docuseal_service.get_docuseal_service", return_value=svc):
        response = client.post(
            "/api/paperwork/PKT-PUSH-1/docuseal",
            json={
                "indemnitors": [{"name": "Alex Rivera", "email": "alex@example.invalid"}],
                "defendant": {"name": "Jordan Lee", "email": "jordan@example.invalid"},
            },
        )
    assert response.status_code == 200, response.text
    bondsman = _bondsman(captured["kwargs"]["submitters"])
    assert bondsman["name"] == HOUSE_NAME
    values = bondsman["values"]
    assert values["agent_name"] == HOUSE_NAME
    assert values["bondsman_name"] == HOUSE_NAME
    assert values["agent_license"] == HOUSE_LICENSE
    assert values["bondsman_license"] == HOUSE_LICENSE
    _no_sample(values)


@pytest.mark.asyncio
async def test_finalize_route_pin_admin_and_kayla(monkeypatch):
    from dashboard.routers.paperwork import paperwork_bp

    ctx = {
        "defendant": {"name": "Jordan Lee", "email": "jordan@example.invalid"},
        "indemnitor": {"name": "Alex Rivera", "email": "alex@example.invalid", "phone": "2395550100"},
        "county": "Lee",
        "state": "FL",
        "bond_amount": 5000,
        "premium_amount": 500,
        "match_status": "validated",
        "bond_case_id": "BC-1",
        "match_id": "M-1",
        "defendant_id": "D-1",
        "indemnitor_id": "I-1",
        "case_number": "26CF1",
        "poa_number": "POA1",
        "booking_number": "B1",
        "surety_id": "osi",
        "charges": "SAMPLE",
    }
    captured = {}
    svc = DocuSealService(
        base_url="https://sign.example.invalid",
        api_key="test-not-a-real-key",
    )

    async def _create(self, **kwargs):
        captured["kwargs"] = kwargs
        return [
            {"id": 1, "submission_id": 44, "role": "bondsman", "slug": "b", "email": "admin@shamrockbailbonds.biz"},
            {"id": 2, "submission_id": 44, "role": "indemnitor", "slug": "i", "email": "alex@example.invalid"},
            {"id": 3, "submission_id": 44, "role": "defendant", "slug": "d", "email": "jordan@example.invalid"},
        ]

    svc.create_submission = _create.__get__(svc, DocuSealService)
    app = FastAPI()
    app.include_router(paperwork_bp)
    client = TestClient(app)

    def _collections(name):
        if name == "poa_inventory":
            return _Col([{"max_bond_value": 50000, "poa_number": "POA1", "surety_id": "osi"}])
        return _Col([])

    with patch("dashboard.services.packet_builder_service.resolve_case_context", new=AsyncMock(return_value=dict(ctx))), \
         patch("dashboard.services.packet_builder_service.resolve_client_esign_provider", new=AsyncMock(return_value="docuseal")), \
         patch("dashboard.routers.helpers.reject_unless_write_book", new=AsyncMock(return_value=None)), \
         patch("dashboard.routers.paperwork.get_collection", side_effect=_collections), \
         patch("dashboard.services.docuseal_service.get_docuseal_service", return_value=svc):
        client.cookies.update(_cookie("god_admin"))
        admin = client.post("/api/paperwork/packet/finalize", json={"surety_id": "osi", "packet_id": "PKT-FIN-ADMIN"})
        assert admin.status_code == 200, admin.text
        admin_bond = _bondsman(captured["kwargs"]["submitters"])
        assert admin_bond["name"] == HOUSE_NAME
        assert admin_bond["values"]["agent_name"] == HOUSE_NAME
        assert admin_bond["values"]["bondsman_name"] == HOUSE_NAME
        assert admin_bond["values"]["agent_license"] == HOUSE_LICENSE

        client.cookies.clear()
        client.cookies.update(_cookie("sub_agent", "Kayla Lukesic", "G356764"))
        captured.clear()
        kayla = client.post("/api/paperwork/packet/finalize", json={"surety_id": "osi", "packet_id": "PKT-FIN-KAYLA"})
        assert kayla.status_code == 200, kayla.text
        kayla_bond = _bondsman(captured["kwargs"]["submitters"])
        assert kayla_bond["name"] == "Kayla Lukesic"
        assert kayla_bond["values"]["agent_name"] == "Kayla Lukesic"
        assert kayla_bond["values"]["agent_license"] == "G356764"
        assert kayla_bond["values"]["bondsman_license"] == "G356764"
        _no_sample(kayla_bond["values"])


def test_apply_writing_agent_writes_one_pair():
    target = {}
    apply_writing_agent(target, "Jason Taylor", "W214323")
    assert target["writing_agent_name"] == "Jason Taylor"
    assert target["bondsman_name"] == "Jason Taylor"
    assert target["agent_license"] == "W214323"
    assert target["license_number"] == "W214323"
