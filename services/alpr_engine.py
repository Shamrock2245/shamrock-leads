"""
Fast-ALPR model wrapper — ShamrockLeads

Lazy-loads ``fast-alpr`` + OpenCV so the dashboard image can import status
helpers without vision packages. Worker image installs the heavy deps.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Min confidence to emit a detection (0–1)
DEFAULT_MIN_CONFIDENCE = float(os.getenv("ALPR_MIN_CONFIDENCE", "0.55"))


@dataclass
class PlateDetection:
    plate_text: str
    confidence: float
    state: str = "FL"
    bounding_box: Optional[List[float]] = None  # [x1,y1,x2,y2] or polygon flat
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


def normalize_plate(text: str) -> str:
    """Uppercase alphanumeric plate token."""
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


def _to_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, (list, tuple)):
        return _to_float(val[0], default=default) if val else default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def probe_alpr_deps() -> Dict[str, Any]:
    """Return availability of vision stack (no model load)."""
    out: Dict[str, Any] = {
        "opencv": False,
        "fast_alpr": False,
        "engine_ready": False,
        "error": None,
    }
    try:
        import cv2  # noqa: F401

        out["opencv"] = True
    except Exception as exc:
        out["error"] = f"opencv: {exc}"
    try:
        import fast_alpr  # noqa: F401

        out["fast_alpr"] = True
    except Exception as exc:
        msg = f"fast_alpr: {exc}"
        out["error"] = f"{out['error']}; {msg}" if out["error"] else msg
    out["engine_ready"] = bool(out["opencv"] and out["fast_alpr"])
    return out


class ALPREngine:
    """
    Thin wrapper around Fast-ALPR.

    Usage::

        engine = ALPREngine()
        hits = engine.detect_bgr(frame)  # numpy BGR image
        hits = engine.detect_bytes(jpeg_bytes)
    """

    def __init__(
        self,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        detector_model: Optional[str] = None,
        ocr_model: Optional[str] = None,
    ):
        self.min_confidence = min_confidence
        self.detector_model = detector_model or os.getenv("ALPR_DETECTOR_MODEL", "")
        self.ocr_model = ocr_model or os.getenv("ALPR_OCR_MODEL", "")
        self._alpr = None
        self._load_error: Optional[str] = None
        self._loaded_at: Optional[float] = None

    @property
    def ready(self) -> bool:
        return self._ensure_loaded()

    @property
    def load_error(self) -> Optional[str]:
        self._ensure_loaded()
        return self._load_error

    def _ensure_loaded(self) -> bool:
        if self._alpr is not None:
            return True
        if self._load_error and self._alpr is None:
            # Allow one retry after failure by clearing error via reload()
            pass
        try:
            from fast_alpr import ALPR  # type: ignore

            kwargs: Dict[str, Any] = {}
            if self.detector_model:
                kwargs["detector_model"] = self.detector_model
            if self.ocr_model:
                kwargs["ocr_model"] = self.ocr_model
            # Constructor signature varies by version — try flexible init
            try:
                self._alpr = ALPR(**kwargs) if kwargs else ALPR()
            except TypeError:
                self._alpr = ALPR()
            self._loaded_at = time.time()
            self._load_error = None
            logger.info("Fast-ALPR engine loaded")
            return True
        except Exception as exc:
            self._load_error = str(exc)[:300]
            logger.warning("Fast-ALPR not available: %s", self._load_error)
            return False

    def reload(self) -> bool:
        self._alpr = None
        self._load_error = None
        return self._ensure_loaded()

    def detect_bgr(self, image) -> List[PlateDetection]:
        """Run ALPR on a BGR numpy array (OpenCV default)."""
        if image is None:
            return []
        if not self._ensure_loaded():
            return []
        try:
            # Prefer RGB if library expects it
            results = self._predict(image)
            return self._parse_results(results)
        except Exception as exc:
            logger.warning("ALPR detect_bgr failed: %s", exc)
            return []

    def detect_bytes(self, data: bytes) -> List[PlateDetection]:
        """Decode image bytes (JPEG/PNG) and run ALPR."""
        if not data:
            return []
        try:
            import cv2
            import numpy as np

            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                logger.warning("ALPR: failed to decode image bytes")
                return []
            return self.detect_bgr(img)
        except Exception as exc:
            logger.warning("ALPR detect_bytes failed: %s", exc)
            return []

    def detect_path(self, path: str) -> List[PlateDetection]:
        try:
            import cv2

            img = cv2.imread(path)
            return self.detect_bgr(img)
        except Exception as exc:
            logger.warning("ALPR detect_path failed: %s", exc)
            return []

    def _predict(self, image) -> Any:
        """Call into fast-alpr with version-tolerant method names."""
        alpr = self._alpr
        assert alpr is not None
        for name in ("predict", "detect", "run", "process"):
            fn = getattr(alpr, name, None)
            if callable(fn):
                return fn(image)
        # Some versions expose __call__
        if callable(alpr):
            return alpr(image)
        raise RuntimeError("fast-alpr ALPR instance has no predict/detect method")

    def _parse_results(self, results: Any) -> List[PlateDetection]:
        detections: List[PlateDetection] = []
        if results is None:
            return detections

        items: List[Any]
        if isinstance(results, list):
            items = results
        elif isinstance(results, dict):
            items = (
                results.get("plates")
                or results.get("detections")
                or results.get("results")
                or [results]
            )
        else:
            # Object with attributes
            items = getattr(results, "plates", None) or getattr(
                results, "detections", None
            ) or [results]

        for item in items:
            det = self._item_to_detection(item)
            if det and det.confidence >= self.min_confidence and det.plate_text:
                detections.append(det)
        return detections

    def _item_to_detection(self, item: Any) -> Optional[PlateDetection]:
        if item is None:
            return None
        if isinstance(item, PlateDetection):
            return item

        # fast-alpr ALPRResult: .ocr.text / .ocr.confidence / .detection.bounding_box
        ocr = getattr(item, "ocr", None)
        detection = getattr(item, "detection", None)
        if ocr is not None or detection is not None:
            text = ""
            conf_val: Any = 0.0
            if ocr is not None:
                text = str(getattr(ocr, "text", None) or getattr(ocr, "prediction", None) or "")
                conf_val = getattr(ocr, "confidence", None)
                if isinstance(conf_val, (list, tuple)):
                    nums = [_to_float(x) for x in conf_val]
                    conf_val = (sum(nums) / len(nums)) if nums else 0.0
            region = ""
            if ocr is not None:
                region = str(getattr(ocr, "region", None) or "")
            bbox = None
            if detection is not None:
                bb = getattr(detection, "bounding_box", None)
                if bb is not None:
                    if hasattr(bb, "x1"):
                        bbox = [
                            float(getattr(bb, "x1", 0) or 0),
                            float(getattr(bb, "y1", 0) or 0),
                            float(getattr(bb, "x2", 0) or 0),
                            float(getattr(bb, "y2", 0) or 0),
                        ]
                    elif hasattr(bb, "__iter__"):
                        bbox = list(bb)
            plate = normalize_plate(text)
            if not plate:
                return None
            return PlateDetection(
                plate_text=plate,
                confidence=_to_float(conf_val),
                state=(region[:4].upper() if region else "FL"),
                bounding_box=bbox,
                raw={},
            )

        if isinstance(item, dict):
            text = (
                item.get("plate")
                or item.get("plate_text")
                or item.get("text")
                or item.get("label")
                or ""
            )
            conf = item.get("confidence") or item.get("score") or item.get("prob") or 0.0
            bbox = item.get("bounding_box") or item.get("bbox") or item.get("box")
            state = item.get("state") or "FL"
            return PlateDetection(
                plate_text=normalize_plate(str(text)),
                confidence=_to_float(conf),
                state=str(state or "FL")[:4].upper(),
                bounding_box=list(bbox) if bbox is not None and hasattr(bbox, "__iter__") else None,
                raw={k: v for k, v in item.items() if k not in ("image", "crop")},
            )

        # Object-style result from fast-alpr (ALPRResult)
        text = (
            getattr(item, "plate", None)
            or getattr(item, "text", None)
            or getattr(item, "label", None)
            or ""
        )
        conf_val = getattr(item, "confidence", None) or getattr(item, "score", None)

        # Nested OCR result in fast-alpr
        ocr = getattr(item, "ocr", None) or getattr(item, "recognition", None)
        if ocr is not None:
            if isinstance(ocr, (list, tuple)) and ocr:
                ocr = ocr[0]
            if not text:
                text = getattr(ocr, "text", None) or getattr(ocr, "label", None) or getattr(ocr, "prediction", None) or ""
            ocr_conf = getattr(ocr, "confidence", None) or getattr(ocr, "score", None)
            if ocr_conf is not None:
                conf_val = ocr_conf

        bbox = getattr(item, "bounding_box", None) or getattr(item, "bbox", None)
        if bbox is not None and hasattr(bbox, "tolist"):
            bbox = bbox.tolist()

        return PlateDetection(
            plate_text=normalize_plate(str(text)),
            confidence=_to_float(conf_val),
            state="FL",
            bounding_box=list(bbox) if bbox is not None and hasattr(bbox, "__iter__") else None,
            raw={},
        )


# Module-level singleton for ad-hoc scan endpoint
_engine: Optional[ALPREngine] = None


def get_alpr_engine() -> ALPREngine:
    global _engine
    if _engine is None:
        _engine = ALPREngine()
    return _engine
