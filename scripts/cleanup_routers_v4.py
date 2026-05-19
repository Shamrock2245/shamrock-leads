#!/usr/bin/env python3
"""
ShamrockLeads — FastAPI Router Cleanup v4
==========================================
Fixes ALL remaining Quart→FastAPI migration issues in dashboard/routers/:

  1. Residual .route() decorators → @bp.get/post/put/delete/patch
  2. jsonify() wrappers (any stragglers missed by v3)
  3. request.get_json() → await request.json()
  4. make_response(x) → JSONResponse(x)
  5. Old-style tuple returns: return expr, 4XX → JSONResponse(expr, status_code=4XX)
  6. Remaining # current_app_removed comment lines (strips the line)
  7. Ensures every async def that uses `request.*` or `await request` has
     `request: Request` in its parameter list
  8. Adds `from fastapi import APIRouter, Request, Query` if Request/Query needed
  9. Blueprint() → APIRouter() for any stragglers (files missed by v3)

Run from repo root:
    python3 scripts/cleanup_routers_v4.py [--dry-run]
"""
from __future__ import annotations

import ast
import os
import re
import sys

DRY_RUN = "--dry-run" in sys.argv
ROUTER_DIR = "dashboard/routers"

# ── helpers ────────────────────────────────────────────────────────────────────

def strip_jsonify(content: str) -> str:
    """Remove jsonify( ... ) using bracket-depth counting."""
    result, i = [], 0
    TRIGGER = "jsonify("
    while i < len(content):
        idx = content.find(TRIGGER, i)
        if idx == -1:
            result.append(content[i:])
            break
        result.append(content[i:idx])
        j = idx + len(TRIGGER)
        depth = 1
        while j < len(content) and depth > 0:
            c = content[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            if depth > 0:
                result.append(c)
            j += 1
        i = j
    return "".join(result)


def fix_route_decorators(content: str) -> str:
    """Convert @bp.route('/path', methods=['GET','POST']) → @bp.get/post('/path') etc."""
    def replace_route(m: re.Match) -> str:
        bp_var = m.group(1)
        path = m.group(2)
        methods_raw = m.group(3)
        methods = (
            [x.strip().strip("\"'").upper() for x in methods_raw.split(",")]
            if methods_raw
            else ["GET"]
        )
        if len(methods) == 1:
            return f'@{bp_var}.{methods[0].lower()}("{path}")'
        ml = ", ".join(f'"{x}"' for x in methods)
        return f'@{bp_var}.api_route("{path}", methods=[{ml}])'

    return re.sub(
        r'@(\w+)\.route\(["\']([^"\']+)["\'](?:,\s*methods=\[([^\]]+)\])?\)',
        replace_route,
        content,
    )


def fix_tuple_returns(content: str) -> str:
    """
    return <expr>, 4XX  →  return JSONResponse(<expr>, status_code=4XX)

    Handles single-line and must NOT match legitimate tuple assignments.
    We only trigger when the last token on the line is a 3-digit HTTP status.
    """
    lines = content.split("\n")
    out = []
    for line in lines:
        m = re.match(r'^(\s*)return (.*),\s*([45]\d{2})\s*$', line)
        if m:
            indent, expr, code = m.group(1), m.group(2).strip(), m.group(3)
            out.append(f"{indent}return JSONResponse({expr}, status_code={code})")
        else:
            out.append(line)
    return "\n".join(out)


def ensure_request_in_sig(content: str) -> str:
    """
    For every async def that uses request.* or await request inside its body
    but does NOT already have `request:` in its parameter list, inject
    `request: Request` as the first parameter.
    """
    lines = content.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Detect async def
        m = re.match(r'^(\s*)async def (\w+)\(([^)]*)\)\s*:', line)
        if m:
            indent, fn_name, params = m.group(1), m.group(2), m.group(3)
            # Collect function body
            body_lines = []
            j = i + 1
            while j < len(lines):
                bl = lines[j]
                if bl.strip() and not bl.strip().startswith("#"):
                    bl_indent = len(bl) - len(bl.lstrip())
                    cur_indent = len(indent) + 4
                    if bl_indent <= len(indent) and bl.strip():
                        break
                body_lines.append(bl)
                j += 1
            body = "\n".join(body_lines)

            needs_request = (
                "request." in body
                or "await request" in body
                or "_qp = dict(request" in body
            )
            has_request = "request:" in params or "request :" in params

            if needs_request and not has_request:
                # Inject request: Request as first param
                stripped = params.strip()
                if stripped:
                    new_params = f"request: Request, {stripped}"
                else:
                    new_params = "request: Request"
                line = f"{indent}async def {fn_name}({new_params}):"

        result.append(line)
        i += 1
    return "\n".join(result)


def fix_imports(content: str, needs_request: bool, needs_query: bool,
                needs_jsonresponse: bool) -> str:
    """Update the fastapi import line to include needed symbols."""
    # Find existing 'from fastapi import ...' line
    fa_m = re.search(r'^from fastapi import (.+)$', content, re.MULTILINE)
    if fa_m:
        existing = {s.strip() for s in fa_m.group(1).split(",")}
        existing.add("APIRouter")
        if needs_request:
            existing.add("Request")
        if needs_query:
            existing.add("Query")
        new_import = f"from fastapi import {', '.join(sorted(existing))}"
        content = re.sub(r'^from fastapi import .+$', new_import, content, flags=re.MULTILINE, count=1)
    else:
        # No fastapi import yet — build from scratch
        symbols = ["APIRouter"]
        if needs_request:
            symbols.append("Request")
        if needs_query:
            symbols.append("Query")
        content = f"from fastapi import {', '.join(sorted(symbols))}\n" + content

    # Ensure JSONResponse import
    if needs_jsonresponse and "JSONResponse" not in content:
        if "from fastapi.responses import" in content:
            content = re.sub(
                r'from fastapi\.responses import (.+)',
                lambda m: f"from fastapi.responses import {', '.join(sorted({s.strip() for s in m.group(1).split(',')} | {'JSONResponse'}))}",
                content, count=1,
            )
        else:
            content = re.sub(
                r'^(from fastapi import .+)$',
                r'\1\nfrom fastapi.responses import JSONResponse',
                content, flags=re.MULTILINE, count=1,
            )
    return content


def fix_blueprint_stragglers(content: str) -> str:
    """Convert any remaining Blueprint() declarations."""
    def replace_bp(m: re.Match) -> str:
        bp_var = m.group(1)
        nm = re.search(r'Blueprint\(["\'](\w+)["\']', m.group(0))
        tag = nm.group(1) if nm else "api"
        return f'{bp_var} = APIRouter(prefix="/api", tags=["{tag}"])'
    return re.sub(
        r'^(\w+)\s*=\s*Blueprint\([^)]+\)\s*$',
        replace_bp,
        content,
        flags=re.MULTILINE,
    )


def remove_current_app_lines(content: str) -> str:
    """Drop lines that are purely a current_app_removed comment (stray artefacts)."""
    lines = content.split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        # Remove lines that are only the comment marker with nothing useful
        if stripped in ("# current_app_removed", "# current_app_removed,"):
            continue
        # Also remove any line that is *only* a comment starting with current_app_removed
        if re.match(r'^\s*# current_app_removed\s*$', line):
            continue
        out.append(line)
    return "\n".join(out)


def transform(content: str) -> tuple[str, dict]:
    stats: dict[str, int] = {
        "route_decorators": 0,
        "jsonify": 0,
        "get_json": 0,
        "make_response": 0,
        "tuple_returns": 0,
        "current_app_lines": 0,
        "blueprint_stragglers": 0,
        "request_injections": 0,
    }

    # 1. Straggler Blueprint()
    before = content
    content = fix_blueprint_stragglers(content)
    stats["blueprint_stragglers"] += content != before

    # 2. .route() decorators
    before = content
    content = fix_route_decorators(content)
    stats["route_decorators"] = len(re.findall(r'@\w+\.route\(', before)) - len(re.findall(r'@\w+\.route\(', content))

    # 3. jsonify()
    before = content
    content = strip_jsonify(content)
    stats["jsonify"] = before.count("jsonify(") - content.count("jsonify(")

    # 4. request.get_json()
    before_count = content.count("request.get_json(")
    content = re.sub(r'await request\.get_json\([^)]*\)', 'await request.json()', content)
    content = re.sub(r'request\.get_json\([^)]*\)', 'await request.json()', content)
    stats["get_json"] = before_count - content.count("request.get_json(")

    # 5. make_response
    before_count = content.count("make_response(")
    content = re.sub(r'\bmake_response\((\w+)\)', r'JSONResponse(\1)', content)
    stats["make_response"] = before_count - content.count("make_response(")

    # 6. Tuple returns
    before = content
    content = fix_tuple_returns(content)
    # Count changed lines
    stats["tuple_returns"] = sum(
        1 for a, b in zip(before.split("\n"), content.split("\n")) if a != b
    )

    # 7. Remove dead current_app_removed lines
    before_lines = content.count("\n")
    content = remove_current_app_lines(content)
    stats["current_app_lines"] = before_lines - content.count("\n")

    # 8. Ensure request: Request in signatures
    before = content
    content = ensure_request_in_sig(content)
    stats["request_injections"] = sum(
        1 for a, b in zip(before.split("\n"), content.split("\n")) if a != b
    )

    # 9. Fix imports
    needs_request = "request: Request" in content or "Request" in content
    needs_query = "Query(" in content
    needs_jsonresponse = "JSONResponse(" in content
    content = fix_imports(content, needs_request, needs_query, needs_jsonresponse)

    return content, stats


def syntax_ok(content: str, path: str) -> bool:
    try:
        ast.parse(content)
        return True
    except SyntaxError as e:
        print(f"    ⚠  SYNTAX ERROR after transform: {e.lineno}: {e.msg}")
        return False


def main():
    files = sorted(f for f in os.listdir(ROUTER_DIR) if f.endswith(".py")
                   and f not in ("__init__.py", "helpers.py"))

    total_stats: dict[str, int] = {}
    changed, unchanged, syntax_errors = 0, 0, 0

    for fname in files:
        path = os.path.join(ROUTER_DIR, fname)
        original = open(path).read()
        transformed, stats = transform(original)

        if transformed == original:
            unchanged += 1
            continue

        ok = syntax_ok(transformed, path)
        if not ok:
            syntax_errors += 1
            print(f"  ❌ {fname}: syntax error — SKIPPED")
            continue

        if not DRY_RUN:
            open(path, "w").write(transformed)

        changed += 1
        summary = ", ".join(f"{k}={v}" for k, v in stats.items() if v > 0)
        tag = "[DRY] " if DRY_RUN else ""
        print(f"  {tag}✅ {fname}: {summary}")

        for k, v in stats.items():
            total_stats[k] = total_stats.get(k, 0) + v

    print(f"\n{'[DRY RUN] ' if DRY_RUN else ''}Changed: {changed} | Unchanged: {unchanged} | Syntax errors: {syntax_errors}")
    print("\nTotal fixes applied:")
    for k, v in sorted(total_stats.items()):
        if v:
            print(f"  {k:30s} {v:4d}")


if __name__ == "__main__":
    main()
