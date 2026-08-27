"""
ShamrockLeads — Driver's License & ID OCR Extraction Service
============================================================
Extracts structured indemnitor PII from front & back photos of US Driver's Licenses,
State IDs, and Passports (Google Vision OCR / Regex parser fallback).

Extracted fields:
  - first_name, last_name, full_name
  - dob (YYYY-MM-DD / MM/DD/YYYY)
  - dl_number, dl_state
  - address, city, state, zip
  - expiration_date
"""
import re
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# State abbreviation matching regex
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"
}

# Apostrophe / hyphen legal names (O'Neal, O'Neill, D'Angelo, St-Pierre).
_APOSTROPHE_CHARS = dict.fromkeys("`'´’‘ʻʼ", "'")
_AAMVA_NAME = r"([A-Za-z][A-Za-z'\-. ]*?)"
_AAMVA_STOP = (
    r"(?=\s*(?:DAC|DCT|DCS|DAB|DAD|DCU|DAQ|DBB|DBA|DBD|DAG|DAI|DAJ|"
    r"DAK|DBC|DAH|DCA|DAU|DAY|DAZ|DDK|DDA|DDB|DCK)|\r|\n|$)"
)


def normalize_person_name(value: Any) -> str:
    """Preserve apostrophes and hyphens; title-case Irish/Scottish/compound names."""
    s = str(value or "").strip()
    if not s:
        return ""
    s = "".join(_APOSTROPHE_CHARS.get(ch, ch) for ch in s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    if not s:
        return ""
    return " ".join(_title_name_token(tok) for tok in s.split(" ") if tok)


def name_letters_key(value: Any) -> str:
    """Lowercase letters only — used to compare names, not to rewrite them."""
    return re.sub(r"[^a-z]", "", normalize_person_name(value).lower())


def last_name_token(value: Any) -> str:
    parts = normalize_person_name(value).split()
    return parts[-1] if parts else ""


def first_name_token(value: Any) -> str:
    parts = normalize_person_name(value).split()
    return parts[0] if parts else ""


def collapse_confusable_surname(value: Any) -> str:
    """Compare-only collapse of Irish Neal/Neil/Neill OCR mixups.

    Never use this to rewrite a stored name. O'Neal and O'Neill are both legal.
    """
    letters = name_letters_key(value)
    letters = re.sub(r"eill$", "eal", letters)
    letters = re.sub(r"eil$", "eal", letters)
    return letters


def surnames_are_confusable(left: Any, right: Any) -> bool:
    """True when last names are the same person-family but different letters (O'Neal vs O'Neill)."""
    a = last_name_token(left)
    b = last_name_token(right)
    if not a or not b:
        return False
    if name_letters_key(a) == name_letters_key(b):
        return False
    return collapse_confusable_surname(a) == collapse_confusable_surname(b)


def replace_last_name(full_name: Any, new_last: Any) -> str:
    full = normalize_person_name(full_name)
    last = last_name_token(new_last) or normalize_person_name(new_last)
    if not full:
        return last
    if not last:
        return full
    parts = full.split()
    parts[-1] = last
    return " ".join(parts)


def resolve_legal_name(ocr_name: Any, confirmed_name: Any = "") -> Dict[str, Any]:
    """Prefer a caller-confirmed spelling on confusable OCR surnames.

    Does **not** globally map O'Neill → O'Neal. If the card and the caller
    agree, or there is no confirmed name, OCR is kept as copied.
    """
    ocr = normalize_person_name(ocr_name)
    confirmed = normalize_person_name(confirmed_name)
    base: Dict[str, Any] = {
        "name": ocr or confirmed,
        "source": "ocr" if ocr else ("confirmed" if confirmed else ""),
        "conflict": None,
        "ocr_name": ocr,
        "confirmed_name": confirmed,
    }
    if not ocr:
        return {**base, "name": confirmed, "source": "confirmed" if confirmed else ""}
    if not confirmed:
        return {**base, "name": ocr, "source": "ocr"}
    if name_letters_key(ocr) == name_letters_key(confirmed):
        return {**base, "name": ocr, "source": "ocr"}

    ocr_last = last_name_token(ocr)
    conf_last = last_name_token(confirmed)
    ocr_first = first_name_token(ocr)
    conf_parts = confirmed.split()
    conf_first = first_name_token(confirmed)
    firsts_ok = (
        len(conf_parts) == 1
        or not ocr_first
        or not conf_first
        or name_letters_key(ocr_first) == name_letters_key(conf_first)
    )
    if surnames_are_confusable(ocr_last, conf_last) and firsts_ok:
        resolved = replace_last_name(ocr, conf_last)
        return {
            **base,
            "name": resolved,
            "source": "confirmed_confusable",
            "conflict": {
                "kind": "confusable_surname",
                "ocr": ocr,
                "confirmed": confirmed,
                "ocr_last": ocr_last,
                "confirmed_last": conf_last,
            },
        }
    return {
        **base,
        "name": ocr,
        "source": "ocr",
        "conflict": {
            "kind": "name_mismatch",
            "ocr": ocr,
            "confirmed": confirmed,
        },
    }


def _simple_cap(token: str) -> str:
    if not token:
        return ""
    return token[0].upper() + token[1:].lower()


def _title_name_token(token: str) -> str:
    if not token:
        return ""
    if "-" in token:
        return "-".join(_title_name_token(part) for part in token.split("-"))
    if "'" in token:
        left, right = token.split("'", 1)
        return _simple_cap(left) + "'" + _simple_cap(right)
    low = token.lower()
    if low.startswith("mc") and len(token) > 3 and token[2].isalpha():
        return "Mc" + _simple_cap(token[2:])
    if low.startswith("mac") and len(token) > 4 and token[3].isalpha():
        return "Mac" + _simple_cap(token[3:])
    return _simple_cap(token)


class IDOCRService:
    """
    Parses OCR text or raw image bytes into structured indemnitor fields.
    """

    @staticmethod
    def parse_dl_text(text: str, default_state: str = "FL") -> Dict[str, Any]:
        """
        Parse raw OCR text string (AAMVA PDF417 format or standard OCR text).
        """
        if not text or not isinstance(text, str):
            return {}

        out: Dict[str, Any] = {
            "first_name": "",
            "last_name": "",
            "full_name": "",
            "dob": "",
            "dl_number": "",
            "dl_state": default_state,
            "address": "",
            "city": "",
            "state": default_state,
            "zip": "",
            "expiration_date": "",
            "raw_text": text,
        }

        # ── AAMVA PDF417 Barcode Format (Back of DL) ──────────────────────
        if "ANSI " in text or "DL" in text or "DAQ" in text:
            # First Name (DAC or DCT) & Last Name (DCS or DAB)
            m_fn = re.search(r"(?:DAC|DCT)\s*" + _AAMVA_NAME + _AAMVA_STOP, text)
            m_ln = re.search(r"(?:DCS|DAB)\s*" + _AAMVA_NAME + _AAMVA_STOP, text)
            m_mn = re.search(r"DAD\s*" + _AAMVA_NAME + _AAMVA_STOP, text)
            m_suf = re.search(r"DCU\s*([A-Za-z0-9\-]+)", text)
            if m_fn:
                out["first_name"] = normalize_person_name(m_fn.group(1).strip())
            if m_ln:
                out["last_name"] = normalize_person_name(m_ln.group(1).strip())
            if m_mn:
                out["middle_name"] = normalize_person_name(m_mn.group(1).strip())
            if m_suf:
                out["suffix"] = m_suf.group(1).upper()
            if out["first_name"] and out["last_name"]:
                mid = f" {out['middle_name']}" if out.get("middle_name") else ""
                suf = f" {out['suffix']}" if out.get("suffix") else ""
                out["full_name"] = f"{out['first_name']}{mid} {out['last_name']}{suf}".strip()

            # DL Number (DAQ)
            m_dl = re.search(r"DAQ\s*([A-Z0-9\-]+)", text)
            if m_dl:
                out["dl_number"] = m_dl.group(1).strip()

            # DOB (DBB - MMDDYYYY or YYYYMMDD)
            m_dob = re.search(r"DBB\s*(\d{8})", text)
            if m_dob:
                raw_dob = m_dob.group(1)
                if raw_dob.startswith("19") or raw_dob.startswith("20"):
                    out["dob"] = f"{raw_dob[4:6]}/{raw_dob[6:8]}/{raw_dob[0:4]}"
                else:
                    out["dob"] = f"{raw_dob[0:2]}/{raw_dob[2:4]}/{raw_dob[4:8]}"

            m_exp = re.search(r"DBA\s*(\d{8})", text)
            if m_exp:
                raw_exp = m_exp.group(1)
                if raw_exp.startswith("19") or raw_exp.startswith("20"):
                    out["expiration_date"] = f"{raw_exp[4:6]}/{raw_exp[6:8]}/{raw_exp[0:4]}"
                else:
                    out["expiration_date"] = f"{raw_exp[0:2]}/{raw_exp[2:4]}/{raw_exp[4:8]}"

            m_iss = re.search(r"DBD\s*(\d{8})", text)
            if m_iss:
                raw_iss = m_iss.group(1)
                if raw_iss.startswith("19") or raw_iss.startswith("20"):
                    out["issue_date"] = f"{raw_iss[4:6]}/{raw_iss[6:8]}/{raw_iss[0:4]}"
                else:
                    out["issue_date"] = f"{raw_iss[0:2]}/{raw_iss[2:4]}/{raw_iss[4:8]}"

            # Address (DAG, DAI, DAJ, DAK)
            m_street = re.search(r"DAG\s*([^\r\n\t]+?)(?=\s*(?:DAI|DAJ|DAK|DBC|DBD|DDB|DAH)|\r|\n|$)", text)
            m_city = re.search(r"DAI\s*([^\r\n\t]+?)(?=\s*(?:DAJ|DAK|DBC|DBD|DDB)|\r|\n|$)", text)
            m_state = re.search(r"DAJ\s*([A-Z]{2})", text)
            m_zip = re.search(r"DAK\s*(\d{5})", text)

            if m_street:
                out["address"] = m_street.group(1).strip().title()
            if m_city:
                out["city"] = m_city.group(1).strip().title()
            if m_state:
                out["state"] = m_state.group(1).upper()
                out["dl_state"] = m_state.group(1).upper()
            if m_zip:
                out["zip"] = m_zip.group(1)

            m_sex = re.search(r"DBC\s*([12MF])", text)
            if m_sex:
                out["sex"] = {"1": "M", "2": "F"}.get(m_sex.group(1), m_sex.group(1))
            m_ht = re.search(r"DAU\s*([0-9]{3}(?:\s*(?:in|cm))?)", text, re.I)
            if m_ht:
                out["height"] = m_ht.group(1).strip()
            m_eye = re.search(r"DAY\s*([A-Z]{3})", text)
            if m_eye:
                out["eye_color"] = m_eye.group(1).title()
            m_hair = re.search(r"DAZ\s*([A-Z]{3})", text)
            if m_hair:
                out["hair_color"] = m_hair.group(1).title()
            m_cls = re.search(r"DCA\s*([A-Z0-9]+)", text)
            if m_cls:
                out["license_class"] = m_cls.group(1)
            m_donor = re.search(r"DDK\s*([01YN])", text, re.I)
            if m_donor:
                out["organ_donor"] = m_donor.group(1).upper() in ("1", "Y")
            m_vet = re.search(r"DDL\s*([01YN])", text, re.I)
            if m_vet:
                out["veteran"] = m_vet.group(1).upper() in ("1", "Y")
            m_cc = re.search(r"DCG\s*([A-Z]{3})", text)
            if m_cc:
                out["issuing_country"] = m_cc.group(1)

            if out["dl_number"] or out["full_name"]:
                return {k: v for k, v in out.items() if v not in ("", None)}

        # ── Standard Front DL OCR Text Fallback ─────────────────────────────
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        # DL Number pattern (FL format: Alpha + 12 digits, or general 7-13 alphanumeric)
        m_fl_dl = re.search(r"\b([A-Z]\d{12}|\d{3}-\d{2}-\d{4}|[A-Z0-9]{8,13})\b", text)
        if m_fl_dl:
            out["dl_number"] = m_fl_dl.group(1)

        # DOB pattern (DOB: MM/DD/YYYY or MM-DD-YYYY)
        m_dob = re.search(r"(?:DOB|Birth|Born)[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text, re.IGNORECASE)
        if m_dob:
            out["dob"] = m_dob.group(1)

        # City, State Zip pattern (e.g. FORT MYERS FL 33901)
        m_csz = re.search(r"([A-Z\s]+)\s+([A-Z]{2})\s+(\d{5})(?:-\d{4})?", text)
        if m_csz:
            c, s, z = m_csz.groups()
            if s in US_STATES:
                out["city"] = c.strip().title()
                out["state"] = s
                out["zip"] = z

        # Name extraction heuristic (LN, FN)
        m_name = re.search(r"(?:FN|LN|NAME)[:\s]*([A-Za-z'\-\s,]+)", text, re.IGNORECASE)
        if m_name:
            out["full_name"] = normalize_person_name(m_name.group(1).replace(",", " "))

        return {k: v for k, v in out.items() if v}

    @classmethod
    def extract_indemnitor_data(cls, front_text: str = "", back_text: str = "") -> Dict[str, Any]:
        """
        Merge extracted fields from front and back photos into single indemnitor dict.
        """
        back_data = cls.parse_dl_text(back_text) if back_text else {}
        front_data = cls.parse_dl_text(front_text) if front_text else {}

        # Merge, preferring back barcode data for high precision
        merged = {**front_data, **back_data}

        # Build indemnitor field dict ready for bond prefill
        return {
            "indemnitor_name": normalize_person_name(
                merged.get("full_name") or f"{merged.get('first_name', '')} {merged.get('last_name', '')}"
            ),
            "indemnitor_dob": merged.get("dob", ""),
            "indemnitor_dl": merged.get("dl_number", ""),
            "indemnitor_dl_state": merged.get("dl_state", "FL"),
            "indemnitor_address": merged.get("address", ""),
            "indemnitor_city": merged.get("city", ""),
            "indemnitor_state": merged.get("state", "FL"),
            "indemnitor_zip": merged.get("zip", ""),
            "ocr_confidence": 0.95 if (merged.get("dl_number") and merged.get("full_name")) else 0.70,
            "raw_extracted": merged,
        }
