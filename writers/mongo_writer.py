"""
MongoDB Writer — Primary data store for ShamrockLeads.

Handles:
- Upsert by booking_number + county (dedup)
- Ingestion logging
- Index creation on first run
- Qualified lead routing
"""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from pymongo import MongoClient, UpdateOne, ASCENDING, DESCENDING
from pymongo.collection import Collection

from core.models import ArrestRecord
from config.settings import settings

logger = logging.getLogger(__name__)


class MongoWriter:
    """
    Writes ArrestRecord instances to MongoDB Atlas.

    Primary collections:
    - arrests: All scraped arrest records (upserted by dedup key)
    - leads: Scored & qualified leads (tenant-routed)
    - ingestion_log: Run-level statistics per county
    - scraper_status: One document per county — latest run state (upserted)
    """

    def __init__(self, uri: str = None, db_name: str = None):
        self.uri = uri or settings.MONGODB_URI
        self.db_name = db_name or settings.MONGODB_DB_NAME

        if not self.uri:
            raise ValueError("MONGODB_URI is required. Set it in .env")

        self.client = MongoClient(self.uri)
        self.db = self.client[self.db_name]

        # Collections
        self.arrests: Collection = self.db["arrests"]
        self.leads: Collection = self.db["leads"]
        self.ingestion_log: Collection = self.db["ingestion_log"]
        self.scraper_status: Collection = self.db["scraper_status"]

        # Ensure indexes on first use
        self._ensure_indexes()

    def _ensure_indexes(self):
        """Create indexes for fast dedup lookups and queries."""
        # Compound unique index for deduplication
        self.arrests.create_index(
            [("county", ASCENDING), ("booking_number", ASCENDING)],
            unique=True,
            name="dedup_county_booking",
        )
        # Query indexes
        self.arrests.create_index([("county", ASCENDING)], name="idx_county")
        self.arrests.create_index([("booking_date", DESCENDING)], name="idx_booking_date")
        self.arrests.create_index([("lead_score", DESCENDING)], name="idx_lead_score")
        self.arrests.create_index([("status", ASCENDING)], name="idx_status")
        self.arrests.create_index(
            [("lead_status", ASCENDING), ("county", ASCENDING)],
            name="idx_lead_status_county",
        )

        # Leads collection indexes
        self.leads.create_index(
            [("arrest_id", ASCENDING), ("tenant_id", ASCENDING)],
            unique=True,
            name="dedup_lead",
        )

        # Scraper status index
        self.scraper_status.create_index(
            [("county", ASCENDING)],
            unique=True,
            name="idx_scraper_status_county",
        )

        # Phase 2: defendant_id back-reference on arrests (sparse — only set after normalization)
        self.arrests.create_index(
            [("defendant_id", ASCENDING)],
            name="idx_defendant_id",
            sparse=True,
        )

        logger.info("✅ MongoDB indexes ensured")

    def write_records(
        self,
        records: List[ArrestRecord],
        county: str,
    ) -> Dict[str, Any]:
        """
        Upsert arrest records into MongoDB.

        Returns statistics dict matching the SheetsWriter interface for
        backward compatibility.
        """
        if not records:
            return {
                "total_records": 0,
                "new_records": 0,
                "updated_records": 0,
                "sheet_name": county,
            }

        now = datetime.now(timezone.utc)
        operations = []
        for record in records:
            doc = record.to_mongo_doc()
            doc["updated_at"] = now  # Track when record was last refreshed
            doc["scraped_at"] = now.isoformat()  # ISO string for dashboard
            operations.append(
                UpdateOne(
                    {"county": record.County, "booking_number": record.Booking_Number},
                    {
                        "$set": doc,
                        "$setOnInsert": {
                            "created_at": now,
                        },
                    },
                    upsert=True,
                )
            )

        result = self.arrests.bulk_write(operations, ordered=False)

        stats = {
            "total_records": len(records),
            "new_records": result.upserted_count,
            "updated_records": result.modified_count,
            "sheet_name": county,
        }

        logger.info(
            f"📝 {county}: {stats['new_records']} new, "
            f"{stats['updated_records']} updated "
            f"(of {stats['total_records']} total)"
        )
        return stats

    def get_arrests_by_county(
        self, county: str, limit: int = 100, status: str = None
    ) -> List[Dict[str, Any]]:
        """Query arrests for a specific county."""
        query = {"county": county}
        if status:
            query["status"] = {"$regex": status, "$options": "i"}
        cursor = self.arrests.find(query).sort("booking_date", DESCENDING).limit(limit)
        return list(cursor)

    def get_qualified_leads(
        self, county: str = None, min_score: int = 70, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get qualified (Hot) leads, optionally filtered by county."""
        query = {"lead_score": {"$gte": min_score}, "lead_status": "Hot"}
        if county:
            query["county"] = county
        cursor = self.arrests.find(query).sort("lead_score", DESCENDING).limit(limit)
        return list(cursor)

    def get_county_stats(self) -> List[Dict[str, Any]]:
        """Aggregate stats per county."""
        pipeline = [
            {
                "$group": {
                    "_id": "$county",
                    "total": {"$sum": 1},
                    "in_custody": {
                        "$sum": {
                            "$cond": [
                                {"$regexMatch": {"input": "$status", "regex": "IN CUSTODY", "options": "i"}},
                                1, 0,
                            ]
                        }
                    },
                    "avg_bond": {"$avg": "$bond_amount"},
                    "hot_leads": {
                        "$sum": {"$cond": [{"$eq": ["$lead_status", "Hot"]}, 1, 0]}
                    },
                    "latest_booking": {"$max": "$booking_date"},
                }
            },
            {"$sort": {"total": -1}},
        ]
        return list(self.arrests.aggregate(pipeline))

    def log_ingestion(
        self, county: str, stats: Dict[str, Any], error: str = None
    ):
        """Log a scraper run."""
        self.ingestion_log.insert_one({
            "timestamp": datetime.now(timezone.utc),
            "county": county,
            "total_records": stats.get("total_records", 0),
            "new_records": stats.get("new_records", 0),
            "updated_records": stats.get("updated_records", 0),
            "status": "ERROR" if error else "SUCCESS",
            "error": error,
        })

    def upsert_scraper_status(
        self,
        county: str,
        records: int = 0,
        hot: int = 0,
        warm: int = 0,
        cold: int = 0,
        disqualified: int = 0,
        duration: float = 0.0,
        status: str = "ok",
        error: str = None,
        run_count_increment: int = 1,
    ):
        """
        Upsert the latest scraper run state into the scraper_status collection.
        One document per county — always reflects the most recent run.
        The dashboard /api/status endpoint reads from this collection.
        """
        now = datetime.now(timezone.utc)
        self.scraper_status.update_one(
            {"county": county},
            {
                "$set": {
                    "county": county,
                    "last_run": now,
                    "last_run_iso": now.isoformat(),
                    "records": records,
                    "hot_leads": hot,
                    "warm_leads": warm,
                    "cold_leads": cold,
                    "disqualified": disqualified,
                    "duration_seconds": round(duration, 1),
                    "status": status,
                    "error": error,
                    "updated_at": now,
                },
                "$inc": {"run_count": run_count_increment},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    def close(self):
        self.client.close()
