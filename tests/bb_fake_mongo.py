"""Tiny in-memory async Mongo stand-in for BlueBubbles webhook / consent tests.

Supports just the query surface those modules use: equality (dotted paths with
list fan-out), $or/$and, $in/$nin, $regex, $exists, $gte, $ne; find_one(sort=),
find().sort().limit().to_list(), async iteration, insert_one, update_one
($set/$setOnInsert, upsert), update_many.  No network, no real database.
"""
from __future__ import annotations

import copy
import itertools
import re
from types import SimpleNamespace
from typing import Any, Dict, List

_ids = itertools.count(1)


def _resolve(doc: Any, path: str) -> List[Any]:
    current: List[Any] = [doc]
    for part in path.split("."):
        nxt: List[Any] = []
        for item in current:
            if isinstance(item, list):
                for sub in item:
                    if isinstance(sub, dict) and part in sub:
                        nxt.append(sub[part])
            elif isinstance(item, dict) and part in item:
                nxt.append(item[part])
        current = nxt
    out: List[Any] = []
    for v in current:
        if isinstance(v, list):
            out.extend(v)
            out.append(v)
        else:
            out.append(v)
    return out


def _match_cond(values: List[Any], cond: Any) -> bool:
    if isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$in":
                if not any(v in arg for v in values) and not (None in arg and not values):
                    return False
            elif op == "$nin":
                if any(v in arg for v in values):
                    return False
            elif op == "$ne":
                if any(v == arg for v in values):
                    return False
            elif op == "$exists":
                if bool(values) != bool(arg):
                    return False
            elif op == "$regex":
                rx = re.compile(arg, re.I if "i" in str(cond.get("$options", "")) else 0)
                if not any(isinstance(v, str) and rx.search(v) for v in values):
                    return False
            elif op == "$options":
                continue
            elif op == "$gte":
                if not any(v is not None and v >= arg for v in values):
                    return False
            else:
                raise NotImplementedError(op)
        return True
    return any(v == cond for v in values)


def matches(doc: Dict[str, Any], flt: Dict[str, Any] | None) -> bool:
    for key, cond in (flt or {}).items():
        if key == "$or":
            if not any(matches(doc, sub) for sub in cond):
                return False
        elif key == "$and":
            if not all(matches(doc, sub) for sub in cond):
                return False
        elif not _match_cond(_resolve(doc, key), cond):
            return False
    return True


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, key, direction=None):
        keys = key if isinstance(key, list) else [(key, direction or 1)]
        for k, d in reversed(keys):
            self._docs.sort(key=lambda x: (x.get(k) is None, x.get(k) or ""), reverse=d == -1)
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs[:length] if length else self._docs)

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, name: str):
        self.name = name
        self.docs: List[Dict[str, Any]] = []

    def _project(self, doc, projection):
        out = copy.deepcopy(doc)
        if projection and projection.get("_id") == 0:
            out.pop("_id", None)
        return out

    async def find_one(self, flt=None, projection=None, sort=None):
        docs = [d for d in self.docs if matches(d, flt)]
        if sort:
            docs = _Cursor(docs).sort(sort)._docs
        return self._project(docs[0], projection) if docs else None

    def find(self, flt=None, projection=None):
        return _Cursor([self._project(d, projection) for d in self.docs if matches(d, flt)])

    async def insert_one(self, doc):
        doc.setdefault("_id", f"oid{next(_ids)}")
        self.docs.append(copy.deepcopy(doc))
        return SimpleNamespace(inserted_id=doc["_id"])

    def _apply(self, doc, update):
        for k, v in (update.get("$set") or {}).items():
            doc[k] = copy.deepcopy(v)

    async def update_one(self, flt, update, upsert=False):
        for d in self.docs:
            if matches(d, flt):
                self._apply(d, update)
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)
        if upsert:
            new = {k: v for k, v in flt.items() if not k.startswith("$")}
            new.update(copy.deepcopy(update.get("$setOnInsert") or {}))
            self._apply(new, update)
            new.setdefault("_id", f"oid{next(_ids)}")
            self.docs.append(new)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=new["_id"])
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

    async def update_many(self, flt, update):
        n = 0
        for d in self.docs:
            if matches(d, flt):
                self._apply(d, update)
                n += 1
        return SimpleNamespace(matched_count=n, modified_count=n)


class FakeDB:
    def __init__(self):
        self.collections: Dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection(name))

    def get_collection(self, name: str) -> FakeCollection:
        return self[name]
