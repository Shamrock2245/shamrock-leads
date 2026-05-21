import os
import ast
import re

def patch_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        tree = ast.parse(content, filename=filepath)
    except Exception as e:
        print(f"❌ AST Parse Error in {filepath}: {e}")
        return False

    scrape_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "scrape":
            scrape_func = node
            break

    if not scrape_func:
        return False

    # Find the except handlers in scrape() that catch Exception or are bare, and don't raise
    swallowing_handlers = []
    for node in ast.walk(scrape_func):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                is_general = False
                if handler.type is None:  # bare except
                    is_general = True
                elif isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
                    is_general = True
                
                if is_general:
                    has_raise = False
                    for handler_stmt in ast.walk(handler):
                        if isinstance(handler_stmt, ast.Raise):
                            has_raise = True
                            break
                    if not has_raise:
                        swallowing_handlers.append(handler)

    if not swallowing_handlers:
        return False

    # We will modify the file content lines
    lines = content.splitlines()
    
    # Process from the bottom of the file up, so that line numbers don't shift!
    # Sort handlers by starting line number in descending order
    swallowing_handlers.sort(key=lambda h: h.lineno, reverse=True)

    modified = False
    for handler in swallowing_handlers:
        # Determine the indentation level of the handler's body
        # Let's look at the first statement inside the handler
        if not handler.body:
            continue
        first_stmt = handler.body[0]
        # We can find the indentation of the first statement line
        stmt_line_idx = first_stmt.lineno - 1
        stmt_line = lines[stmt_line_idx]
        indentation = len(stmt_line) - len(stmt_line.lstrip())
        indent_str = " " * indentation
        
        # We want to replace any return statements that return an empty list or just append raise at the end of the body
        # Let's find the last statement in the handler
        last_stmt = handler.body[-1]
        last_line_idx = last_stmt.end_lineno - 1
        
        # Check if the last statement is a return statement of [] or empty
        is_return_empty = False
        if isinstance(last_stmt, ast.Return):
            if last_stmt.value is None:
                is_return_empty = True
            elif isinstance(last_stmt.value, ast.List) and len(last_stmt.value.elts) == 0:
                is_return_empty = True
                
        if is_return_empty:
            # We can replace the return statement with "raise"
            # Let's inspect the last statement's line
            line_to_modify = lines[last_line_idx]
            # Find the position of 'return' in the line
            return_match = re.search(r'\breturn\b', line_to_modify)
            if return_match:
                start_char = return_match.start()
                # Replace everything from 'return' to the end of the statement on this line with 'raise'
                new_line = line_to_modify[:start_char] + "raise"
                lines[last_line_idx] = new_line
                modified = True
                print(f"  [REPLACE RETURN] Line {last_stmt.lineno}: '{line_to_modify.strip()}' -> 'raise'")
        else:
            # Otherwise, append "raise" on a new line after the last statement of the handler
            # Insert at last_line_idx + 1
            lines.insert(last_line_idx + 1, f"{indent_str}raise")
            modified = True
            print(f"  [APPEND RAISE] After Line {last_stmt.end_lineno}: added 'raise'")

    if modified:
        new_content = "\n".join(lines) + "\n"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"✅ Patched {filepath}")
        return True

    return False

if __name__ == "__main__":
    counties_dir = "scrapers/counties"
    files = [f for f in os.listdir(counties_dir) if f.endswith(".py") and f != "__init__.py"]
    for filename in sorted(files):
        filepath = os.path.join(counties_dir, filename)
        patch_file(filepath)
