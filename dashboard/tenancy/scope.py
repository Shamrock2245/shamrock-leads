"""Tenant-scoped collection proxy.

Flag off: every method is passed through unchanged.
Flag on: tenant-owned collections require a tenant context and pin every
read and write to that tenant. A caller cannot widen the filter.
"""

from __future__ import annotations

import copy
from typing import Any

from dashboard.tenancy.constants import (
    GLOBAL_COLLECTIONS,
    TENANT_FIELD,
    is_global_collection,
    is_platform_collection,
)
from dashboard.tenancy.context import current_context
from dashboard.tenancy.flag import multi_tenant_enabled

_FORBIDDEN_OPERATORS = frozenset({"$where", "$function", "$accumulator"})
_LEAKY_METHODS = frozenset({"watch", "find_raw_batches", "distinct_raw"})


class TenantScopeError(Exception):
    """Raised when a tenant-owned query has no tenant or crosses tenants.

    ``code`` is a stable token. It is not a place for document contents.
    """

    def __init__(self, code: str = "tenant_required"):
        self.code = code
        super().__init__(code)


def tenant_scope_http_error():
    """403 body. Static so a scope failure cannot echo stored data."""
    from starlette.responses import JSONResponse

    return JSONResponse({"error": "tenant_required"}, status_code=403)


def _reject_operators(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _FORBIDDEN_OPERATORS:
                raise TenantScopeError("operator_rejected")
            _reject_operators(value)
    elif isinstance(node, list):
        for item in node:
            _reject_operators(item)


def _reject_foreign_tenant(node: Any, tenant_id: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == TENANT_FIELD and value != tenant_id:
                raise TenantScopeError("cross_tenant_rejected")
            _reject_foreign_tenant(value, tenant_id)
    elif isinstance(node, list):
        for item in node:
            _reject_foreign_tenant(item, tenant_id)


class TenantScopedCollection:
    """Proxy around a Motor or PyMongo collection, or a test double."""

    def __init__(self, raw: Any, name: str):
        self._raw = raw
        self._name = name

    def _enforced(self) -> bool:
        return multi_tenant_enabled() and not is_global_collection(self._name)

    def _tenant_id(self) -> str | None:
        """None means platform-directory access with no row filter."""
        ctx = current_context()
        if is_platform_collection(self._name):
            if ctx is not None and ctx.mode == "platform":
                return None
            raise TenantScopeError("platform_collection")
        if ctx is None or ctx.mode != "tenant" or not ctx.tenant_id:
            raise TenantScopeError("tenant_required")
        return ctx.tenant_id

    def _filter(self, filt: Any):
        if not self._enforced():
            return filt
        tenant_id = self._tenant_id()
        if tenant_id is None:
            if filt is None:
                return {}
            if not isinstance(filt, dict):
                raise TenantScopeError("filter_rejected")
            return filt
        if filt is None:
            filt = {}
        if not isinstance(filt, dict):
            raise TenantScopeError("filter_rejected")
        _reject_operators(filt)
        _reject_foreign_tenant(filt, tenant_id)
        scoped = dict(filt)
        scoped[TENANT_FIELD] = tenant_id
        return scoped

    def _stamp(self, doc: Any, tenant_id: str) -> dict:
        if not isinstance(doc, dict):
            raise TenantScopeError("document_rejected")
        if TENANT_FIELD in doc and doc[TENANT_FIELD] != tenant_id:
            raise TenantScopeError("cross_tenant_rejected")
        doc[TENANT_FIELD] = tenant_id
        return doc

    def _guard_update(self, update: Any, tenant_id: str) -> Any:
        if isinstance(update, list):
            raise TenantScopeError("pipeline_update_rejected")
        if not isinstance(update, dict):
            raise TenantScopeError("update_rejected")
        _reject_operators(update)
        update = copy.deepcopy(update)
        for op in ("$set", "$setOnInsert"):
            payload = update.get(op)
            if isinstance(payload, dict) and TENANT_FIELD in payload:
                if payload[TENANT_FIELD] != tenant_id:
                    raise TenantScopeError("cross_tenant_rejected")
                payload[TENANT_FIELD] = tenant_id
        unset = update.get("$unset")
        if isinstance(unset, dict) and TENANT_FIELD in unset:
            raise TenantScopeError("cross_tenant_rejected")
        _reject_foreign_tenant(update, tenant_id)
        return update

    def find(self, filt=None, *args, **kwargs):
        return self._raw.find(self._filter(filt), *args, **kwargs)

    def find_one(self, filt=None, *args, **kwargs):
        return self._raw.find_one(self._filter(filt), *args, **kwargs)

    def insert_one(self, doc, *args, **kwargs):
        if self._enforced():
            tenant_id = self._tenant_id()
            if tenant_id is not None:
                doc = self._stamp(doc, tenant_id)
        return self._raw.insert_one(doc, *args, **kwargs)

    def insert_many(self, docs, *args, **kwargs):
        if self._enforced():
            tenant_id = self._tenant_id()
            if tenant_id is not None:
                docs = [self._stamp(dict(doc), tenant_id) for doc in docs]
        return self._raw.insert_many(docs, *args, **kwargs)

    def update_one(self, filt, update, *args, **kwargs):
        if self._enforced():
            tenant_id = self._tenant_id()
            if tenant_id is not None:
                update = self._guard_update(update, tenant_id)
        return self._raw.update_one(self._filter(filt), update, *args, **kwargs)

    def update_many(self, filt, update, *args, **kwargs):
        if self._enforced():
            tenant_id = self._tenant_id()
            if tenant_id is not None:
                update = self._guard_update(update, tenant_id)
        return self._raw.update_many(self._filter(filt), update, *args, **kwargs)

    def replace_one(self, filt, replacement, *args, **kwargs):
        if self._enforced():
            tenant_id = self._tenant_id()
            if tenant_id is not None:
                replacement = self._stamp(dict(replacement), tenant_id)
        return self._raw.replace_one(self._filter(filt), replacement, *args, **kwargs)

    def delete_one(self, filt, *args, **kwargs):
        return self._raw.delete_one(self._filter(filt), *args, **kwargs)

    def delete_many(self, filt, *args, **kwargs):
        return self._raw.delete_many(self._filter(filt), *args, **kwargs)

    def count_documents(self, filt=None, *args, **kwargs):
        if filt is None:
            filt = {}
        return self._raw.count_documents(self._filter(filt), *args, **kwargs)

    def estimated_document_count(self, *args, **kwargs):
        if self._enforced():
            return self.count_documents({})
        return self._raw.estimated_document_count(*args, **kwargs)

    def distinct(self, key, filt=None, *args, **kwargs):
        return self._raw.distinct(key, self._filter(filt), *args, **kwargs)

    def find_one_and_update(self, filt, update, *args, **kwargs):
        if self._enforced():
            tenant_id = self._tenant_id()
            if tenant_id is not None:
                update = self._guard_update(update, tenant_id)
        return self._raw.find_one_and_update(self._filter(filt), update, *args, **kwargs)

    def find_one_and_replace(self, filt, replacement, *args, **kwargs):
        if self._enforced():
            tenant_id = self._tenant_id()
            if tenant_id is not None:
                replacement = self._stamp(dict(replacement), tenant_id)
        return self._raw.find_one_and_replace(self._filter(filt), replacement, *args, **kwargs)

    def find_one_and_delete(self, filt, *args, **kwargs):
        return self._raw.find_one_and_delete(self._filter(filt), *args, **kwargs)

    def aggregate(self, pipeline, *args, **kwargs):
        if not self._enforced():
            return self._raw.aggregate(pipeline, *args, **kwargs)
        tenant_id = self._tenant_id()
        if not isinstance(pipeline, list):
            raise TenantScopeError("pipeline_rejected")
        for stage in pipeline:
            if isinstance(stage, dict) and ("$out" in stage or "$merge" in stage):
                raise TenantScopeError("pipeline_rejected")
            _reject_operators(stage)
            if tenant_id is not None:
                _reject_foreign_tenant(stage, tenant_id)
        if tenant_id is None:
            scoped = pipeline
        else:
            scoped = [{ "$match": {TENANT_FIELD: tenant_id} }, *pipeline]
        return self._raw.aggregate(scoped, *args, **kwargs)

    def bulk_write(self, requests, *args, **kwargs):
        if self._enforced():
            raise TenantScopeError("bulk_write_rejected")
        return self._raw.bulk_write(requests, *args, **kwargs)

    def __getattr__(self, name: str):
        if name in _LEAKY_METHODS and self._enforced():
            raise TenantScopeError("operator_rejected")
        return getattr(self._raw, name)


_DB_PASSTHROUGH = frozenset(
    {
        "client",
        "name",
        "delegate",
        "codec_options",
        "read_preference",
        "read_concern",
        "write_concern",
        "list_collection_names",
        "list_collections",
        "command",
        "create_collection",
        "drop_collection",
        "validate_collection",
        "aggregate",
        "watch",
        "with_options",
    }
)


class TenantScopedDatabase:
    """Database proxy. ``db[name]`` and ``db.name`` both go through the allowlist."""

    def __init__(self, raw: Any):
        self._raw = raw

    def __getitem__(self, name: str):
        return apply_scope(self._raw[name], name)

    def get_collection(self, name: str, *args, **kwargs):
        return apply_scope(self._raw.get_collection(name, *args, **kwargs), name)

    def __getattr__(self, name: str):
        if name in _DB_PASSTHROUGH or name.startswith("_"):
            return getattr(self._raw, name)
        raw_attr = getattr(self._raw, name)
        if callable(raw_attr):
            return raw_attr
        return apply_scope(raw_attr, name)


def apply_scope(raw: Any, name: str):
    """Return ``raw`` for global data or when the flag is off."""
    if not multi_tenant_enabled() or name in GLOBAL_COLLECTIONS:
        return raw
    return TenantScopedCollection(raw, name)
