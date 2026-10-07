"""Codex review fixes for surety onboarding (PR #111).

One test per finding: Mongo PDF durability, published POA tiers, the mapped
DocuSeal required-value gate, the Write Bond picker, the full production
packet, and the publish audit record.
"""
from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

import fitz
import pytest

from dashboard.bond_pdf_service import generate_appearance_bonds
from dashboard.services import surety_registry as sr
from dashboard.services import surety_template_store as store
from dashboard.services.docuseal_service import DocuSealService
from dashboard.services.poa_service import determine_surety_from_prefix, get_poa_tier_for_bond
from dashboard.services.surety_packet_fill import (
    FAKE_PREVIEW_BOND,
    SuretyDataMissing,
    docuseal_mapped_aliases,
    production_packet_parts,
)
from dashboard.services.surety_registry import UnsupportedSuretyError


SUGGESTED_FIELDS = [
    ("defendantNameField", "OLD NAME"),
    ("countyField", "OldCounty"),
    ("ArrestNumberField", "OLD-BOOK"),
    ("CaseNumberField", "OLD-CASE"),
    ("chargesField1", "OLD CHARGE"),
    ("numericBondAmount", "$9.00"),
    ("powerNumField", "OLD-POA"),
    ("calculatedPremiumField", "$9.00"),
    ("dayField", "1"),
    ("monthWrittenField", "January"),
    ("yearYYYYField", "1999"),
]


class _Admin:
    def command(self, name):
        if name != "ping":
            raise AssertionError(name)
        return {"ok": 1}


class _Client:
    def __init__(self):
        self.admin = _Admin()


class _Coll:
    def __init__(self, database):
        self.database = database
        self.docs = []

    def replace_one(self, filt, doc, upsert=False):
        key = next(iter(filt))
        for index, existing in enumerate(self.docs):
            if existing.get(key) == filt[key]:
                self.docs[index] = dict(doc)
                return
        if upsert:
            self.docs.append(dict(doc))

    def find(self, query=None):
        return [dict(row) for row in self.docs]

    def find_one(self, query):
        key = next(iter(query))
        for row in self.docs:
            if row.get(key) == query[key]:
                return dict(row)
        return None

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("Result", (), {"inserted_id": len(self.docs)})()


class _DB:
    def __init__(self):
        self.client = _Client()
        self._cols = {}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _Coll(self)
        return self._cols[name]


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("SURETY_TEMPLATE_STORE", raising=False)
    store.reset_for_tests(tmp_path)
    yield
    store.reset_for_tests(tmp_path)


def _acroform(pairs) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 72.0
    for name, value in pairs:
        widget = fitz.Widget()
        widget.field_name = name
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.rect = fitz.Rect(72, y, 420, y + 16)
        widget.field_value = value
        page.add_widget(widget)
        y += 20
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _widgets(pdf: bytes) -> dict:
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        found = {}
        for page in doc:
            for widget in page.widgets() or []:
                found[widget.field_name] = widget.field_value or ""
        return found
    finally:
        doc.close()


def _publish_suggested(surety_id="samplecarrier", **kwargs):
    draft = store.create_draft(
        surety_id=surety_id,
        label="Sample Carrier",
        poa_prefixes=[{"prefix": "SMP5", "max_bond_amount": 5000}],
        repeat_per_charge=True,
        **kwargs,
    )
    store.add_form(draft["version_id"], "packet.pdf", _acroform(SUGGESTED_FIELDS))
    store.update_draft(draft["version_id"], {"use_suggestions": True})
    return store.publish_draft(draft["version_id"], "clerk.one")


def test_published_pdfs_reload_from_mongo_without_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("APP_ENV", raising=False)
    db = _DB()
    store.install_mongo_for_tests(db)

    def _disk_forbidden():
        raise AssertionError("production template store touched container disk")

    monkeypatch.setattr(store, "_default_root", _disk_forbidden)
    published = _publish_suggested()
    uploaded = store.load_form_bytes(published["forms"][0])
    form_id = published["forms"][0]["form_id"]
    assert db[store.FILES_COLLECTION].find_one({"form_id": form_id})["pdf"] == uploaded
    assert uploaded[:4] == b"%PDF"
    assert db[store.VERSIONS_COLLECTION].find_one({"version_id": published["version_id"]})["status"] == "published"
    assert not (tmp_path / "versions.json").exists()

    store.reload_from_durable()
    loaded = store.active_published("samplecarrier")
    assert loaded["version"] == 1
    assert store.load_form_bytes(loaded["forms"][0]) == uploaded


def test_published_poa_prefixes_fail_closed_for_third_party():
    _publish_suggested()
    assert get_poa_tier_for_bond("samplecarrier", 1000) == "SMP5"
    assert determine_surety_from_prefix("SMP5") == "samplecarrier"
    assert determine_surety_from_prefix("SMP5", "samplecarrier") == "samplecarrier"
    assert get_poa_tier_for_bond("osi", 2500) == "OSI-P3"
    assert determine_surety_from_prefix("", None) == "osi"

    with pytest.raises(UnsupportedSuretyError) as mismatch:
        determine_surety_from_prefix("OSI3", "samplecarrier")
    assert mismatch.value.code == "surety_prefix_mismatch"
    assert mismatch.value.surety == "samplecarrier"

    with pytest.raises(UnsupportedSuretyError) as unknown:
        determine_surety_from_prefix("NOPE", "lexington")
    assert unknown.value.surety == "lexington"
    assert unknown.value.code == "unsupported_surety"

    with pytest.raises(UnsupportedSuretyError) as bare:
        get_poa_tier_for_bond("lexington", 1000)
    assert bare.value.code == "poa_tiers_unavailable"


def _bound_packet(**extra):
    data = {
        "bond_case_id": "BC-1",
        "match_id": "M-1",
        "defendant_id": "D-1",
        "indemnitor_id": "I-1",
        "case_number": "26CF1",
        "poa_number": "SMP5 100",
        "booking_number": "B100",
        "match_status": "validated",
        "surety_id": "samplecarrier",
        "surety": "samplecarrier",
        "name": "RIVERA, ALEX",
        "defendant_name": "RIVERA, ALEX",
        "county": "Lee",
        "charge": "BURGLARY",
        "bond_amount": 5000,
        "bond_date": "01/15/2026",
        "indemnitor_name": "Jordan Blake",
        "indemnitor": {"name": "Jordan Blake", "email": "jordan@example.com"},
        "defendant": {"name": "Alex Rivera", "email": "alex@example.com"},
        "include_bondsman": False,
    }
    data.update(extra)
    return data


@pytest.mark.asyncio
async def test_mapped_docuseal_fails_closed_before_submission():
    _publish_suggested(docuseal_template_id="77")
    missing = _bound_packet(charge="", bond_amount=5000)
    with pytest.raises(SuretyDataMissing) as raised:
        docuseal_mapped_aliases("samplecarrier", missing)
    assert "charge.description" in raised.value.missing

    svc = DocuSealService(base_url="https://sign.example", api_key="k")
    with patch.object(svc, "create_submission", new=AsyncMock()) as create:
        with pytest.raises(SuretyDataMissing):
            await svc.create_submission_for_packet(
                template_id=77,
                packet_id="pkt-missing",
                bond_data=missing,
                send_email=False,
            )
        create.assert_not_called()

    ready = _bound_packet()
    ready.pop("premium_amount", None)
    aliases = docuseal_mapped_aliases("samplecarrier", ready)
    assert aliases["calculatedPremiumField"] == "$500.00"

    with patch.object(svc, "create_submission", new=AsyncMock(return_value=[
        {"id": 1, "submission_id": 9, "role": "Indemnitor", "slug": "s", "email": "jordan@example.com"},
    ])) as create:
        await svc.create_submission_for_packet(
            template_id=77,
            packet_id="pkt-ready",
            bond_data=ready,
            send_email=False,
        )
        create.assert_called_once()
        values = create.call_args.kwargs["submitters"][0]["values"]
        assert values["calculatedPremiumField"] == "$500.00"


def test_write_bond_picker_keeps_published_surety():
    _publish_suggested()
    rows = {row["id"]: row for row in sr.picker_options()}
    assert rows["samplecarrier"]["selectable"] is True
    assert rows["lexington"]["selectable"] is False
    source = open("dashboard/sl-features.js", encoding="utf-8").read()
    assert "function writeBondSuretyId" in source
    assert 'id="suretyPublished"' in source
    assert "=== 'palmetto' ? 'palmetto' : 'osi'" not in source
    assert "writeBondSuretyId(lead.surety_id || lead.surety || 'osi')" in source


def test_production_packet_includes_every_uploaded_form():
    draft = store.create_draft(
        surety_id="samplecarrier",
        label="Sample Carrier",
        poa_prefixes=[{"prefix": "SMP5", "max_bond_amount": 5000}],
        repeat_per_charge=True,
    )
    store.add_form(draft["version_id"], "appearance.pdf", _acroform(SUGGESTED_FIELDS))
    store.add_form(draft["version_id"], "static.pdf", _acroform([("collateralField", "OLD")]))
    store.update_draft(draft["version_id"], {"use_suggestions": True})
    store.publish_draft(draft["version_id"], "clerk.one")

    bond = dict(FAKE_PREVIEW_BOND)
    bond["surety"] = "samplecarrier"
    parts = generate_appearance_bonds(bond, template="samplecarrier")
    assert production_packet_parts("osi", bond) is None
    assert len(parts) == 3
    assert _widgets(parts[0])["chargesField1"] == "SAMPLE CHARGE ONLY"
    assert _widgets(parts[1])["chargesField1"] == "SAMPLE CHARGE TWO"
    static = _widgets(parts[2])
    assert static["collateralField"] == "SAMPLE COLLATERAL"
    assert "chargesField1" not in static

    legacy = generate_appearance_bonds({
        "surety": "osi",
        "name": "DOE, JANE",
        "county": "Lee",
        "booking_number": "1029767",
        "bond_date": "01/02/2026",
        "charge_details": [
            {"charge": "CHARGE A", "bond_amount": 1000, "case_number": "C1", "poa_number": "OSI3 1"},
            {"charge": "CHARGE B", "bond_amount": 2000, "case_number": "C2", "poa_number": "OSI3 2"},
        ],
    })
    assert len(legacy) == 2


def test_publish_writes_audit_old_to_new_version(monkeypatch):
    first = _publish_suggested()
    events = store.publish_audit_log()
    assert len(events) == 1
    assert events[0]["actor"] == "clerk.one"
    assert events[0]["event_type"] == "surety_template_published"
    assert events[0]["old_version"] is None
    assert events[0]["new_version"] == 1
    assert events[0]["version_id"] == first["version_id"]

    draft = store.create_draft(
        surety_id="samplecarrier",
        label="Sample Carrier",
        poa_prefixes=[{"prefix": "SMP5", "max_bond_amount": 5000}],
    )
    store.add_form(draft["version_id"], "packet.pdf", _acroform(SUGGESTED_FIELDS))
    store.update_draft(draft["version_id"], {"use_suggestions": True})
    second = store.publish_draft(draft["version_id"], "clerk.two")
    events = store.publish_audit_log()
    assert events[-1]["actor"] == "clerk.two"
    assert events[-1]["old_version"] == 1
    assert events[-1]["new_version"] == 2
    assert second["version"] == 2

    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("APP_ENV", raising=False)
    store.reset_for_tests()
    db = _DB()
    store.install_mongo_for_tests(db)
    published = _publish_suggested()
    audit = db["audit_events"].docs
    assert len(audit) == 1
    assert audit[0]["actor"] == "clerk.one"
    assert audit[0]["old_version"] is None
    assert audit[0]["new_version"] == 1
    assert audit[0]["entity_id"] == published["version_id"]
    assert audit[0]["action"] == "publish"
