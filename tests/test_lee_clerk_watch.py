"""Lee Clerk first-post delay vs fast subsequent jail updates."""
from datetime import datetime, timedelta, timezone

from dashboard.services.lee_clerk_watch import (
    FIRST_CLERK_CHECK_HOURS,
    clerk_check_due,
    clerk_page_indicates_posted,
    jail_refresh_due,
)


NOW = datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc)


def test_clerk_check_waits_about_a_day():
    written = NOW - timedelta(hours=6)
    assert clerk_check_due(written_at=written, now=NOW, already_posted=False) is False
    later = written + timedelta(hours=FIRST_CLERK_CHECK_HOURS + 1)
    assert clerk_check_due(written_at=written, now=later, already_posted=False) is True
    assert clerk_check_due(written_at=written, now=later, already_posted=True) is False


def test_jail_refresh_is_slow_early_then_fast():
    last = NOW - timedelta(minutes=20)
    assert jail_refresh_due(last_refresh_at=last, now=NOW, hours_since_write=6) is False
    assert jail_refresh_due(last_refresh_at=last, now=NOW, hours_since_write=22) is True
    assert jail_refresh_due(last_refresh_at=None, now=NOW, hours_since_write=1) is True


def test_thin_spa_html_never_marks_posted():
    shell = "<html><body>Loading…</body></html>"
    assert clerk_page_indicates_posted(shell, "26CF123") is False


def test_real_clerk_html_with_case_and_surety_marks_posted():
    html = (
        "<html><body>" + ("Lee County Clerk Matrix search results. " * 40)
        + "Case 26CF123 Appearance Bond surety posted."
        + "</body></html>"
    )
    assert clerk_page_indicates_posted(html, "26CF123") is True
    assert clerk_page_indicates_posted(html, "99CF999") is False
