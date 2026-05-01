"""
ShamrockLeads — Self-Hosted Error Tracker
==========================================
Replaces Sentry with a zero-cost MongoDB + Slack error tracking layer.

Every error is:
1. Written to MongoDB `error_log` collection (TTL: 30 days auto-expire)
2. Sent to Slack #scraper-errors for real-time visibility
3. Queryable via /api/errors dashboard endpoint

Usage:
    from dashboard.services.error_tracker import ErrorTracker
    tracker = ErrorTracker(db)
    tracker.log_error("scraper_lee", exception, context={"url": "..."})
"""

import os
import logging
import traceback
import requests as http_requests
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class ErrorTracker:
    """
    Self-hosted error tracking: MongoDB + Slack alerts.
    Drop-in replacement for Sentry — no external dependencies.
    
    Auto-connects to MongoDB from env vars if no db is passed.
    """

    def __init__(self, db=None):
        """
        Args:
            db: PyMongo database instance. If None, auto-connects from MONGODB_URI.
        """
        if db is None:
            # Auto-connect from environment
            try:
                from pymongo import MongoClient
                mongo_uri = os.getenv("MONGODB_URI", "")
                db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
                if mongo_uri:
                    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
                    db = client[db_name]
                    logger.info("[ErrorTracker] Auto-connected to MongoDB")
            except Exception as e:
                logger.warning("[ErrorTracker] Auto-connect failed: %s — log-only mode", e)
                db = None

        self._db = db
        self._collection = db["error_log"] if db is not None else None
        self._slack_webhook = os.getenv("SLACK_WEBHOOK_ERRORS", "")

    # ── Core Logging Methods ──

    def log_error(
        self,
        source: str,
        error: Any = None,
        message: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None,
        county: Optional[str] = None,
        alert_slack: bool = True,
    ) -> Optional[str]:
        """
        Log an error to MongoDB and optionally alert Slack.

        Args:
            source: Component name (e.g., "scraper.lee", "gmail_reader")
            error: Exception object (optional)
            message: String message (optional — used if error is None, or if error is a string)
            context: Additional context dict
            details: Alias for context (convenience)
            county: County name if scraper-related
            alert_slack: Whether to fire a Slack alert

        Returns:
            Inserted document ID as string, or None if write failed.
        """
        # Normalize: accept both Exception objects and plain strings
        if isinstance(error, str):
            message = message or error
            error = None

        error_message = message or (str(error) if error else "Unknown error")
        tb = traceback.format_exc() if error else None

        record = {
            "timestamp": datetime.now(timezone.utc),
            "source": source,
            "level": "ERROR",
            "message": error_message,
            "traceback": tb,
            "context": details or context or {},
            "county": county,
        }

        doc_id = self._write_to_mongo(record)

        if alert_slack and self._slack_webhook:
            self._alert_slack(record)

        logger.error("[ErrorTracker] %s: %s", source, error_message)
        return doc_id

    def log_warning(
        self,
        source: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        county: Optional[str] = None,
    ) -> Optional[str]:
        """Log a warning-level event. No Slack alert by default."""
        record = {
            "timestamp": datetime.now(timezone.utc),
            "source": source,
            "level": "WARNING",
            "message": message,
            "traceback": None,
            "context": context or {},
            "county": county,
        }
        doc_id = self._write_to_mongo(record)
        logger.warning("[ErrorTracker] %s: %s", source, message)
        return doc_id

    def log_info(
        self,
        source: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Log an info-level event. No Slack, no alarm."""
        record = {
            "timestamp": datetime.now(timezone.utc),
            "source": source,
            "level": "INFO",
            "message": message,
            "traceback": None,
            "context": context or {},
            "county": None,
        }
        return self._write_to_mongo(record)

    # ── Query Methods ──

    def get_recent_errors(self, hours: int = 24, limit: int = 100, source: Optional[str] = None, level: Optional[str] = None) -> List[Dict]:
        """Get recent errors from MongoDB."""
        if not self._collection:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = {"timestamp": {"$gte": cutoff}}
        if source:
            query["source"] = {"$regex": source, "$options": "i"}
        if level:
            query["level"] = level.upper()
        else:
            query["level"] = {"$in": ["ERROR", "WARNING"]}
        
        cursor = self._collection.find(
            query,
            {"traceback": 0},  # Exclude full tracebacks for list view
        ).sort("timestamp", -1).limit(limit)

        results = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            # Convert datetime to ISO string for JSON serialization
            if "timestamp" in doc and hasattr(doc["timestamp"], "isoformat"):
                doc["timestamp"] = doc["timestamp"].isoformat()
            results.append(doc)
        return results

    def get_error_stats(self) -> Dict[str, Any]:
        """
        Aggregate error counts by source, level, and time window.
        Returns stats for the last 24h and 7d.
        """
        if not self._collection:
            return {"24h": {}, "7d": {}, "total": 0}

        now = datetime.now(timezone.utc)
        stats = {}

        for label, hours in [("24h", 24), ("7d", 168)]:
            cutoff = now - timedelta(hours=hours)
            pipeline = [
                {"$match": {"timestamp": {"$gte": cutoff}}},
                {"$group": {
                    "_id": {"source": "$source", "level": "$level"},
                    "count": {"$sum": 1},
                }},
            ]
            results = list(self._collection.aggregate(pipeline))
            stats[label] = {
                f"{r['_id']['source']}:{r['_id']['level']}": r["count"]
                for r in results
            }

        stats["total"] = self._collection.estimated_document_count()
        return stats

    def get_error_detail(self, error_id: str) -> Optional[Dict]:
        """Get full error details including traceback."""
        if not self._collection:
            return None
        from bson import ObjectId
        try:
            doc = self._collection.find_one({"_id": ObjectId(error_id)})
            if doc:
                doc["_id"] = str(doc["_id"])
            return doc
        except Exception:
            return None

    # ── Internal Methods ──

    def _write_to_mongo(self, record: Dict) -> Optional[str]:
        """Write an error record to MongoDB."""
        if not self._collection:
            return None
        try:
            result = self._collection.insert_one(record)
            return str(result.inserted_id)
        except Exception as e:
            logger.error("[ErrorTracker] MongoDB write failed: %s", e)
            return None

    def _alert_slack(self, record: Dict):
        """Send a Slack alert for an error."""
        if not self._slack_webhook:
            return

        source = record.get("source", "unknown")
        message = record.get("message", "")[:500]
        county = record.get("county", "")
        timestamp = record.get("timestamp", datetime.now(timezone.utc))

        county_label = f" ({county})" if county else ""

        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🚨 Error — {source}{county_label}",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"```{message}```",
                    },
                },
                {
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": f"_ErrorTracker • {timestamp.strftime('%Y-%m-%d %H:%M UTC')}_",
                    }],
                },
            ]
        }

        try:
            http_requests.post(
                self._slack_webhook,
                json=payload,
                timeout=5,
            )
        except Exception as e:
            logger.warning("[ErrorTracker] Slack alert failed: %s", e)
