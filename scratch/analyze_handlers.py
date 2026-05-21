import os
import ast

counties_dir = "scrapers/counties"
files = [f for f in os.listdir(counties_dir) if f.endswith(".py") and f != "__init__.py"]

def find_top_level_tries(node):
    tries = []
    if isinstance(node, ast.Try):
        tries.append(node)
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tries.extend(find_top_level_tries(child))
    return tries

for filename in sorted(files):
    filepath = os.path.join(counties_dir, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        tree = ast.parse(content, filename=filepath)
    except Exception as e:
        continue

    scrape_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "scrape":
            scrape_func = node
            break

    if not scrape_func:
        continue

    tries = find_top_level_tries(scrape_func)
    if not tries:
        continue

    print(f"=== {filename} ===")
    lines = content.splitlines()
    for t in tries:
        for handler in t.handlers:
            # Print the handler definition and its body statements
            handler_type = "bare"
            if handler.type:
                if isinstance(handler.type, ast.Name):
                    handler_type = handler.type.id
                elif isinstance(handler.type, ast.Attribute):
                    handler_type = f"{handler.type.value.id}.{handler.type.attr}"
                else:
                    handler_type = ast.dump(handler.type)
            
            print(f"  Except {handler_type} (line {handler.lineno}-{handler.end_lineno}):")
            for stmt in handler.body:
                stmt_str = lines[stmt.lineno - 1].strip()
                if stmt.lineno != stmt.end_lineno:
                    stmt_str += " ... " + lines[stmt.end_lineno - 1].strip()
                print(f"    - {type(stmt).__name__}: {stmt_str}")
