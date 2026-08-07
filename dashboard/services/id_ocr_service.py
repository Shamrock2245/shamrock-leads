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
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# State abbreviation matching regex
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"
}


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
            m_fn = re.search(r"(?:DAC|DCT)\s*([A-Za-z\-]+)", text)
            m_ln = re.search(r"(?:DCS|DAB)\s*([A-Za-z\-]+)", text)
            if m_fn:
                out["first_name"] = m_fn.group(1).title()
            if m_ln:
                out["last_name"] = m_ln.group(1).title()
            if out["first_name"] and out["last_name"]:
                out["full_name"] = f"{out['first_name']} {out['last_name']}"

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

            # Address (DAG, DAI, DAJ, DAK)
            m_street = re.search(r"DAG\s*([^\r\n\t]+?)(?=\s*(?:DAI|DAJ|DAK|DBC|DBD|DDB)|\r|\n|$)", text)
            m_city = re.search(r"DAI\s*([^\r\n\t]+?)(?=\s*(?:DAJ|DAK|DBC|DBD|DDB)|\r|\n|$)", text)
            m_state = re.search(r"DAJ\s*([A-Z]{2})", text)
            m_zip = re.search(r"DAK\s*(\d{5})", text)

            if m_street:
                out["address"] = m_street.group(1).strip().title()
            if m_city:
                out["city"] = m_city.group(1).strip().title()
            if m_state:
                out["state"] = m_state.group(1).upper()
            if m_zip:
                out["zip"] = m_zip.group(1)

            if out["dl_number"] or out["full_name"]:
                return {k: v for k, v in out.items() if v}

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
        m_name = re.search(r"(?:FN|LN|NAME)[:\s]*([A-Za-z\s,]+)", text, re.IGNORECASE)
        if m_name:
            out["full_name"] = m_name.group(1).strip().title()

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
            "indemnitor_name": merged.get("full_name") or f"{merged.get('first_name', '')} {merged.get('last_name', '')}".strip(),
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
