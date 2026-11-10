"""Contract tests for the FastAPI + Starlette + sse-starlette upgrade."""

from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[1]


def _parse_pkg_specs(req_path: Path) -> dict[str, str]:
    specs: dict[str, str] = {}
    for raw_line in req_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+(?:\[[A-Za-z0-9_,.\-]+\])?)(.*)$", line)
        if m:
            pkg = m.group(1).lower()
            specs[pkg] = m.group(2).strip()
    return specs


def test_requirements_lift_fastapi_starlette_sse_caps():
    for rel in ("requirements.txt", "requirements-dashboard.txt"):
        specs = _parse_pkg_specs(REPO_ROOT / rel)
        assert "fastapi" in specs, f"{rel} missing fastapi"
        assert "starlette" in specs, f"{rel} missing starlette"
        assert "sse-starlette" in specs, f"{rel} missing sse-starlette"

        assert "<0.120.0" not in specs["fastapi"], f"{rel} still caps fastapi <0.120.0"
        assert ">=0.136.0" in specs["fastapi"], f"{rel} should require fastapi>=0.136.0"

        assert "<0.41.0" not in specs["starlette"], f"{rel} still caps starlette <0.41.0"
        assert ">=1.0.0" in specs["starlette"], f"{rel} should require starlette>=1.0.0"

        assert "<2.2" not in specs["sse-starlette"], f"{rel} still caps sse-starlette <2.2"
        assert ">=3.0.0" in specs["sse-starlette"], f"{rel} should require sse-starlette>=3.0.0"


def test_dashboard_main_uses_lifespan_not_removed_on_startup():
    main_src = (REPO_ROOT / "dashboard" / "main.py").read_text(encoding="utf-8")
    assert "lifespan=lifespan" in main_src
    assert "on_startup=" not in main_src
    assert "on_shutdown=" not in main_src
    assert ".on_event(" not in main_src
