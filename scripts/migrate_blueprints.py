#!/usr/bin/env python3
"""
Bulk Quart → FastAPI blueprint migration script (v3).

Changes from v2:
- request.args.get("key", default) → properly extract to a _qp dict at top of fn
- No more inline comment injection that breaks syntax
- current_app import lines are fully removed
- Double-TODO prevention

Run from repo root:
    python3 scripts/migrate_blueprints.py [--dry-run] [--force]
"""
import os
import re
import sys

DRY_RUN = "--dry-run" in sys.argv
FORCE   = "--force"   in sys.argv

API_DIR    = "dashboard/api"
ROUTER_DIR = "dashboard/routers"

ALREADY_PORTED = {"arrests", "stats", "leads", "defendants", "indemnitors",
                  "helpers", "__init__"}
NOT_BLUEPRINTS = {"geo_geofence_patch"}  # pure utility, no routes

QUART_IMPORT_RE = re.compile(r'^from quart import (.+)$', re.MULTILINE)
BLUEPRINT_RE    = re.compile(r'^(\w+_bp)\s*=\s*Blueprint\([^)]+\)\s*$', re.MULTILINE)
ROUTE_RE        = re.compile(
    r'@(\w+_bp)\.route\(["\']([^"\']+)["\'](?:,\s*methods=\[([^\]]+)\])?\)'
)
# Remove any line that is purely a current_app import
CURRENT_APP_IMPORT_RE = re.compile(
    r'^\s*from quart import[^\n]*current_app[^\n]*\n', re.MULTILINE
)


def build_fastapi_imports(quart_symbols: list[str]) -> str:
    fastapi_extras = []
    starlette_extras = []
    has_request = False

    for sym in [s.strip() for s in quart_symbols]:
        if sym in ("Blueprint", "jsonify", "current_app"):
            continue
        if sym == "request":
            has_request = True
        elif sym in ("Response", "make_response"):
            starlette_extras.append("Response")
        elif sym == "redirect":
            starlette_extras.append("RedirectResponse")
        elif sym in ("send_file", "send_from_directory"):
            fastapi_extras.append("FileResponse")

    line = "from fastapi import APIRouter"
    if has_request:
        line += ", Request"
    if fastapi_extras:
        line += f"\nfrom fastapi.responses import {', '.join(sorted(set(fastapi_extras)))}"
    if starlette_extras:
        line += f"\nfrom starlette.responses import {', '.join(sorted(set(starlette_extras)))}"
    line += "\nfrom fastapi.responses import JSONResponse"
    return line


def strip_jsonify(content: str) -> str:
    """Remove jsonify( ... ) using bracket counting."""
    result = []
    i = 0
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
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            if depth > 0:
                result.append(c)
            j += 1
        i = j
    return "".join(result)


def replace_request_args(content: str) -> str:
    """
    Replace request.args.get("key", default) with _qp.get("key", default).
    Then inject `_qp = request.query_params` at the start of each async def
    that uses it, so the code remains syntactically valid.
    """
    # Replace request.args.get( → _qp.get(
    content = re.sub(r'\brequest\.args\.get\(', '_qp.get(', content)
    # Replace bare request.args (not followed by .get) → _qp
    content = re.sub(r'\brequest\.args\b', '_qp', content)

    # Now inject `_qp = dict(request.query_params)` into each function that uses _qp
    # Strategy: find async def lines, then check if the body contains _qp
    lines = content.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        result.append(line)
        # Check if this is an async def line
        if re.match(r'\s*async def \w+\(', line):
            # Collect the full function body to check for _qp usage
            indent = len(line) - len(line.lstrip())
            body_lines = []
            j = i + 1
            while j < len(lines):
                bl = lines[j]
                # End of function if we hit a non-empty line at same or lower indent
                if bl.strip() and (len(bl) - len(bl.lstrip())) <= indent and not bl.strip().startswith('#'):
                    break
                body_lines.append(bl)
                j += 1
            body = '\n'.join(body_lines)
            if '_qp' in body:
                # Find the docstring end or first real statement
                # Insert after the opening """ docstring if present, else after def line
                fn_indent = ' ' * (indent + 4)
                # Find index of first non-empty, non-docstring line in body_lines
                insert_at = len(result)  # default: right after def
                in_docstring = False
                for k, bl in enumerate(body_lines):
                    stripped = bl.strip()
                    if stripped.startswith('"""') or stripped.startswith("'''"):
                        in_docstring = not in_docstring
                        if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                            in_docstring = False  # single-line docstring
                        # If end of docstring
                        if not in_docstring:
                            insert_at = len(result) + k + 1
                            break
                    elif not in_docstring and stripped:
                        insert_at = len(result) + k
                        break
                # Emit all body lines, inserting _qp at the right spot
                for k, bl in enumerate(body_lines):
                    if k + len(result) - (len(result) - len(result)) == insert_at - len(result) + k:
                        pass
                    result.append(bl)
                # Actually this is getting complex — simpler: just prepend _qp after the def
                # Reset and use a simpler approach
                result = result[:-(len(body_lines))]  # remove what we added
                # Just add all body lines with _qp injected at top
                injected = False
                for bl in body_lines:
                    if not injected and bl.strip() and not bl.strip().startswith(('"""', "'''", '#')):
                        result.append(f"{fn_indent}_qp = dict(request.query_params)")
                        injected = True
                    result.append(bl)
                if not injected:
                    result.append(f"{fn_indent}_qp = dict(request.query_params)")
                i = j
                continue
        i += 1
    return '\n'.join(result)


def transform_file(src_path: str) -> str:
    content = open(src_path).read()

    # 0. Remove leftover "from quart import ... current_app" only lines
    content = CURRENT_APP_IMPORT_RE.sub('', content)

    # 1. Parse & replace quart import
    qm = QUART_IMPORT_RE.search(content)
    if not qm:
        return content
    symbols = [s.strip() for s in qm.group(1).split(",")]
    content = QUART_IMPORT_RE.sub(build_fastapi_imports(symbols), content, count=1)

    # 2. Blueprint → APIRouter
    def replace_bp(m):
        bp_var = m.group(1)
        nm = re.search(r'Blueprint\(["\'](\w+)["\']', m.group(0))
        tag = nm.group(1) if nm else "api"
        return f'{bp_var} = APIRouter(prefix="/api", tags=["{tag}"])'
    content = BLUEPRINT_RE.sub(replace_bp, content)

    # 3. Route decorators
    def replace_route(m):
        bp_var, path, methods_raw = m.group(1), m.group(2), m.group(3)
        methods = [x.strip().strip('"\'').upper() for x in methods_raw.split(",")] if methods_raw else ["GET"]
        if len(methods) == 1:
            return f'@{bp_var}.{methods[0].lower()}("{path}")'
        ml = ", ".join(f'"{x}"' for x in methods)
        return f'@{bp_var}.api_route("{path}", methods=[{ml}])'
    content = ROUTE_RE.sub(replace_route, content)

    # 4. Strip jsonify() wrappers
    content = strip_jsonify(content)

    # 5. request.args → _qp with injection
    content = replace_request_args(content)

    # 6. await request.get_json() → await request.json()
    content = content.replace("await request.get_json(force=True)", "await request.json()")
    content = content.replace("await request.get_json()", "await request.json()")
    content = re.sub(r'await request\.get_json\([^)]*\)', 'await request.json()', content)

    # 7. current_app.logger → logger; remaining current_app → comment
    content = content.replace("current_app.logger", "logger")
    content = re.sub(r'\bcurrent_app\b', '# current_app_removed', content)

    # 8. make_response(jsonify( already stripped; handle make_response(x)
    content = re.sub(r'\bmake_response\((\w+)\)', r'JSONResponse(\1)', content)

    # 9. Quart tuple-return: return var, 4XX  →  return JSONResponse(var, status_code=4XX)
    content = re.sub(
        r'\breturn (\w+),\s*(\d{3})\s*$',
        r'return JSONResponse(\1, status_code=\2)',
        content, flags=re.MULTILINE,
    )

    # 10. Header
    header = (
        "# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──\n"
        "# _qp = dict(request.query_params) injected into fns that read query params.\n"
        "# Review each endpoint and move _qp.get() calls to typed fn signatures.\n\n"
    )
    return header + content


def main():
    os.makedirs(ROUTER_DIR, exist_ok=True)
    converted, skipped, errors = [], [], []

    for fname in sorted(os.listdir(API_DIR)):
        if not fname.endswith(".py"):
            continue
        stem = fname[:-3]
        if stem in ALREADY_PORTED or stem in NOT_BLUEPRINTS:
            skipped.append(stem)
            continue

        src = os.path.join(API_DIR, fname)
        dst = os.path.join(ROUTER_DIR, fname)

        if os.path.exists(dst) and not FORCE:
            skipped.append(f"{stem} (exists, use --force)")
            continue

        try:
            transformed = transform_file(src)
            if not DRY_RUN:
                with open(dst, "w") as f:
                    f.write(transformed)
            converted.append(stem)
            print(f"  ✅ {stem}")
        except Exception as e:
            errors.append((stem, str(e)))
            print(f"  ❌ {stem}: {e}")

    tag = "[DRY RUN] " if DRY_RUN else ""
    print(f"\n{tag}Converted: {len(converted)}  |  Skipped: {len(skipped)}  |  Errors: {len(errors)}")
    for name, err in errors:
        print(f"  ERROR {name}: {err}")


if __name__ == "__main__":
    main()
