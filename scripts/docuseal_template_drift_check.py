#!/usr/bin/env python3
"""
DocuSeal template drift guard — READ-ONLY (HTTP GET only).

Checks the live Write Bond templates (OSI = template 1, Palmetto = template 5)
against what the CRM expects:

  1. template name                == snapshot ``expected_name``
  2. not archived                 (``archived_at`` is null)
  3. submitter roles              == code roles (docuseal_service.ROLE_*)
  4. field names                  == snapshot ``fields`` (added / removed)
  5. hydrated fields              == template fields that receive a prefill key
                                     from DocuSealService.prefill_values_from_bond;
                                     a field that USED to be hydrated and no longer
                                     is (renamed key or renamed field) is drift, and
                                     zero hydrated fields is drift.

The hydration keys are extracted statically (AST) from
``dashboard/services/docuseal_service.py::DocuSealService.prefill_values_from_bond``,
so no bond data is needed.

Auth: ``DOCUSEAL_API_KEY`` sent as ``X-Auth-Token`` to ``DOCUSEAL_URL``
(default https://sign.shamrockbailbonds.biz). The key is NEVER printed.
Template JSON from DocuSeal contains signed document URLs; only template name,
archived flag, role names and field NAMES are read / written — never values,
slugs or URLs.

Usage:
  python scripts/docuseal_template_drift_check.py              # live check (exit code below)
  python scripts/docuseal_template_drift_check.py --json       # machine-readable report
  python scripts/docuseal_template_drift_check.py --offline    # code keys vs snapshot, no network
  python scripts/docuseal_template_drift_check.py --write-snapshot   # (re)capture live fields
  python scripts/docuseal_template_drift_check.py --print-hydration-keys

Exit codes:
  0 no drift
  1 drift detected (report printed)
  2 configuration / auth / network error (e.g. HTTP 401)
  3 unverified: field snapshot still pending live capture (name/roles/archived
    were checked; field-name drift could not be)
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUSEAL_SERVICE_PY = REPO_ROOT / "dashboard" / "services" / "docuseal_service.py"
SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "docuseal_template_snapshot.json"
DEFAULT_DOCUSEAL_URL = "https://sign.shamrockbailbonds.biz"

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2
EXIT_UNVERIFIED = 3

PENDING = "pending_live_capture"
CAPTURED = "captured"


# ─────────────────────────────────────────────────────────────────────────────
# Code-side sources (no network)
# ─────────────────────────────────────────────────────────────────────────────

def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node  # type: ignore[return-value]
    raise LookupError(f"{name} not found in {DOCUSEAL_SERVICE_PY}")


def _range_values(call: ast.AST) -> Optional[List[int]]:
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "range"):
        return None
    try:
        args = [ast.literal_eval(a) for a in call.args]
    except ValueError:
        return None
    return list(range(*args))


def _expand_fstring(node: ast.JoinedStr, var: str, values: Iterable[int]) -> Optional[List[str]]:
    out = []
    for v in values:
        parts = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append(str(part.value))
            elif (isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name)
                  and part.value.id == var and part.format_spec is None):
                parts.append(str(v))
            else:
                return None
        out.append("".join(parts))
    return out


def extract_hydration_keys(source: Optional[str] = None) -> List[str]:
    """
    Every key ``prefill_values_from_bond`` can emit: string-literal dict keys,
    ``x["key"] = ...`` assignments, and ``x[f"key_{i}"]`` inside
    ``for i in range(a, b)`` loops (expanded).
    """
    src = source if source is not None else DOCUSEAL_SERVICE_PY.read_text(encoding="utf-8")
    fn = _find_function(ast.parse(src), "prefill_values_from_bond")
    keys: Set[str] = set()
    unresolved: List[str] = []

    loop_ctx: Dict[int, Tuple[str, List[int]]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            vals = _range_values(node.iter)
            if vals is not None:
                for inner in ast.walk(node):
                    loop_ctx.setdefault(id(inner), (node.target.id, vals))

    def _add(key_node: ast.AST) -> None:
        if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
            keys.add(key_node.value)
        elif isinstance(key_node, ast.JoinedStr):
            ctx = loop_ctx.get(id(key_node))
            expanded = _expand_fstring(key_node, *ctx) if ctx else None
            if expanded is None:
                unresolved.append(ast.unparse(key_node))
            else:
                keys.update(expanded)

    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if k is not None:
                    _add(k)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript):
                    _add(t.slice)
    if unresolved:
        raise ValueError(f"unresolved dynamic hydration keys: {unresolved}")
    return sorted(keys)


def code_roles(source: Optional[str] = None) -> List[str]:
    """Submitter role strings the CRM sends (module-level ROLE_* constants)."""
    src = source if source is not None else DOCUSEAL_SERVICE_PY.read_text(encoding="utf-8")
    roles: Set[str] = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.startswith("ROLE_"):
                    roles.add(node.value.value)
    return sorted(roles)


def load_snapshot(path: Path = SNAPSHOT_PATH) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def template_ids_from_env(snapshot: Dict[str, Any]) -> Dict[str, int]:
    """Env overrides (same vars as resolve_template_id_for_surety), else snapshot ids."""
    ids = {k: int(v["template_id"]) for k, v in snapshot["templates"].items()}
    osi = (os.getenv("DOCUSEAL_TEMPLATE_ID_OSI") or os.getenv("DOCUSEAL_TEMPLATE_ID") or "").strip()
    pal = (os.getenv("DOCUSEAL_TEMPLATE_ID_PALMETTO") or "").strip()
    if osi.isdigit() and "osi" in ids:
        ids["osi"] = int(osi)
    if pal.isdigit() and "palmetto" in ids:
        ids["palmetto"] = int(pal)
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# Live fetch (GET only)
# ─────────────────────────────────────────────────────────────────────────────

class FetchError(Exception):
    pass


def fetch_template(template_id: int, *, timeout: float = 20.0) -> Dict[str, Any]:
    import httpx

    base = (os.getenv("DOCUSEAL_URL") or DEFAULT_DOCUSEAL_URL).strip().rstrip("/")
    key = (os.getenv("DOCUSEAL_API_KEY") or "").strip()
    if not key:
        raise FetchError("DOCUSEAL_API_KEY is not set")
    url = f"{base}/api/templates/{int(template_id)}"
    try:
        resp = httpx.get(url, headers={"X-Auth-Token": key, "Accept": "application/json"},
                         timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise FetchError(f"GET {url} failed: {type(exc).__name__}") from None
    if resp.status_code != 200:
        # Body of DocuSeal errors is short JSON like {"error":"Not authenticated"}; no secrets.
        raise FetchError(f"GET {url} → HTTP {resp.status_code} {resp.text[:120]!r}")
    try:
        data = resp.json()
    except ValueError:
        raise FetchError(f"GET {url} → non-JSON response") from None
    if not isinstance(data, dict):
        raise FetchError(f"GET {url} → unexpected payload type")
    return data


def sanitize_template(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Keep ONLY name, archived flag, role names and field names (no values/URLs/slugs)."""
    fields = raw.get("fields") if isinstance(raw.get("fields"), list) else []
    subs = raw.get("submitters") if isinstance(raw.get("submitters"), list) else []
    return {
        "name": str(raw.get("name") or ""),
        "archived": raw.get("archived_at") not in (None, ""),
        "roles": sorted({str(s.get("name") or "") for s in subs if isinstance(s, dict) and s.get("name")}),
        "fields": sorted({str(f.get("name") or "") for f in fields if isinstance(f, dict) and f.get("name")}),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Comparison
# ─────────────────────────────────────────────────────────────────────────────

def compare_template(
    label: str,
    live: Dict[str, Any],
    expected: Dict[str, Any],
    *,
    hydration_keys: Iterable[str],
    roles: Iterable[str],
) -> Dict[str, Any]:
    hk = set(hydration_keys)
    want_roles = set(roles)
    live_fields = set(live.get("fields") or [])
    live_roles = set(live.get("roles") or [])
    drift: List[str] = []
    notes: List[str] = []

    if live.get("name") != expected.get("expected_name"):
        drift.append(f"name is {live.get('name')!r}, expected {expected.get('expected_name')!r}")
    if live.get("archived"):
        drift.append("template is ARCHIVED")
    missing_roles = sorted(want_roles - live_roles)
    extra_roles = sorted(live_roles - want_roles)
    if missing_roles:
        ci = {r.lower() for r in live_roles}
        case_only = [r for r in missing_roles if r.lower() in ci]
        drift.append(f"roles missing on template: {missing_roles}"
                     + (f" (case differs only: {case_only})" if case_only else ""))
    if extra_roles:
        drift.append(f"unexpected template roles: {extra_roles}")

    hydrated_now = sorted(live_fields & hk)
    if live_fields and not hydrated_now:
        drift.append("no template field matches any prefill key (wrong template or total rename)")

    status = expected.get("status")
    field_check = "verified"
    added: List[str] = []
    removed: List[str] = []
    lost_hydration: List[str] = []
    if status == CAPTURED and isinstance(expected.get("fields"), list):
        snap_fields = set(expected["fields"])
        added = sorted(live_fields - snap_fields)
        removed = sorted(snap_fields - live_fields)
        if added:
            drift.append(f"fields added since snapshot: {added}")
        if removed:
            drift.append(f"fields removed/renamed since snapshot: {removed}")
        lost_hydration = sorted(set(expected.get("hydrated_fields") or []) - set(hydrated_now))
        if lost_hydration:
            drift.append(f"fields that no longer receive prefill: {lost_hydration}")
    else:
        field_check = "unverified_snapshot_pending"
        notes.append("field snapshot pending live capture — run with --write-snapshot using a valid key")

    return {
        "label": label,
        "template_id": expected.get("template_id"),
        "drift": drift,
        "notes": notes,
        "field_check": field_check,
        "live_field_count": len(live_fields),
        "hydrated_field_count": len(hydrated_now),
        "unhydrated_template_fields": sorted(live_fields - hk),  # staff-fill / signature / unmapped
        "fields_added": added,
        "fields_removed": removed,
        "lost_hydration": lost_hydration,
    }


def offline_check(snapshot: Dict[str, Any], hydration_keys: List[str], roles: List[str]) -> List[str]:
    """Code-side drift (CI, no network): prefill keys / roles vs committed snapshot."""
    problems: List[str] = []
    snap_keys = snapshot.get("hydration_keys", {}).get("keys") or []
    added = sorted(set(hydration_keys) - set(snap_keys))
    removed = sorted(set(snap_keys) - set(hydration_keys))
    if added:
        problems.append(f"prefill keys added in code (not in snapshot): {added}")
    if removed:
        problems.append(f"prefill keys removed/renamed in code (template fields may stop filling): {removed}")
    if sorted(snapshot.get("expected_roles") or []) != sorted(roles):
        problems.append(f"code roles {roles} != snapshot expected_roles {snapshot.get('expected_roles')}")
    for label, tpl in (snapshot.get("templates") or {}).items():
        if tpl.get("status") == CAPTURED:
            lost = sorted(set(tpl.get("hydrated_fields") or []) - set(hydration_keys))
            if lost:
                problems.append(f"{label}: template fields no longer covered by any prefill key: {lost}")
    return problems


def run_live(
    snapshot: Dict[str, Any],
    *,
    fetch: Callable[[int], Dict[str, Any]] = fetch_template,
    hydration_keys: Optional[List[str]] = None,
    roles: Optional[List[str]] = None,
) -> Tuple[int, Dict[str, Any]]:
    hk = hydration_keys if hydration_keys is not None else extract_hydration_keys()
    rl = roles if roles is not None else code_roles()
    ids = template_ids_from_env(snapshot)
    report: Dict[str, Any] = {"templates": [], "errors": [], "offline": offline_check(snapshot, hk, rl)}
    for label, expected in snapshot["templates"].items():
        exp = dict(expected, template_id=ids[label])
        try:
            live = sanitize_template(fetch(ids[label]))
        except FetchError as exc:
            report["errors"].append(f"{label} (template {ids[label]}): {exc}")
            continue
        report["templates"].append(compare_template(label, live, exp, hydration_keys=hk, roles=rl))
    if report["errors"]:
        return EXIT_ERROR, report
    if report["offline"] or any(t["drift"] for t in report["templates"]):
        return EXIT_DRIFT, report
    if any(t["field_check"] != "verified" for t in report["templates"]):
        return EXIT_UNVERIFIED, report
    return EXIT_OK, report


def write_snapshot(
    snapshot: Dict[str, Any],
    *,
    fetch: Callable[[int], Dict[str, Any]] = fetch_template,
    path: Path = SNAPSHOT_PATH,
    hydration_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Capture live field names (sanitized) + refresh code hydration keys. GET only."""
    hk = hydration_keys if hydration_keys is not None else extract_hydration_keys()
    ids = template_ids_from_env(snapshot)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = json.loads(json.dumps(snapshot))
    new["hydration_keys"]["keys"] = hk
    for label, tpl in new["templates"].items():
        live = sanitize_template(fetch(ids[label]))
        tpl.update({
            "template_id": ids[label],
            "status": CAPTURED,
            "captured_at": now,
            "live_name_at_capture": live["name"],
            "roles_at_capture": live["roles"],
            "fields": live["fields"],
            "hydrated_fields": sorted(set(live["fields"]) & set(hk)),
        })
        # expected_name is deliberately NOT overwritten from live.
    path.write_text(json.dumps(new, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return new


def format_report(code: int, report: Dict[str, Any]) -> str:
    lines = ["DocuSeal template drift check (read-only)"]
    for e in report.get("errors", []):
        lines.append(f"  ERROR  {e}")
    for p in report.get("offline", []):
        lines.append(f"  DRIFT  code: {p}")
    for t in report.get("templates", []):
        head = f"  [{t['label']} / template {t['template_id']}] fields={t['live_field_count']} " \
               f"hydrated={t['hydrated_field_count']} field_check={t['field_check']}"
        lines.append(head)
        for d in t["drift"]:
            lines.append(f"    DRIFT  {d}")
        for n in t["notes"]:
            lines.append(f"    NOTE   {n}")
    verdict = {EXIT_OK: "OK — no drift", EXIT_DRIFT: "DRIFT DETECTED", EXIT_ERROR: "ERROR — check config/auth",
               EXIT_UNVERIFIED: "UNVERIFIED — field snapshot pending live capture"}[code]
    lines.append(f"  RESULT {verdict} (exit {code})")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print JSON report")
    ap.add_argument("--offline", action="store_true", help="code-side check only (no network)")
    ap.add_argument("--write-snapshot", action="store_true", help="capture live fields into the snapshot")
    ap.add_argument("--print-hydration-keys", action="store_true")
    ap.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH)
    args = ap.parse_args(argv)

    if args.print_hydration_keys:
        print("\n".join(extract_hydration_keys()))
        return EXIT_OK
    snapshot = load_snapshot(args.snapshot)
    if args.write_snapshot:
        try:
            write_snapshot(snapshot, path=args.snapshot)
        except FetchError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"snapshot written: {args.snapshot}")
        return EXIT_OK
    if args.offline:
        problems = offline_check(snapshot, extract_hydration_keys(), code_roles())
        code = EXIT_DRIFT if problems else EXIT_OK
        report = {"templates": [], "errors": [], "offline": problems}
    else:
        code, report = run_live(snapshot)
    print(json.dumps({"exit_code": code, **report}, indent=2) if args.json else format_report(code, report))
    return code


if __name__ == "__main__":
    sys.exit(main())
