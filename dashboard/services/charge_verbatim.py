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
   body. ``|``, a newline, and ``;`` split charges. A comma never splits
   a charge. ``BATTERY, DOMESTIC`` is one charge. A string with no
   roster delimiter is one charge.

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
Capacity comes from the live DocuSeal field inventory in
``tests/fixtures/docuseal_charge_capacity.json`` (template 1
and template 5 exports, 2026-10-09). The repo snapshot's per-template
field lists are still ``pending_live_capture``, so the live export wins.

OSI template 1 (228 unique names, including unnamed boxes) prints each
charge on ``offense_1``..``offense_4`` and the full join on
``charges_summary``. There is no ``charge_N``, ``statute_N``,
``degree_N``, or ``*_addendum_*`` field. More than 4 charges fail closed.
``charges_summary`` still receives every charge that was placed. If that
box cannot show the text at 5.5pt, a layout note says so and the text
is not shortened. The offense rows are what carry each charge.

Palmetto template 5 (157 unique names, including unnamed boxes) has no
offense grid. The only charge text field is ``charges_summary``
(``defendant_prior_offense`` is a prior-offense blank, not this case's
charges). The full join is written there. Render proof on template 6
with template 5's application-box geometry (worst-case glyph mix,
2026-10-09 submissions 46–51) shows 218 characters print at 5pt on two
lines with no clipping, and 219 overflows the box. The fail-closed cap
is 200. The collateral-receipt box is wider (predicted limit 232) and
is not the limit. A 0.45 character-width estimate at 5.5pt is only a
cross-check and must stay at or below 218.

Palmetto template 6 (the review clone) prints each charge on the
paperwork-header rows ``offense_1``..``offense_4``. Those names are on
the template 6 capacity record only. Template 5 and template 1
inventories are unchanged, so a template 5 payload still omits
``offense_*``. One header row longer than 209 characters fails closed
(``capacity_unit`` ``characters``, capacity 209, and the row name).
More charges than rows fails closed. The text is never shortened.
Template 5's 200-character ``charges_summary`` cap is unchanged.

There is no addendum delivery. ``DocuSealService.create_submission``
posts ``template_id`` and ``submitters`` only. ``create_template_from_pdf``
creates a different template. ``get_submission_documents`` downloads a
finished submission. None of those puts an extra page on the template
1 or template 5 packet the signer sees. Unknown field names are dropped
by DocuSeal, so charge values are sent only for names on that template.

An OSI appearance bond has one charge widget (``DefCharge1``). A
Palmetto appearance bond has one (``chargestField1``). Further charges
are additional appearance-bond forms, one per charge. Those local PDFs
are not the DocuSeal signing packet.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

# OSI template 1 offense_1..offense_4. Palmetto template 5 has no offense rows.
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
    """The live template cannot print every charge, and no addendum is delivered."""

    def __init__(
        self,
        charge_count: int,
        capacity: int,
        surety_id: str,
        template: str,
        *,
        unit: str = "rows",
        summary_characters: Optional[int] = None,
        row: Optional[str] = None,
    ):
        self.charge_count = int(charge_count)
        self.capacity = int(capacity)
        self.surety_id = str(surety_id or "")
        self.template = str(template or "")
        self.unit = str(unit or "rows")
        self.summary_characters = (
            None if summary_characters is None else int(summary_characters)
        )
        self.row = str(row or "").strip() or None
        if self.unit == "characters" and self.row:
            shown = self.summary_characters if self.summary_characters is not None else 0
            message = (
                f"{self.row} is {shown} characters and exceeds the "
                f"{self.capacity} character capacity of that row on the "
                f"{self.surety_id} {self.template} template and no charge "
                f"addendum is available"
            )
        elif self.unit == "characters":
            shown = self.summary_characters if self.summary_characters is not None else 0
            message = (
                f"{self.charge_count} charges ({shown} characters) exceed the "
                f"{self.capacity} character capacity of the {self.surety_id} "
                f"{self.template} charges_summary field and no charge addendum "
                f"is available"
            )
        else:
            message = (
                f"{self.charge_count} charges exceed the {self.capacity} charge-row "
                f"capacity of the {self.surety_id} {self.template} template and no "
                f"charge addendum is available"
            )
        super().__init__(message)


def capacity_error_body(exc: "ChargeCapacityError") -> Dict[str, Any]:
    """HTTP body for a 422 ``charge_capacity_exceeded`` response."""
    body = {
        "success": False,
        "error": "charge_capacity_exceeded",
        "message": str(exc),
        "charge_count": exc.charge_count,
        "capacity": exc.capacity,
        "capacity_unit": exc.unit,
    }
    if exc.row:
        body["row"] = exc.row
    return body


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
    """Split a roster charge string on ``|``, newline, or ``;`` only.

    A comma is never a delimiter. ``BATTERY, DOMESTIC`` stays one charge.
    A string with no roster delimiter is one charge. Each piece is
    edge-stripped and kept whole.
    """
    raw = str(text or "")
    if not raw.strip():
        return []
    if re.search(r"[|\n;]", raw):
        parts = _ROSTER_SPLIT.split(raw)
        return [edge_strip(part) for part in parts if edge_strip(part)]
    one = edge_strip(raw)
    return [one] if one else []


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


_INVENTORY_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "docuseal_charge_capacity.json"
)
_CHARGE_ROW_KEY = re.compile(
    r"^(?:offense|charge|statute|degree|case_number|case|bond_amount|"
    r"numeric_bond_amount|poa|poa_number)_\d+$"
)


@lru_cache(maxsize=1)
def _inventory() -> Dict[str, Any]:
    return json.loads(_INVENTORY_PATH.read_text(encoding="utf-8"))


def template_key(surety_id: str) -> str:
    key = str(surety_id or "osi").strip().lower()
    if key in {"palmetto", "psc"}:
        return "palmetto"
    return "osi"


def template_record(surety_id: str) -> Dict[str, Any]:
    return _inventory()["templates"][template_key(surety_id)]


def capacity_record(surety_id: str = "", template_id: Any = None) -> Dict[str, Any]:
    """Inventory for this submission.

    An explicit template id wins. Otherwise the surety's configured
    template id is used. Template 5 and template 1 stay on their own
    records. Template 6 is the review clone with paperwork-header rows.
    """
    inventory = _inventory()["templates"]
    tid = str(template_id).strip() if template_id not in (None, "") else ""
    if not tid:
        from dashboard.services.surety_registry import template_id_for

        tid = str(template_id_for(surety_id) or "").strip()
    if tid:
        for record in inventory.values():
            if str(record.get("template_id")) == tid:
                return record
    return inventory[template_key(surety_id)]


def template_field_names(surety_id: str) -> set:
    return set(template_record(surety_id)["field_names"])


def offense_row_capacity(surety_id: str) -> int:
    """How many per-charge offense rows the live template actually has."""
    return len(template_record(surety_id).get("offense_rows") or [])


def charges_summary_boxes(surety_id: str) -> List[Dict[str, Any]]:
    return [
        box for box in template_record(surety_id).get("charge_boxes") or []
        if box.get("field") == "charges_summary"
    ]


def charges_summary_capacity_record(surety_id: str) -> Optional[Dict[str, Any]]:
    """Proven character cap, when a render has replaced the width estimate."""
    record = template_record(surety_id).get("charges_summary_capacity")
    if isinstance(record, dict) and record.get("cap") is not None:
        return record
    return None


def model_summary_character_capacity(surety_id: str) -> int:
    """Largest single-line length that still prints at or above 5.5pt.

    The smallest ``charges_summary`` box on the template is the limit.
    The same width math as ``text_clips_box`` decides it. For a template
    with a render-proven cap this number is a cross-check only.
    """
    boxes = charges_summary_boxes(surety_id)
    if not boxes:
        return 0
    limit = None
    for box in boxes:
        lo, hi = 0, 4000
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if text_clips_box("x" * mid, float(box["width_pt"]), float(box["height_pt"])):
                hi = mid - 1
            else:
                lo = mid
        limit = lo if limit is None else min(limit, lo)
    return int(limit or 0)


def summary_character_capacity(surety_id: str) -> int:
    """Characters ``charges_summary`` may carry before finalize fails closed.

    A render-proven ``cap`` wins. Otherwise the 5.5pt width estimate is used.
    """
    proven = charges_summary_capacity_record(surety_id)
    if proven is not None:
        return int(proven["cap"])
    return model_summary_character_capacity(surety_id)


def summary_box_clips(surety_id: str, text: str) -> bool:
    """True when ``charges_summary`` cannot show ``text`` on this template."""
    if not text:
        return False
    proven = charges_summary_capacity_record(surety_id)
    if proven is not None:
        return len(text) > int(proven["cap"])
    for box in charges_summary_boxes(surety_id):
        if text_clips_box(text, float(box["width_pt"]), float(box["height_pt"])):
            return True
    return False


def is_charge_payload_key(name: str) -> bool:
    """Charge text and the per-charge row companions sent next to it."""
    if name in {"charges", "charges_summary"}:
        return True
    if "addendum" in name or name.startswith("charge_line_"):
        return True
    return bool(_CHARGE_ROW_KEY.match(name or ""))


def filter_unknown_charge_fields(
    values: Mapping[str, Any],
    surety_id: str,
    template_id: Any = None,
) -> Dict[str, Any]:
    """Drop charge prefill names the target template does not have.

    DocuSeal ignores a field name that is not on the template. Sending one
    looks like the charge was delivered and then vanishes from the signed
    packet. Other prefill keys are left as they are. Template 6 keeps
    ``offense_1``..``offense_4``. Template 5 and template 1 use their
    existing inventories.
    """
    allowed = set(capacity_record(surety_id, template_id).get("field_names") or [])
    return {
        key: value
        for key, value in values.items()
        if not is_charge_payload_key(str(key)) or str(key) in allowed
    }


def _offense_box(surety_id: str, index: int) -> Optional[Dict[str, Any]]:
    name = f"offense_{index}"
    for box in template_record(surety_id).get("charge_boxes") or []:
        if box.get("field") == name:
            return box
    return None


def _layout_notes(
    rows: Sequence[VerbatimCharge],
    summary: str,
    *,
    surety_id: str,
    capacity: int,
) -> List[str]:
    notes: List[str] = []
    if summary and summary_box_clips(surety_id, summary):
        boxes = charges_summary_boxes(surety_id)
        narrow = min(boxes, key=lambda box: float(box["width_pt"])) if boxes else None
        where = ""
        if narrow:
            where = (
                f" The narrowest charges_summary box is {narrow['document']} "
                f"({float(narrow['width_pt']):.0f}pt × {float(narrow['height_pt']):.0f}pt, "
                f"{summary_character_capacity(surety_id)} characters at {_MIN_LEGIBLE_PT}pt)."
            )
        notes.append(
            f"charges_summary contains all {len(rows)} charges "
            f"({len(summary)} characters).{where} The text is not shortened."
        )
    for index, row in enumerate(rows, start=1):
        box = _offense_box(surety_id, index)
        if box and text_clips_box(row.charge, float(box["width_pt"]), float(box["height_pt"])):
            notes.append(
                f"charge {index} is {len(row.charge)} characters. The {surety_id} "
                f"offense_{index} box may clip it. The stored text is not shortened."
            )
    if capacity and len(rows) <= capacity:
        notes.append(
            f"offense_1..{len(rows)} hold these charges. "
            f"charges_summary contains every charge."
        )
    return notes


def _offense_row_character_cap(record: Mapping[str, Any]) -> Optional[int]:
    raw = record.get("offense_row_character_capacity")
    if isinstance(raw, dict) and raw.get("cap") is not None:
        return int(raw["cap"])
    return None


def fit_charges_for_template(
    rows: Sequence[VerbatimCharge],
    *,
    surety_id: str = "",
    template: str = "docuseal",
    template_id: Any = None,
) -> ChargePlacement:
    """Place every charge on a field the live template has, or fail closed.

    OSI template 1 uses ``offense_1``..``offense_4``. Palmetto template 5
    uses ``charges_summary`` only, and only while that text is within the
    render-proven character cap. Palmetto template 6 uses the four
    paperwork-header rows, each capped at 209 characters. There is no
    DocuSeal addendum on the signed packet.
    """
    rows = list(rows)
    summary = join_charge_summary(rows)
    surety = template_key(surety_id)
    record = capacity_record(surety_id, template_id)
    row_names = list(record.get("offense_rows") or [])
    row_capacity = len(row_names)
    if row_capacity < 1:
        char_capacity = summary_character_capacity(surety)
        if rows and (char_capacity < 1 or len(summary) > char_capacity):
            raise ChargeCapacityError(
                len(rows),
                char_capacity,
                surety,
                template,
                unit="characters",
                summary_characters=len(summary),
            )
        return ChargePlacement(
            on_form=[],
            overflow=[],
            summary=summary,
            extra_fields={},
            layout_notes=_layout_notes(rows, summary, surety_id=surety, capacity=0),
            addendum_pdf=b"",
            capacity=char_capacity,
            addendum=False,
        )
    if len(rows) > row_capacity:
        raise ChargeCapacityError(len(rows), row_capacity, surety, template, unit="rows")
    row_cap = _offense_row_character_cap(record)
    if row_cap is not None:
        for index, row in enumerate(rows, start=1):
            if len(row.charge) > row_cap:
                name = row_names[index - 1] if index - 1 < len(row_names) else f"offense_{index}"
                raise ChargeCapacityError(
                    len(rows),
                    row_cap,
                    surety,
                    template,
                    unit="characters",
                    summary_characters=len(row.charge),
                    row=name,
                )
    return ChargePlacement(
        on_form=list(rows),
        overflow=[],
        summary=summary,
        extra_fields={},
        layout_notes=_layout_notes(rows, summary, surety_id=surety, capacity=row_capacity),
        addendum_pdf=b"",
        capacity=row_capacity,
        addendum=False,
    )


def place_verbatim_charges(
    rows: Sequence[VerbatimCharge],
    *,
    capacity: int = 0,
    addendum: bool = False,
    surety_id: str = "",
    template: str = "docuseal",
    template_id: Any = None,
) -> ChargePlacement:
    """Fit charges to the live template. ``capacity`` and ``addendum`` are ignored.

    An addendum flag cannot create fields the template does not have, and
    this client has no API call that attaches an extra PDF to the template
    1 or template 5 submission.
    """
    del capacity, addendum
    return fit_charges_for_template(
        rows,
        surety_id=surety_id,
        template=template,
        template_id=template_id,
    )


def addendum_allowed(bond_data: Optional[Mapping[str, Any]]) -> bool:
    """Always false. No DocuSeal addendum is attached to the signed packet."""
    del bond_data
    return False


