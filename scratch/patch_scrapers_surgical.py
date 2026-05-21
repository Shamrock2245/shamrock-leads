import os
import ast
import re

counties_dir = "scrapers/counties"

def find_top_level_tries(node):
    tries = []
    if isinstance(node, ast.Try):
        tries.append(node)
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tries.extend(find_top_level_tries(child))
    return tries

def find_returns_in_node(node):
    returns = []
    # Helper to recursively find returns, but we must NOT enter nested functions/classes!
    def walk_returns(n):
        if isinstance(n, ast.Return):
            returns.append(n)
        for child in ast.iter_child_nodes(n):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk_returns(child)
    walk_returns(node)
    return returns

def patch_file(filepath, dry_run=True):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        tree = ast.parse(content, filename=filepath)
    except Exception as e:
        print(f"❌ {os.path.basename(filepath)}: AST Parse Error: {e}")
        return False

    scrape_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "scrape":
            scrape_func = node
            break

    if not scrape_func:
        return False

    tries = find_top_level_tries(scrape_func)
    if not tries:
        return False

    lines = content.splitlines()
    modified = False
    
    # We will collect all replacements as (line_idx, start_col, end_col)
    replacements = []

    for t in tries:
        for handler in t.handlers:
            # We check if this handler catches general Exception or bare or ImportError
            is_target_handler = False
            if handler.type is None:  # bare except
                is_target_handler = True
            elif isinstance(handler.type, ast.Name) and handler.type.id in ("Exception", "ImportError"):
                is_target_handler = True
            
            if not is_target_handler:
                continue

            returns = find_returns_in_node(handler)
            for ret in returns:
                # Check if it returns empty/[]/None
                is_empty_return = False
                if ret.value is None:
                    is_empty_return = True
                elif isinstance(ret.value, ast.List) and len(ret.value.elts) == 0:
                    is_empty_return = True
                elif isinstance(ret.value, ast.NameConstant) and ret.value.value is None:
                    is_empty_return = True
                elif isinstance(ret.value, ast.Constant) and ret.value.value is None:
                    is_empty_return = True

                if is_empty_return:
                    replacements.append((ret.lineno - 1, ret.col_offset, ret.end_col_offset))

    if not replacements:
        return False

    # Apply replacements from bottom to top, left to right, or just line-by-line
    # Since we might have multiple replacements on the same line (rare but possible),
    # let's group by line index and sort replacements on the same line in descending order of column offsets!
    from collections import defaultdict
    by_line = defaultdict(list)
    for line_idx, start, end in replacements:
        by_line[line_idx].append((start, end))

    for line_idx, ranges in by_line.items():
        # Sort ranges in descending order so replacing doesn't shift the offsets of previous ones on the same line!
        ranges.sort(key=lambda r: r[0], reverse=True)
        line = lines[line_idx]
        original_line = line
        for start, end in ranges:
            line = line[:start] + "raise" + line[end:]
        lines[line_idx] = line
        modified = True
        print(f"  [PATCH] Line {line_idx + 1}: '{original_line.strip()}' -> '{line.strip()}'")

    if modified:
        if not dry_run:
            new_content = "\n".join(lines) + "\n"
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"  ✅ Saved changes to {os.path.basename(filepath)}")
        else:
            print(f"  ℹ️ [DRY RUN] Would save changes to {os.path.basename(filepath)}")
        return True

    return False

if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    files = [f for f in os.listdir(counties_dir) if f.endswith(".py") and f != "__init__.py"]
    print(f"Surgical patch: dry_run={dry_run}")
    patched_count = 0
    for filename in sorted(files):
        filepath = os.path.join(counties_dir, filename)
        if patch_file(filepath, dry_run=dry_run):
            patched_count += 1
    print(f"Done. Patched {patched_count} files.")
