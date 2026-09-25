"""
Tiny in-memory async Mongo double for DocuSeal completion tests.

Supports just what dashboard/services/docuseal_completion.py and the
webhook / poller use: find_one, find(...).limit, update_one, insert_one,
find_one_and_update (atomic under an asyncio.Lock), dotted paths, $set /
$inc / $addToSet, and query operators $and / $or / $exists / $ne / $lt /
$in / $nin with Mongo's "None matches missing" semantics. No network.
"""
from __future__ import annotations

import asyncio
import copy
from typing import Any, Dict, List, Optional

_MISSING = object()


def _get(doc: Any, path: str) -> Any:
    cur = doc
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _set(doc: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = doc
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _eq(val: Any, target: Any) -> bool:
    if target is None:
        return val is _MISSING or val is None
    if val is _MISSING:
        return False
    if isinstance(val, list) and not isinstance(target, list):
        return target in val
    return val == target


def _match_value(val: Any, cond: Any) -> bool:
    if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$exists":
                if bool(arg) != (val is not _MISSING):
                    return False
            elif op == "$ne":
                if _eq(val, arg):
                    return False
            elif op == "$lt":
                if val is _MISSING or val is None or not (val < arg):
                    return False
            elif op == "$in":
                if not any(_eq(val, a) for a in arg):
                    return False
            elif op == "$nin":
                if any(_eq(val, a) for a in arg):
                    return False
            else:
                raise NotImplementedError(op)
        return True
    return _eq(val, cond)


def matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, cond in (query or {}).items():
        if key == "$and":
            if not all(matches(doc, q) for q in cond):
                return False
        elif key == "$or":
            if not any(matches(doc, q) for q in cond):
                return False
        elif not _match_value(_get(doc, key), cond):
            return False
    return True


def apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
    for op, fields in update.items():
        if op == "$set":
            for k, v in fields.items():
                _set(doc, k, copy.deepcopy(v))
        elif op == "$inc":
            for k, v in fields.items():
                cur = _get(doc, k)
                _set(doc, k, (0 if cur is _MISSING or cur is None else cur) + v)
        elif op == "$addToSet":
            for k, v in fields.items():
                cur = _get(doc, k)
                lst = list(cur) if isinstance(cur, list) else []
                if v not in lst:
                    lst.append(v)
                _set(doc, k, lst)
        else:
            raise NotImplementedError(op)


class _Result:
    def __init__(self, matched: int):
        self.matched_count = matched
        self.modified_count = matched


class _Cursor:
    def __init__(self, docs: List[dict]):
        self._docs = docs

    def limit(self, n: int) -> "_Cursor":
        self._docs = self._docs[: n or None]
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, docs: Optional[List[dict]] = None, *, yield_in_claim: bool = True):
        self.docs: List[dict] = [copy.deepcopy(d) for d in (docs or [])]
        self.updates: List[tuple] = []
        self.inserts: List[dict] = []
        self.claim_calls = 0
        self._lock = asyncio.Lock()
        self._yield = yield_in_claim

    async def insert_one(self, doc):
        self.inserts.append(copy.deepcopy(doc))
        self.docs.append(copy.deepcopy(doc))

    async def update_one(self, filt, update, **_kw):
        self.updates.append((copy.deepcopy(filt), copy.deepcopy(update)))
        for d in self.docs:
            if matches(d, filt):
                apply_update(d, update)
                return _Result(1)
        return _Result(0)

    async def find_one(self, query=None, *_a, **_kw):
        for d in self.docs:
            if matches(d, query or {}):
                return copy.deepcopy(d)
        return None

    def find(self, query=None, *_a, **_kw):
        return _Cursor([copy.deepcopy(d) for d in self.docs if matches(d, query or {})])

    async def find_one_and_update(self, filt, update, return_document=False, **_kw):
        self.claim_calls += 1
        if self._yield:
            await asyncio.sleep(0)  # let concurrent callers interleave before the atomic section
        async with self._lock:  # Mongo findOneAndUpdate is atomic per document
            for d in self.docs:
                if matches(d, filt):
                    before = copy.deepcopy(d)
                    apply_update(d, update)
                    return copy.deepcopy(d) if return_document else before
        return None
