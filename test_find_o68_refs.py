import re

with open("bay_get_raw.html", "r", encoding="utf-8") as f:
    html = f.read()

# Let's search for references to O68
print("Searching for O68 references:")
for i, line in enumerate(html.splitlines(), 1):
    if "O68" in line:
        print(f"L{i}: {line[:300]}")

# Let's search for O25 (the grid) references
print("\nSearching for O25 references:")
for i, line in enumerate(html.splitlines(), 1):
    if "O25" in line:
        print(f"L{i}: {line[:300]}")
