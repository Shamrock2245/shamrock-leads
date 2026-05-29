"""
Dashboard Service — Social Accounts (OAuth Token Store)
=========================================================
Manages connected social media accounts and their OAuth tokens
in MongoDB. Tokens are Fernet-encrypted at rest.

Collection: social_accounts

Supports multiple accounts per platform (e.g. GBP under two
Google accounts). Each record is a unique (platform, account_id) pair.
"""

from __future__ import annotations

import logging
import os
from base64 import urlsafe_b64encode
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from typing import Optional

from cryptography.fernet import Fernet

logger = logging.getLogger("dashboard.services.social_accounts")

# ── Encryption ────────────────────────────────────────────────────────────────

_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    """Derive a Fernet key from SECRET_KEY env var (deterministic 32-byte key)."""
    global _fernet
    if _fernet is None:
        secret = os.getenv("SECRET_KEY", "shamrock-default-secret-change-me")
        key_bytes = sha256(secret.encode()).digest()
        _fernet = Fernet(urlsafe_b64encode(key_bytes))
    return _fernet


def encrypt_token(token: str) -> str:
    """Encrypt an OAuth token for storage."""
    if not token:
        return ""
    return _get_fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a stored OAuth token."""
    if not encrypted:
        return ""
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except Exception as e:
        logger.error("Token decryption failed: %s", e)
        return ""


# ── MongoDB Operations ────────────────────────────────────────────────────────

COLLECTION = "social_accounts"


def _get_collection():
    """Get the social_accounts MongoDB collection."""
    from dashboard.extensions import get_db
    return get_db()[COLLECTION]


async def store_account(
    platform: str,
    account_id: str,
    display_name: str,
    access_token: str,
    refresh_token: str = "",
    token_expires_at: Optional[datetime] = None,
    scopes: Optional[list[str]] = None,
    profile_picture: str = "",
    sub_platforms: Optional[list[str]] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """
    Store or update a connected social account.

    Upserts on (platform, account_id) — reconnecting the same account
    updates the tokens without creating a duplicate.
    """
    col = _get_collection()
    now = datetime.now(timezone.utc)

    doc = {
        "platform": platform,
        "account_id": account_id,
        "display_name": display_name,
        "profile_picture": profile_picture,
        "access_token": encrypt_token(access_token),
        "refresh_token": encrypt_token(refresh_token),
        "token_expires_at": token_expires_at,
        "scopes": scopes or [],
        "sub_platforms": sub_platforms or [],
        "connected_at": now,
        "last_refreshed": now,
        "status": "active",
        "metadata": metadata or {},
    }

    result = await col.update_one(
        {"platform": platform, "account_id": account_id},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )

    logger.info(
        "🔗 Social account stored: %s / %s (%s)",
        platform, display_name,
        "updated" if result.modified_count else "created",
    )
    return doc


async def get_active_token(platform: str, account_id: Optional[str] = None) -> Optional[dict]:
    """
    Get an active token for a platform.

    If account_id is None, returns the first active account for that platform.
    Returns dict with decrypted access_token and refresh_token.
    """
    col = _get_collection()
    query = {"platform": platform, "status": "active"}
    if account_id:
        query["account_id"] = account_id

    doc = await col.find_one(query, sort=[("connected_at", -1)])
    if not doc:
        return None

    return {
        **doc,
        "access_token": decrypt_token(doc.get("access_token", "")),
        "refresh_token": decrypt_token(doc.get("refresh_token", "")),
    }


async def get_all_active_tokens(platform: str) -> list[dict]:
    """Get all active accounts for a platform (e.g. dual GBP accounts)."""
    col = _get_collection()
    cursor = col.find({"platform": platform, "status": "active"})
    results = []
    async for doc in cursor:
        doc["access_token"] = decrypt_token(doc.get("access_token", ""))
        doc["refresh_token"] = decrypt_token(doc.get("refresh_token", ""))
        results.append(doc)
    return results


async def list_connected() -> list[dict]:
    """List all connected accounts (tokens redacted for frontend)."""
    col = _get_collection()
    cursor = col.find({"status": {"$in": ["active", "expired"]}})
    results = []
    async for doc in cursor:
        results.append({
            "platform": doc["platform"],
            "account_id": doc["account_id"],
            "display_name": doc.get("display_name", ""),
            "profile_picture": doc.get("profile_picture", ""),
            "sub_platforms": doc.get("sub_platforms", []),
            "status": doc["status"],
            "connected_at": doc.get("connected_at", ""),
            "last_refreshed": doc.get("last_refreshed", ""),
            "token_expires_at": doc.get("token_expires_at", ""),
            "scopes": doc.get("scopes", []),
            "metadata": {
                k: v for k, v in doc.get("metadata", {}).items()
                if k not in ("access_token", "refresh_token")
            },
        })
    return results


async def update_tokens(
    platform: str,
    account_id: str,
    access_token: str,
    refresh_token: Optional[str] = None,
    token_expires_at: Optional[datetime] = None,
) -> bool:
    """Update tokens after a refresh."""
    col = _get_collection()
    update = {
        "$set": {
            "access_token": encrypt_token(access_token),
            "last_refreshed": datetime.now(timezone.utc),
            "status": "active",
        }
    }
    if refresh_token:
        update["$set"]["refresh_token"] = encrypt_token(refresh_token)
    if token_expires_at:
        update["$set"]["token_expires_at"] = token_expires_at

    result = await col.update_one(
        {"platform": platform, "account_id": account_id},
        update,
    )
    if result.modified_count:
        logger.info("🔄 Tokens refreshed: %s / %s", platform, account_id)
    return result.modified_count > 0


async def mark_expired(platform: str, account_id: str) -> bool:
    """Mark an account as expired (token refresh failed)."""
    col = _get_collection()
    result = await col.update_one(
        {"platform": platform, "account_id": account_id},
        {"$set": {"status": "expired"}},
    )
    logger.warning("⚠️ Social account marked expired: %s / %s", platform, account_id)
    return result.modified_count > 0


async def disconnect(platform: str, account_id: str) -> bool:
    """Disconnect (delete) a social account."""
    col = _get_collection()
    result = await col.delete_one(
        {"platform": platform, "account_id": account_id},
    )
    if result.deleted_count:
        logger.info("🔌 Social account disconnected: %s / %s", platform, account_id)
    return result.deleted_count > 0


async def get_expiring_tokens(within_minutes: int = 60) -> list[dict]:
    """Find all accounts whose tokens expire within N minutes."""
    col = _get_collection()
    cutoff = datetime.now(timezone.utc) + timedelta(minutes=within_minutes)
    cursor = col.find({
        "status": "active",
        "refresh_token": {"$ne": ""},
        "token_expires_at": {"$lte": cutoff, "$ne": None},
    })
    results = []
    async for doc in cursor:
        doc["access_token"] = decrypt_token(doc.get("access_token", ""))
        doc["refresh_token"] = decrypt_token(doc.get("refresh_token", ""))
        results.append(doc)
    return results
