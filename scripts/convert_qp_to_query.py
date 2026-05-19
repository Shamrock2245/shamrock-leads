#!/usr/bin/env python3
"""
ShamrockLeads — _qp.get() → FastAPI Query() converter
=======================================================
Converts the Quart compat shim pattern:

    _qp = dict(request.query_params)
    page = int(_qp.get("page", 0))
    status = _qp.get("status", "")

→ proper FastAPI Query() signatures:

    async def fn(
        request: Request,
        page: int = Query(default=0),
        status: str = Query(default=""),
    ):

Strategy:
  1. Split file into function blocks (preserving line structure)
  2. For each block containing `_qp = dict(request.query_params)`:
     a. Collect all _qp.get() call sites (key + default)
     b. Add typed Query() params to the function signature
     c. Replace _qp.get('k', d) → k  in the body
     d. Remove the _qp = dict(...) line
     e. Remove request from sig if no longer needed
  3. Update fastapi import line to add Query

Run from repo root:
    python3 scripts/convert_qp_to_query.py [--dry-run]
"""
from __future__ import annotations

import ast
import os
import re
import sys
from collections import OrderedDict

DRY_RUN = "--dry-run" in sys.argv
ROUTER_DIR = "dashboard/routers"

PYTHON_KEYWORDS = {
    "type", "from", "import", "class", "return", "yield", "pass",
    "break", "continue", "raise", "try", "except", "finally", "with",
    "as", "for", "while", "if", "elif", "else", "and", "or", "not",
    "in", "is", "lambda", "del", "global", "nonlocal", "assert", "filter",
    "map", "list", "dict", "set", "id", "len", "max", "min", "sum",
    "range", "open", "print", "input", "format", "hash",
}

# Matches _qp.get("key") or _qp.get("key", default)
QP_RE = re.compile(
    r"""_qp\.get\(\s*['"](\w+)['"]\s*(?:,\s*([^)]+?))?\s*\)""",
    re.DOTALL,
)


def safe_name(key: str) -> str:
    """Escape Python keyword conflicts."""
    return key + "_" if key in PYTHON_KEYWORDS else key


def infer_type_and_default(key: str, raw_default: str | None) -> tuple[str, str]:
    """
    Return (python_type_hint, query_default_expr).
    We keep defaults as FastAPI Query(default=...) string values.
    Query params on the wire are always strings; FastAPI coerces typed params.
    """
    if raw_default is None:
        return "str | None", "None"

    d = raw_default.strip()

    # Handle special FastAPI-style extras passed accidentally: "50, type=int"
    if "type=int" in d:
        num_match = re.match(r"(\d+)", d)
        return "int", num_match.group(1) if num_match else "0"

    if d == "None" or d == "":
        return "str | None", "None"

    # Integer literal (possibly negative)
    if re.match(r"^-?\d+$", d):
        return "int", d

    # Float literal
    if re.match(r"^-?\d+\.\d+$", d):
        return "float", d

    # Boolean literals
    if d == "True":
        return "bool", "True"
    if d == "False":
        return "bool", "False"

    # Quoted string (single or double, possibly with value like 'all', '')
    if re.match(r"""^(['"]).*\1$""", d):
        return "str", d

    # Double-quoted numeric strings like "50", "30"
    m = re.match(r'^"(\d+)"$', d)
    if m:
        return "int", m.group(1)

    # Anything else treat as str with default None to be safe
    return "str | None", "None"


def collect_qp_params(body: str) -> OrderedDict[str, tuple[str, str]]:
    """
    Scan body for all _qp.get() calls.
    Returns {key: (safe_name, 'type hint', 'default expr')} ordered by appearance.
    Deduplicates — first occurrence wins for type/default.
    """
    params: OrderedDict[str, tuple[str, str]] = OrderedDict()
    for m in QP_RE.finditer(body):
        key = m.group(1)
        raw_default = m.group(2)
        if key not in params:
            ptype, pdefault = infer_type_and_default(key, raw_default)
            params[key] = (ptype, pdefault)
    return params


def build_query_params(params: OrderedDict) -> list[str]:
    """Build 'name: type = Query(default=x)' strings."""
    result = []
    for key, (ptype, pdefault) in params.items():
        pname = safe_name(key)
        result.append(f"{pname}: {ptype} = Query(default={pdefault})")
    return result


def rewrite_body(body: str, params: OrderedDict) -> str:
    """
    1. Remove the `_qp = dict(request.query_params)` line
    2. Replace _qp.get('key', ...) → safe_name(key)
    """
    # Step 1: remove _qp assignment line
    body = re.sub(r"^\s*_qp\s*=\s*dict\(request\.query_params\)\s*\n", "", body, flags=re.MULTILINE)

    # Step 2: replace each _qp.get() call with just the parameter name
    def replacer(m: re.Match) -> str:
        key = m.group(1)
        return safe_name(key)

    body = QP_RE.sub(replacer, body)
    return body


def after_rewrite_needs_request(body: str, sig_params: str) -> bool:
    """Check if `request` is still used anywhere in body after _qp removal."""
    return "request." in body or "await request" in body


def transform_file(content: str) -> tuple[str, dict]:
    stats = {"functions_converted": 0, "params_added": 0, "qp_calls_replaced": 0}
    lines = content.split("\n")
    result_lines = lines[:]

    # Find all async def positions
    fn_positions = []
    for i, line in enumerate(lines):
        if re.match(r"^\s*async def \w+\(", line):
            fn_positions.append(i)

    # Process in reverse order so line numbers stay valid
    for fn_idx in reversed(fn_positions):
        fn_line = lines[fn_idx]
        sig_match = re.match(r"^(\s*)async def (\w+)\(([^)]*)\)\s*:", fn_line)
        if not sig_match:
            continue

        indent, fn_name, existing_params = (
            sig_match.group(1), sig_match.group(2), sig_match.group(3)
        )
        body_indent = indent + "    "

        # Collect body lines (until next same-or-lower indent def/class or EOF)
        body_start = fn_idx + 1
        body_end = body_start
        while body_end < len(lines):
            bl = lines[body_end]
            # Empty lines and comment-only lines are OK to include
            if bl.strip() and not bl.strip().startswith("#"):
                bl_indent_len = len(bl) - len(bl.lstrip())
                if bl_indent_len <= len(indent) and re.match(r"^\s*(async def|def|class)\b", bl):
                    break
            body_end += 1

        body_slice = lines[body_start:body_end]
        body_text = "\n".join(body_slice)

        # Skip if no _qp pattern in body (or only in docstring)
        if "_qp = dict(request.query_params)" not in body_text:
            continue

        # Check it's not ONLY inside a docstring
        # Strip docstrings and recheck
        body_no_docs = re.sub(r'""".*?"""', "", body_text, flags=re.DOTALL)
        body_no_docs = re.sub(r"'''.*?'''", "", body_no_docs, flags=re.DOTALL)
        if "_qp = dict(request.query_params)" not in body_no_docs:
            # Only in docstring — fix by removing the docstring line only if it's a stray
            # (the _qp assignment in the docstring means the docstring just mentions it)
            continue

        # Collect params from the real body
        params = collect_qp_params(body_no_docs)
        if not params:
            continue

        # Build new body
        new_body_text = rewrite_body(body_text, params)
        qp_replaced = body_text.count("_qp.get(") - new_body_text.count("_qp.get(")

        # Build new signature params
        query_params = build_query_params(params)
        existing = [p.strip() for p in existing_params.split(",") if p.strip()]

        # Check if request is still needed
        still_needs_request = after_rewrite_needs_request(new_body_text, existing_params)

        if still_needs_request:
            new_params_list = existing + query_params
        else:
            # Drop request: Request from existing params
            existing_clean = [p for p in existing if not re.match(r"request\s*:", p.strip())]
            new_params_list = existing_clean + query_params

        new_params_str = ", ".join(new_params_list)
        new_fn_line = f"{indent}async def {fn_name}({new_params_str}):"

        # Reconstruct result lines
        result_lines[fn_idx] = new_fn_line
        for i, bl in enumerate(new_body_text.split("\n")):
            if body_start + i < len(result_lines):
                result_lines[body_start + i] = bl

        stats["functions_converted"] += 1
        stats["params_added"] += len(params)
        stats["qp_calls_replaced"] += qp_replaced

    new_content = "\n".join(result_lines)

    # Update imports: ensure Query is in fastapi import
    if stats["functions_converted"] > 0:
        fa_match = re.search(r"^from fastapi import (.+)$", new_content, re.MULTILINE)
        if fa_match:
            existing_imports = {s.strip() for s in fa_match.group(1).split(",")}
            existing_imports.add("Query")
            new_import = f"from fastapi import {', '.join(sorted(existing_imports))}"
            new_content = re.sub(
                r"^from fastapi import .+$", new_import, new_content,
                flags=re.MULTILINE, count=1,
            )
        # Also ensure Optional is imported if we use str | None anywhere
        # (Python 3.10+ union syntax is fine — no Optional needed)

    return new_content, stats


def syntax_ok(content: str, fname: str) -> bool:
    try:
        ast.parse(content)
        return True
    except SyntaxError as e:
        print(f"    ⚠  SYNTAX ERROR: line {e.lineno}: {e.msg}")
        return False


def main():
    files = sorted(
        f for f in os.listdir(ROUTER_DIR)
        if f.endswith(".py") and f not in ("__init__.py",)
    )

    total_stats: dict[str, int] = {}
    changed, unchanged, syntax_errors = 0, 0, 0

    for fname in files:
        path = os.path.join(ROUTER_DIR, fname)
        original = open(path).read()
        transformed, stats = transform_file(original)

        if transformed == original:
            unchanged += 1
            continue

        if not syntax_ok(transformed, fname):
            syntax_errors += 1
            print(f"  ❌ {fname}: SKIPPED (syntax error after transform)")
            continue

        if not DRY_RUN:
            open(path, "w").write(transformed)

        changed += 1
        tag = "[DRY] " if DRY_RUN else ""
        summary = ", ".join(f"{k}={v}" for k, v in stats.items() if v > 0)
        print(f"  {tag}✅ {fname}: {summary}")

        for k, v in stats.items():
            total_stats[k] = total_stats.get(k, 0) + v

    label = "[DRY RUN] " if DRY_RUN else ""
    print(f"\n{label}Changed: {changed} | Unchanged: {unchanged} | Syntax errors: {syntax_errors}")
    print("\nTotal:")
    for k, v in sorted(total_stats.items()):
        if v:
            print(f"  {k:30s} {v:4d}")


if __name__ == "__main__":
    main()
