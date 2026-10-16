"""Pasco: status from the source's custody-only view, with status_source; no PII in logs."""
import logging

from scrapers.counties import pasco


class _R:
    def __init__(self, payload, code=200):
        self._p, self.status_code = payload, code
        self.headers = {"content-type": "application/json"}
        self.text = ""

    def json(self):
        return self._p


ROW = {"bookingNumber": "2026000123", "nameId": 77, "firstName": "ZZSYNTH", "lastName": "ALPHA",
       "middleName": "", "suffix": "", "dob": "01/02/1990", "bookingDate": "10/15/2026", "bookingTime": "08:00"}


def _run(monkeypatch, charges):
    def fake(session, method, url, **kw):
        if url == pasco.CUSTODY_API:
            return _R([ROW])
        if isinstance(charges, Exception):
            raise charges
        return _R(charges)
    monkeypatch.setattr(pasco, "request", fake)
    return pasco.PascoCountyScraper().scrape()


def test_status_from_custody_view_with_source(monkeypatch):
    rec = _run(monkeypatch, [{"arrestCharge": "1", "chargeDescription": "X", "bondAmount": "0"}])[0]
    assert rec.Status == "In Custody"
    assert rec.extra_data["status_source"] == "published_in_custody_view"
    assert rec.Bond_Amount == "0"  # published 0 kept


def test_unknown_bond_blank(monkeypatch):
    rec = _run(monkeypatch, [{"arrestCharge": "1", "chargeDescription": "X", "bondAmount": None}])[0]
    assert rec.Bond_Amount == ""


def test_charge_failure_logs_no_name(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    rec = _run(monkeypatch, ValueError("ZZSYNTH ALPHA 01/02/1990"))[0]
    assert rec.Charges == "" and rec.Bond_Amount == ""
    assert "ZZSYNTH" not in caplog.text and "1990" not in caplog.text
