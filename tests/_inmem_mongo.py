"""Tiny in-memory async Mongo stand-in for unit tests (no network).

Dotted update paths such as ``indemnitor.email`` are stored as nested fields.
Queries may use ``$elemMatch``. Updates may set ``array.$[name].field`` when
``array_filters`` is passed. ``find_one_and_update(..., return_document=False)``
returns the document from before the write. Omitting ``return_document`` still
returns the updated document.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List

_ARRAY_SET = re.compile(
    r"^(?P<array>[^.]+)\.\$\[(?P<ident>[A-Za-z][A-Za-z0-9]*)\]\.(?P<rest>.+)$"
)


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
        if isinstance(cond, dict) and set(cond) == {"$elemMatch"}:
            val = _get(doc, key)
            spec = cond["$elemMatch"]
            if not isinstance(val, list) or not any(
                isinstance(item, dict) and matches(item, spec) for item in val
            ):
                return False
            continue
        if not _match_value(_get(doc, key), cond):
            return False
    return True


def _array_filter_spec(array_filters: List[Dict[str, Any]], ident: str) -> Dict[str, Any]:
    prefix = ident + "."
    for raw in array_filters:
        if not isinstance(raw, dict):
            continue
        if any(str(key).startswith(prefix) for key in raw):
            return {
                str(key)[len(prefix):]: value
                for key, value in raw.items()
                if str(key).startswith(prefix)
            }
    return {"__array_filter_missing__": ident}


def _apply_array_sets(
    doc: Dict[str, Any],
    updates: List[tuple],
    array_filters: List[Dict[str, Any]],
) -> None:
    """Apply every ``array.$[name].field`` set to the elements that matched first.

    MongoDB identifies those elements on the pre-update document, then applies
    every set. Matching again after the first field changes would drop the rest.
    """
    grouped: Dict[tuple, List[tuple]] = {}
    order: List[tuple] = []
    for path, value in updates:
        match = _ARRAY_SET.match(path)
        if match is None:
            _set_path(doc, path, copy.deepcopy(value))
            continue
        key = (match.group("array"), match.group("ident"))
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append((match.group("rest"), value))
    for array_name, ident in order:
        bucket = doc.get(array_name)
        if not isinstance(bucket, list):
            continue
        spec = _array_filter_spec(array_filters, ident)
        matched = [item for item in bucket if isinstance(item, dict) and matches(item, spec)]
        for item in matched:
            for rest, value in grouped[(array_name, ident)]:
                _set_path(item, rest, copy.deepcopy(value))


def _return_before(return_document: Any) -> bool:
    """True when the caller asked for the pre-update document.

    Motor's ``ReturnDocument.BEFORE`` is ``False``. The historical fake
    default, used when ``return_document`` is omitted, stays the updated doc.
    """
    if return_document is None:
        return False
    if return_document is False:
        return True
    return getattr(return_document, "name", None) == "BEFORE"


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
        stored = copy.deepcopy(doc)
        if "_id" in stored and any(row.get("_id") == stored["_id"] for row in self.docs):
            from dashboard.services.exclusive_insert import DuplicateKeyError

            raise DuplicateKeyError("duplicate _id")
        self.docs.append(stored)

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

    def _apply(self, d, update, inserting, array_filters=None):
        filters = list(array_filters or [])
        plain = []
        array_sets = []
        for k, v in (update.get("$set") or {}).items():
            if ".$[" in k:
                array_sets.append((k, v))
            else:
                plain.append((k, v))
        for k, v in plain:
            _set_path(d, k, copy.deepcopy(v))
        _apply_array_sets(d, array_sets, filters)
        for k, v in (update.get("$inc") or {}).items():
            current = _get(d, k)
            try:
                base = int(current or 0)
            except (TypeError, ValueError):
                base = 0
            _set_path(d, k, base + int(v))
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

    async def update_one(self, flt, update, upsert=False, **kwargs):
        array_filters = kwargs.get("array_filters")
        for d in self.docs:
            if matches(d, flt):
                self._apply(d, update, inserting=False, array_filters=array_filters)
                return _WriteResult(1)
        if upsert:
            d = {k: v for k, v in flt.items() if not k.startswith("$") and not isinstance(v, dict)}
            self._apply(d, update, inserting=True, array_filters=array_filters)
            self.docs.append(d)
            return _WriteResult(0)
        return _WriteResult(0)

    async def find_one_and_update(self, flt, update, upsert=False, return_document=None, **kw):
        array_filters = kw.get("array_filters")
        for d in self.docs:
            if matches(d, flt):
                before = copy.deepcopy(d)
                self._apply(d, update, inserting=False, array_filters=array_filters)
                if _return_before(return_document):
                    return before
                return copy.deepcopy(d)
        if upsert:
            d = {k: v for k, v in (flt or {}).items() if not str(k).startswith("$") and not isinstance(v, dict)}
            self._apply(d, update, inserting=True, array_filters=array_filters)
            self.docs.append(d)
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
