"""
FL511 Live Traffic Camera Auto-Resolver & Dynamic Feed Ingest.

Queries Florida DOT FL511 public API (https://fl511.com/List/GetData/Cameras)
to dynamically discover and resolve live CCTV camera streams (JPEG & HLS m3u8)
for Lee, Collier, Charlotte, and SWFL highway corridors.

Populates/refreshes config/alpr_cameras.json dynamically.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("shamrock.fl511_resolver")

FL511_API_URL = "https://fl511.com/List/GetData/Cameras"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "alpr_cameras.json"

# Priority SWFL counties for bond/skip LPR surveillance
TARGET_COUNTIES = ["Lee", "Collier", "Charlotte", "Hendry", "Sarasota", "Manatee", "Hillsborough", "Pinellas", "Orange", "Miami-Dade"]


def fetch_fl511_cameras_for_county(county: str, max_records: int = 200) -> List[Dict[str, Any]]:
    """Query FL511 API for cameras in a specific county."""
    params = {
        "draw": 1,
        "start": 0,
        "length": max_records,
        "search[value]": county,
    }
    data_bytes = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        FL511_API_URL,
        data=data_bytes,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            res = json.loads(raw)
            return res.get("data", [])
    except Exception as exc:
        logger.error("Failed to query FL511 cameras for %s: %s", county, exc)
        return []


def parse_fl511_camera_record(cam: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse raw FL511 JSON camera object into Shamrock ALPR camera schema."""
    try:
        cam_id = str(cam.get("id") or "").strip()
        if not cam_id:
            return None

        county = str(cam.get("county") or "Unknown").strip()
        roadway = str(cam.get("roadway") or "").strip()
        location = str(cam.get("location") or cam.get("name") or "").strip()
        direction = str(cam.get("direction") or "").strip()

        name_parts = [p for p in [roadway, location, direction] if p]
        name = " - ".join(name_parts) or f"FL511 Cam #{cam_id}"

        # Extract lat/lon
        lat, lon = None, None
        lat_lng_obj = cam.get("latLng") or {}
        if isinstance(lat_lng_obj, dict):
            geo = lat_lng_obj.get("geography") or {}
            wkt = geo.get("wellKnownText") or ""
            if "POINT" in wkt:
                # WKT format: "POINT (-81.742973 26.264409)" -> (lon, lat)
                coords = wkt.replace("POINT (", "").replace(")", "").split()
                if len(coords) >= 2:
                    lon = float(coords[0])
                    lat = float(coords[1])

        # Extract image and video URLs
        imgs = cam.get("images") or []
        img_url = None
        video_url = None

        if imgs and isinstance(imgs, list):
            first_img = imgs[0]
            img_path = first_img.get("imageUrl")
            if img_path:
                img_url = f"https://fl511.com{img_path}" if img_path.startswith("/") else img_path

            v_path = first_img.get("videoUrl")
            if v_path and not first_img.get("videoDisabled"):
                video_url = v_path

        # Prefer JPEG for lower latency sampling, fall back to HLS stream
        stream_url = img_url or video_url
        stream_type = "jpeg" if img_url else ("hls" if video_url else "jpeg")

        if not stream_url:
            return None

        return {
            "id": f"fl511_{cam_id}",
            "name": name,
            "county": county,
            "roadway": roadway,
            "stream_url": stream_url,
            "stream_type": stream_type,
            "video_url": video_url,
            "lat": lat,
            "lon": lon,
            "enabled": True,
            "source": "FL511-FDOT",
        }
    except Exception as exc:
        logger.debug("Skipping unparseable FL511 camera %s: %s", cam, exc)
        return None


def resolve_and_save_swfl_cameras(
    counties: Optional[List[str]] = None,
    output_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Query FL511 API for all target counties, build active camera list, and update JSON config."""
    target_list = counties or TARGET_COUNTIES
    out_file = output_path or CONFIG_PATH

    logger.info("Resolving live FL511 camera feeds for counties: %s", ", ".join(target_list))
    resolved: List[Dict[str, Any]] = []
    seen_ids = set()

    for county in target_list:
        raw_cams = fetch_fl511_cameras_for_county(county)
        for raw in raw_cams:
            parsed = parse_fl511_camera_record(raw)
            if parsed and parsed["id"] not in seen_ids:
                seen_ids.add(parsed["id"])
                resolved.append(parsed)

    if resolved:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(resolved, indent=2), encoding="utf-8")
        logger.info("Successfully saved %d live FL511 camera feeds to %s", len(resolved), out_file)
    else:
        logger.warning("No FL511 cameras resolved for target counties.")

    return resolved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cams = resolve_and_save_swfl_cameras()
    print(f"\n✅ Resolved {len(cams)} live FL511 cameras!")
    for c in cams[:5]:
        print(f"  • [{c['id']}] {c['name']} ({c['county']} Co)")
        print(f"    Stream: {c['stream_url']}")
