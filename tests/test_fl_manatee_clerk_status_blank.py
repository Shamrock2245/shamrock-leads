"""Manatee Clerk: custody is not published, so Status is "" (never "Unknown")."""
from tests.test_manatee_clerk_scraper import DETAILS, LISTS, _scraper


def test_status_blank_not_unknown(monkeypatch):
    sc, _ = _scraper(monkeypatch, LISTS, DETAILS)
    recs = sc.scrape()
    assert recs and all(r.Status == "" for r in recs)
    assert all("status_source" not in r.extra_data for r in recs)
