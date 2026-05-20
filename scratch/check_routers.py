import os
import re

routers_dir = "/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/routers"
pattern = re.compile(r'@(router|\w+_bp)\.(get|post|put|patch|delete)\(["\']([^"\']*)["\']')

results = []

for root, _, files in os.walk(routers_dir):
    for file in files:
        if file.endswith(".py") and file != "helpers.py" and file != "__init__.py":
            filepath = os.path.join(root, file)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
                
            lines = content.splitlines()
            for idx, line in enumerate(lines):
                match = pattern.search(line)
                if match:
                    route_path = match.group(3)
                    if "<" in route_path or ">" in route_path:
                        results.append({
                            "file": file,
                            "line_num": idx + 1,
                            "line": line.strip(),
                            "path": route_path
                        })

print(f"Found {len(results)} routes with legacy angle-bracket parameters:")
for r in results:
    print(f"{r['file']}:{r['line_num']} -> {r['line']}")
