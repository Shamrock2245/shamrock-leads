from dashboard.services.defendant_delivery_authorization import (
    authorized_defendant_delivery_snapshot,
    submitter_has_current_defendant_authorization,
)


def _authorization():
    return {
        "status": "verified_opt_in",
        "authorization_id": "DDA-TEST-1",
        "defendant_id": "DEF-1",
        "contact_verified_at": "2026-08-18T12:00:00+00:00",
        "contact_verified_by": "staff",
        "imessage_opt_in_at": "2026-08-18T12:00:00+00:00",
        "imessage_opt_in_by": "staff",
    }


def test_snapshot_requires_complete_authoritative_defendant_evidence():
    bond_data = {"defendant_id": "DEF-1", "defendant_delivery_authorization": _authorization()}
    snapshot = authorized_defendant_delivery_snapshot(bond_data)

    assert snapshot == {
        "authorization_id": "DDA-TEST-1",
        "defendant_id": "DEF-1",
        "status": "verified_opt_in",
    }

    incomplete = {**bond_data, "defendant_delivery_authorization": {"status": "verified_opt_in"}}
    mismatched = {**bond_data, "defendant_delivery_authorization": {**_authorization(), "defendant_id": "DEF-OTHER"}}
    assert authorized_defendant_delivery_snapshot(incomplete) == {}
    assert authorized_defendant_delivery_snapshot(mismatched) == {}


def test_submitter_requires_exact_packet_and_defendant_authorization_binding():
    packet = {"packet_id": "PKT-1", "defendant_id": "DEF-1"}
    submitter = {
        "metadata": {
            "packet_id": "PKT-1",
            "party_role": "defendant",
            "defendant_delivery_authorization": {
                "status": "verified_opt_in",
                "authorization_id": "DDA-TEST-1",
                "defendant_id": "DEF-1",
            },
        }
    }

    assert submitter_has_current_defendant_authorization(packet=packet, submitter=submitter)
    assert not submitter_has_current_defendant_authorization(
        packet=packet,
        submitter={**submitter, "metadata": {**submitter["metadata"], "party_role": "indemnitor"}},
    )
    assert not submitter_has_current_defendant_authorization(
        packet={**packet, "defendant_id": "DEF-OTHER"}, submitter=submitter
    )
