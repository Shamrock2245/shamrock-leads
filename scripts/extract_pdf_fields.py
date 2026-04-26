#!/usr/bin/env python3
"""
Extract AcroForm field names from PDF templates.
Usage: python scripts/extract_pdf_fields.py
"""
import sys
import json

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. Run: pip install pymupdf")
    sys.exit(1)

PDFS = {
    "osi": "/Users/brendan/Desktop/shamrock-active-software/osiforms/Appearance Bond blank.pdf",
    "palmetto": "/Users/brendan/Desktop/shamrock-active-software/palmetto-forms/Shamrock Palmetto Official Appearance Bond.pdf",
}

results = {}

for surety, path in PDFS.items():
    print(f"\n{'='*60}")
    print(f"  {surety.upper()} APPEARANCE BOND")
    print(f"  {path}")
    print(f"{'='*60}")
    try:
        doc = fitz.open(path)
        print(f"  Pages: {len(doc)}")
        fields = []
        for page_num, page in enumerate(doc):
            widgets = list(page.widgets())
            if widgets:
                print(f"\n  Page {page_num + 1}: {len(widgets)} form fields")
                for w in widgets:
                    field = {
                        "name": w.field_name,
                        "type": w.field_type_string,
                        "value": w.field_value or "",
                        "page": page_num + 1,
                        "rect": [round(x, 1) for x in w.rect],
                    }
                    fields.append(field)
                    print(f"    [{w.field_type_string:10s}] {w.field_name:<40s} = '{w.field_value or ''}' @ {w.rect}")
            else:
                print(f"\n  Page {page_num + 1}: NO form widgets (flat PDF)")
                # Show text snippet to understand structure
                text = page.get_text()[:500]
                print(f"  Text preview:\n{text[:300]}")
        
        results[surety] = {
            "path": path,
            "pages": len(doc),
            "fields": fields,
            "has_form_fields": len(fields) > 0,
        }
        doc.close()
    except Exception as e:
        print(f"  ERROR: {e}")
        results[surety] = {"error": str(e)}

# Save results as JSON for programmatic use
out_path = "/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/scripts/pdf_fields.json"
with open(out_path, "w") as f:
    json.dump(results, f, indent=2)
print(f"\n✅ Field data saved to {out_path}")

# Summary
print(f"\n{'='*60}")
print("SUMMARY")
print(f"{'='*60}")
for surety, data in results.items():
    if "error" in data:
        print(f"  {surety.upper()}: ERROR — {data['error']}")
    else:
        print(f"  {surety.upper()}: {len(data['fields'])} form fields across {data['pages']} pages")
        if not data['has_form_fields']:
            print(f"    ⚠️  No AcroForm fields found — this PDF is likely flat (not fillable)")
            print(f"    → Will need to overlay text at specific coordinates instead")
