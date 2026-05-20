import os
import ast

def find_all_tuple_returns():
    router_dir = "/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/routers"
    for filename in sorted(os.listdir(router_dir)):
        if not filename.endswith(".py") or filename == "__init__.py":
            continue
        
        filepath = os.path.join(router_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        try:
            tree = ast.parse(content, filename=filename)
        except Exception as e:
            print(f"Error parsing {filename}: {e}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Return):
                if isinstance(node.value, ast.Tuple):
                    elts = node.value.elts
                    # Check if the tuple has a literal status code as the second element (Quart style)
                    is_quart_style = False
                    status_val = None
                    if len(elts) == 2:
                        second = elts[1]
                        if isinstance(second, ast.Constant) and isinstance(second.value, int):
                            is_quart_style = True
                            status_val = second.value
                        elif isinstance(second, ast.Num): # Python < 3.8
                            is_quart_style = True
                            status_val = second.n

                    if is_quart_style:
                        print(f"QUART_STYLE_TUPLE: {filename}:{node.lineno} (status {status_val})")
                    else:
                        print(f"OTHER_TUPLE: {filename}:{node.lineno} with {len(elts)} elements")

if __name__ == "__main__":
    find_all_tuple_returns()
