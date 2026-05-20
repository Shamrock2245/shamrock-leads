import fitz
from pathlib import Path

# Template path check
_LOCAL_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
PALMETTO_TEMPLATE = _LOCAL_TEMPLATES / "palmetto" / "Shamrock Palmetto Official Appearance Bond.pdf"

if not PALMETTO_TEMPLATE.exists():
    print(f"Palmetto template not found at {PALMETTO_TEMPLATE}")
else:
    doc = fitz.open(str(PALMETTO_TEMPLATE))
    page = doc[0]
    print(f"Successfully loaded Palmetto template: {PALMETTO_TEMPLATE}")
    print("Widget Fields:")
    for widget in page.widgets():
        print(f"  Field: {widget.field_name:<30} | Font: {widget.text_font:<5} | FontSize: {widget.text_fontsize:<3} | Rect: {widget.rect}")
    doc.close()
