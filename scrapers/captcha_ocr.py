"""
Captcha OCR Stack — open-source ensemble for JailTracker-style image CAPTCHAs
==============================================================================
Optimal stack for short alphanumeric captchas (3–6 chars, often mixed-case,
colored text on dark backgrounds — e.g. JailTracker yellow-on-blue):

  1. **Preprocess** — color-aware masks, scale, threshold, invert (critical)
  2. **ddddocr**    — captcha-specialized, tiny, free  (always-on when installed)
  3. **Tesseract**  — classic, system binary via CLI   (no heavy Python deps)
  4. **PaddleOCR**  — production-grade (optional; heavier)
  5. **EasyOCR**    — simple PyTorch OCR (optional; heavier)

Surya is excellent for *document* layout/tables but is overkill for 100×36
captcha tiles — not loaded here.

Design:
  - Every engine is optional; missing deps soft-skip.
  - Multiple preprocess variants × engines → vote / rank candidates.
  - Caller still runs case-permutations + API multi-try (JailTracker is
    case-sensitive and wrong attempts keep the same captchaKey).

Refs (open-source):
  - PaddleOCR  https://github.com/PaddlePaddle/PaddleOCR
  - Tesseract  https://github.com/tesseract-ocr/tesseract
  - EasyOCR    https://github.com/JaidedAI/EasyOCR
  - ddddocr    https://github.com/sml2h3/ddddocr
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import shutil
import subprocess
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Lazy singletons — engines are expensive to init
_ddddocr_lock = threading.Lock()
_ddddocr_inst = None
_paddle_lock = threading.Lock()
_paddle_inst = None
_easyocr_lock = threading.Lock()
_easyocr_inst = None

MIN_LEN = 3
MAX_LEN = 6
ALPHANUM = re.compile(r"[^A-Za-z0-9]")


@dataclass
class OCRHit:
    engine: str
    variant: str
    text: str
    conf: float = 0.0  # 0–1 if available


@dataclass
class CaptchaOCRResult:
    """Ranked OCR output for a single captcha image."""
    best: str = ""
    candidates: List[str] = field(default_factory=list)
    hits: List[OCRHit] = field(default_factory=list)
    engines_used: List[str] = field(default_factory=list)

    def all_seeds(self) -> List[str]:
        """Unique seeds for case-permutation expansion (best first)."""
        out: List[str] = []
        for s in [self.best, *self.candidates]:
            s = ALPHANUM.sub("", s or "")
            if MIN_LEN <= len(s) <= MAX_LEN and s not in out:
                out.append(s)
        return out


# ── Cleaning ──────────────────────────────────────────────────────────


def _clean(text: str) -> str:
    return ALPHANUM.sub("", (text or "").strip())


def _valid(text: str) -> bool:
    t = _clean(text)
    return MIN_LEN <= len(t) <= MAX_LEN


# ── Preprocessing (JailTracker yellow/orange on dark blue) ─────────────


def _to_png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def preprocess_variants(image_bytes: bytes) -> List[Tuple[str, bytes]]:
    """
    Produce PNG variants optimized for short captcha OCR.

    JailTracker images are typically ~100×36 RGBA with yellow/orange glyphs
    on a dark blue background. Color masks beat grayscale for ddddocr/tesseract.
    """
    variants: List[Tuple[str, bytes]] = [("raw", image_bytes)]
    try:
        from PIL import Image, ImageFilter, ImageOps, ImageEnhance
        import numpy as np
    except ImportError:
        return variants

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except Exception:
        return variants

    arr = np.array(img)
    rgb = arr[:, :, :3].astype("float32")
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]

    # Yellow / warm-text mask (high R+G, lower B)
    yellow = np.clip((r + g) - 1.5 * b, 0, 255).astype("uint8")
    mask = ((r > 110) & (g > 70) & (b < 140)).astype("uint8") * 255

    gray = Image.fromarray(
        (0.299 * r + 0.587 * g + 0.114 * b).astype("uint8"), mode="L"
    )

    def add(name: str, pil_img) -> None:
        try:
            variants.append((name, _to_png_bytes(pil_img)))
        except Exception:
            pass

    # Grayscale family
    add("gray", gray)
    add("gray_ac", ImageOps.autocontrast(gray))
    add("gray_ac_sharp", ImageOps.autocontrast(gray).filter(ImageFilter.SHARPEN))
    add(
        "gray_contrast",
        ImageEnhance.Contrast(ImageOps.autocontrast(gray)).enhance(2.5),
    )
    # Pillow 9 vs 10 resampling constants
    try:
        _LANCZOS = Image.Resampling.LANCZOS
        _NEAREST = Image.Resampling.NEAREST
    except AttributeError:
        _LANCZOS = Image.LANCZOS
        _NEAREST = Image.NEAREST

    g2 = ImageOps.autocontrast(gray).resize(
        (max(gray.width * 3, 120), max(gray.height * 3, 48)), _LANCZOS
    )
    add("gray_3x", g2)

    # Yellow channel
    y_img = Image.fromarray(yellow, mode="L")
    add("yellow", ImageOps.autocontrast(y_img))
    y3 = ImageOps.autocontrast(y_img).resize(
        (max(y_img.width * 3, 120), max(y_img.height * 3, 48)), _LANCZOS
    )
    add("yellow_3x", y3)
    thr = y3.point(lambda x: 255 if x > 70 else 0)
    add("yellow_3x_thr", thr)
    add("yellow_3x_bw", ImageOps.invert(thr))  # black text on white

    # Binary mask (text white on black / black on white)
    m_img = Image.fromarray(mask, mode="L")
    m3 = m_img.resize(
        (max(m_img.width * 3, 120), max(m_img.height * 3, 48)), _NEAREST
    )
    add("mask_3x", m3)
    bw = Image.fromarray(np.where(mask > 0, 0, 255).astype("uint8"), mode="L")
    bw3 = bw.resize(
        (max(bw.width * 3, 120), max(bw.height * 3, 48)), _NEAREST
    )
    add("bw_3x", bw3)
    add("bw_3x_pad", ImageOps.expand(bw3, border=12, fill=255))
    # Slight dilate for thin glyphs
    add("bw_3x_dil", bw3.filter(ImageFilter.MaxFilter(3)))

    # Inverted grayscale (white text → black)
    add("invert", ImageOps.invert(ImageOps.autocontrast(gray)))
    inv3 = ImageOps.invert(ImageOps.autocontrast(gray)).resize(
        (max(gray.width * 3, 120), max(gray.height * 3, 48)), _LANCZOS
    )
    add("invert_3x", inv3)

    return variants


# ── Engines ───────────────────────────────────────────────────────────


def _get_ddddocr():
    global _ddddocr_inst
    if _ddddocr_inst is not None:
        return _ddddocr_inst
    with _ddddocr_lock:
        if _ddddocr_inst is not None:
            return _ddddocr_inst
        import ddddocr

        _ddddocr_inst = ddddocr.DdddOcr(show_ad=False)
        return _ddddocr_inst


def _engine_ddddocr(png: bytes, variant: str) -> List[OCRHit]:
    try:
        ocr = _get_ddddocr()
    except Exception as e:
        logger.debug("ddddocr unavailable: %s", e)
        return []
    try:
        ans = _clean(ocr.classification(png) or "")
        if _valid(ans):
            return [OCRHit("ddddocr", variant, ans, conf=0.55)]
    except Exception as e:
        logger.debug("ddddocr fail [%s]: %s", variant, e)
    return []


def _engine_tesseract(png: bytes, variant: str) -> List[OCRHit]:
    """Call system tesseract CLI (no pytesseract required)."""
    if not shutil.which("tesseract"):
        return []
    hits: List[OCRHit] = []
    # psm 7 = single text line; 8 = single word; 13 = raw line
    for psm in (7, 8, 13):
        try:
            proc = subprocess.run(
                [
                    "tesseract",
                    "stdin",
                    "stdout",
                    "--psm",
                    str(psm),
                    "-l",
                    "eng",
                    "-c",
                    "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
                ],
                input=png,
                capture_output=True,
                timeout=8,
            )
            ans = _clean(proc.stdout.decode("utf-8", errors="ignore"))
            if _valid(ans):
                hits.append(OCRHit("tesseract", f"{variant}/psm{psm}", ans, conf=0.45))
        except Exception as e:
            logger.debug("tesseract fail: %s", e)
    return hits


def _get_paddle():
    global _paddle_inst
    if _paddle_inst is not None:
        return _paddle_inst
    with _paddle_lock:
        if _paddle_inst is not None:
            return _paddle_inst
        # paddleocr is optional and heavy — only load if explicitly allowed or installed
        from paddleocr import PaddleOCR

        # use_angle_cls=False for tiny captchas; english only
        _paddle_inst = PaddleOCR(
            use_angle_cls=False,
            lang="en",
            show_log=False,
            use_gpu=False,
        )
        return _paddle_inst


def _engine_paddle(png: bytes, variant: str) -> List[OCRHit]:
    if os.getenv("CAPTCHA_OCR_PADDLE", "1").lower() in ("0", "false", "no"):
        return []
    try:
        ocr = _get_paddle()
    except Exception as e:
        logger.debug("PaddleOCR unavailable: %s", e)
        return []
    try:
        # paddleocr accepts ndarray or path; use numpy from PNG
        from PIL import Image
        import numpy as np

        arr = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
        result = ocr.ocr(arr, cls=False)
        hits: List[OCRHit] = []
        if not result:
            return hits
        # result: list of pages → list of [box, (text, conf)]
        for page in result:
            if not page:
                continue
            parts = []
            confs = []
            for line in page:
                if not line or len(line) < 2:
                    continue
                text, conf = line[1][0], float(line[1][1] or 0)
                parts.append(text)
                confs.append(conf)
            joined = _clean("".join(parts))
            if _valid(joined):
                hits.append(
                    OCRHit(
                        "paddleocr",
                        variant,
                        joined,
                        conf=sum(confs) / max(len(confs), 1),
                    )
                )
        return hits
    except Exception as e:
        logger.debug("PaddleOCR fail [%s]: %s", variant, e)
        return []


def _get_easyocr():
    global _easyocr_inst
    if _easyocr_inst is not None:
        return _easyocr_inst
    with _easyocr_lock:
        if _easyocr_inst is not None:
            return _easyocr_inst
        import easyocr

        _easyocr_inst = easyocr.Reader(["en"], gpu=False, verbose=False)
        return _easyocr_inst


def _engine_easyocr(png: bytes, variant: str) -> List[OCRHit]:
    if os.getenv("CAPTCHA_OCR_EASYOCR", "1").lower() in ("0", "false", "no"):
        return []
    try:
        reader = _get_easyocr()
    except Exception as e:
        logger.debug("EasyOCR unavailable: %s", e)
        return []
    try:
        # detail=1 → (bbox, text, conf)
        results = reader.readtext(png, detail=1, allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
        if not results:
            return []
        parts = []
        confs = []
        for item in results:
            if len(item) >= 3:
                parts.append(str(item[1]))
                confs.append(float(item[2] or 0))
        joined = _clean("".join(parts))
        if _valid(joined):
            return [
                OCRHit(
                    "easyocr",
                    variant,
                    joined,
                    conf=sum(confs) / max(len(confs), 1),
                )
            ]
    except Exception as e:
        logger.debug("EasyOCR fail [%s]: %s", variant, e)
    return []


# ── Ensemble ───────────────────────────────────────────────────────────

# Default order: light → heavy. Skip heavy engines unless installed.
_ENGINE_ORDER = (
    ("ddddocr", _engine_ddddocr),
    ("tesseract", _engine_tesseract),
    ("paddleocr", _engine_paddle),
    ("easyocr", _engine_easyocr),
)


def _rank_candidates(hits: Sequence[OCRHit]) -> List[str]:
    """
    Rank unique cleaned strings by:
      votes (engine+variant hits) + max confidence + engine prior.
    """
    if not hits:
        return []

    engine_prior = {
        "ddddocr": 0.15,   # captcha-specialized
        "paddleocr": 0.12,
        "easyocr": 0.10,
        "tesseract": 0.08,
    }

    scores: Dict[str, float] = {}
    votes: Counter = Counter()
    for h in hits:
        t = _clean(h.text)
        if not _valid(t):
            continue
        votes[t] += 1
        scores[t] = scores.get(t, 0.0) + float(h.conf or 0) + engine_prior.get(
            h.engine, 0.05
        )

    # Prefer strings that multiple engines agree on (case-sensitive vote first)
    ranked = sorted(
        scores.keys(),
        key=lambda s: (votes[s], scores[s], -abs(4 - len(s))),  # prefer len≈4
        reverse=True,
    )

    # Also surface case-folded consensus: if many agree ignoring case, keep best-cased
    by_lower: Dict[str, List[str]] = {}
    for s in ranked:
        by_lower.setdefault(s.lower(), []).append(s)
    # Prefer the casing that scored highest for each lower-group
    final: List[str] = []
    seen_lower = set()
    for s in ranked:
        low = s.lower()
        if low in seen_lower:
            # still keep if different casing and high votes (JailTracker is case-sensitive)
            if s not in final:
                final.append(s)
            continue
        seen_lower.add(low)
        final.append(s)
    return final


def solve_captcha_image(
    image_b64: str,
    *,
    engines: Optional[Iterable[str]] = None,
    max_variants: int = 12,
    label: str = "captcha",
) -> CaptchaOCRResult:
    """
    Run the open-source OCR stack on a base64 captcha image.

    Args:
        image_b64: raw base64 or data-URL
        engines: subset of {"ddddocr","tesseract","paddleocr","easyocr"}; None = all available
        max_variants: cap preprocess variants (speed)
        label: log prefix
    """
    b64 = image_b64.split(",", 1)[1] if "," in image_b64 else image_b64
    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        logger.warning("[%s] captcha_ocr: bad base64: %s", label, e)
        return CaptchaOCRResult()

    variants = preprocess_variants(raw)
    # Prefer high-signal variants first, then fill
    preferred = (
        "raw",
        "yellow_3x_bw",
        "bw_3x_pad",
        "bw_3x",
        "yellow_3x_thr",
        "mask_3x",
        "invert_3x",
        "gray_3x",
        "gray_ac_sharp",
        "yellow",
        "bw_3x_dil",
        "gray_contrast",
    )
    ordered: List[Tuple[str, bytes]] = []
    by_name = {n: b for n, b in variants}
    for name in preferred:
        if name in by_name:
            ordered.append((name, by_name[name]))
    for name, blob in variants:
        if name not in dict(ordered):
            ordered.append((name, blob))
    ordered = ordered[:max_variants]

    allow = set(engines) if engines is not None else None
    engine_fns = [
        (name, fn)
        for name, fn in _ENGINE_ORDER
        if allow is None or name in allow
    ]

    all_hits: List[OCRHit] = []
    used: List[str] = []

    for eng_name, eng_fn in engine_fns:
        eng_hits: List[OCRHit] = []
        for var_name, png in ordered:
            eng_hits.extend(eng_fn(png, var_name))
            # Early stop per engine once we have a few hits
            if len(eng_hits) >= 4:
                break
        if eng_hits:
            used.append(eng_name)
            all_hits.extend(eng_hits)
            logger.info(
                "[%s] captcha_ocr/%s → %s",
                label,
                eng_name,
                [(h.text, h.variant, round(h.conf, 2)) for h in eng_hits[:4]],
            )

    ranked = _rank_candidates(all_hits)
    best = ranked[0] if ranked else ""
    if best:
        logger.info(
            "[%s] captcha_ocr BEST=%r engines=%s alts=%s",
            label,
            best,
            used,
            ranked[1:6],
        )
    else:
        logger.warning("[%s] captcha_ocr: no valid read (engines tried=%s)", label, used)

    return CaptchaOCRResult(
        best=best,
        candidates=ranked,
        hits=all_hits,
        engines_used=used,
    )


def available_engines() -> Dict[str, bool]:
    """Probe which engines can load (for health checks / diagnostics)."""
    out = {
        "ddddocr": False,
        "tesseract": bool(shutil.which("tesseract")),
        "paddleocr": False,
        "easyocr": False,
    }
    try:
        import ddddocr  # noqa: F401

        out["ddddocr"] = True
    except Exception:
        pass
    try:
        import paddleocr  # noqa: F401

        out["paddleocr"] = True
    except Exception:
        pass
    try:
        import easyocr  # noqa: F401

        out["easyocr"] = True
    except Exception:
        pass
    return out
