"""
ShamrockLeads OSINT Worker v2
==============================
Internal-only FastAPI service running Maigret, Sherlock, Blackbird, SpiderFoot.
The dashboard stores results in Mongo and handles auth/UI.

Endpoints:
  GET  /health
  GET  /status
  POST /v1/scan   (legacy — Maigret + Blackbird only)
  POST /v2/scan   (new — multi-engine)
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from defaults import build_username_candidates, resolve_tool_flags
from runners import execute_scan, execute_scan_v2, probe_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("osint_worker")

WORKER_KEY = os.getenv("OSINT_WORKER_KEY", "").strip()

app = FastAPI(
    title="Shamrock OSINT Worker",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
)


class ScanRequest(BaseModel):
    """Legacy v1 scan request (Maigret + Blackbird only)."""
    usernames: Optional[List[str]] = Field(default_factory=list)
    full_name: Optional[str] = None
    email: Optional[str] = None
    deep_scan: bool = False
    run_maigret: Optional[bool] = None
    run_blackbird: Optional[bool] = None
    second_opinion: bool = False


class ScanRequestV2(BaseModel):
    """v2 multi-engine scan request."""
    usernames: Optional[List[str]] = Field(default_factory=list)
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    deep_scan: bool = False
    engines: List[str] = Field(
        default_factory=lambda: ["maigret"],
        description="Engines to run: maigret, sherlock, blackbird, spiderfoot",
    )
    second_opinion: bool = False


def _check_key(x_worker_key: Optional[str]) -> None:
    if not WORKER_KEY:
        return
    if not x_worker_key or not secrets.compare_digest(x_worker_key, WORKER_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Worker-Key")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "osint-worker", "version": "2.0.0"}


@app.get("/status")
async def status(x_worker_key: Optional[str] = Header(None, alias="X-Worker-Key")):
    _check_key(x_worker_key)
    return probe_tools()


@app.post("/v1/scan")
async def scan_v1(
    body: ScanRequest,
    x_worker_key: Optional[str] = Header(None, alias="X-Worker-Key"),
):
    """Legacy synchronous scan (Maigret + Blackbird only)."""
    _check_key(x_worker_key)

    if not any([body.full_name, body.usernames, body.email]):
        raise HTTPException(status_code=422, detail="At least one identifier required")

    want_maigret, want_blackbird, policy_notes = resolve_tool_flags(
        email=body.email,
        run_maigret=body.run_maigret,
        run_blackbird=body.run_blackbird,
        second_opinion=body.second_opinion,
    )
    candidates = build_username_candidates(body.usernames, body.full_name)

    result = await execute_scan(
        usernames=candidates,
        email=body.email,
        deep_scan=body.deep_scan,
        want_maigret=want_maigret,
        want_blackbird=want_blackbird,
        policy_notes=policy_notes,
    )
    return result


@app.post("/v2/scan")
async def scan_v2(
    body: ScanRequestV2,
    x_worker_key: Optional[str] = Header(None, alias="X-Worker-Key"),
):
    """
    Multi-engine scan. Runs requested engines concurrently.
    May take 30–300s. Dashboard calls from background task.
    """
    _check_key(x_worker_key)

    if not any([body.full_name, body.usernames, body.email, body.phone]):
        raise HTTPException(status_code=422, detail="At least one identifier required")

    valid_engines = {"maigret", "sherlock", "blackbird", "spiderfoot"}
    engines = [e for e in body.engines if e in valid_engines]
    if not engines:
        raise HTTPException(status_code=422, detail="No valid engines specified")

    candidates = build_username_candidates(body.usernames, body.full_name)

    log.info(
        "v2 scan start engines=%s deep=%s users=%d email=%s phone=%s",
        engines, body.deep_scan, len(candidates), bool(body.email), bool(body.phone),
    )

    result = await execute_scan_v2(
        usernames=candidates,
        email=body.email,
        phone=body.phone,
        full_name=body.full_name,
        deep_scan=body.deep_scan,
        engines=engines,
    )
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
