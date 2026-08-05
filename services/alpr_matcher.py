"""
ALPR watchlist matcher + Slack alerts + Mongo persistence.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

LPR_HITS = "lpr_hits"
LPR_WATCHLIST = "lpr_watchlist"
CROPS_DIR = Path(os.getenv("ALPR_CROPS_DIR", "/tmp/alpr_crops"))


def _normalize_plate(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def get_sync_mongo_db():
    """Sync PyMongo database (worker-friendly)."""
    from pymongo import MongoClient

    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("MONGODB_URI is not set")
    db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    return client, client[db_name]


def ensure_indexes(db) -> None:
    try:
        db[LPR_HITS].create_index([("plate_text", 1), ("timestamp", -1)])
        db[LPR_HITS].create_index([("matched_defendant_id", 1), ("timestamp", -1)])
        db[LPR_HITS].create_index([("timestamp", -1)])
        db[LPR_HITS].create_index([("camera_id", 1), ("timestamp", -1)])
        db[LPR_WATCHLIST].create_index("plate_text", unique=True)
        db[LPR_WATCHLIST].create_index("defendant_id")
        db[LPR_WATCHLIST].create_index("active")
    except Exception as exc:
        logger.warning("ALPR index ensure failed: %s", exc)


def load_watchlist_plates(db) -> Dict[str, Dict[str, Any]]:
    """Return map plate_text → watchlist doc for active entries."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        cur = db[LPR_WATCHLIST].find({"active": {"$ne": False}})
        for doc in cur:
            plate = _normalize_plate(doc.get("plate_text") or doc.get("plate") or "")
            if plate:
                out[plate] = doc
    except Exception as exc:
        logger.warning("watchlist load failed: %s", exc)
    return out


def load_active_bond_plates(db) -> Dict[str, Dict[str, Any]]:
    """
    Optional enrichment: plates on active bonds / defendants.

    Looks for common field names without failing if schema differs.
    """
    out: Dict[str, Dict[str, Any]] = {}
    try:
        # active_bonds may store vehicle_plate / plate / vehicles[]
        for doc in db["active_bonds"].find(
            {"status": {"$in": ["active", "monitoring", "alert", "reinstated"]}},
            {
                "vehicle_plate": 1,
                "plate": 1,
                "plate_number": 1,
                "defendant_name": 1,
                "Defendant_Name": 1,
                "case_number": 1,
                "Case_Number": 1,
                "defendant_id": 1,
                "Defendant_ID": 1,
            },
        ).limit(5000):
            for key in ("vehicle_plate", "plate", "plate_number"):
                p = _normalize_plate(doc.get(key) or "")
                if p:
                    out[p] = {
                        "plate_text": p,
                        "defendant_id": str(
                            doc.get("defendant_id") or doc.get("Defendant_ID") or ""
                        ),
                        "defendant_name": doc.get("defendant_name")
                        or doc.get("Defendant_Name")
                        or "",
                        "case_number": doc.get("case_number")
                        or doc.get("Case_Number")
                        or "",
                        "source": "active_bonds",
                    }
    except Exception as exc:
        logger.debug("active_bonds plate load skipped: %s", exc)
    return out


def save_plate_crop(image, bbox: Optional[List[float]], plate_text: str) -> Optional[str]:
    """Save cropped plate image to disk; return local path (or None)."""
    if image is None:
        return None
    try:
        import cv2

        CROPS_DIR.mkdir(parents=True, exist_ok=True)
        crop = image
        if bbox and len(bbox) >= 4:
            # Support [x1,y1,x2,y2]
            h, w = image.shape[:2]
            x1, y1, x2, y2 = [int(float(v)) for v in bbox[:4]]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 > x1 and y2 > y1:
                crop = image[y1:y2, x1:x2]
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe = _normalize_plate(plate_text) or "UNKNOWN"
        path = CROPS_DIR / f"{ts}_{safe}.jpg"
        cv2.imwrite(str(path), crop)
        return str(path)
    except Exception as exc:
        logger.debug("crop save failed: %s", exc)
        return None


def send_alpr_slack_alert(hit: Dict[str, Any]) -> bool:
    """Post skip-target alert to Slack (SLACK_WEBHOOK_LEADS or ALPR-specific)."""
    webhook = (
        os.getenv("SLACK_WEBHOOK_ALPR", "").strip()
        or os.getenv("SLACK_WEBHOOK_LEADS", "").strip()
    )
    if not webhook:
        logger.warning("No Slack webhook for ALPR alert")
        return False

    plate = hit.get("plate_text", "?")
    name = hit.get("defendant_name") or "Unknown"
    case = hit.get("case_number") or "—"
    cam = hit.get("camera_name") or hit.get("camera_id") or "—"
    conf = hit.get("confidence")
    conf_s = f"{float(conf):.0%}" if conf is not None else "—"
    ts = hit.get("timestamp") or datetime.now(timezone.utc).isoformat()
    lat, lon = None, None
    loc = hit.get("location") or {}
    if isinstance(loc, dict) and loc.get("coordinates"):
        try:
            lon, lat = loc["coordinates"][0], loc["coordinates"][1]
        except Exception:
            pass
    map_link = ""
    if lat is not None and lon is not None:
        map_link = f"https://maps.google.com/?q={lat},{lon}"

    text = (
        f"🚨 *SKIP TARGET PLATE SPOTTED*\n"
        f"• *Defendant:* {name}\n"
        f"• *Case #:* {case}\n"
        f"• *Plate:* `{plate}` ({hit.get('state') or 'FL'})\n"
        f"• *Camera:* {cam}\n"
        f"• *Confidence:* {conf_s}\n"
        f"• *Time:* {ts}\n"
    )
    if map_link:
        text += f"• *Map:* {map_link}\n"

    try:
        import requests

        r = requests.post(
            webhook,
            json={"text": text},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as exc:
        logger.warning("ALPR Slack alert failed: %s", exc)
        return False


class ALPRMatcher:
    """Cross-reference detections against watchlist + active bond plates."""

    def __init__(self, db=None):
        self._client = None
        if db is not None:
            self.db = db
        else:
            self._client, self.db = get_sync_mongo_db()
        ensure_indexes(self.db)
        self._watch: Dict[str, Dict[str, Any]] = {}
        self._watch_loaded_at = 0.0
        self._reload_watchlist()

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass

    def _reload_watchlist(self) -> None:
        import time

        w = load_watchlist_plates(self.db)
        bonds = load_active_bond_plates(self.db)
        # Explicit watchlist wins over bond inference
        merged = dict(bonds)
        merged.update(w)
        self._watch = merged
        self._watch_loaded_at = time.time()
        logger.info("ALPR watchlist loaded: %d plates", len(self._watch))

    def maybe_reload(self, every_s: float = 60.0) -> None:
        import time

        if time.time() - self._watch_loaded_at >= every_s:
            self._reload_watchlist()

    def match(
        self,
        plate_text: str,
        *,
        confidence: float,
        state: str,
        camera: Dict[str, Any],
        bbox: Optional[List[float]] = None,
        image=None,
    ) -> Optional[Dict[str, Any]]:
        """
        If plate is on watchlist, persist hit + Slack and return the hit doc.
        Unmatched detections are not stored (noise control) unless
        ``ALPR_STORE_ALL_HITS=true``.
        """
        plate = _normalize_plate(plate_text)
        if not plate or len(plate) < 3:
            return None

        self.maybe_reload()
        watch = self._watch.get(plate)
        store_all = os.getenv("ALPR_STORE_ALL_HITS", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }

        if not watch and not store_all:
            return None

        crop_path = save_plate_crop(image, bbox, plate)
        now = datetime.now(timezone.utc)
        lat, lon = camera.get("lat"), camera.get("lon")
        location = None
        if lat is not None and lon is not None:
            location = {"type": "Point", "coordinates": [float(lon), float(lat)]}

        hit: Dict[str, Any] = {
            "plate_text": plate,
            "state": (state or "FL")[:4].upper(),
            "confidence": float(confidence or 0.0),
            "camera_id": camera.get("id") or camera.get("camera_id"),
            "camera_name": camera.get("name") or camera.get("camera_name"),
            "county": camera.get("county") or "",
            "location": location,
            "timestamp": now,
            "matched": bool(watch),
            "matched_defendant_id": (watch or {}).get("defendant_id")
            or (watch or {}).get("matched_defendant_id")
            or "",
            "defendant_name": (watch or {}).get("defendant_name") or "",
            "case_number": (watch or {}).get("case_number") or "",
            "watchlist_source": (watch or {}).get("source") or "watchlist",
            "image_crop_path": crop_path or "",
            "image_crop_url": "",  # filled if later uploaded to CDN
            "bounding_box": bbox,
            "created_at": now,
        }

        try:
            res = self.db[LPR_HITS].insert_one(hit)
            hit["_id"] = res.inserted_id
        except Exception as exc:
            logger.error("Failed to insert lpr_hit: %s", exc)
            return None

        if watch:
            try:
                send_alpr_slack_alert(hit)
            except Exception as exc:
                logger.warning("Slack after hit failed: %s", exc)

        return hit

    def add_watchlist(
        self,
        plate_text: str,
        defendant_id: str,
        *,
        defendant_name: str = "",
        case_number: str = "",
        notes: str = "",
        actor: str = "api",
    ) -> Dict[str, Any]:
        plate = _normalize_plate(plate_text)
        if not plate or len(plate) < 3:
            raise ValueError("Invalid plate_text")
        if not defendant_id:
            raise ValueError("defendant_id required")
        now = datetime.now(timezone.utc)
        doc = {
            "plate_text": plate,
            "defendant_id": str(defendant_id),
            "defendant_name": defendant_name or "",
            "case_number": case_number or "",
            "notes": notes or "",
            "active": True,
            "source": "manual",
            "created_at": now,
            "updated_at": now,
            "created_by": actor,
        }
        self.db[LPR_WATCHLIST].update_one(
            {"plate_text": plate},
            {"$set": doc, "$setOnInsert": {"first_seen_at": now}},
            upsert=True,
        )
        self._reload_watchlist()
        return doc
