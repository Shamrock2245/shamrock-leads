"""OSI one-PDF stitch prints each ZZ token inside its measured blank."""
from __future__ import annotations

import io

import fitz
import pdfplumber

from dashboard.paperwork_pdf_service import (
    OSI_AGENT_BLOCK,
    OSI_STITCH_BOXES,
    _doc_bytes_for_slug,
    get_template_path,
)

TOKENS = {
    "defendant_name": "ZZTESTDEFENDANT",
    "indemnitor_name": "ZZTESTINDEMNITOR",
    "indemnitor_address": "ZZTESTADDRESS",
    "defendant_address": "ZZDEFSTREET",
    "case_number": "ZZCASE000",
    "county": "ZZCOUNTY",
    "bond_amount": "ZZBONDAMT",
    "poa_number": "ZZPOA",
}


def _data():
    return {
        "defendant_name": TOKENS["defendant_name"],
        "defendant_address": TOKENS["defendant_address"],
        "case_number": TOKENS["case_number"],
        "county": TOKENS["county"],
        "bond_amount": TOKENS["bond_amount"],
        "poa_number": TOKENS["poa_number"],
        "indemnitor_name": TOKENS["indemnitor_name"],
        "indemnitor_address": TOKENS["indemnitor_address"],
    }


def _person():
    return {
        "name": TOKENS["indemnitor_name"],
        "address": TOKENS["indemnitor_address"],
    }


def _inside(inner: fitz.Rect, outer: fitz.Rect, pad: float = 1.5) -> bool:
    return (
        inner.x0 >= outer.x0 - pad
        and inner.y0 >= outer.y0 - pad
        and inner.x1 <= outer.x1 + pad
        and inner.y1 <= outer.y1 + pad
    )


def _area(a, b) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _is_blank_rule(text: str) -> bool:
    """Underscore rules are the blank, not body copy."""
    underscores = text.count("_")
    letters = sum(ch.isalpha() for ch in text)
    if underscores >= 3 and underscores > letters:
        return True
    return not any(ch.isalnum() for ch in text)


def _original_words(path):
    pages = []
    with pdfplumber.open(path) as document:
        for page in document.pages:
            words = []
            for word in page.extract_words() or []:
                text = word.get("text") or ""
                if _is_blank_rule(text):
                    continue
                words.append((word["x0"], word["top"], word["x1"], word["bottom"], text))
            pages.append(words)
    return pages


def test_osi_stitch_tokens_land_in_measured_blanks():
    """Each ZZ token stays inside its blank, off printed words and the agent block.

    The promissory note Defendant's Name cell is the defendant.
    """
    person = _person()
    data = _data()
    agent = fitz.Rect(*OSI_AGENT_BLOCK)
    for slug, fields in OSI_STITCH_BOXES.items():
        blank_path = get_template_path(slug, "osi")
        original = _original_words(blank_path)
        raw = _doc_bytes_for_slug(
            slug, data, "osi", person=person, role="Indemnitor 1", role_index=0
        )
        assert raw, slug
        doc = fitz.open(stream=raw, filetype="pdf")
        try:
            text = "\n".join(page.get_text() for page in doc)
            assert "{{" not in text, slug
            placed = []
            for page in doc:
                for token in TOKENS.values():
                    for rect in page.search_for(token):
                        placed.append((token, rect))
                        assert not rect.intersects(agent), (slug, token, rect)
            expected_tokens = {
                TOKENS[key] for key, boxes in fields.items() if boxes
            }
            found_tokens = {token for token, _rect in placed}
            assert found_tokens == expected_tokens, (slug, found_tokens, expected_tokens)
            for token, rect in placed:
                owners = []
                for key, boxes in fields.items():
                    if TOKENS[key] != token:
                        continue
                    for box in boxes:
                        outer = fitz.Rect(box[0], box[1], box[0] + box[2], box[1] + box[3])
                        if _inside(rect, outer):
                            owners.append(outer)
                assert owners, (slug, token, rect)
            with pdfplumber.open(io.BytesIO(raw)) as document:
                stamped_pages = []
                for page in document.pages:
                    words = []
                    for word in page.extract_words() or []:
                        if any(token in (word.get("text") or "") for token in TOKENS.values()):
                            words.append(
                                (word["x0"], word["top"], word["x1"], word["bottom"], word["text"])
                            )
                    stamped_pages.append(words)
            overlaps = []
            for page_words, stamped_words in zip(original, stamped_pages):
                for stamped_word in stamped_words:
                    for original_word in page_words:
                        if _area(stamped_word, original_word) > 2:
                            overlaps.append((slug, stamped_word[4], original_word[4]))
            assert overlaps == [], overlaps
        finally:
            doc.close()

    note = _doc_bytes_for_slug(
        "promissory-note", data, "osi", person=person, role="Indemnitor 1", role_index=0
    )
    box = OSI_STITCH_BOXES["promissory-note"]["defendant_name"][0]
    cell = fitz.Rect(box[0], box[1], box[0] + box[2], box[1] + box[3])
    doc = fitz.open(stream=note, filetype="pdf")
    try:
        page = doc[0]
        defendant_hits = page.search_for(TOKENS["defendant_name"])
        indemnitor_hits = page.search_for(TOKENS["indemnitor_name"])
        assert defendant_hits, "promissory Defendant's Name cell is empty"
        assert any(_inside(rect, cell) for rect in defendant_hits)
        assert all(not _inside(rect, cell) for rect in indemnitor_hits)
        assert indemnitor_hits == []
    finally:
        doc.close()
