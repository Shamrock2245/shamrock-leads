import os
import ast

counties_dir = "scrapers/counties"
target_files = [
    "brevard.py", "citrus.py", "dixie.py", "escambia.py", 
    "gadsden.py", "highlands.py", "martin.py", "okeechobee.py", 
    "palm_beach.py", "taylor.py"
]

def find_top_level_tries(node):
    tries = []
    if isinstance(node, ast.Try):
        tries.append(node)
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tries.extend(find_top_level_tries(child))
    return tries

for filename in target_files:
    filepath = os.path.join(counties_dir, filename)
    if not os.path.exists(filepath):
        print(f"Skipping {filename}: not found")
        continue

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        tree = ast.parse(content, filename=filepath)
    except Exception as e:
        print(f"❌ {filename}: AST error {e}")
        continue

    scrape_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "scrape":
            scrape_func = node
            break

    if not scrape_func:
        print(f"⚠️ {filename}: no scrape()")
        continue

    tries = find_top_level_tries(scrape_func)
    print(f"=== {filename} ===")
    lines = content.splitlines()
    for t in tries:
        for handler in t.handlers:
            is_general = False
            if handler.type is None:
                is_general = True
            elif isinstance(handler.type, ast.Name) and handler.type.id in ("Exception", "ImportError"):
                is_general = True
            
            if not is_general:
                continue

            has_raise = False
            for handler_stmt in ast.walk(handler):
                if isinstance(handler_stmt, ast.Raise):
                    has_raise = True
                    break
            
            if not has_raise:
                print(f"  Except block on lines {handler.lineno} to {handler.end_lineno}:")
                for line_no in range(handler.lineno, handler.end_lineno + 1):
                    print(f"    {line_no}: {lines[line_no - 1]}")
