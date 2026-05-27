import re

HTML_COOLER = """
<tr><td>Commissary Number</td><td>8112259</td></tr>
<tr><td>Make Calls</td><td>Yes</td></tr>
<tr><td>Bond</td><td>No Bond</td></tr>
"""

HTML_WITH_BOND = """
<tr><td>Commissary Number</td><td>8112259</td></tr>
<tr><td>Make Calls</td><td>Yes</td></tr>
<tr><td>Bond</td><td>$5,000</td></tr>
"""

# New proposed safer table-based pattern
pattern = re.compile(
    r"(?:bond|amt|amount)[^<]{0,50}<\/td>\s*<td[^>]*>\s*[\$]?\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE
)

def test(html, label):
    print(f"\nTesting {label}:")
    match = pattern.search(html)
    if match:
        print(f"  Matched: {match.group(1)}")
    else:
        print("  No Match")

if __name__ == "__main__":
    test(HTML_COOLER, "Cooler HTML (No Bond)")
    test(HTML_WITH_BOND, "HTML with actual Bond ($5,000)")
