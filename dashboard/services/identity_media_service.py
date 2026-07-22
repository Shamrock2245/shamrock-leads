"""
Identity media (DL/ID front & back + selfie) storage helpers.

Shared by indemnitor and defendant upload routes. Files live under
``dashboard/uploads/<entity_key>/`` and metadata is stored on the parent
Mongo document as ``id_photos`` (typed slots) + optional ``kyc_uploads`` list.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# dashboard/uploads  (same path indemnitors router already uses)
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "heic"}

# Canonical identity slots — required by the UI
ID_PHOTO_SLOTS = {
    "govt_id_front": "Driver License / ID (Front)",
    "govt_id_back": "Driver License / ID (Back)",
    "selfie": "Selfie / Photo Verification",
}

# Extra KYC types (indemnitor documents tab)
EXTRA_DOC_TYPES = {
    "pay_stub": "Pay Stub / Proof of Income",
    "utility_bill": "Utility Bill / Proof of Address",
    "other": "Other Supporting Document",
}

ALL_DOC_TYPES = {**ID_PHOTO_SLOTS, **EXTRA_DOC_TYPES}


def safe_entity_key(raw: str) -> str:
    """Sanitize a booking# / entity id for use as a directory name."""
    key = (raw or "").strip()
    # Collapse path separators and dots that enable traversal
    key = key.replace("..", "").replace("/", "_").replace("\\", "_")
    return key or "unknown"


def save_upload_file(
    entity_key: str,
    doc_type: str,
    original_filename: str,
    contents: bytes,
) -> dict[str, Any]:
    """Write bytes to disk and return upload metadata dict."""
    if doc_type not in ALL_DOC_TYPES:
        doc_type = "other"

    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File type .{ext or '?'} not allowed. Use: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    key = safe_entity_key(entity_key)
    entity_dir = UPLOAD_DIR / key
    entity_dir.mkdir(parents=True, exist_ok=True)

    file_id = str(uuid.uuid4())[:8]
    safe_name = f"{doc_type}_{file_id}.{ext}"
    file_path = entity_dir / safe_name
    file_path.write_bytes(contents)

    now = datetime.now(timezone.utc)
    return {
        "file_id": file_id,
        "filename": original_filename,
        "saved_as": safe_name,
        "doc_type": doc_type,
        "doc_type_label": ALL_DOC_TYPES.get(doc_type, "Other"),
        "extension": ext,
        "size_bytes": len(contents),
        "uploaded_at": now.isoformat(),
        "path": str(file_path),
        "url": f"/uploads/{key}/{safe_name}",
        "entity_key": key,
    }


def delete_upload_file(meta: dict) -> None:
    """Best-effort delete of a file on disk from metadata."""
    path = Path(meta.get("path") or "")
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
    else:
        # Fallback: reconstruct from entity_key + saved_as
        key = meta.get("entity_key") or ""
        saved = meta.get("saved_as") or ""
        if key and saved:
            candidate = UPLOAD_DIR / safe_entity_key(key) / saved
            if candidate.is_file():
                try:
                    candidate.unlink()
                except OSError:
                    pass


def resolve_upload_path(entity_key: str, filename: str) -> Optional[Path]:
    """Return a resolved path inside UPLOAD_DIR, or None if invalid/missing."""
    if ".." in filename or "/" in filename or "\\" in filename:
        return None
    key = safe_entity_key(entity_key)
    path = (UPLOAD_DIR / key / filename).resolve()
    try:
        path.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


def slot_map_from_uploads(uploads: list) -> dict[str, Optional[dict]]:
    """Pick the latest upload per identity slot for UI rendering."""
    slots: dict[str, Optional[dict]] = {k: None for k in ID_PHOTO_SLOTS}
    for u in uploads or []:
        dt = u.get("doc_type")
        if dt in slots:
            # Prefer most recent by uploaded_at
            prev = slots[dt]
            if prev is None or str(u.get("uploaded_at", "")) >= str(prev.get("uploaded_at", "")):
                slots[dt] = u
    return slots


def merge_id_photos_field(existing: dict, upload_meta: dict) -> dict:
    """Update the structured id_photos map with a new slot upload."""
    photos = dict(existing or {})
    dt = upload_meta.get("doc_type")
    if dt in ID_PHOTO_SLOTS:
        photos[dt] = {
            "file_id": upload_meta["file_id"],
            "saved_as": upload_meta["saved_as"],
            "url": upload_meta["url"],
            "uploaded_at": upload_meta["uploaded_at"],
            "extension": upload_meta.get("extension", ""),
            "size_bytes": upload_meta.get("size_bytes", 0),
            "filename": upload_meta.get("filename", ""),
        }
    return photos
