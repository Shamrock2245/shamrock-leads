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
from pymongo.errors import OperationFailure

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

        # Ensure indexes on first use — must not crash writer init
        # (a failed writer = zero scrapes written to Mongo system-wide).
        try:
            self._ensure_indexes()
        except Exception as idx_err:
            logger.error(
                "⚠️ MongoDB index ensure failed (writer will still run): %s",
                idx_err,
            )

    @staticmethod
    def _safe_create_index(collection: Collection, keys, **kwargs) -> None:
        """
        create_index that recovers from IndexKeySpecsConflict (code 86).

        Happens when an index name already exists with different options
        (e.g. sparse=True vs non-sparse). Drop + recreate; never abort writer init.
        """
        name = kwargs.get("name")
        try:
            collection.create_index(keys, **kwargs)
            return
        except OperationFailure as exc:
            # 85 IndexOptionsConflict, 86 IndexKeySpecsConflict
            if getattr(exc, "code", None) not in (85, 86):
                raise
            if not name:
                logger.warning("Index conflict without name on %s: %s", collection.name, exc)
                return
            try:
                collection.drop_index(name)
                logger.info("♻️ Dropped conflicting index %s on %s — recreating", name, collection.name)
                collection.create_index(keys, **kwargs)
            except Exception as retry_err:
                logger.warning(
                    "⚠️ Could not recreate index %s on %s: %s",
                    name,
                    collection.name,
                    retry_err,
                )
        except Exception as exc:
            logger.warning("⚠️ create_index %s on %s failed: %s", name, collection.name, exc)

    def _ensure_indexes(self):
        """Create indexes for fast dedup lookups and queries."""
        # Compound unique index for deduplication — STATE-AWARE (July 2026).
        # Multi-state expansion introduced county-name collisions (Lee FL/GA/SC,
        # Sumter FL/GA/SC, …). The legacy (county, booking_number) unique key
        # could let a GA Lee record overwrite an FL Lee record with the same
        # booking number, so the natural key is now (state, county, booking_number).
        try:
            self.arrests.drop_index("dedup_county_booking")
            logger.info("♻️ Dropped legacy dedup_county_booking index (state-aware replacement)")
        except Exception:
            pass  # Already dropped or never existed
        try:
            # Backfill: legacy docs written before `state` was mandatory default
            # to FL (the only state in production before the 2026 expansion),
            # so the new unique key never sees a null component.
            self.arrests.update_many(
                {"$or": [{"state": {"$exists": False}}, {"state": None}, {"state": ""}]},
                {"$set": {"state": "FL"}},
            )
        except Exception as backfill_err:
            logger.warning(f"⚠️ arrests.state backfill skipped: {backfill_err}")
        self._safe_create_index(
            self.arrests,
            [("state", ASCENDING), ("county", ASCENDING), ("booking_number", ASCENDING)],
            unique=True,
            name="dedup_state_county_booking",
        )
        # Query indexes
        self._safe_create_index(self.arrests, [("county", ASCENDING)], name="idx_county")
        self._safe_create_index(self.arrests, [("state", ASCENDING)], name="idx_state")
        self._safe_create_index(self.arrests, [("booking_date", DESCENDING)], name="idx_booking_date")
        self._safe_create_index(self.arrests, [("lead_score", DESCENDING)], name="idx_lead_score")
        self._safe_create_index(self.arrests, [("status", ASCENDING)], name="idx_status")
        self._safe_create_index(
            self.arrests,
            [("lead_status", ASCENDING), ("county", ASCENDING)],
            name="idx_lead_status_county",
        )
        # Retention / "oldest first" eviction indexes (M0 512MB guard)
        self._safe_create_index(
            self.arrests,
            [("updated_at", ASCENDING)],
            name="idx_updated_at",
        )
        self._safe_create_index(
            self.arrests,
            [("created_at", ASCENDING)],
            name="idx_created_at",
        )
        self._safe_create_index(
            self.arrests,
            [("scraped_at", DESCENDING)],
            name="idx_scraped_at",
            sparse=True,
        )
        self._safe_create_index(
            self.arrests,
            [("state", ASCENDING), ("lead_status", ASCENDING), ("updated_at", ASCENDING)],
            name="idx_state_lead_updated",
        )

        # Leads collection indexes
        self._safe_create_index(
            self.leads,
            [("arrest_id", ASCENDING), ("tenant_id", ASCENDING)],
            unique=True,
            name="dedup_lead",
        )

        # Scraper status index — STATE-AWARE (July 2026). A unique index on the
        # bare county name raised DuplicateKeyError when GA/SC counties sharing
        # an FL county name (Lee, Sumter, …) tried to upsert their run status,
        # silently dropping their status writes.
        try:
            self.scraper_status.drop_index("idx_scraper_status_county")
            logger.info("♻️ Dropped legacy idx_scraper_status_county index (state-aware replacement)")
        except Exception:
            pass
        try:
            self.scraper_status.update_many(
                {"$or": [{"state": {"$exists": False}}, {"state": None}, {"state": ""}]},
                {"$set": {"state": "FL"}},
            )
        except Exception as backfill_err:
            logger.warning(f"⚠️ scraper_status.state backfill skipped: {backfill_err}")
        self._safe_create_index(
            self.scraper_status,
            [("state", ASCENDING), ("county", ASCENDING)],
            unique=True,
            name="idx_scraper_status_state_county",
        )

        # FTA intelligence fields (populated by hybrid_scorer via base_scraper)
        self._safe_create_index(
            self.arrests,
            [("fta_risk_level", ASCENDING), ("fta_risk_score", DESCENDING)],
            name="idx_fta_risk",
            sparse=True,
        )

        # Phase 2: defendant_id back-reference on arrests (sparse — only set after normalization)
        self._safe_create_index(
            self.arrests,
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
                "skipped_invalid": 0,
                "sheet_name": county,
            }

        now = datetime.now(timezone.utc)
        operations = []
        skipped_invalid = 0
        # Map bulk op index → original record index (after filtering invalids)
        op_to_record_idx: list[int] = []

        for idx, record in enumerate(records):
            booking = (record.Booking_Number or "").strip()
            county_name = (record.County or county or "").strip()
            state = (record.State or "FL").strip().upper() or "FL"
            if not booking or not county_name:
                skipped_invalid += 1
                logger.debug(
                    "Skip invalid record (missing booking/county): name=%r county=%r",
                    getattr(record, "Full_Name", ""),
                    county_name,
                )
                continue
            # Normalize identity onto the record so writers/downstream agree
            record.Booking_Number = booking
            record.County = county_name
            record.State = state

            doc = record.to_mongo_doc()
            doc["state"] = state
            doc["county"] = county_name
            doc["booking_number"] = booking
            doc["updated_at"] = now  # Track when record was last refreshed (retention signal)
            doc["last_seen_at"] = now
            # Refresh scraped_at on every write so live activity KPIs stay honest;
            # created_at (setOnInsert) remains the original discovery time.
            doc["scraped_at"] = now.isoformat()

            # ── Promote FTA intelligence & charge details fields to top-level for querying ──
            extra = record.extra_data or {}
            if extra.get("charge_details"):
                doc["charge_details"] = extra["charge_details"]
            if extra.get("fta_risk_score") is not None:
                doc["fta_risk_score"] = extra["fta_risk_score"]
                doc["fta_risk_level"] = extra.get("fta_risk_level")
                doc["fta_risk_confidence"] = extra.get("fta_risk_confidence")
                doc["scoring_method"] = extra.get("scoring_method", "rule_based")
                if extra.get("ml_score") is not None:
                    doc["ml_score"] = extra["ml_score"]

            operations.append(
                UpdateOne(
                    {
                        # State-aware natural key — Lee (FL) ≠ Lee (GA) ≠ Lee (SC)
                        "state": state,
                        "county": county_name,
                        "booking_number": booking,
                    },
                    {
                        "$set": doc,
                        "$setOnInsert": {
                            "created_at": now,
                        },
                    },
                    upsert=True,
                )
            )
            op_to_record_idx.append(idx)

        if not operations:
            return {
                "total_records": 0,
                "new_records": 0,
                "updated_records": 0,
                "skipped_invalid": skipped_invalid,
                "sheet_name": county,
            }

        result = self.arrests.bulk_write(operations, ordered=False)

        # Which input records were genuinely NEW (upserted, not just refreshed)?
        # bulk_api_result["upserted"] indexes refer to `operations` order;
        # map back to original `records` indexes via op_to_record_idx.
        try:
            new_record_indexes = [
                op_to_record_idx[u["index"]]
                for u in result.bulk_api_result.get("upserted", [])
                if u.get("index") is not None and u["index"] < len(op_to_record_idx)
            ]
        except Exception:
            new_record_indexes = []

        stats = {
            "total_records": len(operations),
            "new_records": result.upserted_count,
            "updated_records": result.modified_count,
            "skipped_invalid": skipped_invalid,
            "new_record_indexes": new_record_indexes,
            "sheet_name": county,
        }

        logger.info(
            f"📝 {county}: {stats['new_records']} new, "
            f"{stats['updated_records']} updated "
            f"(of {stats['total_records']} written"
            f"{f', {skipped_invalid} skipped' if skipped_invalid else ''})"
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
        self, county: str, stats: Dict[str, Any], error: str = None, state: str = None
    ):
        """Log a scraper run (include state for multi-state ops)."""
        self.ingestion_log.insert_one({
            "timestamp": datetime.now(timezone.utc),
            "county": county,
            "state": (state or "FL").upper(),
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
        state: str = None,
        scraper_id: str = None,
    ):
        """
        Upsert the latest scraper run state into the scraper_status collection.

        Identity is multi-state aware:
        - ``county`` stored as bare name (``Lee``) for legacy readers
        - ``county_label`` as ``Lee (FL)`` and ``state`` for dashboard joins
        - ``scraper_id`` when provided (``scraper_lee`` / ``scraper_ga_lee``)

        Prefer matching on county_label when present so Lee FL ≠ Lee SC.
        """
        import re

        now = datetime.now(timezone.utc)
        bare = re.sub(r"\s*\([A-Za-z]{2}\)\s*$", "", (county or "").strip()).strip()
        st_match = re.search(r"\(([A-Za-z]{2})\)\s*$", (county or "").strip())
        st = (state or (st_match.group(1) if st_match else None) or "FL").upper()
        label = f"{bare} ({st})" if bare else county

        # Prefer state-aware identity; fall back to bare county for older docs
        filter_q: dict
        if bare:
            filter_q = {
                "$or": [
                    {"county_label": label},
                    {"county": label},
                    # Bare match only when state agrees or state not set (legacy)
                    {"county": bare, "state": st},
                    {"county": bare, "state": {"$exists": False}},
                    {"county": bare, "state": None},
                    {"county": bare, "state": ""},
                ]
            }
        else:
            filter_q = {"county": county}

        self.scraper_status.update_one(
            filter_q,
            {
                "$set": {
                    "county": bare or county,
                    "county_label": label,
                    "state": st,
                    "scraper_id": scraper_id,
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
