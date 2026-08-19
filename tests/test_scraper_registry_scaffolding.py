from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "main.py"
EXTENSIONS = ROOT / "dashboard" / "extensions.py"


def _registered_counties() -> list[str]:
    """Read the dashboard's canonical labels without importing app dependencies."""
    tree = ast.parse(EXTENSIONS.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "REGISTERED_COUNTIES" for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "sorted":
            value = value.args[0]
        if isinstance(value, ast.List):
            labels = [item.value for item in value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
            assert labels, "REGISTERED_COUNTIES is empty"
            return labels
    raise AssertionError("REGISTERED_COUNTIES assignment not found")


REGISTERED_COUNTIES = _registered_counties()

EXPECTED_COUNTS = {
    "AL": 16,
    "CT": 6,
    "FL": 67,
    "GA": 85,
    "LA": 13,
    "MS": 9,
    "NC": 60,
    "OH": 3,
    "SC": 46,
    "TN": 22,
    "TX": 34,
}

DIRECTORY_BY_STATE = {
    "FL": "counties",
    "GA": "counties_ga",
    "SC": "counties_sc",
    "NC": "counties_nc",
    "TN": "counties_tn",
    "TX": "counties_tx",
    "LA": "counties_la",
    "AL": "counties_al",
    "CT": "counties_ct",
    "MS": "counties_ms",
    "OH": "counties_oh",
}

# These labels describe statewide/city scopes rather than a conventional county
# filename.  They remain part of the same scheduler coverage contract.
MODULE_SLUG_OVERRIDES = {
    ("CT DOC", "CT"): "ct_doc",
    ("Statewide", "CT"): "statewide_docket",
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _label_parts(label: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+) \(([A-Z]{2})\)", label)
    assert match, f"Malformed registered county label: {label!r}"
    return match.group(1), match.group(2)


def _registered_runtime_modules() -> set[str]:
    """Return imported county modules whose classes are scheduler-registered.

    AST inspection keeps this contract test side-effect free: it neither imports
    county modules nor issues any source request.
    """
    tree = ast.parse(ENTRYPOINT.read_text())
    imported_aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("scrapers.counties"):
            for alias in node.names:
                imported_aliases[alias.asname or alias.name] = node.module

    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "register_scraper" or not node.args:
            continue
        candidate = node.args[0]
        if isinstance(candidate, ast.Call) and isinstance(candidate.func, ast.Name):
            aliases.add(candidate.func.id)

    unresolved = aliases - imported_aliases.keys()
    assert not unresolved, f"Scheduler refers to non-imported scraper aliases: {sorted(unresolved)}"
    return {imported_aliases[alias] for alias in aliases}


def test_registered_counties_have_one_unique_state_qualified_label():
    counts = Counter(_label_parts(label)[1] for label in REGISTERED_COUNTIES)
    assert len(REGISTERED_COUNTIES) == sum(EXPECTED_COUNTS.values()) == 361
    assert counts == EXPECTED_COUNTS
    assert len(REGISTERED_COUNTIES) == len(set(REGISTERED_COUNTIES))


def test_each_registered_county_has_local_scaffold_and_scheduler_registration():
    runtime_modules = _registered_runtime_modules()
    missing_files: list[str] = []
    unregistered_modules: list[str] = []

    for label in REGISTERED_COUNTIES:
        county, state = _label_parts(label)
        slug = MODULE_SLUG_OVERRIDES.get((county, state), _slug(county))
        module = f"scrapers.{DIRECTORY_BY_STATE[state]}.{slug}"
        source = ROOT / (module.replace(".", "/") + ".py")
        if not source.is_file():
            missing_files.append(f"{label} -> {source.relative_to(ROOT)}")
        elif module not in runtime_modules:
            unregistered_modules.append(f"{label} -> {module}")

    assert not missing_files, "Registered counties missing local scraper scaffolds:\n" + "\n".join(missing_files)
    assert not unregistered_modules, "Registered county scaffolds missing scheduler registration:\n" + "\n".join(unregistered_modules)
