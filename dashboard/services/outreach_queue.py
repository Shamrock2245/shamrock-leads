"""
ShamrockLeads — Resilient outreach_queue Service
===================================================
A background queue implementation with exponential backoff for outreach
messages to handle iMac / ngrok tunnel temporary offline states.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from bson import ObjectId
from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)


async def enqueue_message(
    phone: str,
    message: str,
    file_path: Optional[str] = None,
    context: str = "outreach",
) -> str:
    """
    Queue an outreach message for dispatch via BlueBubbles.
    
    Args:
        phone: Target recipient phone number (E.164)
        message: Text body of the message
        file_path: Optional path to local attachment file
        context: Description metadata of dispatch context
        
    Returns:
        The queued document's ID as a string.
    """
    queue_col = get_collection("outreach_queue")
    now = datetime.now(timezone.utc)
    
    doc = {
        "phone": phone,
        "message": message,
        "file_path": file_path,
        "context": context,
        "status": "pending",
        "retries": 0,
        "next_attempt": now,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    
    result = await queue_col.insert_one(doc)
    logger.info("[outreach_queue] Enqueued message to ...%s [ID: %s]", phone[-4:] if phone else "???", result.inserted_id)
    return str(result.inserted_id)


async def process_outreach_queue(db=None) -> dict:
    """
    Scan all 'pending' messages in the outreach queue whose next_attempt time has passed.
    Tries direct dispatch via BlueBubblesClient. On failure, recalculates exponential backoff.
    
    Returns:
        Dict summarizing results: processed, sent, retried, failed.
    """
    queue_col = get_collection("outreach_queue") if db is None else db["outreach_queue"]
    now = datetime.now(timezone.utc)
    
    # Query for pending messages whose next_attempt time is now or in the past
    query = {
        "status": "pending",
        "next_attempt": {"$lte": now}
    }
    
    processed = 0
    sent = 0
    retried = 0
    failed = 0
    
    # Process sequentially to avoid race conditions and preserve message order
    cursor = queue_col.find(query).sort("created_at", 1)
    
    # Local imports inside function to strictly prevent circular imports with bb_client
    from dashboard.services.bb_client import _send_message_direct, _send_attachment_direct
    
    async for msg in cursor:
        processed += 1
        msg_id = msg["_id"]
        phone = msg["phone"]
        message = msg["message"]
        file_path = msg.get("file_path")
        retries = msg.get("retries", 0)
        
        # Lock record by setting status to sending
        await queue_col.update_one(
            {"_id": msg_id},
            {"$set": {"status": "sending", "updated_at": datetime.now(timezone.utc)}}
        )
        
        success = False
        error_msg = None
        
        try:
            if file_path:
                logger.info("[outreach_queue] Processing message %s with attachment to ...%s", msg_id, phone[-4:])
                res = await _send_attachment_direct(phone, message, file_path)
            else:
                logger.info("[outreach_queue] Processing text message %s to ...%s", msg_id, phone[-4:])
                res = await _send_message_direct(phone, message)
                
            if res.get("success"):
                success = True
            else:
                error_msg = res.get("error", "Unknown BlueBubbles error")
        except Exception as e:
            error_msg = str(e)
            
        now_updated = datetime.now(timezone.utc)
        if success:
            logger.info("[outreach_queue] ✅ Successfully sent queued message %s to ...%s", msg_id, phone[-4:])
            await queue_col.update_one(
                {"_id": msg_id},
                {
                    "$set": {
                        "status": "sent",
                        "sent_at": now_updated,
                        "updated_at": now_updated
                    }
                }
            )
            sent += 1
        else:
            new_retries = retries + 1
            logger.warning("[outreach_queue] ⚠️ Failed to send queued message %s (attempt %d): %s", msg_id, new_retries, error_msg)
            
            if new_retries >= 5:
                # Mark as permanently failed after 5 attempts
                logger.error("[outreach_queue] ❌ Exceeded maximum retries for message %s to ...%s", msg_id, phone[-4:])
                await queue_col.update_one(
                    {"_id": msg_id},
                    {
                        "$set": {
                            "status": "failed",
                            "last_error": error_msg,
                            "updated_at": now_updated
                        }
                    }
                )
                failed += 1
            else:
                # Recalculate exponential backoff: 30 * (2 ** retries) seconds
                backoff_seconds = 30 * (2 ** retries)
                next_attempt = now_updated + timedelta(seconds=backoff_seconds)
                
                await queue_col.update_one(
                    {"_id": msg_id},
                    {
                        "$set": {
                            "status": "pending",
                            "retries": new_retries,
                            "next_attempt": next_attempt,
                            "last_error": error_msg,
                            "updated_at": now_updated
                        }
                    }
                )
                retried += 1
                
    return {
        "processed": processed,
        "sent": sent,
        "retried": retried,
        "failed": failed
    }
