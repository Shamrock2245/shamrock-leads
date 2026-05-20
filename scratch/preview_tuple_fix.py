import ast
import os
import difflib

def preview_fix_for_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        tree = ast.parse(content, filename=filepath)
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
        return

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
                    # It's a quart-style tuple return!
                    # Get the source of elts[0]
                    elts0_src = ast.get_source_segment(content, elts[0])
                    # Get character range for the Return node
                    # Return node starts at node.lineno, node.col_offset
                    # and ends at node.end_lineno, node.end_col_offset
                    replacements.append({
                        "node": node,
                        "status_val": status_val,
                        "elts0_src": elts0_src,
                    })

    if not replacements:
        print(f"No tuple returns found in {os.path.basename(filepath)}")
        return

    # To apply replacements correctly without messing up offsets,
    # we sort them in reverse order (from bottom of the file to top)
    lines = content.splitlines()
    new_content = content

    # Sort replacements by lineno desc, col_offset desc
    replacements.sort(key=lambda r: (r["node"].lineno, r["node"].col_offset), reverse=True)

    for r in replacements:
        node = r["node"]
        status = r["status_val"]
        elts_src = r["elts0_src"]
        
        # We need to reconstruct the return statement.
        # Preserve indentation (col_offset)
        indent = " " * node.col_offset
        new_return = f"return JSONResponse(status_code={status}, content={elts_src})"
        
        # We want to replace from start of node to end of node.
        # Let's convert line/col to character indexes in new_content.
        # But wait! A simpler way is to replace line by line if it's on a single line,
        # or use line numbers directly if we split the file into lines.
        # Let's do a robust line-based replacement.
        # Since we sorted reverse, we can replace lines[node.lineno-1 : node.end_lineno]
        # with the new return statement (with proper indent).
        lines_before = lines[:node.lineno-1]
        lines_after = lines[node.end_lineno:]
        
        # Keep indentation of the original line
        orig_line = lines[node.lineno-1]
        orig_indent = orig_line[:len(orig_line) - len(orig_line.lstrip())]
        
        # Format the new return statement with the original indent
        new_lines = [f"{orig_indent}{new_return}"]
        
        lines = lines_before + new_lines + lines_after

    new_content = "\n".join(lines)

    # Check if JSONResponse is imported
    has_json_response = "JSONResponse" in content
    if not has_json_response:
        # Let's add the import statement at the top of the file
        # Find a good place to insert: after __future__ imports, or just at the top
        lines = new_content.splitlines()
        insert_idx = 0
        for i, line in enumerate(lines[:10]):
            if "from __future__" in line:
                insert_idx = i + 1
                break
        lines.insert(insert_idx, "from fastapi.responses import JSONResponse")
        new_content = "\n".join(lines)

    # Print diff
    orig_lines = content.splitlines(keepends=True)
    new_lines_split = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        orig_lines, new_lines_split,
        fromfile=f"a/{os.path.basename(filepath)}",
        tofile=f"b/{os.path.basename(filepath)}"
    )
    print("".join(diff))

if __name__ == "__main__":
    preview_fix_for_file("/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/routers/automation_control.py")
