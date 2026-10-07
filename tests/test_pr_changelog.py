"""Changelog gate for scraper and dashboard pull requests."""
from __future__ import annotations

from scripts.check_pr_changelog import needs_changelog


def test_dashboard_or_scraper_edits_require_changelog():
    assert needs_changelog(["dashboard/sl-help.js"], []) is True
    assert needs_changelog(["scrapers/counties/lee.py"], []) is True
    assert needs_changelog(
        ["dashboard/routers/bonds.py", "CHANGELOG.md"],
        [],
    ) is False


def test_other_paths_do_not_require_changelog():
    assert needs_changelog([".github/workflows/ci.yml", "scripts/check_brand_contacts.py"], []) is False
    assert needs_changelog(["docs/ECOSYSTEM_OPERATIONS_MANUAL.md"], []) is False
    assert needs_changelog([], []) is False


def test_skip_changelog_label_passes():
    assert needs_changelog(["scrapers/counties/lee.py"], ["skip-changelog"]) is False
    assert needs_changelog(["dashboard/main.py"], ["dependencies", "skip-changelog"]) is False
