import os
import re

routers_dir = "/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/routers"

# Regex to match FastAPI / Quart decorators
# e.g., @bonds_bp.post("/active-bonds/<booking_number>/check-in")
decorator_pattern = re.compile(
    r'^(\s*)@(\w+)\.(get|post|put|patch|delete)\((["\'])([^"\']*)(["\'])(.*)\)',
    re.MULTILINE
)

# Regex to match Flask/Quart angle bracket parameters (optional type prefix followed by name)
# e.g., <booking_number>, <int:webhook_id>, <string:county>
angle_bracket_pattern = re.compile(r'<(?:[a-zA-Z_][a-zA-Z0-9_]*:)?([a-zA-Z_][a-zA-Z0-9_]*)>')

total_files_modified = 0
total_replacements = 0
total_duplicates_removed = 0

for root, _, files in os.walk(routers_dir):
    for file in files:
        if file.endswith(".py") and file not in ("helpers.py", "__init__.py"):
            filepath = os.path.join(root, file)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            lines = content.splitlines()
            new_lines = []
            file_modified = False
            last_decorator_sig = None
            
            idx = 0
            while idx < len(lines):
                line = lines[idx]
                stripped = line.strip()
                
                # Check for duplicate consecutive decorators
                match = decorator_pattern.match(line)
                if match:
                    indent, bp_name, method, quote, path, quote_end, rest = match.groups()
                    
                    # Convert angle brackets to curly brackets in the path
                    new_path, count = angle_bracket_pattern.subn(r'{\1}', path)
                    if count > 0:
                        line = f"{indent}@{bp_name}.{method}({quote}{new_path}{quote_end}{rest})"
                        file_modified = True
                        total_replacements += count
                    
                    # Deduplicate logic
                    sig = (bp_name, method, new_path)
                    if sig == last_decorator_sig:
                        # Duplicate found! Skip this line
                        idx += 1
                        file_modified = True
                        total_duplicates_removed += 1
                        continue
                    else:
                        last_decorator_sig = sig
                else:
                    if stripped:  # reset if we hit non-decorator non-empty lines
                        # (Allow successive different decorators, but if we hit an actual function definition or other code, reset)
                        if not stripped.startswith('@'):
                            last_decorator_sig = None

                new_lines.append(line)
                idx += 1

            if file_modified:
                new_content = "\n".join(new_lines) + "\n"
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                total_files_modified += 1
                print(f"Refactored: {file}")

print(f"\nSummary:")
print(f"Total files modified: {total_files_modified}")
print(f"Total legacy angle-bracket parameters converted: {total_replacements}")
print(f"Total duplicate decorators removed: {total_duplicates_removed}")
