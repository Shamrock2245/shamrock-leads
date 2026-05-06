"""
ShamrockLeads — BlueBubbles Firebase URL Sync
=============================================
Polls the Firebase Firestore database to keep the BlueBubbles server URL
up to date on the VPS — no manual restarts or URL updates needed.

How It Works
------------
When BlueBubbles Server starts on the office iMac, it writes its current
tunnel URL (Cloudflare, ngrok, etc.) to a Firestore document. This module
reads that document every 5 minutes and automatically updates the in-memory
BB_SERVERS config on the VPS.

This replaces the need for a static BLUEBUBBLES_URL in .env — the URL is
always fetched live from Firebase.

Firestore Document Path (BlueBubbles default):
  servers/{server_id}/config  →  { "serverURL": "https://..." }

Environment Variables
---------------------
  FIREBASE_ADMINSDK_PATH  — Path to the firebase-adminsdk.json file
                            Default: /app/config/firebase-adminsdk.json (Docker container path)
  FIREBASE_POLL_INTERVAL  — Seconds between polls (default: 300)
  FIREBASE_ENABLED        — Set to "false" to disable (default: "true")
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_ADMINSDK_PATH = os.getenv(
    "FIREBASE_ADMINSDK_PATH",
    "/app/config/firebase-adminsdk.json"  # Docker container path (host: /opt/shamrock-leads/config/)
)
_POLL_INTERVAL = int(os.getenv("FIREBASE_POLL_INTERVAL", "300"))  # 5 minutes
_ENABLED = os.getenv("FIREBASE_ENABLED", "true").lower() != "false"

_firebase_app = None
_last_known_url: Optional[str] = None


def _init_firebase() -> bool:
    """Initialize the Firebase Admin SDK. Returns True on success."""
    global _firebase_app
    if _firebase_app is not None:
        return True
    if not os.path.exists(_ADMINSDK_PATH):
        logger.warning(
            "Firebase Admin SDK not found at %s — Firebase URL sync disabled",
            _ADMINSDK_PATH
        )
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials
        cred = credentials.Certificate(_ADMINSDK_PATH)
        _firebase_app = firebase_admin.initialize_app(cred, name="shamrock-bb-sync")
        logger.info("☘️  Firebase Admin SDK initialized (project: bluebubblesapp-e2c4e)")
        return True
    except Exception as e:
        logger.error("Firebase init failed: %s", e)
        return False


def _fetch_bb_url_from_firestore() -> Optional[str]:
    """
    Fetch the current BlueBubbles server URL from Firestore.

    BlueBubbles writes its URL to Firestore under the collection 'servers'.
    The document contains a 'serverURL' field with the current tunnel URL.
    """
    try:
        from firebase_admin import firestore
        db = firestore.client(app=_firebase_app)

        # BlueBubbles stores server info in the 'servers' collection
        servers_ref = db.collection("servers")
        docs = list(servers_ref.limit(5).stream())

        if not docs:
            logger.debug("Firebase: no documents in 'servers' collection yet")
            return None

        for doc in docs:
            data = doc.to_dict()
            logger.debug("Firebase doc %s: %s", doc.id, data)

            # Try common field names BlueBubbles uses
            url = (
                data.get("serverURL") or
                data.get("server_url") or
                data.get("url") or
                data.get("ngrokUrl") or
                data.get("cloudflareUrl") or
                data.get("proxyUrl")
            )
            if url and url.startswith("http"):
                return url.rstrip("/")

        # Also check top-level 'config' collection
        config_ref = db.collection("config")
        config_docs = list(config_ref.limit(5).stream())
        for doc in config_docs:
            data = doc.to_dict()
            url = data.get("serverURL") or data.get("server_url") or data.get("url")
            if url and url.startswith("http"):
                return url.rstrip("/")

        return None

    except Exception as e:
        logger.error("Firebase Firestore fetch failed: %s", e)
        return None


async def poll_firebase_for_bb_url() -> None:
    """
    Background async loop that polls Firebase every FIREBASE_POLL_INTERVAL seconds
    and updates the in-memory BB_SERVERS config when the URL changes.
    """
    global _last_known_url

    if not _ENABLED:
        logger.info("Firebase URL sync disabled (FIREBASE_ENABLED=false)")
        return

    if not _init_firebase():
        logger.warning("Firebase URL sync could not start — SDK init failed")
        return

    logger.info(
        "☘️  Firebase BB URL poller started (interval: %ds, path: %s)",
        _POLL_INTERVAL, _ADMINSDK_PATH
    )

    while True:
        try:
            # Run the blocking Firestore call in a thread pool
            loop = asyncio.get_event_loop()
            url = await loop.run_in_executor(None, _fetch_bb_url_from_firestore)

            if url and url != _last_known_url:
                from dashboard.extensions import update_bb_url, BB_SERVERS
                update_bb_url("0178", url)
                logger.info(
                    "☘️  Firebase URL sync: BB URL updated → %s (was: %s)",
                    url, _last_known_url or "none"
                )
                _last_known_url = url
            elif url:
                logger.debug("Firebase URL sync: URL unchanged (%s)", url)
            else:
                logger.debug(
                    "Firebase URL sync: no URL in Firestore yet "
                    "(BB may not have started or written its URL)"
                )

        except Exception as e:
            logger.error("Firebase URL sync loop error: %s", e)

        await asyncio.sleep(_POLL_INTERVAL)


def get_last_known_firebase_url() -> Optional[str]:
    """Return the last URL fetched from Firebase (for health checks)."""
    return _last_known_url
