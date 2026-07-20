"""Shared helpers for FastAPI routers."""
from __future__ import annotations

import csv
import io
import math
from datetime import datetime
from bson import ObjectId


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in miles between two GPS coordinates using the Haversine formula."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def mask_phone(phone: str | None) -> str:
    """PII-safe phone representation for logs — keep only the last 4 digits.

    SOC II logging rule: never write a full phone number to application logs.
    """
    if not phone:
        return "(none)"
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    return f"...{digits[-4:]}" if len(digits) >= 4 else "...****"


def serialize_doc(doc: dict) -> dict:
    """Convert values to JSON-safe types.

    - datetime  → ISO-8601 string
    - ObjectId  → hex string
    - booking_number (int/float) → string  (prevents JS .replace() TypeError)
    """
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
        elif isinstance(v, ObjectId):
            doc[k] = str(v)
        elif k == "booking_number" and not isinstance(v, str):
            doc[k] = str(v) if v is not None else ""
    return doc


async def async_csv_streamer(cursor, fieldnames: list[str]):
    """Asynchronous generator to stream CSV rows using DictWriter.

    Enforces memory safety by flushing each row directly and prevents crashes
    from MongoDB schema variations using extrasaction='ignore'.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")

    # Write and yield the header row
    writer.writeheader()
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    # Stream documents from MongoDB cursor
    async for doc in cursor:
        clean_doc = {}
        for k in fieldnames:
            v = doc.get(k)
            if v is None:
                clean_doc[k] = ""
            elif isinstance(v, datetime):
                clean_doc[k] = v.isoformat()
            elif isinstance(v, ObjectId):
                clean_doc[k] = str(v)
            elif isinstance(v, (list, dict)):
                clean_doc[k] = str(v)
            else:
                clean_doc[k] = v

        writer.writerow(clean_doc)
        data = buffer.getvalue()
        yield data
        buffer.seek(0)
        buffer.truncate(0)


async def async_csv_list_streamer(cursor, row_extractor_fn, header: list[str]):
    """Asynchronous generator to stream CSV rows generated as list rows.

    Takes a motor cursor, a mapping function `row_extractor_fn(doc) -> list`,
    and a header list, streaming formatted rows securely.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    # Write and yield the header row
    writer.writerow(header)
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    # Stream and extract rows
    async for doc in cursor:
        row = row_extractor_fn(doc)
        clean_row = []
        for v in row:
            if v is None:
                clean_row.append("")
            elif isinstance(v, datetime):
                clean_row.append(v.isoformat())
            elif isinstance(v, ObjectId):
                clean_row.append(str(v))
            elif isinstance(v, (list, dict)):
                clean_row.append(str(v))
            else:
                clean_row.append(v)

        writer.writerow(clean_row)
        data = buffer.getvalue()
        yield data
        buffer.seek(0)
        buffer.truncate(0)
