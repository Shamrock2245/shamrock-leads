"""SignNow packet manifest — full phase docs + appearance bond never e-signed."""
from dashboard.services.signnow_packet_service import SignNowPacketService


def test_phase1_manifest_full_and_multi_indemnitor():
    svc = SignNowPacketService()
    m = svc.build_packet_manifest(phase=1, surety_id="osi", num_indemnitors=2)
    keys = [x["doc_key"] for x in m]
    assert "paperwork-header" in keys
    assert "faq-cosigners" in keys
    assert "indemnity-agreement" in keys
    assert keys.count("indemnity-agreement") == 2
    assert "promissory-note" in keys
    assert "disclosure-form" in keys
    assert keys.count("ssa-release") == 2
    assert keys.count("master-waiver") == 2
    assert "appearance-bond" not in keys
    assert len(m) == 10


def test_phase2_manifest_full():
    svc = SignNowPacketService()
    m = svc.build_packet_manifest(phase=2, surety_id="osi")
    keys = [x["doc_key"] for x in m]
    assert keys == [
        "faq-defendants",
        "defendant-application",
        "surety-terms",
        "master-waiver",
        "ssa-release",
        "collateral-receipt",
        "payment-plan",
    ]
    assert "appearance-bond" not in keys


def test_palmetto_uses_override_template_ids():
    svc = SignNowPacketService()
    m = svc.build_packet_manifest(phase=2, surety_id="palmetto")
    by_key = {x["doc_key"]: x for x in m}
    assert by_key["defendant-application"]["template_key"] == "defendant-application-palmetto"
    assert by_key["surety-terms"]["template_key"] == "surety-terms-palmetto"
    assert by_key["collateral-receipt"]["template_key"] == "collateral-receipt-palmetto"
    # payment-plan is agnostic — no -palmetto key
    assert by_key["payment-plan"]["template_key"] == "payment-plan"


def test_custom_manifest_strips_appearance_bond():
    svc = SignNowPacketService()
    m = svc.build_packet_manifest(
        phase=1,
        surety_id="osi",
        custom_manifest=[
            "appearance-bond",
            "appearance-bond-palmetto",
            "indemnity-agreement",
            "faq-cosigners",
        ],
        num_indemnitors=1,
    )
    keys = [x["doc_key"] for x in m]
    assert "appearance-bond" not in keys
    assert "appearance-bond-palmetto" not in keys
    assert "indemnity-agreement" in keys
    assert "faq-cosigners" in keys


def test_appearance_bond_doc_rule_is_print_only():
    rule = SignNowPacketService.DOC_RULES["appearance-bond"]
    assert rule["rule"] == "print-only"
    assert rule.get("e_sign") is False
    assert "wet" in (rule.get("signature_mode") or "") or "print" in rule["label"].lower()
