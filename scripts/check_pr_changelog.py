#!/usr/bin/env python3
"""Fail a PR that edits scrapers/ or dashboard/ without touching CHANGELOG.md.

A ``skip-changelog`` label on the pull request passes the check. The workflow
passes ``BASE_SHA`` and ``PR_LABELS`` (a JSON list of label names).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

WATCHED_PREFIXES = ("dashboard/", "scrapers/")
CHANGELOG_PATH = "CHANGELOG.md"
SKIP_LABEL = "skip-changelog"
_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")


def needs_changelog(paths: list[str], labels: list[str]) -> bool:
    """True when the check should fail."""
    if SKIP_LABEL in labels:
        return False
    watched = [path for path in paths if path.startswith(WATCHED_PREFIXES)]
    if not watched:
        return False
    return CHANGELOG_PATH not in paths


def changed_paths(base_sha: str) -> list[str]:
    if not _SHA.fullmatch(base_sha or ""):
        raise SystemExit("BASE_SHA must be a git commit sha")
    out = subprocess.check_output(
        ["git", "diff", "--name-only", base_sha, "HEAD"],
        text=True,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def _labels_from_env() -> list[str]:
    raw = os.environ.get("PR_LABELS") or "[]"
    parsed = json.loads(raw)
    if parsed is None:
        return []
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise SystemExit("PR_LABELS must be a JSON list of strings")
    return parsed


def main() -> int:
    base = os.environ.get("BASE_SHA", "")
    paths = changed_paths(base)
    labels = _labels_from_env()
    if not needs_changelog(paths, labels):
        if SKIP_LABEL in labels:
            print(f"{SKIP_LABEL} label set; changelog check skipped")
        else:
            print("changelog check ok")
        return 0
    watched = [path for path in paths if path.startswith(WATCHED_PREFIXES)]
    print("CHANGELOG.md was not updated.", file=sys.stderr)
    print("These paths require a changelog note (or the skip-changelog label):", file=sys.stderr)
    for path in watched:
        print(f"  {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
