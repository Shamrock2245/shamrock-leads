"""
Deduplication Engine — Prevents duplicate record ingestion.

Uses the composite key: County + Booking_Number.
Backed by an in-memory LRU cache + MongoDB upsert for persistent dedup.
"""

import logging
from typing import List, Set

from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class DedupEngine:
    """
    In-memory deduplication filter.

    Called between scrape() and write() to filter out records that
    have already been seen in the current runtime session.

    MongoDB's upsert handles cross-session dedup at the storage layer.
    """

    def __init__(self, max_keys: int = 100_000):
        self._seen: Set[str] = set()
        self._max_keys = max_keys

    def filter_new(self, records: List[ArrestRecord]) -> List[ArrestRecord]:
        """Return only records not yet seen in this session."""
        new_records = []
        for record in records:
            key = record.get_dedup_key()
            if key not in self._seen:
                self._seen.add(key)
                new_records.append(record)

        dupes = len(records) - len(new_records)
        if dupes > 0:
            logger.info(f"🔁 Dedup: {dupes} duplicates filtered, {len(new_records)} new")

        # Evict oldest entries if cache grows too large
        if len(self._seen) > self._max_keys:
            excess = len(self._seen) - self._max_keys
            # Remove first `excess` items (not truly LRU, but sufficient)
            to_remove = list(self._seen)[:excess]
            for k in to_remove:
                self._seen.discard(k)
            logger.info(f"♻️ Evicted {excess} entries from dedup cache")

        return new_records

    def seed_from_mongo(self, mongo_writer) -> int:
        """Seed the dedup cache from existing MongoDB records."""
        try:
            cursor = mongo_writer.arrests.find(
                {}, {"county": 1, "booking_number": 1, "_id": 0}
            )
            count = 0
            for doc in cursor:
                key = f"{doc.get('county', '')}:{doc.get('booking_number', '')}"
                self._seen.add(key)
                count += 1
            logger.info(f"📚 Seeded dedup cache with {count} existing records")
            return count
        except Exception as e:
            logger.error(f"❌ Failed to seed dedup cache: {e}")
            return 0

    @property
    def size(self) -> int:
        return len(self._seen)

    def clear(self):
        self._seen.clear()
