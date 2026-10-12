"""Hendry (FL) dedup / key rules after the fail-closed change (#143, 06f58e5).

#143 retired the OCV parser (``_parse_inmate``) because the feed's only id,
``inmateID`` = ``HCSO<YY>MNI<NNNNNN>``, is a person (Master Name Index) id, not
a booking number. The old tests here called ``_parse_inmate`` and asserted that
``inmateID`` became the booking key; that is exactly what #143 forbids, so they
now assert the stricter rule: no parser, no emitted record, no fetch. Person-id
cleanup of stored rows waits on Brendan and is not touched here.
Synthetic data only.
"""
from __future__ import annotations

import socket

import pytest

from core.models import ArrestRecord
from scrapers.counties.hendry import HendryCountyScraper


@pytest.fixture()
def no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)


def test_hendry_has_no_parser_and_emits_no_record(no_network):
    """No row (name-only, document-id-only, or person-id) becomes a booking key."""
    scraper = HendryCountyScraper()
    assert not hasattr(scraper, "_parse_inmate")
    assert HendryCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert scraper.scrape() == []


def test_hendry_never_keys_on_person_id_and_dedup_format_unchanged(no_network):
    """The MNI person id is never used as a booking key; the dedup key format for a
    real source booking number (if Hendry ever reopens) stays County:Booking."""
    scraper = HendryCountyScraper()
    assert scraper.scrape() == []
    assert "MNI" in HendryCountyScraper.SOURCE_CONTRACT_REASON
    record = ArrestRecord(County="Hendry", Booking_Number="SOURCE-12345")
    assert record.get_dedup_key() == "Hendry:SOURCE-12345"
