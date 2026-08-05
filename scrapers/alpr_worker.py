"""
FL511 ALPR Worker — ShamrockLeads
=================================
Background loop: sample public traffic cameras → Fast-ALPR → watchlist match
→ MongoDB ``lpr_hits`` + Slack on skip/watch targets.

Run:
  python -m scrapers.alpr_worker
  # or
  python scrapers/alpr_worker.py

Env:
  MONGODB_URI, MONGODB_DB_NAME
  SLACK_WEBHOOK_LEADS or SLACK_WEBHOOK_ALPR
  ALPR_FRAME_INTERVAL_S (default 2.5)
  ALPR_ENABLED=true|false (default true)
  ALPR_CAMERAS_JSON / ALPR_CAMERAS_FILE
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure project root on path when run as script
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=os.getenv("ALPR_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("alpr_worker")

_SHUTDOWN = False


def _handle_signal(signum, frame):  # noqa: ARG001
    global _SHUTDOWN
    logger.info("Shutdown signal %s received", signum)
    _SHUTDOWN = True


def _worker_status_doc(
    stream_status: Dict[str, Any],
    *,
    engine_ready: bool,
    engine_error: Optional[str],
    cycle: int,
    hits_total: int,
    last_error: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "service": "alpr-worker",
        "updated_at": datetime.now(timezone.utc),
        "cycle": cycle,
        "engine_ready": engine_ready,
        "engine_error": engine_error,
        "hits_total": hits_total,
        "last_error": last_error,
        "streams": stream_status,
        "pid": os.getpid(),
    }


def run_forever() -> None:
    if os.getenv("ALPR_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        logger.warning("ALPR_ENABLED=false — worker sleeping idle")
        while not _SHUTDOWN:
            time.sleep(30)
        return

    from services.alpr_engine import ALPREngine, probe_alpr_deps
    from services.alpr_matcher import ALPRMatcher
    from services.alpr_stream_manager import ALPRStreamManager

    deps = probe_alpr_deps()
    logger.info("ALPR deps: %s", deps)
    if not deps.get("engine_ready"):
        logger.error(
            "Vision stack incomplete (opencv=%s fast_alpr=%s). "
            "Install worker deps or run alpr-worker image. Error: %s",
            deps.get("opencv"),
            deps.get("fast_alpr"),
            deps.get("error"),
        )
        # Stay alive so orchestrators see a running container; health shows not ready
        engine = ALPREngine()
    else:
        engine = ALPREngine()

    try:
        matcher = ALPRMatcher()
    except Exception as exc:
        logger.error("Mongo init failed: %s — cannot run ALPR worker", exc)
        raise

    # Auto-resolve live FL511 cameras on startup
    if os.getenv("ALPR_AUTO_RESOLVE_FL511", "true").strip().lower() in {"1", "true", "yes"}:
        try:
            from services.fl511_camera_resolver import resolve_and_save_swfl_cameras
            resolve_and_save_swfl_cameras()
        except Exception as exc:
            logger.warning("FL511 camera auto-resolver failed on startup: %s", exc)

    streams = ALPRStreamManager()
    cycle = 0
    hits_total = 0
    last_error: Optional[str] = None

    # Heartbeat collection for dashboard status probe
    status_col = matcher.db["alpr_worker_status"]

    logger.info(
        "ALPR worker started — cameras=%d interval=%.1fs",
        len(streams.streams),
        streams.frame_interval_s,
    )

    while not _SHUTDOWN:
        cycle += 1
        t0 = time.time()
        try:
            matcher.maybe_reload(every_s=60.0)
            due = streams.iter_due_frames()
            for state, frame in due:
                if not engine.ready:
                    continue
                detections = engine.detect_bgr(frame)
                cam_meta = {
                    "id": state.camera_id,
                    "name": state.name,
                    "lat": state.lat,
                    "lon": state.lon,
                    "county": state.county,
                }
                for det in detections:
                    hit = matcher.match(
                        det.plate_text,
                        confidence=det.confidence,
                        state=det.state,
                        camera=cam_meta,
                        bbox=det.bounding_box,
                        image=frame,
                    )
                    if hit and hit.get("matched"):
                        hits_total += 1
                        logger.info(
                            "WATCHLIST HIT plate=%s cam=%s conf=%.2f def=%s",
                            det.plate_text,
                            state.camera_id,
                            det.confidence,
                            hit.get("defendant_name") or hit.get("matched_defendant_id"),
                        )
            last_error = None
        except Exception as exc:
            last_error = str(exc)[:300]
            logger.exception("ALPR cycle %d error: %s", cycle, exc)

        # Persist status every cycle (lightweight)
        try:
            status_col.update_one(
                {"_id": "alpr-worker"},
                {
                    "$set": _worker_status_doc(
                        streams.status(),
                        engine_ready=engine.ready,
                        engine_error=engine.load_error,
                        cycle=cycle,
                        hits_total=hits_total,
                        last_error=last_error,
                    )
                },
                upsert=True,
            )
        except Exception as exc:
            logger.debug("status upsert failed: %s", exc)

        elapsed = time.time() - t0
        sleep_s = max(0.5, streams.frame_interval_s - elapsed)
        # Interruptible sleep
        end = time.time() + sleep_s
        while not _SHUTDOWN and time.time() < end:
            time.sleep(min(0.5, end - time.time()))

    logger.info("ALPR worker shutting down cleanly")
    streams.close_all()
    matcher.close()


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run_forever()


if __name__ == "__main__":
    main()
