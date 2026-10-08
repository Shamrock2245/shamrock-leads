"""Tiny in-memory async Mongo stand-in for unit tests (no network).

Dotted update paths such as ``indemnitor.email`` are stored as nested fields.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List


def _set_path(doc: Dict[str, Any], key: str, value: Any) -> None:
    """Write a Mongo update path, including dotted fields such as indemnitor.email."""
    if "." not in key:
        doc[key] = value
        return
    parts = key.split(".")
    node = doc
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _get(doc: Dict[str, Any], key: str):
    cur: Any = doc
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _match_value(val, cond) -> bool:
    if isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
        if "$regex" in cond:
            flags = re.IGNORECASE if "i" in str(cond.get("$options") or "") else 0
            if val is None or re.search(str(cond["$regex"]), str(val), flags) is None:
                return False
        for op, arg in cond.items():
            if op in ("$regex", "$options"):
                continue
            if op == "$in" and val not in arg:
                return False
            if op == "$nin" and val in arg:
                return False
            if op == "$lte" and not (val is not None and val <= arg):
                return False
            if op == "$lt" and not (val is not None and val < arg):
                return False
            if op == "$gte" and not (val is not None and val >= arg):
                return False
            if op == "$gt" and not (val is not None and val > arg):
                return False
            if op == "$ne" and val == arg:
                return False
            if op == "$exists" and (val is not None) != bool(arg):
                return False
        return True
    return val == cond


def matches(doc: Dict[str, Any], flt: Dict[str, Any]) -> bool:
    for key, cond in (flt or {}).items():
        if key == "$or":
            if not any(matches(doc, sub) for sub in cond):
                return False
            continue
        if key == "$and":
            if not all(matches(doc, sub) for sub in cond):
                return False
            continue
        if not _match_value(_get(doc, key), cond):
            return False
    return True


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def limit(self, n: int):
        self._docs = self._docs[:n]
        return self

    def sort(self, *a, **k):
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return copy.deepcopy(next(self._it))
        except StopIteration:
            raise StopAsyncIteration


class _WriteResult:
    def __init__(self, matched: int):
        self.matched_count = matched
        self.modified_count = matched


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    async def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    async def find_one(self, flt=None, projection=None, sort=None, **kwargs):
        hits = [d for d in self.docs if matches(d, flt or {})]
        if sort:
            for field, direction in reversed(list(sort)):
                hits.sort(
                    key=lambda d, field=field: (d.get(field) is None, d.get(field)),
                    reverse=int(direction) < 0,
                )
        if not hits:
            return None
        return copy.deepcopy(hits[0])

    def find(self, flt=None, projection=None):
        return _Cursor([d for d in self.docs if matches(d, flt or {})])

    def _apply(self, d, update, inserting):
        for k, v in (update.get("$set") or {}).items():
            _set_path(d, k, copy.deepcopy(v))
        for k, v in (update.get("$push") or {}).items():
            if "." in k:
                bucket = _get(d, k)
                if not isinstance(bucket, list):
                    bucket = []
                    _set_path(d, k, bucket)
                bucket.append(copy.deepcopy(v))
            else:
                bucket = d.get(k)
                if not isinstance(bucket, list):
                    bucket = []
                    d[k] = bucket
                bucket.append(copy.deepcopy(v))
        if inserting:
            for k, v in (update.get("$setOnInsert") or {}).items():
                _set_path(d, k, copy.deepcopy(v))

    async def update_one(self, flt, update, upsert=False):
        for d in self.docs:
            if matches(d, flt):
                self._apply(d, update, inserting=False)
                return _WriteResult(1)
        if upsert:
            d = {k: v for k, v in flt.items() if not k.startswith("$") and not isinstance(v, dict)}
            self._apply(d, update, inserting=True)
            self.docs.append(d)
            return _WriteResult(0)
        return _WriteResult(0)

    async def find_one_and_update(self, flt, update, **kw):
        for d in self.docs:
            if matches(d, flt):
                self._apply(d, update, inserting=False)
                return copy.deepcopy(d)
        return None


class FakeDB(dict):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __missing__(self, name):
        col = FakeCollection()
        self[name] = col
        return col

    def get_collection(self, name):
        return self[name]
