"""
ShamrockLeads — ID / Driver License / Passport AI Scanner Service.

Uses OpenAI GPT-4o-mini Vision (primary) or local regex/OCR rules (fallback)
to extract structured identity data from Driver's Licenses, State IDs, and Passports.
Returns structured dict for instant indemnitor intake auto-fill.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class IDScannerService:

    @staticmethod
    def _encode_image(image_bytes: bytes) -> str:
        """Encode image bytes to base64 string."""
        return base64.b64encode(image_bytes).decode("utf-8")

    @classmethod
    async def scan_id_image(cls, image_bytes: bytes, filename: str = "") -> dict[str, Any]:
        """
        Main entry point: scan an ID photo/PDF page and extract structured identity data.
        Returns JSON-serializable dict of indemnitor profile fields.
        """
        if not image_bytes:
            return {"success": False, "error": "No image data provided"}

        # Try OpenAI GPT-4o-mini Vision if key present
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if api_key:
            try:
                res = await cls._scan_with_openai_vision(image_bytes, api_key)
                if res and res.get("success"):
                    return res
            except Exception as exc:
                logger.warning("[id_scanner] OpenAI Vision scan failed: %s", exc)

        # Fallback to local regex/OCR parser
        return cls._scan_with_local_ocr(image_bytes, filename=filename)

    @classmethod
    async def _scan_with_openai_vision(cls, image_bytes: bytes, api_key: str) -> dict[str, Any]:
        """Call OpenAI GPT-4o-mini Vision to parse ID card or Passport."""
        import aiohttp

        b64_img = cls._encode_image(image_bytes)
        mime_type = "image/jpeg"
        if image_bytes.startswith(b"\x89PNG"):
            mime_type = "image/png"
        elif image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:20]:
            mime_type = "image/webp"

        system_prompt = (
            "You are an expert OCR parser for Driver's Licenses, State IDs, and Passports. "
            "Extract the personal identification details from the provided image. "
            "Return strictly valid JSON with no markdown formatting, using this exact schema:\n"
            "{\n"
            '  "first_name": "JOHN",\n'
            '  "middle_name": "ROBERT",\n'
            '  "last_name": "DOE",\n'
            '  "full_name": "JOHN ROBERT DOE",\n'
            '  "dob": "1985-06-15",\n'
            '  "dl_number": "D123456789010",\n'
            '  "dl_state": "FL",\n'
            '  "address": "1234 MAIN ST",\n'
            '  "city": "FORT MYERS",\n'
            '  "state": "FL",\n'
            '  "zip": "33901",\n'
            '  "sex": "M",\n'
            '  "expiration_date": "2028-06-15",\n'
            '  "id_type": "driver_license"\n'
            "}\n"
            "Rules:\n"
            "- Standardize dates to YYYY-MM-DD format.\n"
            "- Extract 2-letter state codes for dl_state and state.\n"
            "- If a field is not visible or unknown, set its value to null.\n"
            "- id_type must be one of: 'driver_license', 'state_id', 'passport', 'other'.\n"
            "- Carefully read all text blocks and use context to infer address, even if poorly lit, blurred, or upside-down.\n"
            "- Distinguish between mailing address and physical address if both are present, preferring physical.\n"
            "- Extract the full middle name if present, not just the initial.\n"
            "- Handle vertical, sideways, or upside-down images gracefully."
        )

        payload = {
            "model": "gpt-4o-mini",
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract all identity details from this Driver License / ID / Passport."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64_img}"},
                        },
                    ],
                },
            ],
            "max_tokens": 500,
            "temperature": 0.1,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning("[id_scanner] OpenAI API returned HTTP %s: %s", resp.status, text[:200])
                    return {"success": False, "error": f"Vision API HTTP {resp.status}"}

                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)

                # Normalize keys for indemnitor intake
                extracted = cls._normalize_extracted_fields(parsed)
                return {
                    "success": True,
                    "engine": "openai_vision",
                    "extracted": extracted,
                    "raw": parsed,
                }

    @classmethod
    def _scan_with_local_ocr(cls, image_bytes: bytes, filename: str = "") -> dict[str, Any]:
        """Local OCR & Regex extraction fallback."""
        text = ""
        try:
            import io
            from PIL import Image

            img = Image.open(io.BytesIO(image_bytes))
            try:
                import pytesseract
                text = pytesseract.image_to_string(img)
            except Exception:
                try:
                    import ddddocr
                    ocr = ddddocr.DdddOcr(show_ad=False)
                    text = ocr.classification(image_bytes)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("[id_scanner] Local OCR image open error: %s", exc)

        extracted = cls.parse_raw_text(text)
        return {
            "success": True,
            "engine": "local_ocr",
            "extracted": extracted,
            "raw_text_preview": text[:300],
        }

    @classmethod
    def parse_raw_text(cls, text: str) -> dict[str, Any]:
        """Parse raw text with regex for US / FL Driver Licenses and Passports."""
        res: dict[str, Any] = {
            "first_name": None,
            "middle_name": None,
            "last_name": None,
            "full_name": None,
            "dob": None,
            "dl_number": None,
            "dl_state": "FL",
            "address": None,
            "city": None,
            "state": "FL",
            "zip": None,
            "sex": None,
            "expiration_date": None,
            "id_type": "driver_license",
        }

        if not text:
            return res

        lines = [line.strip() for line in text.splitlines() if line.strip()]

        # FL DL number pattern (1 letter + 12 digits, e.g. D123-456-78-901-0 or D123456789010)
        m_dl = re.search(r"\b([A-Z]\d{3}[-\s]?\d{3}[-\s]?\d{2}[-\s]?\d{3}[-\s]?\d|\d{9}|\d{10})\b", text)
        if m_dl:
            res["dl_number"] = m_dl.group(1).replace("-", "").replace(" ", "")

        # Passport MRZ pattern (P<USA...)
        m_pass = re.search(r"P<USA([A-Z<]+)", text)
        if m_pass:
            res["id_type"] = "passport"
            parts = [p for p in m_pass.group(1).split("<") if p]
            if len(parts) >= 2:
                res["last_name"] = parts[0]
                res["first_name"] = parts[1]
                res["full_name"] = f"{parts[1]} {parts[0]}"

        # Date of Birth (DOB 01/15/1990 or 4b 01/15/1990)
        m_dob = re.search(r"(?:DOB|4b|BIRTH|BORN)[:\s]*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})", text, re.I)
        if m_dob:
            res["dob"] = cls._normalize_date(m_dob.group(1))

        # Expiration Date (EXP 01/15/2028 or 4b/4b/4a)
        m_exp = re.search(r"(?:EXP|EXPIRES|4a)[:\s]*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})", text, re.I)
        if m_exp:
            res["expiration_date"] = cls._normalize_date(m_exp.group(1))

        # Sex / Gender (SEX M / SEX F / 15 SEX M)
        m_sex = re.search(r"(?:SEX|GENDER)[:\s]*([MF])\b", text, re.I)
        if m_sex:
            res["sex"] = m_sex.group(1).upper()

        # ZIP code pattern
        m_zip = re.search(r"\b(\d{5}(?:-\d{4})?)\b", text)
        if m_zip:
            res["zip"] = m_zip.group(1)

        # Name heuristic from lines
        for line in lines:
            if re.search(r"^1\s+([A-Z\s]+)$", line):
                res["last_name"] = line.split(maxsplit=1)[1].strip()
            elif re.search(r"^2\s+([A-Z\s]+)$", line):
                parts = line.split(maxsplit=1)[1].strip().split()
                if parts:
                    res["first_name"] = parts[0]
                    if len(parts) > 1:
                        res["middle_name"] = " ".join(parts[1:])

        if res["first_name"] or res["last_name"]:
            full = f"{res['first_name'] or ''} {res['middle_name'] or ''} {res['last_name'] or ''}".strip()
            res["full_name"] = " ".join(full.split())

        return res

    @staticmethod
    def _normalize_extracted_fields(parsed: dict[str, Any]) -> dict[str, Any]:
        """Map raw AI/OCR extracted output into standardized indemnitor intake keys."""
        first = str(parsed.get("first_name") or "").strip()
        middle = str(parsed.get("middle_name") or "").strip()
        last = str(parsed.get("last_name") or "").strip()
        full = str(parsed.get("full_name") or "").strip()

        if not full and (first or last):
            full = " ".join(filter(None, [first, middle, last]))

        dl_no = str(parsed.get("dl_number") or "").strip()
        if dl_no:
            dl_no = dl_no.replace("-", "").replace(" ", "").upper()

        dl_st = str(parsed.get("dl_state") or parsed.get("state") or "FL").strip().upper()[:2]
        st = str(parsed.get("state") or "FL").strip().upper()[:2]

        return {
            "first_name": first or None,
            "middle_name": middle or None,
            "last_name": last or None,
            "full_name": full or None,
            "dob": parsed.get("dob"),
            "dl_number": dl_no or None,
            "dl_state": dl_st or "FL",
            "address": parsed.get("address"),
            "city": parsed.get("city"),
            "state": st or "FL",
            "zip": parsed.get("zip"),
            "sex": str(parsed.get("sex") or "").strip().upper()[:1] or None,
            "expiration_date": parsed.get("expiration_date"),
            "id_type": parsed.get("id_type") or "driver_license",
        }

    @staticmethod
    def _normalize_date(raw_date: str) -> str | None:
        """Convert MM/DD/YYYY or DD-MM-YYYY to YYYY-MM-DD."""
        if not raw_date:
            return None
        parts = re.split(r"[/\-\.]", raw_date.strip())
        if len(parts) == 3:
            m, d, y = parts[0], parts[1], parts[2]
            if len(y) == 2:
                y = f"20{y}" if int(y) < 50 else f"19{y}"
            if len(m) <= 2 and len(d) <= 2 and len(y) == 4:
                try:
                    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
                except ValueError:
                    pass
        return raw_date
