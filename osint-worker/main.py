"""
ShamrockLeads OSINT Worker
==========================
Internal-only FastAPI service that runs Maigret / Blackbird on a writable
filesystem. The dashboard stores results in Mongo and handles auth/UI.

Endpoints:
  GET  /health
  GET  /status
  POST /v1/scan
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from defaults import build_username_candidates, resolve_tool_flags
from runners import execute_scan, probe_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("osint_worker")

WORKER_KEY = os.getenv("OSINT_WORKER_KEY", "").strip()

app = FastAPI(
    title="Shamrock OSINT Worker",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)


class ScanRequest(BaseModel):
    usernames: Optional[List[str]] = Field(default_factory=list)
    full_name: Optional[str] = None
    email: Optional[str] = None
    deep_scan: bool = False
    # None = apply policy defaults (maigret on, blackbird off unless email/second opinion)
    run_maigret: Optional[bool] = None
    run_blackbird: Optional[bool] = None
    second_opinion: bool = Field(
        False,
        description="Force dual-engine (Maigret + Blackbird) for a second opinion",
    )


def _check_key(x_worker_key: Optional[str]) -> None:
    if not WORKER_KEY:
        return  # open on internal network if unset (compose should set it)
    if not x_worker_key or not secrets.compare_digest(x_worker_key, WORKER_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Worker-Key")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "osint-worker"}


@app.get("/status")
async def status(x_worker_key: Optional[str] = Header(None, alias="X-Worker-Key")):
    _check_key(x_worker_key)
    return probe_tools()


@app.post("/v1/scan")
async def scan(
    body: ScanRequest,
    x_worker_key: Optional[str] = Header(None, alias="X-Worker-Key"),
):
    """
    Synchronous scan. May take 30–180s. Dashboard should call with a long timeout
    from a background task (not the browser request path).
    """
    _check_key(x_worker_key)

    if not any([body.full_name, body.usernames, body.email]):
        raise HTTPException(
            status_code=422,
            detail="At least one of full_name, usernames, or email is required",
        )

    want_maigret, want_blackbird, policy_notes = resolve_tool_flags(
        email=body.email,
        run_maigret=body.run_maigret,
        run_blackbird=body.run_blackbird,
        second_opinion=body.second_opinion,
    )
    candidates = build_username_candidates(body.usernames, body.full_name)

    log.info(
        "scan start maigret=%s blackbird=%s deep=%s users=%d email=%s",
        want_maigret,
        want_blackbird,
        body.deep_scan,
        len(candidates),
        bool(body.email),
    )

    result = await execute_scan(
        usernames=candidates,
        email=body.email,
        deep_scan=body.deep_scan,
        want_maigret=want_maigret,
        want_blackbird=want_blackbird,
        policy_notes=policy_notes,
    )
    # Strip bulky raw JSON from default response? Keep for forensic store by dashboard.
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5065")),
        workers=1,
        log_level="info",
    )
