import fitz
from pathlib import Path

# Template path check
_LOCAL_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
OSI_TEMPLATE = _LOCAL_TEMPLATES / "osi" / "Appearance Bond blank.pdf"

if not OSI_TEMPLATE.exists():
    print(f"OSI template not found at {OSI_TEMPLATE}")
else:
    doc = fitz.open(str(OSI_TEMPLATE))
    page = doc[0]
    print(f"Successfully loaded OSI template: {OSI_TEMPLATE}")
    print("Widget Fields:")
    for widget in page.widgets():
        print(f"  Field: {widget.field_name:<25} | Font: {widget.text_font:<5} | FontSize: {widget.text_fontsize:<3} | Rect: {widget.rect}")
    doc.close()
