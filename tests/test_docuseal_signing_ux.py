"""Official DocuSeal embed / OpenAPI helpers for Shamrock signing UX."""

from dashboard.services.docuseal_signing_ux import (
    embed_form_config,
    friendly_fields_for_values,
    role_copy,
    sign_host,
)


def test_self_hosted_host_and_slug_src():
    cfg = embed_form_config(
        src="https://sign.shamrockbailbonds.biz/s/abc123",
        email="party@example.com",
        role="Defendant",
    )
    assert cfg["data-host"] == "sign.shamrockbailbonds.biz"
    assert cfg["data-src"].endswith("/s/abc123")
    assert cfg["data-email"] == "party@example.com"
    assert cfg["data-only-required-fields"] == "true"
    assert cfg["data-remember-signature"] == "true"
    assert cfg["data-go-to-last"] == "true"
    assert "16a34a" in cfg["data-custom-css"]
    assert "Finish signing" in cfg["data-i18n"]


def test_sign_host_strips_scheme():
    assert sign_host("https://sign.shamrockbailbonds.biz") == "sign.shamrockbailbonds.biz"


def test_friendly_titles_from_prefill_keys():
    fields = friendly_fields_for_values({
        "indemnitor_name": "Mary",
        "defendant_name": "John",
        "poa_number": "OSI3-1",
    })
    names = {f["name"] for f in fields}
    assert "indemnitor_name" in names
    assert "defendant_name" in names
    assert "poa_number" not in names
    titles = {f["name"]: f["title"] for f in fields}
    assert titles["indemnitor_name"] == "Your full legal name"


def test_role_copy_covers_all_parties():
    assert "defendant" in role_copy("Defendant")["you_are"].lower()
    assert "co-indemnitor" in role_copy("Co-Indemnitor")["you_are"].lower()
    assert "indemnitor" in role_copy("indemnitor")["you_are"].lower()
