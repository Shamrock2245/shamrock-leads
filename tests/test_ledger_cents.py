"""Ledger integer-cents integrity tests."""

from dashboard.services.ledger_service import LedgerService


def test_to_cents_basic():
    assert LedgerService.to_cents(10) == 1000
    assert LedgerService.to_cents(10.50) == 1050
    assert LedgerService.to_cents("1,234.56") == 123456
    assert LedgerService.to_cents("$99.99") == 9999
    assert LedgerService.to_cents(-50.25) == -5025


def test_to_cents_rounding():
    # ROUND_HALF_UP
    assert LedgerService.to_cents("10.005") == 1001
    assert LedgerService.to_cents("10.004") == 1000


def test_from_cents():
    assert LedgerService.from_cents(123456) == 1234.56
    assert LedgerService.from_cents(-5025) == -50.25
    assert LedgerService.from_cents(0) == 0.0


def test_invalid_amounts():
    assert LedgerService.to_cents(None) == 0
    assert LedgerService.to_cents("not-a-number") == 0
    assert LedgerService.to_cents("") == 0
