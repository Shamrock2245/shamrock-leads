"""
Palmetto packet field placement.

Coordinates are PDF points, origin top-left (PyMuPDF). DocuSeal normalized
areas are x/y/w/h as fractions of the page, origin top-left, page 1-based
within that carrier document.

The appearance-bond widgets already sit on the printed blanks. Their names
in this spec are the blank's real widget names (including the carrier typo
``chargestField1``). The other Palmetto carrier PDFs shipped with only a
zero-size signature widget; fields below were placed on the label blanks
measured from the text layer and tesseract word boxes.

DocuSeal templates 1 and 5 are not in this repo (the drift snapshot has
``fields: null``). This spec is what an apply script sends. It does not
invent a carrier form.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# page width, height in PDF points
PAGE_SIZE: Dict[str, tuple] = {
    "appearance-bond": (613.44, 784.56),
    "defendant-application": (612.0, 789.18),
    "indemnity-agreement": (611.61, 797.65),
    "collateral-receipt": (611.23, 789.18),
    "bail-bond-information-sheet-palmetto": (612.0, 792.0),
}

# Live template 5 submitters, lowercase, matching the DocuSeal export.
ROLE_DEFENDANT = "defendant"
ROLE_INDEMNITOR = "indemnitor"
ROLE_COINDEMNITOR = "coindemnitor"
ROLE_BONDSMAN = "bondsman"

# AGENT row on the application and indemnity headers.
# Each box is ((x, y, w, h) in PDF points, preferences or None).
# Verified on template 6 at 08:27 ET. Change one line per box.
AGENT_LINE_BOXES: Dict[str, Dict[str, tuple]] = {
    "defendant-application": {
        "name": ((446, 14, 84, 13), {"valign": "bottom", "font_size": 11}),
        "license": ((534, 14, 49, 13), {"align": "right", "valign": "bottom", "font_size": 11}),
    },
    "indemnity-agreement": {
        "name": ((446, 16, 84, 14), None),
        "license": ((534, 16, 49, 14), {"align": "right"}),
    },
}


def _f(
    document: str,
    name: str,
    kind: str,
    role: str,
    x: float,
    y: float,
    w: float,
    h: float,
    source: str,
    *,
    required: bool = False,
    docuseal: bool = True,
    page: int = 1,
    preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pw, ph = PAGE_SIZE[document]
    row = {
        "document": document,
        "name": name,
        "type": kind,
        "role": role,
        "page": page,
        "x": round(x, 2),
        "y": round(y, 2),
        "w": round(w, 2),
        "h": round(h, 2),
        "x_norm": round(x / pw, 6),
        "y_norm": round(y / ph, 6),
        "w_norm": round(w / pw, 6),
        "h_norm": round(h / ph, 6),
        "data_source": source,
        "required": required,
        "docuseal": docuseal,
    }
    # Omitted preferences keep the previous field dict. DocuSeal then uses its defaults.
    if preferences:
        row["preferences"] = dict(preferences)
    return row


def _appearance() -> List[Dict[str, Any]]:
    """Rects copied from the blank's existing widgets (already on the labels)."""
    d = "appearance-bond"
    b = ROLE_BONDSMAN
    return [
        _f(d, "powerNumField", "text", b, 81.6, 93.4, 160.7, 15.7, "poa_number", required=True),
        _f(d, "ArrestNumberField", "text", b, 81.3, 112.6, 163.0, 14.7, "booking_number", required=True),
        _f(d, "defendantNameField", "text", b, 27.8, 173.0, 217.9, 16.9, "defendant_name", required=True),
        _f(d, "cirCoField", "text", b, 320.6, 137.3, 217.0, 17.4, "court_type"),
        _f(d, "countyField", "text", b, 319.4, 171.0, 218.4, 18.7, "county", required=True),
        _f(d, "numericBondAmount", "text", b, 448.4, 229.7, 145.4, 15.5, "bond_amount_numeric", required=True),
        _f(d, "dayField", "text", b, 132.9, 274.9, 107.5, 15.5, "day", required=True),
        _f(d, "monthWrittenField", "text", b, 277.5, 274.9, 178.1, 15.5, "month", required=True),
        _f(d, "yearYYYYField", "text", b, 482.4, 276.2, 74.6, 14.1, "year", required=True),
        _f(d, "CourtDateAndTimeField", "text", b, 367.0, 292.0, 165.8, 17.0, "court_datetime", required=True),
        _f(d, "chargestField1", "text", b, 27.8, 321.6, 559.7, 14.4, "charge_line_1", required=True),
        _f(d, "chargesField2", "text", b, 28.8, 336.0, 558.7, 12.5, "charge_line_2"),
        _f(d, "agentBailLicNumField", "text", b, 403.2, 442.7, 116.6, 12.4, "agent_license", required=True),
        _f(d, "AgentField", "text", b, 342.6, 456.8, 150.0, 15.9, "agent_name", required=True),
        _f(d, "AgentField", "text", b, 40.8, 518.9, 150.0, 15.9, "agent_name", required=True),
        _f(d, "DefendantAddress", "text", b, 28.8, 588.0, 559.7, 13.2, "defendant_address", required=True),
        _f(d, "writtenPremiumAmountField", "text", b, 219.3, 602.5, 226.6, 12.8, "premium_words", required=True),
        _f(d, "calculatedPremiumField", "text", b, 455.6, 600.2, 126.4, 14.8, "premium_numeric", required=True),
        _f(d, "whoSignedField", "text", b, 307.8, 629.0, 271.7, 11.8, "who_signed"),
        _f(d, "CollateralField", "text", b, 29.8, 653.5, 558.7, 12.0, "collateral"),
        _f(d, "collateralDescriptionField", "text", b, 30.2, 666.9, 558.7, 15.1, "collateral_description"),
        _f(d, "AgencyField", "text", b, 84.5, 723.8, 208.3, 16.3, "agency_name", required=True),
        _f(d, "Transfer agent", "text", b, 330.9, 71.8, 250.5, 49.2, "transfer_agent"),
    ]


def _application() -> List[Dict[str, Any]]:
    d = "defendant-application"
    # Live template 5 puts every prefilled text and date box on bondsman.
    df = ROLE_BONDSMAN
    bd = ROLE_BONDSMAN
    agent_name, name_prefs = AGENT_LINE_BOXES[d]["name"]
    agent_license, license_prefs = AGENT_LINE_BOXES[d]["license"]
    rows: List[Dict[str, Any]] = [
        # Right-hand bond box. Values start after each printed label.
        # No preferences: centered alignment and auto-size print this at 7pt,
        # with the baseline clear of the underline. A worst-case real name is
        # 160pt at 11pt, and this line only has room for about 138pt.
        _f(d, "app_defendant_name", "text", df, 468, 0, 138, 12.87, "defendant_name", required=True),
        _f(d, "app_agent_name", "text", bd, *agent_name, "agent_name", required=True, preferences=name_prefs),
        _f(d, "app_agent_license", "text", bd, *agent_license, "agent_license", preferences=license_prefs),
        # Height 14 or more auto-sizes to 11pt. font_size is not locked.
        _f(d, "app_power_number", "text", bd, 466, 27.2, 140, 14, "poa_number", required=True, preferences={"valign": "bottom"}),
        # Under 13.98pt, so this auto-sizes to 7pt. An 11pt box would cross POWER NO.
        _f(d, "app_case_number", "text", bd, 458, 42, 148, 11.9, "case_number", required=True, preferences={"valign": "bottom"}),
        _f(d, "app_execution_date", "text", bd, 488, 58, 118, 12, "today_date", required=True),
        _f(d, "app_contacted_by", "text", bd, 482, 72, 124, 12, "contacted_by"),
        _f(d, "app_contact_address", "text", bd, 458, 86, 148, 12, "defendant_address"),
        _f(d, "app_contact_date", "text", bd, 438, 100, 48, 12, "today_date"),
        _f(d, "app_contact_time", "text", bd, 516, 100, 90, 12, "court_time"),
        _f(d, "app_relationship", "text", df, 478, 114, 128, 12, "relationship"),
        _f(d, "app_bond_amount", "text", df, 292, 50, 110, 12, "bond_amount_numeric", required=True),
        _f(d, "app_court_name", "text", df, 58, 64, 136, 12, "court_type", required=True),
        _f(d, "app_court_county", "text", df, 228, 64, 128, 12, "county", required=True),
        _f(d, "app_charge", "text", df, 82, 78, 310, 12, "charge_line_1", required=True),
        # Bond No. underline is y=150; the premium underline is y=158.
        # A 12pt box on that 8pt pitch covered the next line.
        # Same poa_number field as POWER NO. DocuSeal stores preferences per field.
        _f(d, "app_bond_no", "text", df, 340, 143, 78, 7, "poa_number", preferences={"valign": "bottom"}),
        _f(d, "app_bond_date", "text", df, 441, 143, 100, 7, "today_date"),
        _f(d, "app_premium_words", "text", df, 318, 151, 122, 7, "premium_words", required=True),
        _f(d, "app_premium_numeric", "text", df, 476, 151, 94, 7, "premium_numeric", required=True),
        _f(d, "app_def_name", "text", df, 112, 188, 190, 12, "defendant_name", required=True),
        _f(d, "app_alias", "text", df, 372, 188, 220, 12, "defendant_alias"),
        _f(d, "app_street", "text", df, 96, 203, 155, 12, "defendant_address", required=True),
        _f(d, "app_city", "text", df, 276, 203, 90, 12, "defendant_city"),
        _f(d, "app_state", "text", df, 386, 203, 28, 12, "defendant_state"),
        _f(d, "app_zip", "text", df, 438, 203, 58, 12, "defendant_zip"),
        _f(d, "app_address_how_long", "text", df, 544, 203, 60, 12, "defendant_address_how_long"),
        _f(d, "app_former_address", "text", df, 100, 218, 390, 12, "defendant_former_address"),
        _f(d, "app_former_how_long", "text", df, 544, 218, 60, 12, "defendant_former_how_long"),
        _f(d, "app_phone", "text", df, 66, 233, 136, 12, "defendant_phone"),
        _f(d, "app_email", "text", df, 276, 233, 310, 12, "defendant_email"),
        _f(d, "app_employer", "text", df, 90, 248, 148, 12, "defendant_employer"),
        _f(d, "app_boss", "text", df, 268, 248, 228, 12, "defendant_boss"),
        _f(d, "app_employer_how_long", "text", df, 544, 248, 60, 12, "defendant_employer_how_long"),
        _f(d, "app_employer_address", "text", df, 114, 263, 250, 12, "defendant_employer_address"),
        _f(d, "app_employer_phone", "text", df, 410, 263, 180, 12, "defendant_employer_phone"),
        _f(d, "app_previous_employment", "text", df, 122, 278, 370, 12, "defendant_previous_employment"),
        _f(d, "app_previous_how_long", "text", df, 544, 278, 60, 12, "defendant_previous_how_long"),
        _f(d, "app_dob", "text", df, 88, 293, 50, 12, "defendant_dob", required=True),
        _f(d, "app_height", "text", df, 174, 293, 50, 12, "defendant_height"),
        _f(d, "app_weight", "text", df, 260, 293, 54, 12, "defendant_weight"),
        _f(d, "app_eyes", "text", df, 344, 293, 52, 12, "defendant_eyes"),
        _f(d, "app_hair", "text", df, 422, 293, 58, 12, "defendant_hair"),
        _f(d, "app_race", "text", df, 508, 293, 96, 12, "defendant_race"),
        _f(d, "app_tattoos", "text", df, 152, 323, 260, 12, "defendant_tattoos"),
        _f(d, "app_ssn", "text", df, 494, 323, 108, 12, "defendant_ssn"),
        _f(d, "app_spouse_employer", "text", df, 124, 338, 148, 12, "defendant_spouse_employer"),
        _f(d, "app_spouse_address", "text", df, 316, 338, 178, 12, "defendant_spouse_address"),
        _f(d, "app_spouse_phone", "text", df, 530, 338, 74, 12, "defendant_spouse_phone"),
        _f(d, "app_children_1", "text", df, 140, 353, 230, 12, "children_names_ages_1"),
        _f(d, "app_school_1", "text", df, 410, 353, 190, 12, "children_school_1"),
        _f(d, "app_school_2", "text", df, 410, 368, 190, 12, "children_school_2"),
        _f(d, "app_vehicle_year", "text", df, 108, 503, 46, 12, "def_vehicle_year"),
        _f(d, "app_vehicle_make", "text", df, 184, 503, 48, 12, "def_vehicle_make"),
        _f(d, "app_vehicle_model", "text", df, 266, 503, 48, 12, "def_vehicle_model"),
        _f(d, "app_vehicle_color", "text", df, 346, 503, 50, 12, "def_vehicle_color"),
        _f(d, "app_vehicle_plate", "text", df, 450, 503, 150, 12, "def_vehicle_plate"),
        _f(d, "app_vehicle_where", "text", df, 168, 518, 150, 12, "def_vehicle_purchase_location"),
        _f(d, "app_vehicle_owed", "text", df, 384, 518, 48, 12, "def_vehicle_amount_owed"),
        _f(d, "app_vehicle_lender", "text", df, 476, 518, 120, 12, "def_vehicle_lender"),
        _f(d, "app_dl", "text", df, 115, 533, 148, 12, "defendant_dl"),
        _f(d, "app_dl_state", "text", df, 296, 533, 80, 12, "defendant_dl_state"),
        _f(d, "app_social_login", "text", df, 112, 548, 180, 12, "defendant_social_login"),
        _f(d, "app_social_password", "text", df, 346, 548, 240, 12, "defendant_social_password"),
        _f(d, "app_remarks", "text", df, 80, 578, 510, 16, "def_remarks"),
        _f(d, "app_signed_day", "text", df, 134, 723, 32, 12, "day", required=True),
        _f(d, "app_signed_month", "text", df, 198, 723, 298, 12, "month", required=True),
        _f(d, "app_signed_year", "text", df, 512, 723, 70, 12, "year", required=True),
        # Live template 5 agent_signature_5 / defendant_signature_3. The measured
        # guess sat left of the AGENT WITNESS line; these are the live areas.
        _f(d, "app_agent_signature", "signature", bd, 130.97, 736.30, 155.45, 22.89, ""),
        _f(d, "app_defendant_signature", "signature", ROLE_DEFENDANT, 403.31, 736.30, 168.30, 20.52, ""),
    ]
    relatives = [
        (385, "app_parent", "def_parent_name", "def_parent_address", "def_parent_phone"),
        (400, "app_spouse_parent", "def_spouse_parent_name", "def_spouse_parent_address", "def_spouse_parent_phone"),
        (415, "app_sibling_1", "def_sibling_1_name", "def_sibling_1_address", "def_sibling_1_phone"),
        (430, "app_sibling_2", "def_sibling_2_name", "def_sibling_2_address", "def_sibling_2_phone"),
        (445, "app_sibling_3", "def_sibling_3_name", "def_sibling_3_address", "def_sibling_3_phone"),
        (460, "app_best_friend", "def_best_friend_name", "def_best_friend_address", "def_best_friend_phone"),
        (475, "app_attorney", "def_attorney_name", "def_attorney_address", "def_attorney_phone"),
    ]
    for y, prefix, n_src, a_src, p_src in relatives:
        rows.append(_f(d, f"{prefix}_name", "text", df, 120, y, 145, 12, n_src))
        rows.append(_f(d, f"{prefix}_address", "text", df, 306, y, 172, 12, a_src))
        rows.append(_f(d, f"{prefix}_phone", "text", df, 516, y, 88, 12, p_src))
    return rows


def _indemnity() -> List[Dict[str, Any]]:
    d = "indemnity-agreement"
    # Live template 5 puts every prefilled text and date box on bondsman.
    ind = ROLE_BONDSMAN
    df = ROLE_DEFENDANT
    co = ROLE_COINDEMNITOR
    bd = ROLE_BONDSMAN
    agent_name, name_prefs = AGENT_LINE_BOXES[d]["name"]
    agent_license, license_prefs = AGENT_LINE_BOXES[d]["license"]
    rows = [
        _f(d, "ind_agent_name", "text", bd, *agent_name, "agent_name", required=True, preferences=name_prefs),
        _f(d, "ind_agent_license", "text", bd, *agent_license, "agent_license", preferences=license_prefs),
        _f(d, "ind_power_number", "text", bd, 468, 36, 130, 13, "poa_number", required=True),
        _f(d, "ind_case_number", "text", bd, 460, 54, 140, 13, "case_number", required=True),
        _f(d, "ind_execution_date", "text", bd, 490, 72, 110, 13, "today_date", required=True),
        # Identity rows are about 10pt apart. Height 8 sits on the line
        # without covering the row below.
        _f(d, "ind_first_name", "text", ind, 128, 97, 72, 8, "indemnitor_first", required=True),
        _f(d, "ind_middle_name", "text", ind, 204, 97, 70, 8, "indemnitor_middle"),
        _f(d, "ind_last_name", "text", ind, 278, 97, 58, 8, "indemnitor_last", required=True),
        _f(d, "ind_phone", "text", ind, 372, 97, 66, 8, "indemnitor_phone"),
        _f(d, "ind_dob", "text", ind, 500, 97, 100, 8, "indemnitor_dob", required=True),
        _f(d, "ind_address", "text", ind, 118, 114, 188, 8, "indemnitor_address", required=True),
        _f(d, "ind_city", "text", ind, 336, 114, 90, 8, "indemnitor_city"),
        _f(d, "ind_state", "text", ind, 460, 114, 46, 8, "indemnitor_state"),
        _f(d, "ind_zip", "text", ind, 528, 114, 70, 8, "indemnitor_zip"),
        _f(d, "ind_ssn", "text", ind, 118, 124, 100, 8, "indemnitor_ssn"),
        _f(d, "ind_email", "text", ind, 258, 124, 175, 8, "indemnitor_email"),
        _f(d, "ind_phone_2", "text", ind, 474, 124, 120, 8, "indemnitor_phone2"),
        _f(d, "ind_employer", "text", ind, 94, 134, 125, 8, "indemnitor_employer"),
        _f(d, "ind_employer_address", "text", ind, 268, 134, 320, 8, "indemnitor_employer_address"),
        _f(d, "ind_spouse_name", "text", ind, 100, 144, 120, 8, "indemnitor_spouse_name"),
        _f(d, "ind_spouse_dob", "text", ind, 286, 144, 58, 8, "indemnitor_spouse_dob"),
        _f(d, "ind_spouse_email", "text", ind, 380, 144, 210, 8, "indemnitor_spouse_email"),
        _f(d, "ind_spouse_employer", "text", ind, 94, 154, 160, 8, "indemnitor_spouse_employer"),
        _f(d, "ind_spouse_employer_address", "text", ind, 305, 154, 280, 8, "indemnitor_spouse_employer_address"),
        _f(d, "ind_parents", "text", ind, 76, 165, 180, 8, "def_parent_name"),
        _f(d, "ind_parents_address", "text", ind, 305, 165, 280, 8, "def_parent_address"),
        # WHEREAS lines are ~7.3pt apart. Boxes sit on the underline and
        # stop before the printed words ("of" / "Dollars" / "($" / "number(s)").
        _f(d, "ind_bond_words", "text", ind, 210, 413, 122, 7, "bond_amount_words", required=True),
        _f(d, "ind_bond_numeric", "text", ind, 373, 413, 129, 7, "bond_amount_numeric", required=True),
        _f(d, "ind_poa_1", "text", ind, 168, 421, 84, 6.5, "poa_1", required=True),
        _f(d, "ind_poa_2", "text", ind, 266, 421, 88, 6.5, "poa_2"),
        _f(d, "ind_poa_3", "text", ind, 370, 421, 92, 6.5, "poa_3"),
        _f(d, "ind_poa_4", "text", ind, 474, 421, 96, 6.5, "poa_4"),
        # Stop above the WITNESSES signature row at y=639.7.
        _f(d, "ind_signed_day", "text", ind, 252, 628, 58, 8, "day", required=True),
        _f(d, "ind_signed_month", "text", ind, 340, 628, 155, 8, "month", required=True),
        _f(d, "ind_signed_year", "text", ind, 510, 628, 80, 8, "year", required=True),
        # Live template 5 rows. The previous y values sat one line too high.
        _f(d, "ind_defendant_signature", "signature", df, 322.93, 641.31, 250.15, 19.14, ""),
        _f(d, "ind_indemnitor_signature", "signature", ROLE_INDEMNITOR, 322.32, 659.66, 250.15, 19.14, ""),
        _f(d, "ind_coindemnitor_signature", "signature", co, 321.71, 677.20, 250.15, 19.14, ""),
        # Three WITNESSES lines. Live field agent_signature_4 (also on the waiver).
        _f(d, "agent_signature_4", "signature", bd, 95.41, 639.72, 190.82, 19.94, ""),
        _f(d, "agent_signature_4", "signature", bd, 96.02, 659.66, 190.82, 19.94, ""),
        _f(d, "agent_signature_4", "signature", bd, 95.41, 679.60, 190.82, 19.94, ""),
        # State underline y=709, county y=718, day/month/year y=731.
        # 12pt boxes on that pitch overlapped each other.
        _f(d, "ind_notary_state", "text", ind, 76, 701, 104, 8, "state"),
        _f(d, "ind_notary_county", "text", ind, 84, 710, 98, 8, "county"),
        _f(d, "ind_notary_day", "text", ind, 70, 724, 46, 7, "day"),
        _f(d, "ind_notary_month", "text", ind, 146, 724, 80, 7, "month"),
        _f(d, "ind_notary_year", "text", ind, 246, 724, 42, 7, "year"),
        _f(d, "ind_notary_appeared", "text", ind, 40, 739, 320, 7, "indemnitor_name"),
    ]
    for i, y in enumerate((193, 205, 217), start=1):
        rows.append(_f(d, f"ind_ref_{i}_name", "text", ind, 55, y, 230, 11, f"reference_{i}_name"))
        rows.append(_f(d, f"ind_ref_{i}_address", "text", ind, 300, y, 200, 11, f"reference_{i}_address"))
        rows.append(_f(d, f"ind_ref_{i}_phone", "text", ind, 510, y, 85, 11, f"reference_{i}_phone"))
    return rows


def _collateral() -> List[Dict[str, Any]]:
    d = "collateral-receipt"
    ind = ROLE_INDEMNITOR
    df = ROLE_DEFENDANT
    bd = ROLE_BONDSMAN
    return [
        # Promissory note occupies the top of this carrier page (Palmetto has
        # no separate promissory-note PDF). Text stays on bondsman, matching
        # live template 5. The $ underline ends at x=129, before City and State.
        _f(d, "note_amount_numeric", "text", bd, 41, 22, 86, 8, "bond_amount_numeric", required=True),
        _f(d, "note_amount_words", "text", bd, 36, 70, 480, 14, "bond_amount_words", required=True),
        _f(d, "note_defendant", "text", bd, 76, 166, 400, 14, "defendant_name", required=True),
        _f(d, "note_date", "text", bd, 56, 228, 160, 14, "today_date", required=True),
        _f(d, "note_defendant_signature", "signature", df, 340, 206, 220, 16, ""),
        _f(d, "note_indemnitor_signature", "signature", ind, 340, 226, 220, 16, ""),
        _f(d, "note_coindemnitor_signature", "signature", ROLE_COINDEMNITOR, 340, 244, 220, 16, ""),
        _f(d, "cr_number", "text", bd, 476, 296, 110, 14, "collateral_receipt_number", required=True),
        # Receipt Date is the live defendant date-signed box (uuid 3b8faaa0).
        # No second blank exists, so a prefilled today_date is not placed there.
        _f(d, "cr_promissory_note", "checkbox", bd, 42, 346, 11, 11, "collateral_promissory_note"),
        _f(d, "cr_indemnity", "checkbox", bd, 221, 346, 11, 11, "collateral_indemnity"),
        _f(d, "cr_mortgage", "checkbox", bd, 366, 346, 11, 11, "collateral_mortgage"),
        _f(d, "cr_description", "text", bd, 28, 368, 550, 16, "collateral_description"),
        _f(d, "cr_cash", "checkbox", bd, 41, 422, 11, 11, "collateral_cash"),
        _f(d, "cr_check", "checkbox", bd, 82, 422, 11, 11, "collateral_check"),
        _f(d, "cr_money_order", "checkbox", bd, 129, 422, 11, 11, "collateral_money_order"),
        _f(d, "cr_credit_card", "checkbox", bd, 215, 422, 11, 11, "collateral_credit_card"),
        _f(d, "cr_other", "checkbox", bd, 446, 422, 11, 11, "collateral_other"),
        _f(d, "cr_card_amount", "text", bd, 328, 420, 110, 12, "premium_numeric"),
        _f(d, "cr_received_from", "text", bd, 120, 444, 460, 14, "indemnitor_name", required=True),
        _f(d, "cr_address", "text", bd, 82, 461, 500, 14, "indemnitor_address", required=True),
        _f(d, "cr_defendant", "text", bd, 90, 490, 290, 14, "defendant_name", required=True),
        _f(d, "cr_bond_amount", "text", bd, 450, 490, 140, 14, "bond_amount_numeric", required=True),
        _f(d, "cr_power", "text", bd, 78, 507, 200, 14, "poa_number", required=True),
        _f(d, "cr_court", "text", bd, 326, 507, 250, 14, "court_type"),
        _f(d, "cr_charge", "text", bd, 102, 524, 480, 14, "charge_line_1", required=True),
        _f(d, "cr_card_fee_pct", "text", bd, 186, 541, 100, 12, "card_fee_percent"),
        # Received By is the signature line (agent_signature_4). There is no
        # printed name line. In Trust for has no underline clear of the palm logo.
        # Live template 5. Received By is agent_signature_4. The return block is
        # agent_signature_5 (agent) and the bottom agent line is agent_signature_6.
        _f(d, "agent_signature_4", "signature", bd, 433.36, 540.59, 155.25, 23.68, ""),
        _f(d, "agent_signature_5", "signature", bd, 87.41, 731.57, 183.98, 28.41, ""),
        _f(d, "cr_agent_signature", "signature", bd, 364.29, 764.72, 213.93, 20.52, ""),
        _f(d, "cr_indemnitor_signature", "signature", ind, 400.97, 730.78, 190.70, 14.21, ""),
    ]


def _information_sheet() -> List[Dict[str, Any]]:
    """Florida Form 704 / CMA 7824, the Palmetto bail bond information sheet.

    Underlines were measured on the rendered page of
    ``surety-terms-palmetto.pdf`` (the file behind template 5 document 6).
    The Shamrock agency block is preprinted. Signature roles are provisional:
    the form prints SIGN and does not name the signer.
    """
    d = "bail-bond-information-sheet-palmetto"
    return [
        # Left underline under BOND DEFENDANT:, y=182, x=21.5–230.
        _f(d, "bbis_defendant", "text", ROLE_BONDSMAN, 24, 168, 204, 13, "defendant_name", required=True),
        # Two right-hand underlines under POWER OF ATTORNEY NUMBER(S):.
        _f(d, "bbis_poa_1", "text", ROLE_BONDSMAN, 350, 154, 226, 13, "poa_1", required=True),
        _f(d, "bbis_poa_2", "text", ROLE_BONDSMAN, 350, 168, 228, 13, "poa_2"),
        # SIGN lines at y=681. Left x=58–255, right x=366–580.
        # Live template 5 defendant_signature_3 and indemnitor_signature_3.
        _f(d, "bbis_sign_left", "signature", ROLE_DEFENDANT, 56.92, 657.36, 216.65, 22.97, ""),
        _f(d, "bbis_sign_right", "signature", ROLE_INDEMNITOR, 366.59, 655.78, 199.51, 22.18, ""),
        # Unlabeled rule under the right SIGN, y=705, x=366–580.
        _f(d, "bbis_sign_right_second", "signature", ROLE_COINDEMNITOR, 368, 690, 208, 14, ""),
    ]


PALMETTO_FIELDS: List[Dict[str, Any]] = (
    _appearance() + _application() + _indemnity() + _collateral() + _information_sheet()
)


def fields_for(document: str) -> List[Dict[str, Any]]:
    return [f for f in PALMETTO_FIELDS if f["document"] == document]


def required_text_fields(document: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = PALMETTO_FIELDS if document is None else fields_for(document)
    return [f for f in rows if f["required"] and f["type"] == "text"]


def docuseal_fields() -> List[Dict[str, Any]]:
    return [f for f in PALMETTO_FIELDS if f.get("docuseal")]


def iter_documents() -> Iterable[str]:
    return tuple(PAGE_SIZE.keys())


# Carrier inventory. DocuSeal template documents are NOT in the repo.
PACKET_INVENTORY: List[Dict[str, Any]] = [
    {
        "slug": "paperwork-header",
        "osi": "surety-agnostic-shamrock/paperwork-header.pdf (1 page)",
        "palmetto": "same Shamrock form (shared)",
        "docuseal": "inside template 1 and template 5; field boxes not in repo",
    },
    {
        "slug": "faq-cosigners",
        "osi": "surety-agnostic-shamrock/faq-cosigners.pdf (2 pages)",
        "palmetto": "same Shamrock form (shared)",
        "docuseal": "inside templates; field boxes not in repo",
    },
    {
        "slug": "faq-defendants",
        "osi": "surety-agnostic-shamrock/faq-defendants.pdf (2 pages)",
        "palmetto": "same Shamrock form (shared)",
        "docuseal": "inside templates; field boxes not in repo",
    },
    {
        "slug": "master-waiver",
        "osi": "surety-agnostic-shamrock/master-waiver.pdf (4 pages)",
        "palmetto": "same Shamrock form (shared)",
        "docuseal": "inside templates; field boxes not in repo",
    },
    {
        "slug": "ssa-release",
        "osi": "surety-agnostic-shamrock/ssa-release.pdf (1 page)",
        "palmetto": "same Shamrock form (shared)",
        "docuseal": "inside templates; field boxes not in repo",
    },
    {
        "slug": "payment-plan",
        "osi": "surety-agnostic-shamrock/payment-plan.pdf (4 pages)",
        "palmetto": "same Shamrock form (shared). Not a Palmetto premium receipt.",
        "docuseal": "inside templates; field boxes not in repo",
    },
    {
        "slug": "appearance-bond",
        "osi": "osi/Appearance Bond blank.pdf (1 page, 26 widgets)",
        "palmetto": "palmetto/Shamrock Palmetto Official Appearance Bond.pdf (1 page, 23 widgets)",
        "docuseal": "print/wet-ink; not confirmed inside the e-sign templates",
    },
    {
        "slug": "indemnity-agreement",
        "osi": "osi/indemnity-agreement.pdf (1 page, no placed fields)",
        "palmetto": "palmetto/indemnity-agreement-palmetto.pdf (1 page)",
        "docuseal": "template field boxes not in repo",
    },
    {
        "slug": "defendant-application",
        "osi": "osi/defendant-application.pdf (2 pages, no placed fields)",
        "palmetto": "palmetto/defendant-application-palmetto.pdf (1 page)",
        "docuseal": "template field boxes not in repo",
    },
    {
        "slug": "bail-bond-information-sheet-palmetto",
        "osi": (
            "osi/surety-terms.pdf is OSI-0521-FL, 'SURETY BAIL BOND TERMS AND "
            "CONDITIONS OF CONTRACT INFORMATIONAL SHEET'. It is not Form 704. "
            "No OSI or shared Shamrock PDF in templates/ contains Form#704 or CMA 7824."
        ),
        "palmetto": (
            "palmetto/surety-terms-palmetto.pdf is Florida BAIL BOND INFORMATION "
            "SHEET Form#704 / CMA 7824, with the Shamrock agent box preprinted. "
            "Template 5 document index 6, filename surety-terms-palmetto. "
            "Spec name is bail-bond-information-sheet-palmetto. The PDF file "
            "name is unchanged so the print stitch slug surety-terms still resolves."
        ),
        "docuseal": "template 5 document index 6; live boxes were 5 text and 2 signature fields",
    },
    {
        "slug": "collateral-receipt",
        "osi": "osi/collateral-receipt.pdf (1 page, no placed fields)",
        "palmetto": "palmetto/collateral-receipt-palmetto.pdf (1 page; promissory note on top, receipt below)",
        "docuseal": "template field boxes not in repo",
    },
    {
        "slug": "promissory-note",
        "osi": "osi/promissory-note.pdf (1 page). Stitch still shares this file with Palmetto.",
        "palmetto": "No separate file. Equivalent is the promissory note printed on collateral-receipt-palmetto.pdf.",
        "docuseal": "OSI template historically 13 documents; Palmetto 11. This is one of the two likely omissions.",
    },
    {
        "slug": "disclosure-form",
        "osi": "osi/disclosure-form.pdf (1 page). Stitch still shares this file with Palmetto.",
        "palmetto": "MISSING. No Palmetto carrier disclosure in templates/.",
        "docuseal": "not in repo",
    },
    {
        "slug": "premium-receipt",
        "osi": "MISSING as its own carrier PDF (payment-plan is the shared schedule).",
        "palmetto": "MISSING. No Palmetto premium receipt PDF.",
        "docuseal": "not in repo",
    },
    {
        "slug": "check-in",
        "osi": "MISSING. Check-in is portal/GPS, not a carrier PDF.",
        "palmetto": "MISSING.",
        "docuseal": "not in repo",
    },
    {
        "slug": "mortgage",
        "osi": "osi/OSI Mortgage Philip Blasone.pdf is a filled example, not a blank.",
        "palmetto": "MISSING. No Palmetto mortgage blank. Not invented.",
        "docuseal": "not in repo",
    },
]


QUESTIONS: List[Dict[str, str]] = [
    {
        "topic": "DocuSeal template field boxes",
        "detail": (
            "Template 1 (shamrock-osi-paperwork-complete) and template 5 "
            "(shamrock-palmetto-paperwork-complete) have fields: null in "
            "tests/fixtures/docuseal_template_snapshot.json. This environment "
            "has no DOCUSEAL_API_KEY, so the live boxes were not visible. "
            "The spec places fields from the carrier PDFs. Re-run the apply "
            "script only after you confirm attachment names on template 5."
        ),
        "page_image": "",
    },
    {
        "topic": "Appearance bond has no case-number blank",
        "detail": (
            "OCR of the Palmetto appearance bond shows POWER # and ARREST # "
            "and no CASE label. The old writer key CaseNumberField matched "
            "nothing. Case number is placed on the application and indemnity "
            "headers instead. OSI's appearance bond does have CaseNum."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/appearance-bond_p1.png",
    },
    {
        "topic": "Application header ADDRESS / CONTACTED BY / RELATIONSHIP",
        "detail": (
            "The right-hand box labels ADDRESS, CONTACTED BY, DATE, TIME, and "
            "RELATIONSHIP are not the body street-address line. ADDRESS is "
            "filled with the defendant address because that is the OSI "
            "defendant_address source. CONTACTED BY has no OSI source and "
            "stays blank. TIME is filled with court time only because that "
            "is the only time the bond payload already has."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/defendant-application_p1.png",
    },
    {
        "topic": "Application agent-witness line",
        "detail": (
            "The form prints one left line, AGENT WITNESS HERE / SIGNATURE OF "
            "AGENT, and one right line, DEFENDANT SIGN HERE. Those are the two "
            "signature widgets. There is no separate witness role."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/defendant-application_p1.png",
    },
    {
        "topic": "Social media password",
        "detail": (
            "The application prints a Password blank next to Social Media Login. "
            "The widget exists and is filled only when the bond payload already "
            "has defendant_social_password. The sample packet does not invent one."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/defendant-application_p1.png",
    },
    {
        "topic": "Which license number",
        "detail": (
            "Appearance-bond agentBailLicNumField still uses the bond's license "
            "when one is present, otherwise the constant P139768 already printed "
            "on that blank (the previous fill was correct for that widget). "
            "Other forms leave the license line to the agent name only. "
            "A different writing agent with no license on the bond will not "
            "get an invented license, but the appearance-bond license line "
            "will still show P139768."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/appearance-bond_p1.png",
    },
    {
        "topic": "Brendan Form 704 candidate",
        "detail": (
            "user-form-palmetto-candidate.pdf was described as a one-page "
            "PDFfiller scan of Florida Form#704 / CMA 7824 with the Shamrock "
            "agent box. The file was not on this machine, so it was not "
            "pixel-compared. The template 5 document already prints that title, "
            "Form#704, CMA 7824, and the Shamrock header, so that PDF was kept."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/bail-bond-information-sheet_p1.png",
    },
    {
        "topic": "Form 704 signature roles",
        "detail": (
            "Florida BAIL BOND INFORMATION SHEET Form#704 prints two SIGN lines "
            "and a third unlabeled rule under the right SIGN. It does not say "
            "who signs. Provisional roles, matching the lowercase template 5 "
            "submitters: left SIGN defendant, right SIGN indemnitor, the line "
            "under the right SIGN coindemnitor. OSI's different sheet "
            "(OSI-0521-FL) labels Principal/Defendant first and then three "
            "indemnitor lines. Confirm or correct these three roles."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/bail-bond-information-sheet_p1.png",
    },
    {
        "topic": "Form 704 has two POA lines",
        "detail": (
            "POWER OF ATTORNEY NUMBER(S) has two underlines. They are filled "
            "from poa_1 and poa_2, the same slots the indemnity agreement uses. "
            "A bond with a third or fourth POA has nowhere to print it on this sheet."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/bail-bond-information-sheet_p1.png",
    },
    {
        "topic": "OSI has no Form 704",
        "detail": (
            "OSI template files in the repo do not include Florida Form#704 / "
            "CMA 7824. osi/surety-terms.pdf is the OSI-0521-FL terms and "
            "conditions informational sheet (defendant/principal, four "
            "offense/case/power/amount rows, four signature captions). "
            "Say if OSI should get Form 704. This PR does not change OSI."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/osi-surety-terms_p1.png",
    },
    {
        "topic": "Collateral receipt return-footer date",
        "detail": (
            "Still open. Live template 5 has no date widget on the "
            "return-of-collateral footer. The unnamed defendant date "
            "(uuid 3b8faaa0) is the Receipt Date blank and also spans the "
            "SSA release. It is copied through unchanged. No date was added "
            "on the footer."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/collateral-receipt_p1.png",
    },
    {
        "topic": "Collateral Receipt Date prefill",
        "detail": (
            "The Receipt Date underline is already the live defendant "
            "date-signed box (uuid 3b8faaa0). A bondsman today_date on that "
            "blank would print a second date in the same space. The form has "
            "no other Receipt Date blank, so the prefill was not placed. "
            "The promissory-note Date line still receives today_date. "
            "Confirm whether Receipt Date should stay the signer's date only."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/collateral-receipt_p1.png",
    },
    {
        "topic": "Collateral Received By name",
        "detail": (
            "Received By is the signature line (live agent_signature_4, "
            "uuid 21ba7f86). There is no separate printed name line. The "
            "typed agent_name box was removed so it does not sit inside the "
            "signature. Say if a name belongs somewhere else on this form."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/collateral-receipt_p1.png",
    },
    {
        "topic": "Collateral In Trust for name",
        "detail": (
            "In Trust for: has no underline clear of the palm-tree logo. "
            "The logo occupies the blank to the right of the label, and the "
            "Palmetto address block starts immediately after the logo. The "
            "indemnitor_name box was removed so it does not cover the logo. "
            "Say where that name should go."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/collateral-receipt_p1.png",
    },
    {
        "topic": "Promissory-note dollar amount",
        "detail": (
            "The note's $ line and DOLLARS line are filled with the penal bond "
            "amount (same figure OSI writes as the bond amount), not the premium. "
            "Say if Palmetto wants the note to be the premium or the collateral sum."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/collateral-receipt_p1.png",
    },
    {
        "topic": "Indemnity financial-statement dollars",
        "detail": (
            "Cash, stocks, real estate, and liability lines have no OSI prefill "
            "source. Widgets were not added there, so empty underlines stay "
            "empty rather than receiving an invented balance."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/indemnity-agreement_p1.png",
    },
    {
        "topic": "DocuSeal role spelling",
        "detail": (
            "Template 5 submitters are bondsman, indemnitor, defendant, and "
            "coindemnitor. The spec uses those lowercase strings. If a role "
            "is rejected, the apply script stops and does not create a submission."
        ),
        "page_image": "",
    },
    {
        "topic": "Palmetto fields OSI does not have",
        "detail": (
            "Palmetto application asks for social-media login, contacted-by, "
            "and a witness line. Palmetto collateral receipt combines the "
            "promissory note and a credit-card fee percent. "
            "Those widgets are Palmetto-only and stay blank unless the payload "
            "already has a value. In Trust for has no clear underline; see that question."
        ),
        "page_image": "/opt/cursor/artifacts/palmetto-rebuild/pages/defendant-application_p1.png",
    },
]
