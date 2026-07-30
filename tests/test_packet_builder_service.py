"""Unit tests for adaptive packet builder (no MongoDB required)."""
import pytest

from dashboard.services.packet_builder_service import (
    apply_self_indemnitor,
    assemble_manifest,
    build_adaptive_field_map,
    hydration_score,
    template_slug_for_catalog_key,
    verify_self_indemnitor_pin,
)


def test_self_indemnitor_pin_gate():
    assert verify_self_indemnitor_pin("224545") is True
    assert verify_self_indemnitor_pin(" wrong ") is False
    assert verify_self_indemnitor_pin("") is False


def test_apply_self_indemnitor_copies_defendant():
    ctx = {
        "defendant": {
            "name": "Jane Defendant",
            "phone": "2395550199",
            "address": "100 Oak St",
            "dob": "02/02/1992",
            "email": "jane@example.com",
        },
        "indemnitor": {"phone": ""},
        "bond_amount": 1500,
    }
    out = apply_self_indemnitor(ctx, "224545")
    assert out["self_indemnitor"] is True
    assert out["indemnitor"]["name"] == "Jane Defendant"
    assert out["indemnitor"]["phone"] == "2395550199"
    assert out["indemnitor"]["relationship"] == "Self"
    with pytest.raises(PermissionError):
        apply_self_indemnitor(ctx, "000000")


def test_adaptive_field_map_and_hydration():
    ctx = {
        "defendant": {
            "name": "John Doe",
            "dob": "01/01/1990",
            "address": "1 Main",
            "phone": "2395550100",
        },
        "indemnitor": {
            "name": "Mary Doe",
            "phone": "2395550101",
            "address": "2 Main",
            "email": "mary@example.com",
        },
        "bond_amount": 5000,
        "premium_amount": 500,
        "county": "Lee",
        "booking_number": "BK1",
        "case_number": "CASE1",
        "poa_number": "POA99",
        "surety_id": "osi",
        "charges": "Battery",
    }
    fields = build_adaptive_field_map(ctx)
    assert fields["defendant_name"] == "John Doe"
    assert fields["IndemnitorName"] == "Mary Doe"
    assert "BondAmount" in fields
    audit = hydration_score(fields)
    assert audit["hydration_score"] >= 90
    assert audit["hydrated_count"] >= 10


def test_catalog_to_template_and_manifest():
    assert template_slug_for_catalog_key("indemnity_agreement") == "indemnity-agreement"
    assert template_slug_for_catalog_key("master_bail_application") == "defendant-application"
    cats = {
        "universal": ["indemnity_agreement", "master_bail_application"],
        "payment_plan": ["payment_plan_agreement"],
        "osi_surety": ["osi_appearance_bond"],
        "palmetto_surety": ["palmetto_appearance_bond"],
        "conditional": ["cosigner_addendum"],
    }
    man = assemble_manifest(cats, surety_id="osi", include_payment_plan=True, self_indemnitor=True)
    keys = [m["catalog_key"] for m in man]
    assert "indemnity_agreement" in keys
    assert "payment_plan_agreement" in keys
    assert "osi_appearance_bond" in keys
    # self-indemnitor skips cosigner addendum
    assert "cosigner_addendum" not in keys
    print_only = [m for m in man if m["print_only"]]
    ab = next(m for m in print_only if m["template_slug"] == "appearance-bond")
    assert ab["e_sign"] is False
    assert ab["signature_mode"] == "wet_ink_live"
    assert ab["delivery"] == "print_and_jail"
    assert "wet" in ab["procedure"].lower() or "jail" in ab["procedure"].lower()
