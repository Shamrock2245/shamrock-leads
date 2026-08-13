"""Indemnitor / defendant sign-link helpers."""

from dashboard.services.paperwork_signers import (
    branded_sign_url,
    normalize_role,
    party_signers_from_packet,
    party_signers_from_submitters,
    pick_party,
)


def test_normalize_role_aliases():
    assert normalize_role("Defendant") == "defendant"
    assert normalize_role("Co-Indemnitor") == "coindemnitor"
    assert normalize_role("ind") == "indemnitor"


def test_branded_urls():
    url = branded_sign_url("PKT-ABC", "Defendant")
    assert url.endswith("/sign/PKT-ABC/defendant")
    assert "paperwork.shamrockbailbonds.biz" in url


def test_party_cards_from_submitters():
    parties = party_signers_from_submitters(
        [
            {
                "role": "indemnitor",
                "name": "Mary Doe",
                "phone": "2395550100",
                "slug": "ind-slug",
                "status": "pending",
            },
            {
                "role": "Defendant",
                "name": "John Doe",
                "phone": "2395550199",
                "sign_url": "https://sign.shamrockbailbonds.biz/s/def-slug",
                "status": "pending",
            },
        ],
        packet_id="PKT-1",
    )
    assert [p["role"] for p in parties] == ["indemnitor", "defendant"]
    assert parties[0]["share_url"].endswith("/sign/PKT-1/indemnitor")
    assert parties[1]["sign_url"].endswith("/s/def-slug")
    picked = pick_party(parties, role="defendant")
    assert picked["name"] == "John Doe"
    by_phone = pick_party(parties, phone="239-555-0100")
    assert by_phone["role"] == "indemnitor"


def test_packet_uses_cache_when_no_submitters():
    packet = {
        "packet_id": "PKT-9",
        "parties": [
            {
                "role": "indemnitor",
                "share_url": "https://paperwork.shamrockbailbonds.biz/sign/PKT-9/indemnitor",
                "sign_url": "https://sign.example/s/x",
            }
        ],
        "docuseal_submitters": [],
    }
    assert party_signers_from_packet(packet)[0]["share_url"].endswith("/indemnitor")


def test_packet_rebuilds_from_submitters_over_stale_cache():
    packet = {
        "packet_id": "PKT-9",
        "parties": [
            {
                "role": "indemnitor",
                "share_url": "https://paperwork.shamrockbailbonds.biz/sign/PKT-9/indemnitor",
                "sign_url": "https://sign.example/s/stale",
            }
        ],
        "docuseal_submitters": [
            {"role": "Defendant", "sign_url": "https://sign.shamrockbailbonds.biz/s/fresh"},
        ],
    }
    parties = party_signers_from_packet(packet)
    assert [p["role"] for p in parties] == ["defendant"]
    assert parties[0]["sign_url"].endswith("/s/fresh")
