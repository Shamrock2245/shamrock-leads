"""Request and job tenant context.

The contextvar is the only place a tenant id is allowed to come from.
Routes and jobs do not pass tenant ids into queries by hand.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator, Literal

from dashboard.tenancy.constants import (
    SAAS_APP_SUFFIX,
    SHAMROCK_HOSTS,
    SHAMROCK_TENANT_ID,
)
from dashboard.tenancy.flag import multi_tenant_enabled

Mode = Literal["tenant", "platform"]
_SLUG = re.compile(r"^[a-z][a-z0-9_]{1,48}$")


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str | None
    mode: Mode
    job_name: str = ""


@dataclass(frozen=True)
class TenantDecision:
    """Host routing result. ``membership_required`` is the authorization gate.

    The host picks a candidate tenant. It does not grant access. When the
    flag is on, a customer subdomain always requires an active membership,
    and a Shamrock host requires one whenever the caller is authenticated.
    Anonymous webhook and machine calls on Shamrock hosts stay tenant #1.
    """

    tenant_id: str | None
    membership_required: bool
    source: str


_current: ContextVar[TenantContext | None] = ContextVar("shamrock_tenant", default=None)


def current_context() -> TenantContext | None:
    return _current.get()


def current_tenant_id() -> str | None:
    ctx = _current.get()
    if ctx is None or ctx.mode != "tenant":
        return None
    return ctx.tenant_id


def normalize_slug(value: str | None) -> str | None:
    slug = (value or "").strip().lower()
    if not _SLUG.match(slug):
        return None
    return slug


def normalize_host(host: str | None) -> str:
    raw = (host or "").split(",")[0].strip().lower()
    if raw.startswith("["):
        end = raw.find("]")
        return raw[1:end] if end > 1 else raw
    if raw.count(":") == 1:
        return raw.split(":", 1)[0]
    return raw


def _authenticated(session_email: str | None) -> bool:
    return bool((session_email or "").strip())


def classify_tenant_request(
    *,
    host: str | None,
    session_tenant: str | None,
    header_tenant: str | None,
    is_platform_admin: bool,
    session_email: str | None = None,
) -> TenantDecision:
    """Route a request to a candidate tenant.

    Flag off: always Shamrock, including a hostile Host header, and the
    membership gate stays off so today's behavior does not change.

    Flag on: the host is routing input. ``{slug}.app.shamrockbailbonds.biz``
    is that slug only after an active membership check. Shamrock's own
    hostnames stay tenant #1 for anonymous callers (webhooks, machine auth).
    An authenticated caller on those hosts must be a member of the tenant
    the host selected. A platform super-admin may pass ``X-Tenant-Id``;
    that header is the impersonation path and is not a host selection.
    """
    if not multi_tenant_enabled():
        return TenantDecision(SHAMROCK_TENANT_ID, False, "flag_off")

    if is_platform_admin and (header_tenant or "").strip():
        return TenantDecision(normalize_slug(header_tenant), False, "platform_header")

    session_slug = normalize_slug(session_tenant)
    if session_slug and session_slug != SHAMROCK_TENANT_ID and not is_platform_admin:
        session_slug = None

    hostname = normalize_host(host)
    signed_in = _authenticated(session_email)
    if hostname.endswith(SAAS_APP_SUFFIX):
        label = hostname[: -len(SAAS_APP_SUFFIX)]
        if "." in label:
            return TenantDecision(None, True, "customer_host")
        return TenantDecision(normalize_slug(label), True, "customer_host")

    if (
        hostname in SHAMROCK_HOSTS
        or hostname.endswith(".shamrockbailbonds.biz")
        or hostname in {"", "localhost"}
    ):
        tenant_id = session_slug or SHAMROCK_TENANT_ID
        # Anonymous Shamrock-host traffic (webhooks, machine keys) keeps
        # tenant #1. A signed-in user must actually belong to that tenant.
        return TenantDecision(tenant_id, signed_in, "shamrock_host")
    return TenantDecision(None, True, "rejected")


def resolve_tenant_id(
    *,
    host: str | None,
    session_tenant: str | None,
    header_tenant: str | None,
    is_platform_admin: bool,
    session_email: str | None = None,
) -> str | None:
    """Candidate tenant id. Host selection still needs a membership check."""
    return classify_tenant_request(
        host=host,
        session_tenant=session_tenant,
        header_tenant=header_tenant,
        is_platform_admin=is_platform_admin,
        session_email=session_email,
    ).tenant_id


@contextmanager
def bind_job_tenant(tenant_id: str, job_name: str = "") -> Iterator[TenantContext]:
    """Run a background job as one agency. Flag off still records Shamrock."""
    slug = SHAMROCK_TENANT_ID if not multi_tenant_enabled() else normalize_slug(tenant_id)
    if not slug:
        from dashboard.tenancy.scope import TenantScopeError

        raise TenantScopeError("tenant_required")
    ctx = TenantContext(tenant_id=slug, mode="tenant", job_name=job_name)
    token: Token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


@contextmanager
def bind_platform_job(job_name: str = "") -> Iterator[TenantContext]:
    """Scrapers and other platform jobs. Tenant-owned collections fail closed."""
    ctx = TenantContext(tenant_id=None, mode="platform", job_name=job_name)
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def _header(scope: dict, name: str) -> str:
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or ():
        if key.lower() == target:
            return value.decode("latin-1", errors="ignore")
    return ""


def _state_get(scope: dict, name: str) -> str:
    state = scope.get("state")
    if state is None:
        return ""
    if isinstance(state, dict):
        return str(state.get(name) or "")
    return str(getattr(state, name, "") or "")


class TenantContextMiddleware:
    """Pure ASGI middleware so the tenant contextvar stays on the request task.

    Added inside PIN auth (see dashboard/main.py) so the signed session is
    already on scope['state']. Flag off never changes the response.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        from dashboard.auth.super_admin import is_super_admin_email

        email = _state_get(scope, "sl_email")
        role = _state_get(scope, "sl_role")
        is_admin = role in {"god_admin", "admin"} and is_super_admin_email(email)
        host = _header(scope, "x-forwarded-host") or _header(scope, "host")
        decision = classify_tenant_request(
            host=host,
            session_tenant=_state_get(scope, "sl_tenant_id"),
            header_tenant=_header(scope, "x-tenant-id"),
            is_platform_admin=is_admin,
            session_email=email,
        )
        tenant_id = decision.tenant_id
        if tenant_id is not None and decision.membership_required:
            from dashboard.tenancy.membership import has_active_membership

            if not await has_active_membership(email, tenant_id):
                tenant_id = None
        if tenant_id is None:
            from starlette.responses import JSONResponse

            response = JSONResponse({"error": "tenant_required"}, status_code=403)
            await response(scope, receive, send)
            return

        ctx = TenantContext(tenant_id=tenant_id, mode="tenant")
        token = _current.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)
