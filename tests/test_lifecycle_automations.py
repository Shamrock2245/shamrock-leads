"""Lifecycle automations — unit tests (no Mongo / SignNow network)."""
from __future__ import annotations

from dashboard.services.lifecycle_automations import interpret_signnow_payload
from dashboard.services.automation_config import DEFAULT_CONFIG
from dashboard.cron import CRON_REGISTRY


class TestInterpretSignNow:
    def test_all_field_invites_fulfilled(self):
        payload = {
            "field_invites": [
                {"status": "fulfilled"},
                {"status": "fulfilled"},
            ]
        }
        assert interpret_signnow_payload(payload) == "signed"

    def test_mixed_pending(self):
        payload = {
            "field_invites": [
                {"status": "fulfilled"},
                {"status": "pending"},
            ]
        }
        assert interpret_signnow_payload(payload) == "pending"

    def test_declined(self):
        payload = {"field_invites": [{"status": "declined"}]}
        assert interpret_signnow_payload(payload) == "voided"

    def test_top_level_complete(self):
        assert interpret_signnow_payload({"status": "document.complete"}) == "signed"

    def test_empty_unknown(self):
        assert interpret_signnow_payload({}) == "unknown"
        assert interpret_signnow_payload(None) == "unknown"  # type: ignore[arg-type]


class TestLifecycleConfigAndCron:
    def test_defaults_enabled(self):
        for key in (
            "forfeiture_scan",
            "signnow_poller",
            "compliance_backfill",
            "matching_backlog",
        ):
            assert key in DEFAULT_CONFIG
            assert DEFAULT_CONFIG[key]["enabled"] is True

    def test_cron_registry(self):
        names = {c.name: c for c in CRON_REGISTRY}
        for key in (
            "forfeiture_scan",
            "signnow_poller",
            "compliance_backfill",
            "matching_backlog",
        ):
            assert key in names
            assert names[key].default_enabled is True

    def test_packet_document_id_normalization(self):
        from dashboard.services.lifecycle_automations import LifecycleAutomations

        ids = LifecycleAutomations._packet_document_ids({
            "signnow_document_id": ["abc", "def"],
            "document_ids": ["def", "ghi"],
            "document_id": "xyz",
        })
        assert ids == ["abc", "def", "ghi", "xyz"]
