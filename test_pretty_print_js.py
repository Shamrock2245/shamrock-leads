import re

with open("bay_get_raw.html", "r", encoding="utf-8") as f:
    html = f.read()

# Let's find definitions of O68 and O25 in line 37
# Component initializations are usually separated by semi-colon or comma
# Let's search for O68 in the JS block
js_block = html.splitlines()[36] # line 37 is index 36

# Let's find O68 initialization
print("O68 Initialization JS:")
match_o68 = re.search(r'(\bO68\b\s*=\s*new\s*Ext\.[^;]+;)', js_block)
if match_o68:
    print(match_o68.group(1))
else:
    print("Not found as new Ext...")

print("\nO25 Initialization JS:")
match_o25 = re.search(r'(\bO25\b\s*=\s*new\s*Ext\.[^;]+;)', js_block)
if match_o25:
    print(match_o25.group(1))
else:
    print("Not found as new Ext...")

print("\nLet's extract all parts of line 37 that mention O68:")
o68_mentions = [m.start() for m in re.finditer(r'\bO68\b', js_block)]
for start_idx in o68_mentions:
    snippet = js_block[max(0, start_idx-200):min(len(js_block), start_idx+200)]
    print(f"\n--- Mention at {start_idx} ---")
    print(snippet)
