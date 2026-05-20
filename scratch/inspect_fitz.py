import fitz
import sys

print("PyMuPDF Version:", fitz.__doc__)

# Create a blank PDF and a text widget to inspect its attributes
doc = fitz.open()
page = doc.new_page()
widget = fitz.Widget()
widget.rect = fitz.Rect(50, 50, 200, 70)
widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
widget.field_name = "test_field"
widget.field_value = "Initial value"

print("Widget attributes:")
for attr in dir(widget):
    if not attr.startswith("_"):
        try:
            val = getattr(widget, attr)
            if not callable(val):
                print(f"  {attr}: {type(val).__name__} = {val}")
        except Exception as e:
            print(f"  {attr}: Error reading: {e}")
