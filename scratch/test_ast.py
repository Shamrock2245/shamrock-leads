import ast

filepath = "/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/routers/automation_control.py"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

tree = ast.parse(content)
for node in ast.walk(tree):
    if isinstance(node, ast.Return):
        print(f"Return at line {node.lineno}:")
        print(f"  value type: {type(node.value)}")
        if isinstance(node.value, ast.Tuple):
            print("  It IS a Tuple!")
        else:
            print("  It is NOT a Tuple!")
