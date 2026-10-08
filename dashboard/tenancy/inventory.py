"""Collection names the application actually opens.

The backfill and the tenancy tests both use this scan. A name that shows up
here and is not on the global allowlist is tenant-owned and must be stamped.
Platform directory collections are recognized and are not stamped onto every
row as Shamrock's book.

Patterns are the ones this repo uses to open a Mongo collection. A new access
style has to be added here or the test will not see it.
"""

from __future__ import annotations

import re
from pathlib import Path

# Application packages. Tests, one-off scripts, and the OpenCut tree are not
# the running dashboard or writer path.
SCAN_ROOTS = (
    "api",
    "blog",
    "core",
    "dashboard",
    "models",
    "scoring",
    "services",
    "social",
    "wix",
    "writers",
)
# Root modules the dashboard process imports. One-off scripts stay out.
SCAN_FILES = ("cron.py",)

_SKIP_DIRS = {
    "__pycache__",
    "tests",
    "node_modules",
    "opencut",
    ".git",
}

_CONST_ASSIGN = re.compile(
    r"""^(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*['\"](?P<value>[a-z][a-z0-9_]*)['\"]\s*(?:#.*)?$""",
    re.M,
)
_ATTR_ASSIGN = re.compile(
    r"""self\.(?P<name>_sync_collection)\s*=\s*['\"](?P<value>[a-z][a-z0-9_]*)['\"]"""
)
_GET = re.compile(
    r"""(?:get_collection|_get_collection)\(\s*(?:['\"]([a-z][a-z0-9_]*)['\"]|([A-Z][A-Z0-9_]*))\s*\)"""
)
_SUB = re.compile(
    r"""(?:\bdb\b|get_db\(\)|_get_db\(\))\s*\[\s*(?:['\"]([a-z][a-z0-9_]*)['\"]|([A-Z][A-Z0-9_]*)|self\.(_sync_collection))\s*\]"""
)
_ATTR = re.compile(
    r"""(?:\bdb\b|self\.db|self\._db)\.([a-z][a-z0-9_]+)\.(?:find|find_one|insert_one|insert_many|update_one|update_many|delete_one|delete_many|count_documents|aggregate|create_index|distinct|replace_one|bulk_write|find_one_and_update|find_one_and_delete|find_one_and_replace|drop|estimated_document_count)\b"""
)
_IDX = re.compile(r"""_idx\(\s*['\"]([a-z][a-z0-9_]*)['\"]""")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _iter_py_files(root: Path):
    for name in SCAN_FILES:
        path = root / name
        if path.is_file():
            yield path
    for name in SCAN_ROOTS:
        base = root / name
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            yield path


def _resolve(token: str | None, constants: dict[str, str]) -> str | None:
    if not token:
        return None
    if token in constants:
        return constants[token]
    return None


def used_collection_names(root: Path | None = None) -> dict[str, list[str]]:
    """Return collection name -> sorted 'path:line' locations."""
    base = root or repo_root()
    found: dict[str, set[str]] = {}

    def add(name: str | None, location: str) -> None:
        if not name or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            return
        found.setdefault(name, set()).add(location)

    for path in _iter_py_files(base):
        text = path.read_text(encoding="utf-8", errors="ignore")
        constants: dict[str, str] = {}
        for match in _CONST_ASSIGN.finditer(text):
            constants[match.group("name")] = match.group("value")
        for match in _ATTR_ASSIGN.finditer(text):
            constants[match.group("name")] = match.group("value")
        rel = path.relative_to(base).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            location = f"{rel}:{lineno}"
            for match in _GET.finditer(line):
                add(match.group(1) or _resolve(match.group(2), constants), location)
            for match in _SUB.finditer(line):
                add(
                    match.group(1)
                    or _resolve(match.group(2), constants)
                    or _resolve(match.group(3), constants),
                    location,
                )
            for match in _ATTR.finditer(line):
                add(match.group(1), location)
            for match in _IDX.finditer(line):
                add(match.group(1), location)
    return {name: sorted(locations) for name, locations in sorted(found.items())}
