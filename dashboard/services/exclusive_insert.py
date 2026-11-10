"""Insert a document whose ``_id`` is the dedupe key.

Every MongoDB collection already has a unique index on ``_id``. Inserting a
deterministic ``_id`` is the coordination: the second insert raises
``DuplicateKeyError`` (code 11000). This module does not call ``create_index``
and does not add a collection or a migration.
"""
from __future__ import annotations

from typing import Any, Dict


class DuplicateKeyError(Exception):
    """Same contract as a Mongo duplicate-key error, for stores that are not pymongo."""

    code = 11000


def is_duplicate_key(exc: BaseException) -> bool:
    if getattr(exc, "code", None) == 11000:
        return True
    if type(exc).__name__ == "DuplicateKeyError":
        return True
    text = str(exc)
    return "E11000" in text or "duplicate key" in text.lower()


async def insert_new(collection: Any, doc: Dict[str, Any]) -> bool:
    """Insert ``doc`` or return False when its ``_id`` is already stored.

    A returned False means another writer owns that primary key. The caller
    reads the stored row and continues that row. Other errors propagate.
    """
    if not doc.get("_id"):
        raise ValueError("exclusive insert requires _id")
    try:
        await collection.insert_one(doc)
    except Exception as exc:
        if is_duplicate_key(exc):
            return False
        raise
    return True
