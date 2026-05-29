"""
Shamrock Social Engine — OAuth Token Bridge
=============================================
Provides dynamic token loading from MongoDB for social platform adapters.

The adapters in social/platforms/ historically load static credentials from
environment variables at import time. This bridge adds a dynamic fallback:
if a platform has an active OAuth connection in MongoDB `social_accounts`,
use that token instead.

Usage in any adapter:
    from social.oauth_bridge import get_live_token
    token = await get_live_token("twitter")
    # token = {"access_token": "...", "metadata": {...}} or None
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("social.oauth_bridge")


async def get_live_token(platform: str) -> Optional[dict]:
    """
    Fetch the active OAuth token for a platform from MongoDB.

    Returns a dict with:
        access_token, refresh_token, display_name, metadata, ...
    or None if no active connection exists.

    This function bridges the dashboard's OAuth storage (social_accounts_service)
    with the social engine's posting adapters.
    """
    try:
        from dashboard.services.social_accounts_service import get_active_token
        return await get_active_token(platform)
    except ImportError:
        logger.debug("social_accounts_service not available — using static config")
        return None
    except Exception as e:
        logger.warning("OAuth bridge error for %s: %s", platform, e)
        return None


async def get_all_live_tokens(platform: str) -> list[dict]:
    """
    Fetch all active OAuth tokens for a platform (e.g. dual GBP accounts).

    Returns a list of dicts, each with access_token, metadata, etc.
    """
    try:
        from dashboard.services.social_accounts_service import get_all_active_tokens
        return await get_all_active_tokens(platform)
    except ImportError:
        return []
    except Exception as e:
        logger.warning("OAuth bridge error (multi) for %s: %s", platform, e)
        return []


async def get_platform_metadata(platform: str) -> dict:
    """
    Get just the metadata dict for a connected platform.

    Useful for getting page_id, organization_urn, ig_business_id, etc.
    without exposing the access token.
    """
    token = await get_live_token(platform)
    if token and token.get("metadata"):
        return token["metadata"]
    return {}
