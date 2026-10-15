"""Lee outreach / bounty alerts: a missing or unrecognised status is unknown, never in custody."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import lee_county_master as lee


@pytest.mark.parametrize("status,want", [
    ("In Custody", True), ("In Jail", True), ("in_custody", True), ("active", True),
    ("", False), (None, False), ("Released", False), ("Unknown", False), ("Pending", False),
])
def test_is_in_custody_status(status, want):
    assert lee.is_in_custody_status(status) is want


def _no_exists_false(q):
    return '"$exists": false' not in json.dumps(q, default=str).lower()


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *a, **k):
        return self

    def skip(self, *a):
        return self

    def limit(self, *a):
        return self

    async def to_list(self, length=None):
        return list(self.docs)


def test_autopilot_query_requires_custody_and_skips_unknown():
    db = MagicMock()
    cfg_col = MagicMock()
    cfg_col.find_one = AsyncMock(return_value={"autopilot_enabled": True, "emergency_bypass": True})
    arrests = MagicMock()
    docs = [
        {"booking_number": "A1", "status": "", "first_name": "X", "last_name": "Y"},
        {"booking_number": "A2", "first_name": "X", "last_name": "Y"},           # missing
        {"booking_number": "A3", "status": "In Custody", "first_name": "X", "last_name": "Y"},
    ]
    arrests.find.return_value = _Cursor(docs)
    db.__getitem__.side_effect = lambda k: {"lee_county_config": cfg_col, "arrests": arrests}[k]

    fam = AsyncMock(return_value=[{"phone": "+12395550100", "name": "Z"}])
    send = AsyncMock(return_value={"ok": True})
    with patch.object(lee, "find_family_contacts", fam), patch.object(lee, "send_lee_county_outreach", send):
        res = asyncio.run(lee.run_lee_county_autopilot_sweep(db))

    query = arrests.find.call_args[0][0]
    assert lee.IN_CUSTODY_FILTER in query["$and"] and _no_exists_false(query)
    sent = [c.kwargs["booking_number"] for c in send.call_args_list]
    assert sent == ["A3"]
    assert res["contacted_count"] == 1


def test_overview_in_custody_count_excludes_missing_status():
    db = MagicMock()
    arrests = MagicMock()
    queries = []

    async def count(q):
        queries.append(q)
        return 0

    arrests.count_documents = count
    arrests.find.return_value = _Cursor([])
    arrests.aggregate.return_value = _Cursor([])
    other = MagicMock()
    other.find_one = AsyncMock(return_value={})
    other.count_documents = AsyncMock(return_value=0)
    other.find.return_value = _Cursor([])
    db.__getitem__.side_effect = lambda k: arrests if k == "arrests" else other
    try:
        asyncio.run(lee.get_lee_county_overview(db))
    except Exception:
        pass  # later sections may need more fakes; the custody count runs first
    custody_q = [q for q in queries if "status" in json.dumps(q, default=str) and "In Custody" in json.dumps(q, default=str)]
    assert custody_q, "in-custody count query not issued"
    assert all(_no_exists_false(q) for q in custody_q)


def test_source_has_no_missing_status_custody_default():
    import inspect
    src = inspect.getsource(lee)
    assert 'or "In Custody"' not in src
    assert '{"status": {"$exists": False}}' not in src


def test_bounty_alert_excludes_blank_custody_status():
    from dashboard.services import bounty_hunter_service as bh

    db = MagicMock()
    arrests = MagicMock()
    arrests.find.return_value = _Cursor([])
    db.__getitem__.side_effect = lambda k: arrests
    with patch.object(bh, "SlackNotifier", MagicMock()):
        svc = bh.BountyHunterService(db=db)
        asyncio.run(svc.scan_and_alert())
    q = arrests.find.call_args[0][0]
    allowed = q["custody_status"]["$in"]
    assert "" not in allowed and None not in allowed
    assert "In Custody" in allowed
