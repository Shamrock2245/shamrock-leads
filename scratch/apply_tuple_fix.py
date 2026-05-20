import ast
import os

def is_json_response_imported(tree, content):
    if "from fastapi.responses import JSONResponse" in content:
        return True
    if "from starlette.responses import JSONResponse" in content:
        return True
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in ("fastapi.responses", "starlette.responses"):
                for name in node.names:
                    if name.name == "JSONResponse":
                        return True
    return False

def apply_fix_to_all_routers():
    router_dir = "/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/routers"
    modified_files = 0
    total_replacements = 0

    for filename in sorted(os.listdir(router_dir)):
        if not filename.endswith(".py") or filename == "__init__.py":
            continue
        
        filepath = os.path.join(router_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        try:
            tree = ast.parse(content, filename=filepath)
        except Exception as e:
            print(f"Error parsing {filename}: {e}")
            continue

        # Find all QUART_STYLE_TUPLE returns
        replacements = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
                elts = node.value.elts
                if len(elts) == 2:
                    second = elts[1]
                    status_val = None
                    if isinstance(second, ast.Constant) and isinstance(second.value, int):
                        status_val = second.value
                    elif isinstance(second, ast.Num): # Python < 3.8
                        status_val = second.n
                    
                    if status_val is not None:
                        # Quart-style tuple return found!
                        elts0_src = ast.get_source_segment(content, elts[0])
                        replacements.append({
                            "node": node,
                            "status_val": status_val,
                            "elts0_src": elts0_src,
                        })

        if not replacements:
            continue

        print(f"Applying fix to {filename} ({len(replacements)} replacements)...")
        lines = content.splitlines()

        # Sort replacements in reverse order (bottom to top) to keep line offsets stable
        replacements.sort(key=lambda r: (r["node"].lineno, r["node"].col_offset), reverse=True)

        for r in replacements:
            node = r["node"]
            status = r["status_val"]
            elts_src = r["elts0_src"]
            
            new_return = f"return JSONResponse(status_code={status}, content={elts_src})"
            
            # Extract indentation of the original return statement line
            orig_line = lines[node.lineno - 1]
            orig_indent = orig_line[:len(orig_line) - len(orig_line.lstrip())]
            
            new_lines = [f"{orig_indent}{new_return}"]
            
            # Replace lines from node.lineno-1 to node.end_lineno (inclusive)
            lines_before = lines[:node.lineno - 1]
            lines_after = lines[node.end_lineno:]
            
            lines = lines_before + new_lines + lines_after

        new_content = "\n".join(lines)

        # Check and insert import if missing
        if not is_json_response_imported(tree, content):
            lines = new_content.splitlines()
            insert_idx = 0
            for i, line in enumerate(lines[:15]):
                if "from __future__" in line:
                    insert_idx = i + 1
                    break
            lines.insert(insert_idx, "from fastapi.responses import JSONResponse")
            new_content = "\n".join(lines)
            print(f"  -> Added JSONResponse import to {filename}")

        # Write the modified content back
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        
        modified_files += 1
        total_replacements += len(replacements)

    print(f"\nSuccessfully finished automated refactoring!")
    print(f"Modified files: {modified_files}")
    print(f"Total replacements applied: {total_replacements}")

if __name__ == "__main__":
    apply_fix_to_all_routers()
