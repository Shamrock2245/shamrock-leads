"""Contract tests for .github/dependabot.yml version-scoped ignores."""
from __future__ import annotations

from pathlib import Path
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_dependabot_scopes_starlette_and_sse_starlette_ignores_to_incompatible_ranges():
    cfg_path = REPO_ROOT / ".github" / "dependabot.yml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["version"] == 2

    pip_entries = [
        entry for entry in data.get("updates", [])
        if entry.get("package-ecosystem") == "pip"
    ]
    assert len(pip_entries) == 1
    ignores = {
        item["dependency-name"]: item.get("versions")
        for item in pip_entries[0].get("ignore", [])
    }
    assert ignores.get("starlette") == [">= 0.41.0"]
    assert ignores.get("sse-starlette") == [">= 2.2.0"]
