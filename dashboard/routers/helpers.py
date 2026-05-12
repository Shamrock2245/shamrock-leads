"""Shared helpers for FastAPI routers."""
from datetime import datetime


def serialize_doc(doc: dict) -> dict:
    """Convert datetime values to ISO strings for JSON serialization."""
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc
