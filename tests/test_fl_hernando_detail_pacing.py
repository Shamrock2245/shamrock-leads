"""Hernando detail fetches stay polite: a pause before every GET and a per-run cap."""
from __future__ import annotations

from core.models import ArrestRecord
from scrapers.counties import hernando
from tests.test_fl_hernando_detail_status import _detail, _Resp


class _CountingSession:
    def __init__(self):
        self.urls = []

    def get(self, url, **kw):
        self.urls.append(url)
        return _Resp(_detail(booking=url.rsplit("=", 1)[1]))


def _rows(n):
    return [ArrestRecord(County="Hernando", Booking_Number=f"HCSO26JBN{i:06d}", Full_Name="DOE, JANE",
                         Charges="ROSTER") for i in range(1, n + 1)]


def test_every_detail_get_is_paced(monkeypatch):
    sleeps = []
    monkeypatch.setattr(hernando.time, "sleep", lambda s: sleeps.append(s))
    session = _CountingSession()
    hernando.HernandoCountyScraper()._enrich_from_details(session, _rows(5))
    assert len(session.urls) == 5
    assert sleeps == [hernando.REQUEST_PAUSE_S] * 5 and hernando.REQUEST_PAUSE_S >= 0.25


def test_detail_gets_are_capped_per_run(monkeypatch):
    monkeypatch.setattr(hernando.time, "sleep", lambda s: None)
    monkeypatch.setattr(hernando, "MAX_DETAILS_PER_RUN", 3)
    session = _CountingSession()
    out = hernando.HernandoCountyScraper()._enrich_from_details(session, _rows(7))
    assert len(session.urls) == 3  # rows past the cap are not fetched
    assert len(out) == 3  # and not written (nothing blanked)
    assert hernando.MAX_DETAILS_PER_RUN.__class__ is int
