"""
ShamrockLeads — Paperwork PDF Engine
Stitches together the 14-document packets and places SignNow Text Tags.
"""
from __future__ import annotations
import io
import fitz  # PyMuPDF
from pathlib import Path
from typing import List, Dict, Any

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "blanks"

def get_template_path(slug: str) -> Path:
    return TEMPLATES_DIR / f"{slug}.pdf"

def place_text_by_anchor(page: fitz.Page, anchor: str, text: str, dx: float = 0, dy: float = 0, font_size: float = 10, index: int = 0):
    """
    Search for an anchor string on the page, and place the `text` at an offset.
    """
    if not text:
        return
        
    rects = page.search_for(anchor)
    if rects and len(rects) > index:
        r = rects[index]
        point = fitz.Point(r.x1 + dx, r.y0 + dy)
        page.insert_text(point, str(text), fontsize=font_size, color=(0, 0, 0))

def hydrate_indemnity_agreement(data: Dict[str, Any], indemnitor_index: int = 0) -> bytes:
    """Fills the OSI Indemnity Agreement and places SignNow signature tags."""
    doc = fitz.open(get_template_path("indemnity-agreement"))
    page = doc[0]
    
    ind = data.get("indemnitors", [{}])[indemnitor_index] if "indemnitors" in data else {}
    def_name = data.get("defendant_name", "")
    
    # ── Hydrate Text Fields ──
    # Name
    place_text_by_anchor(page, "Name", ind.get("name", ""), dx=10, dy=10)
    # Address
    place_text_by_anchor(page, "Address", ind.get("address", ""), dx=10, dy=10)
    # Defendant Name (in the middle paragraph)
    place_text_by_anchor(page, "(Defendant/Principal)", def_name, dx=5, dy=-15)
    
    # ── Place SignNow Text Tags ──
    # The tag tells SignNow to create a field here during /document/fieldextract
    # Format: {{s1_SignerRole}} for signatures, {{d1_SignerRole}} for dates
    role = f"Indemnitor {indemnitor_index + 1}"
    
    # Signature line
    sig_tag = f"{{{{s1_{role}}}}}"
    place_text_by_anchor(page, "INDEMNITOR:", sig_tag, dx=50, dy=10, font_size=8)
    
    # Date line
    date_tag = f"{{{{d1_{role}}}}}"
    place_text_by_anchor(page, "this", date_tag, dx=20, dy=0, font_size=8)
    
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    buf.seek(0)
    return buf.read()

def generate_full_packet(data: Dict[str, Any], surety: str = "osi") -> bytes:
    """
    Stitches all required documents into a single PDF.
    """
    out_doc = fitz.open()
    
    # 1. Indemnity Agreements (one per indemnitor)
    inds = data.get("indemnitors", [{}])
    for i in range(len(inds)):
        ind_bytes = hydrate_indemnity_agreement(data, indemnitor_index=i)
        ind_doc = fitz.open("pdf", ind_bytes)
        out_doc.insert_pdf(ind_doc)
        ind_doc.close()
        
    # TODO: Add the other documents (Defendant App, Promissory, etc.)
    
    buf = io.BytesIO()
    out_doc.save(buf)
    out_doc.close()
    buf.seek(0)
    return buf.read()
