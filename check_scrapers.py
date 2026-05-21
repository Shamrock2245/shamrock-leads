import os
import re
import ast

counties_dir = "scrapers/counties"
files = [f for f in os.listdir(counties_dir) if f.endswith(".py") and f != "__init__.py"]

print(f"Scanning {len(files)} county scrapers...")

for filename in sorted(files):
    filepath = os.path.join(counties_dir, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Check for general exception swallowing in scrape() method
    # Let's parse with AST to be precise
    try:
        tree = ast.parse(content, filename=filepath)
    except Exception as e:
        print(f"❌ {filename}: AST Parse Error: {e}")
        continue

    scrape_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "scrape":
            scrape_func = node
            break

    if not scrape_func:
        print(f"⚠️ {filename}: No scrape() method found!")
        continue

    # Let's check if there is a try-except block at the top level of scrape
    swallows = False
    has_try = False
    
    # We can look for try blocks in the scrape method
    for stmt in scrape_func.body:
        if isinstance(stmt, ast.Try):
            has_try = True
            for handler in stmt.handlers:
                # Check if it catches general Exception or is bare except
                is_general = False
                if handler.type is None:  # bare except
                    is_general = True
                elif isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
                    is_general = True
                
                if is_general:
                    # Check what is inside the handler body: does it raise/re-raise?
                    has_raise = False
                    for handler_stmt in ast.walk(handler):
                        if isinstance(handler_stmt, ast.Raise):
                            has_raise = True
                            break
                    if not has_raise:
                        swallows = True
                        break

    # Check for undefined detail_text references
    detail_text_refs = re.findall(r"\bdetail_text\b", content)
    
    status = []
    if has_try:
        status.append("has_try=True")
    if swallows:
        status.append("🚨 SWALLOWS_EXCEPTIONS")
    if detail_text_refs:
        status.append(f"⚠️ Has 'detail_text' ref count={len(detail_text_refs)}")

    if status:
        print(f"{filename.ljust(25)}: {', '.join(status)}")
