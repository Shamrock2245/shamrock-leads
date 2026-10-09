"""Verbatim charge text for bond paperwork.

Clerks reject a packet when the charge text differs by one character.
Nothing in this module title-cases, re-encodes, or drops a charge.

Whitespace
----------
Leading and trailing whitespace is removed from charge text, statute,
degree, and case number. That is the same edge strip the paperwork
fillers already apply (``str.strip``). Characters inside the text,
including apostrophes, em dashes, and section signs, are kept.

Charge source precedence
------------------------
``build_bond_data_from_dashboard`` is the paperwork source of truth.
The first non-empty value wins:

1. Request body ``charge_details``, else body ``charge_list``.
2. Resolved case context ``charge_details``, else ``charge_list``.
   Context rows come from ``charge_details_from_sources``, which itself
   prefers, in order: ``arrest.charge_details`` (staff edit or the
   writer's copy), ``arrest.extra.charge_details`` (scraped original),
   BondCase ``charge_details`` / ``charge_list``, then the plain
   ``charges`` string.
3. Intake document ``charge_details``, else ``charge_list``.
4. The plain ``charges`` string: context, then intake, then the request
   body. A string that contains ``|``, a newline, or ``;`` is split on
   those roster delimiters. A comma splits charges only when none of
   those delimiters is present, because a statute citation can contain
   a comma.

``prefill_values_from_bond`` then reads the merged bond dict in this
order: ``charge_details``, ``charge_list``, ``charges``, then the same
keys on the nested defendant. An empty list does not count.

Joined summary
--------------
``charges`` and ``charges_summary`` are every charge text joined with
``, `` (comma, space). The join is not a truncation. If a printed box
cannot hold that string, ``layout_notes`` says so and the string stays
whole.

Row capacity
------------
The live DocuSeal templates (OSI template 1 and Palmetto template 5)
share one prefill grid: ``offense_1``..``offense_4`` and
``charge_1``..``charge_4``. Capacity is 4 rows for both sureties.

An OSI appearance bond has one charge widget (``DefCharge1``). A
Palmetto appearance bond has one charge widget (``chargestField1``;
``chargesField2`` is a wrap line for that same charge, not a second
charge). Further charges are additional appearance-bond forms, one per
charge, in order (``generate_appearance_bonds``).

When a 4-row grid has more charges than rows, the overflow is written
in the same order onto a continuation addendum (``charge_addendum_N``
and a one-page-per-charge PDF). A placement that has no addendum fails
closed with the charge count and the capacity. It does not drop or
shorten a charge.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

# Live DocuSeal OSI (template 1) and Palmetto (template 5) prefill grid.
DOCUSEAL_CHARGE_ROW_CAPACITY = 4

# One charge widget per appearance-bond form. The next charge is another form.
APPEARANCE_CHARGE_ROW_CAPACITY = 1

# Measured on the shipped blanks. Used only to report clipping.
_APPEARANCE_CHARGE_BOX = {
    "osi": {"field": "DefCharge1", "width": 539.9, "height": 19.5},
    "palmetto": {"field": "chargestField1", "width": 559.7, "height": 14.4},
}

_MIN_LEGIBLE_PT = 5.5
_DEFAULT_FONT_PT = 10.0
_CHAR_WIDTH = 0.45

_ROSTER_SPLIT = re.compile(r"[|\n;]+")


class ChargeCapacityError(ValueError):
    """More charges than the template can print, and no addendum to carry them."""

    def __init__(self, charge_count: int, capacity: int, surety_id: str, template: str):
        self.charge_count = int(charge_count)
        self.capacity = int(capacity)
        self.surety_id = str(surety_id or "")
        self.template = str(template or "")
        super().__init__(
            f"{self.charge_count} charges exceed the {self.capacity} charge-row "
            f"capacity of the {self.surety_id} {self.template} template and no "
            f"charge addendum is available"
        )


def edge_strip(value: Any) -> str:
    """Remove leading and trailing whitespace. Leave every other character."""
    if value is None:
        return ""
    return str(value).strip()


@dataclass
class VerbatimCharge:
    charge: str
    statute: str = ""
    degree: str = ""
    case_number: str = ""
    bond_amount: Any = None
    poa_number: str = ""
    raw: Any = None


@dataclass
class ChargePlacement:
    on_form: List[VerbatimCharge] = field(default_factory=list)
    overflow: List[VerbatimCharge] = field(default_factory=list)
    summary: str = ""
    extra_fields: Dict[str, str] = field(default_factory=dict)
    layout_notes: List[str] = field(default_factory=list)
    addendum_pdf: bytes = b""
    capacity: int = 0
    addendum: bool = True

    @property
    def charges(self) -> List[VerbatimCharge]:
        return list(self.on_form) + list(self.overflow)


def _statute(item: Mapping[str, Any]) -> str:
    return edge_strip(
        item.get("statute")
        or item.get("statute_citation")
        or item.get("statute_code")
        or ""
    )


def _degree(item: Mapping[str, Any]) -> str:
    return edge_strip(item.get("degree") or item.get("charge_degree") or "")


def _case_number(item: Mapping[str, Any]) -> str:
    return edge_strip(item.get("case_number") or item.get("Case_Number") or "")


def _poa(item: Mapping[str, Any]) -> str:
    return edge_strip(item.get("poa_number") or item.get("POA_Number") or "")


def _bond(item: Mapping[str, Any]) -> Any:
    if "bond_amount" in item and item.get("bond_amount") not in (None, ""):
        return item.get("bond_amount")
    if item.get("amount") not in (None, ""):
        return item.get("amount")
    if item.get("bond") not in (None, ""):
        return item.get("bond")
    return None


def charge_from_item(item: Any) -> Optional[VerbatimCharge]:
    """One structured row or one already-split charge string. No rewriting."""
    if isinstance(item, Mapping):
        text = edge_strip(item.get("charge") or item.get("description") or item.get("name") or "")
        if not text:
            return None
        return VerbatimCharge(
            charge=text,
            statute=_statute(item),
            degree=_degree(item),
            case_number=_case_number(item),
            bond_amount=_bond(item),
            poa_number=_poa(item),
            raw=item,
        )
    text = edge_strip(item)
    if not text:
        return None
    return VerbatimCharge(charge=text, raw=item)


def split_charges_text(text: str) -> List[str]:
    """Split a roster charge string. Do not split on a comma inside a citation.

    ``|``, newline, and ``;`` are the roster delimiters. A comma splits only
    when none of those is present. Each piece is edge-stripped and kept whole.
    """
    raw = str(text or "")
    if not raw.strip():
        return []
    if re.search(r"[|\n;]", raw):
        parts = _ROSTER_SPLIT.split(raw)
    else:
        parts = raw.split(",")
    return [edge_strip(part) for part in parts if edge_strip(part)]


def resolve_verbatim_charge_rows(bond_data: Optional[Mapping[str, Any]]) -> List[VerbatimCharge]:
    """Rows already chosen by ``build_bond_data_from_dashboard``.

    Read order on the merged dict: ``charge_details``, ``charge_list``,
    ``charges``, then the nested defendant's ``charge_details`` and
    ``charges``. An empty list is skipped. Charge text is not rebuilt
    from statute or degree; those stay on their own fields.
    """
    data = bond_data or {}
    defendant = data.get("defendant") if isinstance(data.get("defendant"), Mapping) else {}
    sources = (
        data.get("charge_details"),
        data.get("charge_list"),
        data.get("charges"),
        defendant.get("charge_details"),
        defendant.get("charges"),
    )
    chosen: Any = None
    for source in sources:
        if isinstance(source, list) and source:
            chosen = source
            break
        if isinstance(source, str) and source.strip():
            chosen = source
            break
    if chosen is None:
        return []
    if isinstance(chosen, list):
        rows = []
        for item in chosen:
            row = charge_from_item(item)
            if row is not None:
                rows.append(row)
        return rows
    return [charge_from_item(part) for part in split_charges_text(chosen)]


def join_charge_summary(rows: Sequence[VerbatimCharge]) -> str:
    """Every charge text, in order, joined with ``', '``. Nothing removed."""
    return ", ".join(row.charge for row in rows)


def text_clips_box(
    text: str,
    width: float,
    height: float,
    *,
    default_font: float = _DEFAULT_FONT_PT,
) -> bool:
    """True when the appearance-bond font floor (5.5pt) cannot fit ``text``.

    Same width/height estimate as ``_set_widget_value_with_scaling``. This
    does not change ``text``.
    """
    if not text:
        return False
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    max_line = max((len(line) for line in lines), default=0)
    num_lines = max(1, len(lines))
    size_by_width = width / (max_line * _CHAR_WIDTH) if max_line else default_font
    if num_lines > 1:
        size_by_height = height / (num_lines * 1.25)
    else:
        size_by_height = height * 0.8
    needed = min(default_font, size_by_width, size_by_height)
    return needed < _MIN_LEGIBLE_PT


def appearance_box_clips(surety_id: str, text: str) -> bool:
    box = _APPEARANCE_CHARGE_BOX.get(str(surety_id or "").lower())
    if not box:
        return False
    return text_clips_box(text, box["width"], box["height"])


def _layout_notes(
    rows: Sequence[VerbatimCharge],
    summary: str,
    *,
    surety_id: str,
    capacity: int,
    overflow: Sequence[VerbatimCharge],
) -> List[str]:
    notes: List[str] = []
    box = _APPEARANCE_CHARGE_BOX.get(str(surety_id or "").lower())
    if summary and box and text_clips_box(summary, box["width"], box["height"]):
        notes.append(
            f"charges and charges_summary contain all {len(rows)} charges "
            f"({len(summary)} characters). The {surety_id} {box['field']} box "
            f"({box['width']:.0f}pt × {box['height']:.0f}pt) clips below "
            f"{_MIN_LEGIBLE_PT}pt. The text is not shortened."
        )
    for index, row in enumerate(rows, start=1):
        if box and text_clips_box(row.charge, box["width"], box["height"]):
            notes.append(
                f"charge {index} is {len(row.charge)} characters. The {surety_id} "
                f"{box['field']} box may clip it. The stored text is not shortened."
            )
    if overflow:
        start = capacity + 1
        end = capacity + len(overflow)
        notes.append(
            f"charges {start}-{end} are on the charge addendum in that order. "
            f"offense_1..{capacity} hold the first {capacity}. "
            f"charges and charges_summary still contain every charge."
        )
    return notes


def _addendum_fields(overflow: Sequence[VerbatimCharge]) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for index, row in enumerate(overflow, start=1):
        fields[f"charge_addendum_{index}"] = row.charge
        fields[f"offense_addendum_{index}"] = row.charge
        if row.statute:
            fields[f"statute_addendum_{index}"] = row.statute
        if row.degree:
            fields[f"degree_addendum_{index}"] = row.degree
        if row.case_number:
            fields[f"case_addendum_{index}"] = row.case_number
        if row.bond_amount not in (None, ""):
            fields[f"bond_addendum_{index}"] = edge_strip(row.bond_amount)
        if row.poa_number:
            fields[f"poa_addendum_{index}"] = row.poa_number
    return fields


def _unicode_font() -> Optional[str]:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        if os.path.isfile(path):
            return path
    return None


def render_charge_addendum_pdf(
    rows: Sequence[VerbatimCharge],
    *,
    surety_id: str = "",
) -> bytes:
    """One page per overflow charge, in order. The widget value is the charge text.

    The page also prints statute, degree, case number, and the per-charge
    bond when those values are present. They are not folded into the charge
    text.
    """
    import fitz

    doc = fitz.open()
    fontfile = _unicode_font()
    try:
        for index, row in enumerate(rows, start=1):
            page = doc.new_page(width=612, height=792)
            header = f"Charge addendum {index}  {surety_id}".strip()
            if fontfile:
                page.insert_text((36, 48), header, fontfile=fontfile, fontsize=11)
            else:
                page.insert_text((36, 48), header, fontsize=11)
            widget = fitz.Widget()
            widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            widget.field_name = f"charge_addendum_{index}"
            widget.field_value = row.charge
            widget.rect = fitz.Rect(36, 72, 576, 160)
            widget.text_fontsize = 10
            page.add_widget(widget)
            extras = []
            if row.statute:
                extras.append(("statute", row.statute))
            if row.degree:
                extras.append(("degree", row.degree))
            if row.case_number:
                extras.append(("case_number", row.case_number))
            if row.bond_amount not in (None, ""):
                extras.append(("bond_amount", edge_strip(row.bond_amount)))
            top = 180
            for name, value in extras:
                extra = fitz.Widget()
                extra.field_type = fitz.PDF_WIDGET_TYPE_TEXT
                extra.field_name = f"{name}_addendum_{index}"
                extra.field_value = value
                extra.rect = fitz.Rect(36, top, 576, top + 22)
                extra.text_fontsize = 10
                page.add_widget(extra)
                top += 28
        if doc.page_count == 0:
            return b""
        return doc.tobytes()
    finally:
        doc.close()


def place_verbatim_charges(
    rows: Sequence[VerbatimCharge],
    *,
    capacity: int,
    addendum: bool,
    surety_id: str = "",
    template: str = "docuseal",
) -> ChargePlacement:
    """Put charges on the form rows, then on the addendum. Never drop one.

    ``addendum=False`` raises ``ChargeCapacityError`` when there are more
    charges than ``capacity``. The message names both numbers.
    """
    capacity = int(capacity)
    if capacity < 1:
        raise ChargeCapacityError(len(rows), capacity, surety_id, template)
    if len(rows) > capacity and not addendum:
        raise ChargeCapacityError(len(rows), capacity, surety_id, template)
    on_form = list(rows[:capacity])
    overflow = list(rows[capacity:])
    summary = join_charge_summary(rows)
    pdf = render_charge_addendum_pdf(overflow, surety_id=surety_id) if overflow else b""
    return ChargePlacement(
        on_form=on_form,
        overflow=overflow,
        summary=summary,
        extra_fields=_addendum_fields(overflow),
        layout_notes=_layout_notes(
            rows,
            summary,
            surety_id=surety_id,
            capacity=capacity,
            overflow=overflow,
        ),
        addendum_pdf=pdf,
        capacity=capacity,
        addendum=bool(addendum),
    )


def addendum_allowed(bond_data: Optional[Mapping[str, Any]]) -> bool:
    """Default is an addendum. An explicit false refuses the continuation."""
    if not isinstance(bond_data, Mapping) or "allow_charge_addendum" not in bond_data:
        return True
    flag = bond_data.get("allow_charge_addendum")
    if flag is False or flag == 0:
        return False
    if isinstance(flag, str) and flag.strip().lower() in {"0", "false", "no"}:
        return False
    return True
