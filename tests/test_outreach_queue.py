"""
Tests for ShamrockLeads Resilient Outreach Queue Service
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from bson import ObjectId

from dashboard.services.outreach_queue import enqueue_message, process_outreach_queue


@pytest.mark.asyncio
@patch("dashboard.services.outreach_queue.get_collection")
async def test_enqueue_message(mock_get_collection):
    # Setup mock collection and insert result
    mock_col = MagicMock()
    mock_insert_result = MagicMock()
    mock_insert_result.inserted_id = ObjectId("664bca4506c134017f8b4567")
    mock_col.insert_one = AsyncMock(return_value=mock_insert_result)
    mock_get_collection.return_value = mock_col

    # Call target function
    queue_id = await enqueue_message(
        phone="+12395550178",
        message="Hello testing!",
        file_path="/tmp/test.pdf",
        context="test_ctx"
    )

    # Verify ID returned correctly
    assert queue_id == "664bca4506c134017f8b4567"

    # Verify inserted document fields
    mock_col.insert_one.assert_called_once()
    inserted_doc = mock_col.insert_one.call_args[0][0]
    assert inserted_doc["phone"] == "+12395550178"
    assert inserted_doc["message"] == "Hello testing!"
    assert inserted_doc["file_path"] == "/tmp/test.pdf"
    assert inserted_doc["context"] == "test_ctx"
    assert inserted_doc["status"] == "pending"
    assert inserted_doc["retries"] == 0
    assert isinstance(inserted_doc["next_attempt"], datetime)
    assert inserted_doc["last_error"] is None


@pytest.mark.asyncio
@patch("dashboard.services.bb_client._send_message_direct")
async def test_process_outreach_queue_success(mock_send_direct):
    # Setup mock DB and collection
    mock_db = MagicMock()
    mock_col = MagicMock()
    mock_db.__getitem__.return_value = mock_col

    # Setup direct send mock to succeed
    mock_send_direct.return_value = {"success": True, "data": {"messageId": "msg123"}}

    # Mock cursor return values
    mock_cursor = MagicMock()
    msg_doc = {
        "_id": ObjectId("664bca4506c134017f8b4567"),
        "phone": "+12395550178",
        "message": "Immediate send text",
        "retries": 0,
        "status": "pending",
    }
    
    # Simple async iterator for cursor
    async def mock_async_iter():
        yield msg_doc

    mock_cursor.__aiter__ = MagicMock(return_value=mock_async_iter())
    mock_cursor.sort.return_value = mock_cursor
    mock_col.find.return_value = mock_cursor
    
    # Mock update_one
    mock_col.update_one = AsyncMock()

    # Process queue
    results = await process_outreach_queue(mock_db)

    # Verify stats
    assert results["processed"] == 1
    assert results["sent"] == 1
    assert results["retried"] == 0
    assert results["failed"] == 0

    # Verify direct send call
    mock_send_direct.assert_called_once_with("+12395550178", "Immediate send text")

    # Verify updates made (first lock record to 'sending', then set to 'sent')
    assert mock_col.update_one.call_count == 2
    
    # Check second update (final state)
    last_update_args = mock_col.update_one.call_args_list[1][0]
    assert last_update_args[0] == {"_id": ObjectId("664bca4506c134017f8b4567")}
    assert last_update_args[1]["$set"]["status"] == "sent"
    assert isinstance(last_update_args[1]["$set"]["sent_at"], datetime)


@pytest.mark.asyncio
@patch("dashboard.services.bb_client._send_message_direct")
async def test_process_outreach_queue_retry(mock_send_direct):
    # Setup mock DB and collection
    mock_db = MagicMock()
    mock_col = MagicMock()
    mock_db.__getitem__.return_value = mock_col

    # Setup direct send mock to fail (e.g. ngrok tunnel offline)
    mock_send_direct.return_value = {"success": False, "error": "Tunnel not found"}

    # Mock cursor return values
    mock_cursor = MagicMock()
    msg_doc = {
        "_id": ObjectId("664bca4506c134017f8b4567"),
        "phone": "+12395550178",
        "message": "Retry message text",
        "retries": 1,
        "status": "pending",
    }
    
    async def mock_async_iter():
        yield msg_doc

    mock_cursor.__aiter__ = MagicMock(return_value=mock_async_iter())
    mock_cursor.sort.return_value = mock_cursor
    mock_col.find.return_value = mock_cursor
    mock_col.update_one = AsyncMock()

    # Process queue
    results = await process_outreach_queue(mock_db)

    # Verify stats
    assert results["processed"] == 1
    assert results["sent"] == 0
    assert results["retried"] == 1
    assert results["failed"] == 0

    # Verify updates made (locked to 'sending', then back to 'pending' with retry increment and backoff)
    assert mock_col.update_one.call_count == 2
    
    # Check final update args
    last_update_args = mock_col.update_one.call_args_list[1][0]
    assert last_update_args[0] == {"_id": ObjectId("664bca4506c134017f8b4567")}
    
    update_set = last_update_args[1]["$set"]
    assert update_set["status"] == "pending"
    assert update_set["retries"] == 2
    assert update_set["last_error"] == "Tunnel not found"
    
    # Backoff for retries=1: 30 * (2 ** 1) = 60 seconds
    assert isinstance(update_set["next_attempt"], datetime)
    time_diff = update_set["next_attempt"] - datetime.now(timezone.utc)
    # Allowing minor CPU latency tolerance (should be roughly 60s)
    assert 55 <= time_diff.total_seconds() <= 65


@pytest.mark.asyncio
@patch("dashboard.services.bb_client._send_message_direct")
async def test_process_outreach_queue_permanent_failure(mock_send_direct):
    # Setup mock DB and collection
    mock_db = MagicMock()
    mock_col = MagicMock()
    mock_db.__getitem__.return_value = mock_col

    # Setup direct send mock to fail
    mock_send_direct.return_value = {"success": False, "error": "Fatal iMac offline"}

    # Mock cursor returning a message that already has 4 retries (attempt #5)
    mock_cursor = MagicMock()
    msg_doc = {
        "_id": ObjectId("664bca4506c134017f8b4567"),
        "phone": "+12395550178",
        "message": "Last attempt message",
        "retries": 4,
        "status": "pending",
    }
    
    async def mock_async_iter():
        yield msg_doc

    mock_cursor.__aiter__ = MagicMock(return_value=mock_async_iter())
    mock_cursor.sort.return_value = mock_cursor
    mock_col.find.return_value = mock_cursor
    mock_col.update_one = AsyncMock()

    # Process queue
    results = await process_outreach_queue(mock_db)

    # Verify stats (processed=1, failed=1 because new_retries = 5 >= 5)
    assert results["processed"] == 1
    assert results["sent"] == 0
    assert results["retried"] == 0
    assert results["failed"] == 1

    # Verify updates
    assert mock_col.update_one.call_count == 2
    
    last_update_args = mock_col.update_one.call_args_list[1][0]
    assert last_update_args[0] == {"_id": ObjectId("664bca4506c134017f8b4567")}
    
    update_set = last_update_args[1]["$set"]
    assert update_set["status"] == "failed"
    assert update_set["last_error"] == "Fatal iMac offline"
